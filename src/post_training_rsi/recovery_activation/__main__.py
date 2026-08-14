from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import (
    RecoveryActivationContractError,
    RecoveryActivationError,
    RecoveryActivationPlan,
    RecoveryActivationRequest,
    RecoveryPreflightObservation,
    canonical_json,
)
from .planner import (
    DEFAULT_ALLOWED_REVIEWER_ROLES,
    DEFAULT_MAX_PLAN_TTL_SECONDS,
    RecoveryActivationPolicy,
    plan_sha256,
    run_preflight,
    verify_plan,
)

MAX_INPUT_BYTES = 4 * 1024 * 1024


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m post_training_rsi.recovery_activation",
        description=(
            "Build and verify content-bound recovery activation plans without "
            "executing a pointer switch"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "build",
        help="convert an authorized request into an immutable activation plan",
    )
    build.add_argument("--request", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    _add_policy_arguments(build)

    verify = subparsers.add_parser(
        "verify",
        help="verify one activation plan and its static policy bounds",
    )
    verify.add_argument("--plan", type=Path, required=True)
    _add_policy_arguments(verify)

    preflight = subparsers.add_parser(
        "preflight",
        help="compare a plan with explicit live/evidence observations",
    )
    preflight.add_argument("--plan", type=Path, required=True)
    preflight.add_argument("--observation", type=Path, required=True)
    _add_policy_arguments(preflight)
    return parser


def _add_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--allowed-reviewer-role",
        action="append",
        default=None,
        help=(
            "allowed reviewer role; repeat to define a complete local allowlist "
            f"(default: {', '.join(DEFAULT_ALLOWED_REVIEWER_ROLES)})"
        ),
    )
    parser.add_argument(
        "--max-plan-ttl-seconds",
        type=int,
        default=DEFAULT_MAX_PLAN_TTL_SECONDS,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        policy = RecoveryActivationPolicy.from_roles(
            args.allowed_reviewer_role or DEFAULT_ALLOWED_REVIEWER_ROLES,
            max_plan_ttl_seconds=args.max_plan_ttl_seconds,
        )
        if args.command == "build":
            request = RecoveryActivationRequest.from_dict(
                _read_json_object(args.request, "activation request")
            )
            plan = verify_plan(request.to_plan(), policy=policy)
            _write_json_exclusive(args.output, plan.to_dict())
            _print_json(
                {
                    "schema_version": "post-training-rsi.recovery-plan-result/v1",
                    "status": "planned",
                    "plan_id": plan.plan_id,
                    "plan_sha256": plan_sha256(plan),
                    "output": str(args.output.expanduser().absolute()),
                    "executed": False,
                }
            )
            return 0
        if args.command == "verify":
            plan = RecoveryActivationPlan.from_dict(
                _read_json_object(args.plan, "activation plan")
            )
            verify_plan(plan, policy=policy)
            _print_json(
                {
                    "schema_version": "post-training-rsi.recovery-plan-result/v1",
                    "status": "verified",
                    "plan_id": plan.plan_id,
                    "plan_sha256": plan_sha256(plan),
                    "executed": False,
                }
            )
            return 0
        if args.command == "preflight":
            plan = RecoveryActivationPlan.from_dict(
                _read_json_object(args.plan, "activation plan")
            )
            observation = RecoveryPreflightObservation.from_dict(
                _read_json_object(args.observation, "preflight observation")
            )
            _print_json(run_preflight(plan, observation, policy=policy).to_dict())
            return 0
    except RecoveryActivationError as exc:
        _print_json(
            {
                "schema_version": "post-training-rsi.recovery-plan-result/v1",
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
                "executed": False,
            }
        )
        return 2
    raise AssertionError(f"unsupported command: {args.command}")


def _read_json_object(path: Path, label: str) -> Mapping[str, object]:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise RecoveryActivationContractError(f"{label} must not be a symbolic link")
    try:
        size = expanded.stat().st_size
    except FileNotFoundError as exc:
        raise RecoveryActivationContractError(f"{label} does not exist: {expanded}") from exc
    if size > MAX_INPUT_BYTES:
        raise RecoveryActivationContractError(f"{label} exceeds the input size limit")
    try:
        value = json.loads(expanded.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryActivationContractError(f"{label} is not readable JSON") from exc
    if not isinstance(value, Mapping):
        raise RecoveryActivationContractError(f"{label} must be a JSON object")
    if any(not isinstance(key, str) for key in value):
        raise RecoveryActivationContractError(f"{label} keys must be strings")
    return value


def _write_json_exclusive(path: Path, value: Mapping[str, object]) -> None:
    expanded = path.expanduser().absolute()
    expanded.parent.mkdir(parents=True, exist_ok=True)
    if expanded.is_symlink():
        raise RecoveryActivationContractError(
            "activation plan output must not be a symbolic link"
        )
    encoded = (canonical_json(value) + "\n").encode("utf-8")
    try:
        descriptor = os.open(
            expanded,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise RecoveryActivationContractError(
            f"activation plan output already exists: {expanded}"
        ) from exc
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise RecoveryActivationContractError(
                    "activation plan write made no progress"
                )
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _print_json(value: dict[str, Any]) -> None:
    print(
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
