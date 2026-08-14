from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ..approval import ApprovalStore, record_sha256
from ..control_plane import DecisionSubject, StateSnapshot
from ..harness.coevolution_store import CoEvolutionRunMetadata, CoEvolutionRunStore
from ..harness.persistence import HarnessPointerStore, HarnessSnapshotStore
from ..lineage import (
    ArtifactStore,
    CheckpointBundleStore,
    ControlRecordStore,
    PeakPointerStore,
    QuarantineMarker,
    QuarantineStore,
)
from .contracts import (
    AuditCheck,
    AuditStatus,
    CoEvolutionAuditReport,
    CoEvolutionStatusView,
    overall_status,
)


class CoEvolutionAuditError(RuntimeError):
    """Raised when a lightweight status view cannot be loaded safely."""


class CoEvolutionAuditor:
    """Read and verify the durable local Co-Evolution evidence graph."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.clock = clock or _utc_now

    def status(
        self,
        *,
        expected_run_id: str | None = None,
        expected_config_sha256: str | None = None,
    ) -> CoEvolutionStatusView:
        try:
            metadata = CoEvolutionRunStore(self.workspace).load(
                expected_run_id=expected_run_id,
                expected_config_sha256=expected_config_sha256,
            )
            control_store = ControlRecordStore(self.workspace)
            transaction = control_store.load_transaction(
                metadata.latest_transaction_id
            )
            snapshot = control_store.load_snapshot(metadata.latest_snapshot_id)
            if not any(
                ref.record_type == StateSnapshot.RECORD_TYPE
                and ref.record_id == snapshot.snapshot_id
                for ref in transaction.records
            ):
                raise CoEvolutionAuditError(
                    "latest StateSnapshot is not committed by latest transaction"
                )
            _verify_run_snapshot(metadata, snapshot)
            return CoEvolutionStatusView(
                run_id=metadata.run_id,
                runtime_status=metadata.status,
                state=snapshot.state.value,
                revision=metadata.revision,
                current_cycle=metadata.current_cycle,
                completed_cycles=metadata.completed_cycles,
                active_checkpoint_id=metadata.active_checkpoint_id,
                active_model_score=metadata.active_model_score,
                active_harness_id=metadata.active_harness_id,
                active_harness_score=metadata.active_harness_score,
                latest_snapshot_id=metadata.latest_snapshot_id,
                latest_transaction_id=metadata.latest_transaction_id,
                total_cost_usd=snapshot.total_cost_usd,
                pending_approval_request_id=(
                    metadata.pending_approval_request_id
                ),
                pending_approval_subject=metadata.pending_approval_subject,
            )
        except CoEvolutionAuditError:
            raise
        except Exception as exc:
            raise CoEvolutionAuditError(
                f"unable to load Co-Evolution status: {type(exc).__name__}: {exc}"
            ) from exc

    def audit(
        self,
        *,
        strict: bool = False,
        expected_run_id: str | None = None,
        expected_config_sha256: str | None = None,
        write_report: bool = True,
    ) -> CoEvolutionAuditReport:
        checks: list[AuditCheck] = []
        counts: dict[str, int] = {
            "transactions": 0,
            "committed_records": 0,
            "orphan_records": 0,
            "checkpoint_bundles": 0,
            "harness_snapshots": 0,
            "trace_datasets": 0,
            "approval_requests": 0,
            "approval_decisions": 0,
            "quarantine_markers": 0,
            "lock_files": 0,
        }
        active: dict[str, Any] = {}
        metadata: CoEvolutionRunMetadata | None = None
        snapshot: StateSnapshot | None = None
        control_store: ControlRecordStore | None = None

        if not self.workspace.is_dir():
            checks.append(
                _check(
                    "audit-workspace-exists",
                    AuditStatus.FAIL,
                    self.workspace.as_posix(),
                    "Co-Evolution workspace does not exist.",
                    recovery_hint=(
                        "Restore the workspace from an immutable backup; do not create "
                        "empty control directories as a substitute for evidence."
                    ),
                )
            )
            return self._finalize(
                checks,
                counts,
                active,
                strict=strict,
                run_id=None,
                write_report=write_report,
            )

        checks.append(
            _check(
                "audit-workspace-exists",
                AuditStatus.PASS,
                self.workspace.as_posix(),
                "Workspace exists.",
            )
        )

        try:
            metadata = CoEvolutionRunStore(self.workspace).load(
                expected_run_id=expected_run_id,
                expected_config_sha256=expected_config_sha256,
            )
            active.update(
                {
                    "run_id": metadata.run_id,
                    "revision": metadata.revision,
                    "state": metadata.state.value,
                    "current_cycle": metadata.current_cycle,
                    "completed_cycles": metadata.completed_cycles,
                    "active_checkpoint_id": metadata.active_checkpoint_id,
                    "active_model_score": metadata.active_model_score,
                    "active_harness_id": metadata.active_harness_id,
                    "active_harness_score": metadata.active_harness_score,
                    "pending_approval_request_id": (
                        metadata.pending_approval_request_id
                    ),
                    "pending_approval_subject": metadata.pending_approval_subject,
                }
            )
            checks.append(
                _check(
                    "audit-run-metadata",
                    AuditStatus.PASS,
                    "coevolution/run.json",
                    "Run pointer and immutable revision history agree.",
                    revision=metadata.revision,
                    config_sha256=metadata.config_sha256,
                )
            )
        except Exception as exc:
            checks.append(
                _failure(
                    "audit-run-metadata",
                    "coevolution/run.json",
                    exc,
                    recovery_hint=(
                        "Restore run.json and its matching immutable revision from the "
                        "same backup. Never synthesize a revision number or config hash."
                    ),
                )
            )

        try:
            control_store = ControlRecordStore(self.workspace)
            if metadata is None:
                raise CoEvolutionAuditError(
                    "Run metadata is unavailable; latest Snapshot cannot be linked."
                )
            transaction = control_store.load_transaction(
                metadata.latest_transaction_id
            )
            snapshot = control_store.load_snapshot(metadata.latest_snapshot_id)
            if not any(
                ref.record_type == StateSnapshot.RECORD_TYPE
                and ref.record_id == snapshot.snapshot_id
                for ref in transaction.records
            ):
                raise CoEvolutionAuditError(
                    "latest Snapshot is absent from latest transaction manifest"
                )
            _verify_run_snapshot(metadata, snapshot)
            active["total_cost_usd"] = snapshot.total_cost_usd
            checks.append(
                _check(
                    "audit-latest-snapshot",
                    AuditStatus.PASS,
                    snapshot.snapshot_id,
                    "Latest committed Snapshot matches the Run pointer.",
                    transaction_id=metadata.latest_transaction_id,
                    state=snapshot.state.value,
                    cycle=snapshot.cycle,
                    iteration=snapshot.iteration,
                )
            )
        except Exception as exc:
            checks.append(
                _failure(
                    "audit-latest-snapshot",
                    "control/snapshots",
                    exc,
                    recovery_hint=(
                        "Restore the exact transaction, referenced records, and Run "
                        "revision together. Do not repoint run.json to an uncommitted file."
                    ),
                )
            )

        if control_store is not None:
            self._audit_control_records(control_store, checks, counts)
            self._audit_peak(
                control_store,
                metadata,
                checks,
                counts,
            )
            self._audit_harness(
                control_store,
                metadata,
                checks,
                counts,
            )
            self._audit_quarantine(control_store, checks, counts)

        self._audit_trace_datasets(checks, counts)
        self._audit_approvals(metadata, checks, counts)
        self._audit_locks(checks, counts)

        return self._finalize(
            checks,
            counts,
            active,
            strict=strict,
            run_id=metadata.run_id if metadata is not None else None,
            write_report=write_report,
        )

    def _audit_control_records(
        self,
        store: ControlRecordStore,
        checks: list[AuditCheck],
        counts: dict[str, int],
    ) -> None:
        transactions_root = self.workspace / "control" / "transactions"
        committed: set[tuple[str, str]] = set()
        failures = 0
        for path in sorted(transactions_root.glob("*.json")):
            try:
                manifest = store.load_transaction(path.stem)
                counts["transactions"] += 1
                for ref in manifest.records:
                    committed.add((ref.record_type, ref.record_id))
            except Exception as exc:
                failures += 1
                checks.append(
                    _failure(
                        "audit-control-transaction",
                        path.relative_to(self.workspace).as_posix(),
                        exc,
                        recovery_hint=(
                            "Restore the transaction marker and every referenced record "
                            "from one consistent backup. Do not rewrite hashes in place."
                        ),
                    )
                )
        counts["committed_records"] = len(committed)
        if not transactions_root.is_dir():
            checks.append(
                _check(
                    "audit-control-transactions",
                    AuditStatus.FAIL,
                    "control/transactions",
                    "Control transaction directory is missing.",
                )
            )
        elif failures == 0:
            checks.append(
                _check(
                    "audit-control-transactions",
                    AuditStatus.PASS,
                    "control/transactions",
                    "All control transactions and referenced record hashes verify.",
                    transaction_count=counts["transactions"],
                    committed_record_count=len(committed),
                )
            )

        directories = {
            "evidence": "evidence",
            "decision": "decisions",
            "transition": "transitions",
            "state_snapshot": "snapshots",
        }
        orphans: list[str] = []
        for record_type, directory in directories.items():
            root = self.workspace / "control" / directory
            for path in sorted(root.glob("*.json")):
                if (record_type, path.stem) not in committed:
                    orphans.append(path.relative_to(self.workspace).as_posix())
        counts["orphan_records"] = len(orphans)
        checks.append(
            _check(
                "audit-control-orphans",
                AuditStatus.WARN if orphans else AuditStatus.PASS,
                "control",
                (
                    "Uncommitted immutable record files were found."
                    if orphans
                    else "No uncommitted control record files were found."
                ),
                orphan_count=len(orphans),
                orphan_paths=orphans,
                recovery_hint=(
                    "Investigate an interrupted writer. Preserve files for forensics; "
                    "do not delete them until their transaction history is understood."
                    if orphans
                    else "No action required."
                ),
            )
        )

    def _audit_peak(
        self,
        control_store: ControlRecordStore,
        metadata: CoEvolutionRunMetadata | None,
        checks: list[AuditCheck],
        counts: dict[str, int],
    ) -> None:
        checkpoint_store = CheckpointBundleStore(self.workspace, control_store)
        peak_store = PeakPointerStore(
            self.workspace,
            control_store,
            checkpoint_store,
        )
        try:
            peak = peak_store.load()
            if peak is None:
                raise CoEvolutionAuditError("Peak pointer is missing")
            if metadata is not None:
                if peak.run_id != metadata.run_id:
                    raise CoEvolutionAuditError("Peak and Run IDs differ")
                if peak.checkpoint_id != metadata.active_checkpoint_id:
                    raise CoEvolutionAuditError(
                        "Peak Checkpoint differs from active Run Checkpoint"
                    )
                if not math.isclose(
                    peak.score,
                    metadata.active_model_score,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    raise CoEvolutionAuditError(
                        "Peak score differs from active model score"
                    )
            bundle = checkpoint_store.load(peak.checkpoint_id)
            artifact_check = _verify_bundle_artifact(bundle.manifest.artifact_uri, bundle.manifest.artifact_sha256)
            checks.append(
                _check(
                    "audit-active-peak",
                    artifact_check[0],
                    peak.checkpoint_id,
                    artifact_check[1],
                    score=peak.score,
                    bundle_manifest_sha256=bundle.manifest.manifest_sha256,
                    artifact_uri=bundle.manifest.artifact_uri,
                )
            )
        except Exception as exc:
            checks.append(
                _failure(
                    "audit-active-peak",
                    "peak_checkpoint.json",
                    exc,
                    recovery_hint=(
                        "Restore Peak pointer, its immutable history entry, committed "
                        "PROMOTE Decision, Checkpoint bundle, and model artifact together."
                    ),
                )
            )

        checkpoints_root = self.workspace / "checkpoints"
        failures = 0
        unbundled: list[str] = []
        for directory in sorted(checkpoints_root.iterdir()) if checkpoints_root.is_dir() else ():
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            if not (directory / "bundle_manifest.json").is_file():
                unbundled.append(directory.relative_to(self.workspace).as_posix())
                continue
            try:
                checkpoint_store.load(directory.name)
                counts["checkpoint_bundles"] += 1
            except Exception as exc:
                failures += 1
                checks.append(
                    _failure(
                        "audit-checkpoint-bundle",
                        directory.relative_to(self.workspace).as_posix(),
                        exc,
                        recovery_hint=(
                            "Restore the entire immutable Checkpoint directory and its "
                            "referenced control transaction; do not edit manifests in place."
                        ),
                    )
                )
        if failures == 0:
            checks.append(
                _check(
                    "audit-checkpoint-bundles",
                    AuditStatus.WARN if unbundled else AuditStatus.PASS,
                    "checkpoints",
                    (
                        "Checkpoint bundles verify; unbundled directories also exist."
                        if unbundled
                        else "All discovered Checkpoint bundles verify."
                    ),
                    bundle_count=counts["checkpoint_bundles"],
                    unbundled_paths=unbundled,
                )
            )

    def _audit_harness(
        self,
        control_store: ControlRecordStore,
        metadata: CoEvolutionRunMetadata | None,
        checks: list[AuditCheck],
        counts: dict[str, int],
    ) -> None:
        snapshot_store = HarnessSnapshotStore(self.workspace, control_store)
        pointer_store = HarnessPointerStore(
            self.workspace,
            control_store,
            snapshot_store,
        )
        try:
            pointer = pointer_store.load()
            if pointer is None:
                raise CoEvolutionAuditError("active Harness pointer is missing")
            if metadata is not None:
                if pointer.run_id != metadata.run_id:
                    raise CoEvolutionAuditError("Harness and Run IDs differ")
                if pointer.harness_id != metadata.active_harness_id:
                    raise CoEvolutionAuditError(
                        "active Harness pointer differs from Run metadata"
                    )
                if not math.isclose(
                    pointer.score,
                    metadata.active_harness_score,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    raise CoEvolutionAuditError(
                        "Harness pointer score differs from Run metadata"
                    )
            bundle = snapshot_store.load(pointer.harness_id)
            checks.append(
                _check(
                    "audit-active-harness",
                    AuditStatus.PASS,
                    pointer.harness_id,
                    "Active Harness pointer, ACCEPT Decision, and snapshot verify.",
                    score=pointer.score,
                    cycle=pointer.cycle,
                    harness_sha256=bundle.spec.content_sha256,
                )
            )
        except Exception as exc:
            checks.append(
                _failure(
                    "audit-active-harness",
                    "active_harness.json",
                    exc,
                    recovery_hint=(
                        "Restore active_harness.json, its history entry, ACCEPT Decision, "
                        "and immutable Harness snapshot together."
                    ),
                )
            )

        root = self.workspace / "harness" / "snapshots"
        failures = 0
        for directory in sorted(root.iterdir()) if root.is_dir() else ():
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                snapshot_store.load(directory.name)
                counts["harness_snapshots"] += 1
            except Exception as exc:
                failures += 1
                checks.append(
                    _failure(
                        "audit-harness-snapshot",
                        directory.relative_to(self.workspace).as_posix(),
                        exc,
                        recovery_hint=(
                            "Restore the complete Harness snapshot directory and its "
                            "referenced transaction; do not recalculate IDs manually."
                        ),
                    )
                )
        if failures == 0:
            checks.append(
                _check(
                    "audit-harness-snapshots",
                    AuditStatus.PASS,
                    "harness/snapshots",
                    "All discovered immutable Harness snapshots verify.",
                    snapshot_count=counts["harness_snapshots"],
                )
            )

    def _audit_trace_datasets(
        self,
        checks: list[AuditCheck],
        counts: dict[str, int],
    ) -> None:
        root = self.workspace / "trace-datasets"
        required = {
            "raw.jsonl",
            "accepted.jsonl",
            "quarantine.jsonl",
            "filter_audit.jsonl",
            "harvest_manifest.json",
            "dataset_summary.json",
        }
        failures = 0
        for directory in sorted(root.iterdir()) if root.is_dir() else ():
            if not directory.is_dir() or directory.name.startswith("."):
                continue
            try:
                present = {path.name for path in directory.iterdir() if path.is_file()}
                missing = sorted(required - present)
                if missing:
                    raise CoEvolutionAuditError(
                        f"Trace Dataset bundle is missing files: {missing}"
                    )
                rows = {
                    name: _read_jsonl(directory / name)
                    for name in (
                        "raw.jsonl",
                        "accepted.jsonl",
                        "quarantine.jsonl",
                        "filter_audit.jsonl",
                    )
                }
                manifest = _read_json(directory / "harvest_manifest.json")
                summary = _read_json(directory / "dataset_summary.json")
                actual_sha256 = _sha256_file(directory / "accepted.jsonl")
                declared_hashes = {
                    value
                    for value in (
                        _find_string(manifest, "dataset_sha256"),
                        _find_string(summary, "dataset_sha256"),
                    )
                    if value is not None
                }
                if declared_hashes and declared_hashes != {actual_sha256}:
                    raise CoEvolutionAuditError(
                        "Trace Dataset SHA-256 differs from manifest or summary"
                    )
                expected_counts = {
                    "raw_count": len(rows["raw.jsonl"]),
                    "accepted_count": len(rows["accepted.jsonl"]),
                    "rejected_count": len(rows["quarantine.jsonl"]),
                }
                for key, actual in expected_counts.items():
                    declared = _find_int(summary, key)
                    if declared is not None and declared != actual:
                        raise CoEvolutionAuditError(
                            f"Trace Dataset {key}={declared}, actual={actual}"
                        )
                counts["trace_datasets"] += 1
                checks.append(
                    _check(
                        "audit-trace-dataset",
                        AuditStatus.PASS,
                        directory.relative_to(self.workspace).as_posix(),
                        "Trace Dataset files, JSONL rows, counts, and SHA-256 verify.",
                        dataset_sha256=actual_sha256,
                        **expected_counts,
                    )
                )
            except Exception as exc:
                failures += 1
                checks.append(
                    _failure(
                        "audit-trace-dataset",
                        directory.relative_to(self.workspace).as_posix(),
                        exc,
                        recovery_hint=(
                            "Quarantine the whole Trace Dataset bundle and restore it from "
                            "the same immutable source; never train from partially repaired rows."
                        ),
                    )
                )
        if not root.is_dir() or not any(root.iterdir()):
            checks.append(
                _check(
                    "audit-trace-datasets",
                    AuditStatus.WARN,
                    "trace-datasets",
                    "No Trace Dataset bundles were found.",
                    recovery_hint=(
                        "This may be expected before the Trace stage. Confirm the durable "
                        "State before treating the absence as corruption."
                    ),
                )
            )
        elif failures == 0:
            checks.append(
                _check(
                    "audit-trace-datasets",
                    AuditStatus.PASS,
                    "trace-datasets",
                    "All discovered Trace Dataset bundles verify.",
                    dataset_count=counts["trace_datasets"],
                )
            )

    def _audit_approvals(
        self,
        metadata: CoEvolutionRunMetadata | None,
        checks: list[AuditCheck],
        counts: dict[str, int],
    ) -> None:
        store = ApprovalStore(self.workspace)
        failures = 0
        pending_ids: list[str] = []
        try:
            request_ids = store.list_request_ids()
        except Exception as exc:
            checks.append(
                _failure(
                    "audit-approval-store",
                    "approvals",
                    exc,
                    recovery_hint=(
                        "Restore approval samples, Requests, and Decisions as one set; "
                        "do not issue a replacement Decision for a changed Request."
                    ),
                )
            )
            return
        for request_id in request_ids:
            try:
                request = store.load_request(request_id)
                store.load_sample(request_id)
                counts["approval_requests"] += 1
                if store.has_decision(request_id):
                    decision = store.load_decision(request_id)
                    counts["approval_decisions"] += 1
                    if decision.request_sha256 != record_sha256(request.to_dict()):
                        raise CoEvolutionAuditError(
                            "Approval Decision Request SHA-256 mismatch"
                        )
                else:
                    pending_ids.append(request_id)
            except Exception as exc:
                failures += 1
                checks.append(
                    _failure(
                        "audit-approval-record",
                        request_id,
                        exc,
                        recovery_hint=(
                            "Restore the immutable Request/Sample/Decision bytes. Never "
                            "approve a reconstructed Request without a new review."
                        ),
                    )
                )
        if metadata is not None and metadata.pending_approval_request_id is not None:
            request_id = metadata.pending_approval_request_id
            try:
                request = store.load_request(request_id)
                actual = record_sha256(request.to_dict())
                if actual != metadata.pending_approval_request_sha256:
                    raise CoEvolutionAuditError(
                        "Run pending approval SHA-256 differs from stored Request"
                    )
                checks.append(
                    _check(
                        "audit-pending-approval",
                        AuditStatus.PASS,
                        request_id,
                        "Run pointer is bound to the exact immutable approval Request.",
                        subject=metadata.pending_approval_subject,
                        decision_available=store.has_decision(request_id),
                    )
                )
            except Exception as exc:
                failures += 1
                checks.append(
                    _failure(
                        "audit-pending-approval",
                        request_id,
                        exc,
                        recovery_hint=(
                            "Restore the exact pending Request or restore the preceding Run "
                            "revision. Do not clear pending authority by editing run.json."
                        ),
                    )
                )
        stale_pending = pending_ids
        if metadata is not None and metadata.pending_approval_request_id in stale_pending:
            stale_pending = [
                item
                for item in stale_pending
                if item != metadata.pending_approval_request_id
            ]
        if failures == 0:
            checks.append(
                _check(
                    "audit-approval-store",
                    AuditStatus.WARN if stale_pending else AuditStatus.PASS,
                    "approvals",
                    (
                        "Approval records verify; unresolved Requests not referenced by the "
                        "Run pointer also exist."
                        if stale_pending
                        else "All discovered approval records verify."
                    ),
                    request_count=counts["approval_requests"],
                    decision_count=counts["approval_decisions"],
                    unreferenced_pending_requests=stale_pending,
                    recovery_hint=(
                        "Review whether the unresolved Requests are historical interrupted "
                        "work. Preserve them; do not auto-delete review evidence."
                        if stale_pending
                        else "No action required."
                    ),
                )
            )

    def _audit_quarantine(
        self,
        control_store: ControlRecordStore,
        checks: list[AuditCheck],
        counts: dict[str, int],
    ) -> None:
        root = self.workspace / "quarantine"
        store = QuarantineStore(self.workspace, control_store)
        failures = 0
        for path in sorted(root.glob("*.json")) if root.is_dir() else ():
            try:
                marker = QuarantineMarker.from_dict(_read_json(path))
                store.load(
                    iteration=marker.iteration,
                    subject_type=marker.subject_type,
                    subject_id=marker.subject_id,
                )
                counts["quarantine_markers"] += 1
            except Exception as exc:
                failures += 1
                checks.append(
                    _failure(
                        "audit-quarantine-marker",
                        path.relative_to(self.workspace).as_posix(),
                        exc,
                        recovery_hint=(
                            "Restore the marker and its committed REJECT/ROLLBACK/QUARANTINE "
                            "Decision together. Do not drop rejected lineage."
                        ),
                    )
                )
        if failures == 0:
            checks.append(
                _check(
                    "audit-quarantine-markers",
                    AuditStatus.PASS,
                    "quarantine",
                    "All discovered quarantine/rollback markers verify.",
                    marker_count=counts["quarantine_markers"],
                )
            )

    def _audit_locks(
        self,
        checks: list[AuditCheck],
        counts: dict[str, int],
    ) -> None:
        locks = sorted(
            path.relative_to(self.workspace).as_posix()
            for path in self.workspace.rglob("*.lock")
            if path.is_file() or path.is_symlink()
        )
        counts["lock_files"] = len(locks)
        checks.append(
            _check(
                "audit-lock-files",
                AuditStatus.WARN if locks else AuditStatus.PASS,
                self.workspace.as_posix(),
                (
                    "Lock files remain and require human stale-writer investigation."
                    if locks
                    else "No retained lock files were found."
                ),
                lock_count=len(locks),
                lock_paths=locks,
                recovery_hint=(
                    "Confirm the owning process and storage lease. Never auto-delete a lock "
                    "or infer staleness from timestamp alone."
                    if locks
                    else "No action required."
                ),
            )
        )

    def _finalize(
        self,
        checks: list[AuditCheck],
        counts: dict[str, int],
        active: dict[str, Any],
        *,
        strict: bool,
        run_id: str | None,
        write_report: bool,
    ) -> CoEvolutionAuditReport:
        status = overall_status(checks, strict=strict)
        report_path = (
            self.workspace / "reports" / "coevolution-audit.json"
            if write_report
            else None
        )
        report = CoEvolutionAuditReport(
            generated_at=self.clock(),
            strict=strict,
            status=status,
            run_id=run_id,
            checks=tuple(checks),
            counts=counts,
            active=active,
            report_path=report_path.as_posix() if report_path is not None else None,
        )
        if report_path is not None:
            ArtifactStore(self.workspace).write_report(
                report_path.name,
                report.to_dict(),
            )
        return report


def _verify_run_snapshot(
    metadata: CoEvolutionRunMetadata,
    snapshot: StateSnapshot,
) -> None:
    if snapshot.run_id != metadata.run_id:
        raise CoEvolutionAuditError("Run pointer and Snapshot Run IDs differ")
    if snapshot.snapshot_id != metadata.latest_snapshot_id:
        raise CoEvolutionAuditError("Run pointer and Snapshot IDs differ")
    if snapshot.state is not metadata.state:
        raise CoEvolutionAuditError("Run pointer and Snapshot States differ")
    if snapshot.cycle != metadata.current_cycle:
        raise CoEvolutionAuditError("Run pointer and Snapshot cycles differ")
    if snapshot.active_checkpoint_id != metadata.active_checkpoint_id:
        raise CoEvolutionAuditError(
            "Run pointer and Snapshot active Checkpoint IDs differ"
        )
    if snapshot.active_harness_id != metadata.active_harness_id:
        raise CoEvolutionAuditError(
            "Run pointer and Snapshot active Harness IDs differ"
        )
    model_score = snapshot.metadata.get("active_model_score")
    if isinstance(model_score, bool) or not isinstance(model_score, (int, float)):
        raise CoEvolutionAuditError(
            "Snapshot metadata.active_model_score is missing or invalid"
        )
    if not math.isclose(
        float(model_score),
        metadata.active_model_score,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise CoEvolutionAuditError(
            "Run pointer and Snapshot active model scores differ"
        )
    if snapshot.peak_score is None or not math.isclose(
        snapshot.peak_score,
        metadata.active_harness_score,
        rel_tol=0.0,
        abs_tol=1e-12,
    ):
        raise CoEvolutionAuditError(
            "Run pointer and Snapshot active Harness scores differ"
        )


def _verify_bundle_artifact(
    artifact_uri: str,
    expected_sha256: str,
) -> tuple[AuditStatus, str]:
    parsed = urlparse(artifact_uri)
    if parsed.scheme != "file":
        return (
            AuditStatus.WARN,
            "Checkpoint metadata verifies, but non-local artifact bytes were not read.",
        )
    path = Path(unquote(parsed.path))
    if not path.exists():
        raise CoEvolutionAuditError(
            f"local Checkpoint artifact is missing: {path}"
        )
    actual = _sha256_path(path)
    if actual != expected_sha256:
        raise CoEvolutionAuditError(
            "local Checkpoint artifact SHA-256 differs from bundle manifest"
        )
    return AuditStatus.PASS, "Peak pointer, Checkpoint bundle, and local artifact verify."


def _sha256_path(path: Path) -> str:
    if path.is_symlink():
        raise CoEvolutionAuditError(f"artifact path is a symlink: {path}")
    if path.is_file():
        return _sha256_file(path)
    if not path.is_dir():
        raise CoEvolutionAuditError(f"artifact path is not a file or directory: {path}")
    digest = hashlib.sha256()
    root = path.resolve()
    for entry in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
        if entry.is_symlink():
            raise CoEvolutionAuditError(
                f"artifact directory contains a symlink: {entry}"
            )
        relative = entry.resolve().relative_to(root).as_posix().encode("utf-8")
        if entry.is_dir():
            digest.update(b"D\0" + relative + b"\0")
        elif entry.is_file():
            digest.update(b"F\0" + relative + b"\0")
            digest.update(str(entry.stat().st_size).encode("ascii") + b"\0")
            with entry.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        else:
            raise CoEvolutionAuditError(f"unsupported artifact entry: {entry}")
    return digest.hexdigest()


def _check(
    check_id: str,
    status: AuditStatus,
    subject: str,
    message: str,
    **details: Any,
) -> AuditCheck:
    return AuditCheck(
        check_id=check_id,
        status=status,
        subject=subject,
        message=message,
        details=details,
    )


def _failure(
    check_id: str,
    subject: str,
    error: Exception,
    *,
    recovery_hint: str,
) -> AuditCheck:
    return _check(
        check_id,
        AuditStatus.FAIL,
        subject,
        "Integrity verification failed.",
        error_type=type(error).__name__,
        error=str(error),
        recovery_hint=recovery_hint,
    )


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise CoEvolutionAuditError(f"JSON path is a symlink: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise CoEvolutionAuditError(f"expected a JSON object in {path}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink():
        raise CoEvolutionAuditError(f"JSONL path is a symlink: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise CoEvolutionAuditError(
                f"expected a JSON object at {path}:{line_number}"
            )
        rows.append(value)
    return rows


def _find_string(value: Mapping[str, Any], key: str) -> str | None:
    item = value.get(key)
    return item if isinstance(item, str) else None


def _find_int(value: Mapping[str, Any], key: str) -> int | None:
    item = value.get(key)
    return item if isinstance(item, int) and not isinstance(item, bool) else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
