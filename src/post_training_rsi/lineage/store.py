from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

from ..models import SyntheticExample, TrainingResult, VerificationBatch
from .manifest import LineageManifest


class ArtifactStore:
    """Immutable-by-convention local artifact store with atomic manifests."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in ("iterations", "checkpoints", "harness", "reports", "quarantine"):
            (self.root / directory).mkdir(exist_ok=True)

    def write_iteration_bundle(
        self,
        *,
        iteration: int,
        raw_examples: list[SyntheticExample],
        verification: VerificationBatch,
        synthesis_manifest: dict[str, Any],
    ) -> tuple[Path, str]:
        iteration_dir = self.root / "iterations" / f"iter-{iteration:03d}"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        raw_path = iteration_dir / "raw.jsonl"
        accepted_path = iteration_dir / "accepted.jsonl"
        quarantine_path = iteration_dir / "quarantine.jsonl"
        audit_path = iteration_dir / "filter_audit.jsonl"
        self.write_jsonl(raw_path, (example.to_dict() for example in raw_examples))
        self.write_jsonl(accepted_path, (example.to_dict() for example in verification.accepted))
        self.write_jsonl(
            quarantine_path, (example.to_dict() for example in verification.quarantined)
        )
        self.write_jsonl(audit_path, (record.to_dict() for record in verification.records))
        self.write_json(iteration_dir / "synthesis_manifest.json", synthesis_manifest)
        self.write_json(
            iteration_dir / "dataset_summary.json",
            {
                "iteration": iteration,
                "raw_count": len(raw_examples),
                "accepted_count": len(verification.accepted),
                "rejected_count": len(verification.quarantined),
                "acceptance_rate": verification.acceptance_rate,
                "rejection_counts": verification.rejection_counts,
            },
        )
        return accepted_path, self.sha256_file(accepted_path)

    def write_checkpoint(
        self,
        training: TrainingResult,
        manifest: LineageManifest,
        *,
        status: str,
    ) -> Path:
        checkpoint_dir = self.root / "checkpoints" / training.checkpoint_id
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        checkpoint_payload = training.to_dict()
        checkpoint_payload["status"] = status
        self.write_json(checkpoint_dir / "checkpoint.json", checkpoint_payload)
        self.write_json(checkpoint_dir / "lineage_manifest.json", manifest.to_dict())
        return checkpoint_dir

    def write_peak(
        self,
        *,
        checkpoint_id: str,
        score: float,
        model_id: str,
        iteration: int,
    ) -> None:
        self.write_json(
            self.root / "peak_checkpoint.json",
            {
                "checkpoint_id": checkpoint_id,
                "score": score,
                "model_id": model_id,
                "iteration": iteration,
            },
        )

    def load_peak(self) -> dict[str, Any] | None:
        path = self.root / "peak_checkpoint.json"
        return self.read_json(path) if path.exists() else None

    def mark_iteration_quarantined(
        self, *, iteration: int, checkpoint_id: str, reason: str
    ) -> Path:
        marker = self.root / "iterations" / f"iter-{iteration:03d}" / "QUARANTINED.json"
        self.write_json(
            marker,
            {"iteration": iteration, "checkpoint_id": checkpoint_id, "reason": reason},
        )
        return marker

    def write_harness_snapshot(self, version: str, payload: dict[str, Any]) -> Path:
        path = self.root / "harness" / f"{version}.json"
        self.write_json(path, payload)
        return path

    def write_report(self, name: str, payload: dict[str, Any]) -> Path:
        path = self.root / "reports" / name
        self.write_json(path, payload)
        return path

    @staticmethod
    def sha256_file(path: str | Path) -> str:
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def write_json(path: str | Path, payload: dict[str, Any]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _atomic_write(target, serialized)

    @staticmethod
    def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        serialized = "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows
        )
        _atomic_write(target, serialized)

    @staticmethod
    def read_json(path: str | Path) -> dict[str, Any]:
        with Path(path).open("r", encoding="utf-8") as handle:
            value = json.load(handle)
        if not isinstance(value, dict):
            raise ValueError(f"expected object in {path}")
        return value

    @staticmethod
    def read_jsonl(path: str | Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with Path(path).open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError(f"expected object row in {path}")
                    rows.append(value)
        return rows


def _atomic_write(path: Path, content: str) -> None:
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)
