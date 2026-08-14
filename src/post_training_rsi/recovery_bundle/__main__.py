from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .bundle import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    RecoveryBundleError,
    create_bundle,
    stage_bundle,
    verify_bundle,
    verify_staged_directory,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m post_training_rsi.recovery_bundle",
        description=(
            "Create, verify, and stage deterministic content-addressed recovery bundles"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser(
        "create",
        help="create a new bundle outside the source workspace",
    )
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--bundle", type=Path, required=True)
    create.add_argument("--source-label", default=None)
    create.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    create.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)

    verify = subparsers.add_parser(
        "verify",
        help="verify a bundle manifest and all referenced blobs",
    )
    verify.add_argument("--bundle", type=Path, required=True)

    stage = subparsers.add_parser(
        "stage",
        help="restore a verified bundle into a new, inactive directory",
    )
    stage.add_argument("--bundle", type=Path, required=True)
    stage.add_argument("--destination", type=Path, required=True)

    verify_stage = subparsers.add_parser(
        "verify-stage",
        help="compare a staged directory against a verified bundle",
    )
    verify_stage.add_argument("--bundle", type=Path, required=True)
    verify_stage.add_argument("--destination", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "create":
            report = create_bundle(
                args.source,
                args.bundle,
                source_label=args.source_label,
                max_files=args.max_files,
                max_bytes=args.max_bytes,
            )
            _print_json(report.to_dict())
            return 0
        if args.command == "verify":
            _print_json(verify_bundle(args.bundle).to_dict())
            return 0
        if args.command == "stage":
            _print_json(stage_bundle(args.bundle, args.destination).to_dict())
            return 0
        if args.command == "verify-stage":
            report = verify_staged_directory(args.bundle, args.destination)
            value = report.to_dict()
            value["status"] = "stage-verified"
            value["destination"] = str(args.destination.expanduser().absolute())
            _print_json(value)
            return 0
    except RecoveryBundleError as exc:
        _print_json(
            {
                "status": "error",
                "error_type": type(exc).__name__,
                "message": str(exc),
            }
        )
        return 2
    raise AssertionError(f"unsupported command: {args.command}")


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
