from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..adapter_runtime.integrity import canonical_sha256, sha256_path
from ..control_plane import EvidenceKind, EvidenceRecord
from ..control_plane.validation import canonical_json, validate_id


class RSIEvidenceFactory:
    """Create deterministic control evidence for controller-owned artifacts."""

    def __init__(self, *, run_id: str) -> None:
        self.run_id = validate_id(run_id, "run_id")

    def file(
        self,
        *,
        iteration: int,
        kind: EvidenceKind,
        stage: str,
        path: Path,
        created_at: str,
        metadata: dict[str, Any] | None = None,
        producer: str = "orchestration.rsi",
    ) -> EvidenceRecord:
        resolved = path.resolve(strict=True)
        digest = sha256_path(resolved)
        return EvidenceRecord(
            evidence_id=self._id(
                stage,
                iteration,
                f"{resolved.as_posix()}:{digest}",
            ),
            run_id=self.run_id,
            iteration=iteration,
            kind=kind,
            producer=producer,
            uri=resolved.as_uri(),
            created_at=created_at,
            sha256=digest,
            metadata=metadata or {},
        )

    def inline(
        self,
        *,
        iteration: int,
        kind: EvidenceKind,
        stage: str,
        value: dict[str, Any],
        created_at: str,
        uri: str | None = None,
        producer: str = "orchestration.rsi",
    ) -> EvidenceRecord:
        digest = canonical_sha256(value)
        return EvidenceRecord(
            evidence_id=self._id(stage, iteration, digest),
            run_id=self.run_id,
            iteration=iteration,
            kind=kind,
            producer=producer,
            uri=uri or f"rsi://{self.run_id}/{iteration}/{stage}",
            created_at=created_at,
            sha256=digest,
            metadata=value,
        )

    def _id(self, stage: str, iteration: int, subject: str) -> str:
        stage = validate_id(stage, "stage")
        digest = hashlib.sha256(
            canonical_json(
                {
                    "run_id": self.run_id,
                    "iteration": iteration,
                    "stage": stage,
                    "subject": subject,
                }
            ).encode("utf-8")
        ).hexdigest()
        return f"ev.rsi.{stage}.{digest[:24]}"


def write_canonical_json(path: Path, value: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    path.write_text(payload + "\n", encoding="utf-8")
    return path
