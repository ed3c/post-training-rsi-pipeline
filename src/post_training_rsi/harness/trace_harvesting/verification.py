from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from ...control_plane import EvidenceKind, EvidenceRecord, JSONValue
from ...models import SyntheticExample
from ...verification.pipeline import VerificationPipeline
from .contracts import (
    HarvestedTraceBatch,
    TraceContractError,
    TraceDatasetResult,
    TraceTrainingExample,
)
from .harvester import TraceHarvester


class TraceDatasetConflictError(TraceContractError):
    """Raised when an immutable Trace Dataset path already has different bytes."""


@dataclass(frozen=True, slots=True)
class TraceVerificationBundle:
    result: TraceDatasetResult
    trace_dataset_evidence: EvidenceRecord
    audit_evidence: EvidenceRecord
    quarantine_evidence: EvidenceRecord | None

    @property
    def evidence_records(self) -> tuple[EvidenceRecord, ...]:
        records = [self.trace_dataset_evidence, self.audit_evidence]
        if self.quarantine_evidence is not None:
            records.append(self.quarantine_evidence)
        return tuple(records)


class TraceVerificationService:
    """Run the existing data gates and commit an immutable local Trace Dataset bundle."""

    def __init__(
        self,
        *,
        verifier: VerificationPipeline,
        output_root: str | Path,
        harvester: TraceHarvester,
    ) -> None:
        root = Path(output_root)
        if root.exists() and root.is_symlink():
            raise TraceContractError("Trace Dataset output root must not be a symlink")
        self.output_root = root.resolve()
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.verifier = verifier
        self.harvester = harvester

    def verify(
        self,
        batch: HarvestedTraceBatch,
        *,
        created_at: str,
    ) -> TraceVerificationBundle:
        examples = self.harvester.to_training_examples(batch)
        verification = self.verifier.verify(
            cast(Iterable[SyntheticExample], examples)
        )

        directory = self.output_root / "trace-datasets" / batch.batch_id
        if directory.exists() and directory.is_symlink():
            raise TraceDatasetConflictError(
                "Trace Dataset directory must not be a symlink"
            )
        directory.mkdir(parents=True, exist_ok=True)

        raw_path = directory / "raw.jsonl"
        accepted_path = directory / "accepted.jsonl"
        quarantine_path = directory / "quarantine.jsonl"
        audit_path = directory / "filter_audit.jsonl"
        manifest_path = directory / "harvest_manifest.json"
        summary_path = directory / "dataset_summary.json"

        raw_bytes = _jsonl_bytes(example.to_dict() for example in examples)
        accepted_examples = tuple(
            cast(TraceTrainingExample, example)
            for example in verification.accepted
        )
        quarantined_examples = tuple(
            cast(TraceTrainingExample, example)
            for example in verification.quarantined
        )
        accepted_bytes = _jsonl_bytes(
            example.to_dict() for example in accepted_examples
        )
        quarantine_bytes = _jsonl_bytes(
            example.to_dict() for example in quarantined_examples
        )
        audit_bytes = _jsonl_bytes(
            record.to_dict() for record in verification.records
        )
        dataset_sha256 = hashlib.sha256(accepted_bytes).hexdigest()
        dataset_id = _content_id(
            "trace-dataset",
            {
                "batch_id": batch.batch_id,
                "dataset_sha256": dataset_sha256,
                "filter_config_hash": self.verifier.config_hash,
            },
        )

        manifest: dict[str, JSONValue] = {
            "batch_id": batch.batch_id,
            "dataset_id": dataset_id,
            "run_id": batch.run_id,
            "cycle": batch.cycle,
            "model_checkpoint_id": batch.model_checkpoint_id,
            "harness_id": batch.harness_id,
            "selection_seed": batch.selection_seed,
            "target_count": batch.target_count,
            "selected_trace_count": batch.selected_count,
            "harvest_rejected_count": batch.rejected_count,
            "selected_traces": [
                {
                    "trace_id": trace.trace_id,
                    "trace_sha256": trace.trace_sha256,
                    "task_id": trace.task_id,
                    "task_family": trace.task_family,
                    "score": trace.score,
                }
                for trace in batch.selected
            ],
            "harvest_rejections": [item.to_dict() for item in batch.rejected],
            "filter_config_hash": self.verifier.config_hash,
            "dataset_sha256": dataset_sha256,
            "created_at": created_at,
        }
        summary: dict[str, JSONValue] = {
            "batch_id": batch.batch_id,
            "dataset_id": dataset_id,
            "raw_count": len(examples),
            "accepted_count": len(accepted_examples),
            "rejected_count": len(quarantined_examples),
            "acceptance_rate": verification.acceptance_rate,
            "rejection_counts": dict(verification.rejection_counts),
            "dataset_sha256": dataset_sha256,
            "filter_config_hash": self.verifier.config_hash,
        }

        _write_immutable_or_equal(raw_path, raw_bytes)
        _write_immutable_or_equal(accepted_path, accepted_bytes)
        _write_immutable_or_equal(quarantine_path, quarantine_bytes)
        _write_immutable_or_equal(audit_path, audit_bytes)
        _write_immutable_or_equal(manifest_path, _json_bytes(manifest))
        _write_immutable_or_equal(summary_path, _json_bytes(summary))

        dataset_evidence = EvidenceRecord(
            evidence_id=_evidence_id("trace-dataset", dataset_id),
            run_id=batch.run_id,
            iteration=batch.cycle,
            kind=EvidenceKind.TRACE_DATASET,
            producer="trace-verification",
            uri=accepted_path.resolve().as_uri(),
            created_at=created_at,
            sha256=dataset_sha256,
            metadata={
                "batch_id": batch.batch_id,
                "dataset_id": dataset_id,
                "accepted_count": len(accepted_examples),
                "model_checkpoint_id": batch.model_checkpoint_id,
                "harness_id": batch.harness_id,
                "filter_config_hash": self.verifier.config_hash,
            },
        )
        audit_evidence = EvidenceRecord(
            evidence_id=_evidence_id("trace-audit", dataset_id),
            run_id=batch.run_id,
            iteration=batch.cycle,
            kind=EvidenceKind.VERIFICATION_AUDIT,
            producer="trace-verification",
            uri=audit_path.resolve().as_uri(),
            created_at=created_at,
            sha256=_sha256_file(audit_path),
            metadata={
                "batch_id": batch.batch_id,
                "dataset_id": dataset_id,
                "raw_count": len(examples),
                "accepted_count": len(accepted_examples),
                "rejected_count": len(quarantined_examples),
                "acceptance_rate": verification.acceptance_rate,
                "rejection_counts": dict(verification.rejection_counts),
            },
        )
        quarantine_evidence: EvidenceRecord | None = None
        if quarantined_examples:
            quarantine_evidence = EvidenceRecord(
                evidence_id=_evidence_id("trace-quarantine", dataset_id),
                run_id=batch.run_id,
                iteration=batch.cycle,
                kind=EvidenceKind.QUARANTINE_DATASET,
                producer="trace-verification",
                uri=quarantine_path.resolve().as_uri(),
                created_at=created_at,
                sha256=_sha256_file(quarantine_path),
                metadata={
                    "batch_id": batch.batch_id,
                    "dataset_id": dataset_id,
                    "rejected_count": len(quarantined_examples),
                    "rejection_counts": dict(verification.rejection_counts),
                },
            )

        evidence_ids = tuple(
            evidence.evidence_id
            for evidence in (
                dataset_evidence,
                audit_evidence,
                quarantine_evidence,
            )
            if evidence is not None
        )
        result = TraceDatasetResult(
            batch_id=batch.batch_id,
            dataset_id=dataset_id,
            run_id=batch.run_id,
            cycle=batch.cycle,
            model_checkpoint_id=batch.model_checkpoint_id,
            harness_id=batch.harness_id,
            dataset_path=accepted_path.as_posix(),
            dataset_sha256=dataset_sha256,
            audit_path=audit_path.as_posix(),
            raw_count=len(examples),
            accepted_count=len(accepted_examples),
            rejected_count=len(quarantined_examples),
            acceptance_rate=verification.acceptance_rate,
            rejection_counts=dict(verification.rejection_counts),
            accepted_example_ids=tuple(
                example.example_id for example in accepted_examples
            ),
            created_at=created_at,
            evidence_ids=evidence_ids,
        )
        return TraceVerificationBundle(
            result=result,
            trace_dataset_evidence=dataset_evidence,
            audit_evidence=audit_evidence,
            quarantine_evidence=quarantine_evidence,
        )


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return b"".join(
        (
            json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")
        for row in rows
    )


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _write_immutable_or_equal(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise TraceDatasetConflictError(f"immutable path is a symlink: {path}")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError:
        if path.read_bytes() != content:
            raise TraceDatasetConflictError(
                f"immutable Trace Dataset path already has different bytes: {path}"
            )
        return
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_id(prefix: str, payload: Mapping[str, JSONValue]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:24]}"


def _evidence_id(prefix: str, subject_id: str) -> str:
    digest = hashlib.sha256(f"{prefix}|{subject_id}".encode("utf-8")).hexdigest()
    return f"ev-{prefix}-{digest[:20]}"
