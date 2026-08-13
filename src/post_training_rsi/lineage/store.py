from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from ..domain import Checkpoint, TrainingExample, VerificationReport
from ..hashing import canonical_json
from .manifest import LineageManifest


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.iterations_dir = root / "iterations"
        self.checkpoints_dir = root / "checkpoints"
        self.harness_dir = root / "harness"
        self.reports_dir = root / "reports"
        for directory in (
            self.iterations_dir,
            self.checkpoints_dir,
            self.harness_dir,
            self.reports_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def write_iteration(
        self,
        *,
        iteration: int,
        raw_examples: Iterable[TrainingExample],
        report: VerificationReport,
    ) -> Path:
        directory = self.iterations_dir / f"iter-{iteration:03d}"
        directory.mkdir(parents=True, exist_ok=True)
        self.write_jsonl(directory / "raw.jsonl", (item.to_dict() for item in raw_examples))
        self.write_jsonl(directory / "accepted.jsonl", (item.to_dict() for item in report.accepted))
        self.write_jsonl(
            directory / "quarantine.jsonl",
            (item.to_dict() for item in report.quarantined),
        )
        self.write_jsonl(
            directory / "filter_audit.jsonl",
            (decision.to_dict() for decision in report.decisions),
        )
        self.write_json(
            directory / "dataset_summary.json",
            {
                "dataset_hash": report.dataset_hash,
                "accepted_count": len(report.accepted),
                "rejected_count": report.rejected_count,
                "acceptance_rate": report.acceptance_rate,
            },
        )
        return directory

    def write_checkpoint(self, checkpoint: Checkpoint) -> Path:
        directory = self.checkpoints_dir / checkpoint.checkpoint_id
        directory.mkdir(parents=True, exist_ok=True)
        self.write_json(directory / "checkpoint.json", checkpoint.to_dict())
        return directory

    def read_checkpoint(self, checkpoint_id: str) -> Checkpoint:
        data = json.loads(
            (self.checkpoints_dir / checkpoint_id / "checkpoint.json").read_text(encoding="utf-8")
        )
        return Checkpoint(
            checkpoint_id=str(data["checkpoint_id"]),
            parent_checkpoint_id=data.get("parent_checkpoint_id"),
            artifact_path=Path(data["artifact_path"]),
            dataset_hash=str(data["dataset_hash"]),
            training_loss_final=float(data["training_loss_final"]),
            metadata=data.get("metadata", {}),
        )

    def write_manifest(self, manifest: LineageManifest) -> Path:
        path = self.checkpoints_dir / manifest.checkpoint_id / "lineage_manifest.json"
        self.write_json(path, manifest.to_dict())
        return path

    def update_manifest(self, checkpoint_id: str, **changes: Any) -> LineageManifest:
        updated = replace(self.read_manifest(checkpoint_id), **changes)
        self.write_manifest(updated)
        return updated

    def read_manifest(self, checkpoint_id: str) -> LineageManifest:
        path = self.checkpoints_dir / checkpoint_id / "lineage_manifest.json"
        return LineageManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def list_manifests(self) -> list[LineageManifest]:
        return [
            LineageManifest.from_dict(json.loads(path.read_text(encoding="utf-8")))
            for path in sorted(self.checkpoints_dir.glob("*/lineage_manifest.json"))
        ]

    def set_peak_checkpoint(self, checkpoint_id: str, score: float) -> Path:
        path = self.root / "peak_checkpoint.json"
        self.write_json(path, {"checkpoint_id": checkpoint_id, "score": score})
        return path

    def read_peak_checkpoint(self) -> dict[str, Any] | None:
        path = self.root / "peak_checkpoint.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def write_harness_snapshot(self, version: str, payload: dict[str, Any]) -> Path:
        path = self.harness_dir / f"{version}.json"
        self.write_json(path, payload)
        return path

    def quarantine_iteration(self, iteration: int, reason: str) -> Path:
        path = self.iterations_dir / f"iter-{iteration:03d}" / "QUARANTINED.json"
        self.write_json(path, {"reason": reason, "status": "DIRTY"})
        return path

    @staticmethod
    def write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)

    @staticmethod
    def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(canonical_json(record) + "\n")
        os.replace(temp, path)

    @staticmethod
    def git_commit_hash(cwd: Path | None = None) -> str:
        del cwd
        return os.getenv("GIT_COMMIT_SHA", "git-not-recorded")
