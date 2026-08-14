from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .approval import ApprovalPolicy, ApprovalService, ApprovalStore, record_sha256
from .audit import (
    COEVOLUTION_STATUS_SCHEMA_VERSION,
    CoEvolutionAuditError,
    CoEvolutionAuditor,
)
from .config import PipelineConfig
from .engine import build_default_engine
from .lineage import (
    ArtifactStore,
    CheckpointBundleStore,
    ControlRecordStore,
    PeakPointerStore,
)
from .models import SyntheticExample
from .orchestration import (
    build_converged_rsi_controller,
    build_reference_coevolution_controller,
)
from .verification.pipeline import VerificationPipeline


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="post-training-rsi",
        description="Evidence-first post-training and RSI reference pipeline",
    )
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path("artifacts/default"),
    )
    parser.add_argument("--run-id", default="rsi-run-default")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "demo",
        help="run the stable one-iteration compatibility demo",
    )
    subparsers.add_parser(
        "rsi",
        help="run or resume the converged multi-iteration RSI controller",
    )
    coevolve = subparsers.add_parser(
        "coevolve",
        help=(
            "run or resume the deterministic durable Model/Harness "
            "Co-Evolution reference runtime"
        ),
        description=(
            "Run the deterministic durable Model/Harness Co-Evolution "
            "reference runtime. This command does not claim real GPU, "
            "cloud provider, or production benchmark execution."
        ),
    )
    coevolve.set_defaults(runtime_kind="deterministic-reference")

    status = subparsers.add_parser(
        "coevolve-status",
        help="read-only lightweight Co-Evolution Run status view",
        description=(
            "Read-only status view linking the Co-Evolution Run pointer, its "
            "latest control transaction, and its latest StateSnapshot. This "
            "command never writes to the workspace. It is not a full integrity "
            "audit; use coevolve-audit for that."
        ),
    )
    _add_identity_arguments(status)

    coevolve_audit = subparsers.add_parser(
        "coevolve-audit",
        help="read-only integrity audit of the durable Co-Evolution evidence graph",
        description=(
            "Read-only integrity audit of the durable local Model/Harness "
            "Co-Evolution evidence graph. It may write one report to "
            "<workspace>/reports/coevolution-audit.json and changes nothing "
            "else. With --strict, any warning becomes a failing result."
        ),
    )
    coevolve_audit.add_argument(
        "--strict",
        action="store_true",
        help="promote any warning to a failing overall result",
    )
    _add_identity_arguments(coevolve_audit)

    verify = subparsers.add_parser(
        "verify",
        help="verify a JSONL Dataset with the configured admission gates",
    )
    verify.add_argument("--input", type=Path, required=True)
    verify.add_argument("--iteration", type=int, default=1)

    audit = subparsers.add_parser(
        "audit",
        help="audit a committed Checkpoint bundle and its control transaction",
    )
    audit.add_argument("--checkpoint-id", required=True)

    approvals = subparsers.add_parser(
        "approvals",
        help="list immutable HITL approval requests and current states",
    )
    approvals.add_argument("--include-decided", action="store_true")

    review = subparsers.add_parser(
        "review",
        help="commit one immutable approval or denial decision",
    )
    review.add_argument("--request-id", required=True)
    review.add_argument("--expected-request-sha256", required=True)
    decision = review.add_mutually_exclusive_group(required=True)
    decision.add_argument("--approve", action="store_true")
    decision.add_argument("--deny", action="store_true")
    review.add_argument("--reviewer", required=True)
    review.add_argument("--role", required=True)
    review.add_argument("--reason", required=True)
    review.add_argument("--decided-at", default=None)
    return parser


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--expect-run-id",
        default=None,
        help="fail when the workspace belongs to a different Run ID",
    )
    parser.add_argument(
        "--expect-config-sha256",
        default=None,
        help="fail when the Run was created from a different configuration",
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = PipelineConfig.load(args.config)
    workspace = args.workspace.resolve()

    if args.command == "demo":
        demo_result = build_default_engine(config, workspace=workspace).run()
        _print_json(demo_result.to_dict())
        return 0

    if args.command == "rsi":
        rsi_result = build_converged_rsi_controller(
            config,
            workspace=workspace,
            run_id=args.run_id,
        ).run()
        _print_json(rsi_result.to_dict())
        return 0

    if args.command == "coevolve":
        coevolution_result = build_reference_coevolution_controller(
            config,
            workspace=workspace,
            run_id=args.run_id,
        ).run()
        _print_json(coevolution_result.to_dict())
        return 0

    if args.command == "coevolve-status":
        return _coevolution_status(
            workspace=workspace,
            expected_run_id=args.expect_run_id,
            expected_config_sha256=args.expect_config_sha256,
        )

    if args.command == "coevolve-audit":
        report = CoEvolutionAuditor(workspace).audit(
            strict=args.strict,
            expected_run_id=args.expect_run_id,
            expected_config_sha256=args.expect_config_sha256,
        )
        _print_json(report.to_dict())
        return report.exit_code

    if args.command == "verify":
        _print_json(
            _verify_dataset(
                config=config,
                workspace=workspace,
                input_path=args.input,
                iteration=args.iteration,
            )
        )
        return 0

    if args.command == "audit":
        _print_json(
            _audit_checkpoint(
                workspace=workspace,
                checkpoint_id=args.checkpoint_id,
            )
        )
        return 0

    approval_service = _approval_service(config, workspace)
    if args.command == "approvals":
        _print_json(
            {
                "requests": _approval_rows(
                    approval_service,
                    include_decided=args.include_decided,
                )
            }
        )
        return 0

    if args.command == "review":
        reviewed = approval_service.review(
            request_id=args.request_id,
            expected_request_sha256=args.expected_request_sha256,
            approved=args.approve,
            reviewer_id=args.reviewer,
            reviewer_role=args.role,
            reason=args.reason,
            decided_at=args.decided_at,
        )
        _print_json(
            {
                "request": reviewed.request.to_dict(),
                "decision": reviewed.decision.to_dict(),
                "control_decision": reviewed.control_decision.to_dict(),
                "request_evidence": reviewed.request_evidence.to_dict(),
                "decision_evidence": reviewed.decision_evidence.to_dict(),
            }
        )
        return 0

    raise AssertionError(f"unsupported command: {args.command}")


def _coevolution_status(
    *,
    workspace: Path,
    expected_run_id: str | None,
    expected_config_sha256: str | None,
) -> int:
    try:
        view = CoEvolutionAuditor(workspace).status(
            expected_run_id=expected_run_id,
            expected_config_sha256=expected_config_sha256,
        )
    except CoEvolutionAuditError as exc:
        _print_json(
            {
                "schema_version": COEVOLUTION_STATUS_SCHEMA_VERSION,
                "runtime_status": "ERROR",
                "error": str(exc),
            }
        )
        return 2
    _print_json(view.to_dict())
    return 0


def _verify_dataset(
    *,
    config: PipelineConfig,
    workspace: Path,
    input_path: Path,
    iteration: int,
) -> dict[str, Any]:
    if iteration < 1:
        raise ValueError("iteration must be at least 1")
    examples = _read_examples(input_path)
    verifier = VerificationPipeline(
        config.verification,
        benchmark_texts=config.benchmark_texts,
    )
    verification = verifier.verify(examples)
    store = ArtifactStore(workspace)
    dataset_path, dataset_hash = store.write_iteration_bundle(
        iteration=iteration,
        raw_examples=examples,
        verification=verification,
        synthesis_manifest={
            "source": input_path.resolve().as_uri(),
            "mode": "verify-cli",
            "example_count": len(examples),
        },
    )
    return {
        "status": "verified",
        "iteration": iteration,
        "raw_count": len(examples),
        "accepted_count": len(verification.accepted),
        "rejected_count": len(verification.quarantined),
        "acceptance_rate": verification.acceptance_rate,
        "rejection_counts": verification.rejection_counts,
        "accepted_dataset_path": str(dataset_path),
        "accepted_dataset_hash": dataset_hash,
        "filter_config_hash": verifier.config_hash,
    }


def _audit_checkpoint(
    *,
    workspace: Path,
    checkpoint_id: str,
) -> dict[str, Any]:
    control_store = ControlRecordStore(workspace)
    checkpoint_store = CheckpointBundleStore(
        workspace,
        control_store=control_store,
    )
    peak_store = PeakPointerStore(
        workspace,
        control_store=control_store,
        checkpoint_store=checkpoint_store,
    )
    bundle = checkpoint_store.load(checkpoint_id)
    transaction = control_store.load_transaction(
        bundle.manifest.control_transaction_id
    )
    peak = peak_store.load()
    report = {
        "status": "audited",
        "checkpoint_id": checkpoint_id,
        "is_current_peak": (
            peak is not None and peak.checkpoint_id == checkpoint_id
        ),
        "checkpoint": bundle.checkpoint,
        "lineage_manifest": bundle.lineage.to_dict(),
        "bundle_manifest": bundle.manifest.to_dict(),
        "control_transaction": transaction.to_dict(),
        "peak_pointer": peak.to_dict() if peak is not None else None,
    }
    ArtifactStore(workspace).write_report(
        f"regression-audit-{checkpoint_id}.json",
        report,
    )
    return report


def _approval_service(
    config: PipelineConfig,
    workspace: Path,
) -> ApprovalService:
    policy = ApprovalPolicy(
        policy_id="hitl-rsi-v1",
        dataset_review_required=config.approval.dataset_review_required,
        checkpoint_review_required=config.approval.checkpoint_review_required,
        harness_review_required=config.approval.harness_review_required,
        sample_rate=config.approval.sample_rate,
        min_sample_items=config.approval.min_sample_items,
        max_sample_items=config.approval.max_sample_items,
        decision_ttl_seconds=config.approval.decision_ttl_seconds,
        allowed_reviewer_roles=config.approval.allowed_reviewer_roles,
    )
    return ApprovalService(
        store=ApprovalStore(workspace),
        policy=policy,
        clock=_utc_now,
    )


def _approval_rows(
    service: ApprovalService,
    *,
    include_decided: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for request_id in service.store.list_request_ids():
        request = service.store.load_request(request_id)
        as_of = _not_before_timestamp(_utc_now(), request.requested_at)
        if service.store.has_decision(request_id):
            decision = service.store.load_decision(request_id)
            as_of = _not_before_timestamp(as_of, decision.decided_at)
        status = service.status(request_id, as_of=as_of)
        if not include_decided and status.state.value not in {"PENDING", "EXPIRED"}:
            continue
        rows.append(
            {
                "request_id": request_id,
                "request_sha256": record_sha256(request.to_dict()),
                "state": status.state.value,
                "run_id": request.run_id,
                "iteration": request.iteration,
                "subject_type": request.subject_type.value,
                "subject_id": request.subject_id,
                "requested_action": request.requested_action.value,
                "requested_at": request.requested_at,
                "expires_at": request.expires_at,
                "sample_count": request.sample_count,
                "reviewer_id": status.reviewer_id,
                "reviewer_role": status.reviewer_role,
                "decided_at": status.decided_at,
            }
        )
    return rows


def _read_examples(path: Path) -> list[SyntheticExample]:
    examples: list[SyntheticExample] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(
                    f"input line {line_number} must be a JSON object"
                )
            examples.append(SyntheticExample.from_dict(value))
    if not examples:
        raise ValueError("input Dataset is empty")
    return examples


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


def _not_before_timestamp(value: str, minimum: str) -> str:
    value_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
    minimum_time = datetime.fromisoformat(minimum.replace("Z", "+00:00"))
    return value if value_time >= minimum_time else minimum


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    raise SystemExit(main())
