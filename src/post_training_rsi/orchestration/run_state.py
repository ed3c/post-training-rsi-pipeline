from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..control_plane import StateSnapshot
from ..control_plane.validation import canonical_json, normalize_timestamp, validate_id
from ..lineage import ControlRecordStore
from ..lineage._io import LineageConflictError, LineageIntegrityError, read_json_object

RUN_METADATA_SCHEMA_VERSION = "post-training-rsi.run/v1"


@dataclass(frozen=True, slots=True)
class RunMetadata:
    run_id: str
    config_sha256: str
    started_at: str
    code_git_commit: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "run_id", validate_id(self.run_id, "run_id"))
        if len(self.config_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.config_sha256
        ):
            raise ValueError("config_sha256 must contain 64 lowercase hex characters")
        object.__setattr__(self, "started_at", normalize_timestamp(self.started_at))
        if not isinstance(self.code_git_commit, str) or not self.code_git_commit.strip():
            raise ValueError("code_git_commit must be a non-empty string")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": RUN_METADATA_SCHEMA_VERSION,
            "run_id": self.run_id,
            "config_sha256": self.config_sha256,
            "started_at": self.started_at,
            "code_git_commit": self.code_git_commit,
        }

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> RunMetadata:
        expected = {
            "schema_version",
            "run_id",
            "config_sha256",
            "started_at",
            "code_git_commit",
        }
        missing = sorted(expected - set(value))
        unknown = sorted(set(value) - expected)
        if missing or unknown:
            raise LineageIntegrityError(
                f"run metadata fields mismatch: missing={missing}, unknown={unknown}"
            )
        if value["schema_version"] != RUN_METADATA_SCHEMA_VERSION:
            raise LineageIntegrityError("unsupported run metadata schema")
        for key in ("run_id", "config_sha256", "started_at", "code_git_commit"):
            if not isinstance(value[key], str):
                raise LineageIntegrityError(f"run metadata {key} must be a string")
        return cls(
            run_id=value["run_id"],
            config_sha256=value["config_sha256"],
            started_at=value["started_at"],
            code_git_commit=value["code_git_commit"],
        )


class RunMetadataStore:
    """One immutable logical clock anchor and config identity per run."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.runs_root = self.root / "runs"
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def path(self, run_id: str) -> Path:
        return self.runs_root / validate_id(run_id, "run_id") / "run.json"

    def load_or_create(
        self,
        *,
        run_id: str,
        config_sha256: str,
        started_at: str,
        code_git_commit: str,
    ) -> RunMetadata:
        metadata = RunMetadata(
            run_id=run_id,
            config_sha256=config_sha256,
            started_at=started_at,
            code_git_commit=code_git_commit,
        )
        path = self.path(run_id)
        payload = canonical_json(metadata.to_dict()) + "\n"
        if path.exists() or path.is_symlink():
            existing = self.load(run_id)
            if existing.config_sha256 != config_sha256:
                raise LineageConflictError(
                    "run config changed; use a new run_id or a new workspace"
                )
            return existing
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_once(path, payload)
        return self.load(run_id)

    def load(self, run_id: str) -> RunMetadata:
        path = self.path(run_id)
        if not path.exists():
            raise FileNotFoundError(path)
        if path.is_symlink() or not path.is_file():
            raise LineageIntegrityError("run metadata must be a regular file")
        value = read_json_object(path)
        metadata = RunMetadata.from_dict(value)
        if metadata.run_id != run_id:
            raise LineageIntegrityError("run metadata ID and directory differ")
        return metadata


class LogicalRunClock:
    """Derive retry-stable timestamps from the immutable run start time."""

    def __init__(self, started_at: str) -> None:
        normalized = normalize_timestamp(started_at)
        self.started_at = datetime.fromisoformat(normalized.replace("Z", "+00:00"))

    def at(self, *, iteration: int, offset_seconds: int) -> str:
        if iteration < 0 or offset_seconds < 0:
            raise ValueError("logical clock offsets must be non-negative")
        value = self.started_at + timedelta(
            hours=iteration,
            seconds=offset_seconds,
        )
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def config_sha256(config_value: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(config_value).encode("utf-8")).hexdigest()


def latest_snapshot(
    store: ControlRecordStore,
    *,
    run_id: str,
) -> StateSnapshot | None:
    run_id = validate_id(run_id, "run_id")
    candidates: list[StateSnapshot] = []
    for path in sorted(store.transactions_root.glob("*.json")):
        manifest = store.load_transaction(path.stem)
        if manifest.run_id != run_id:
            continue
        for ref in manifest.records:
            if ref.record_type == StateSnapshot.RECORD_TYPE:
                snapshot = store.load_snapshot(ref.record_id)
                if snapshot.run_id == run_id:
                    candidates.append(snapshot)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            datetime.fromisoformat(item.entered_at.replace("Z", "+00:00")),
            item.iteration,
            item.snapshot_id,
        ),
    )


def _write_once(path: Path, payload: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            existing = path.read_text(encoding="utf-8")
            if existing != payload:
                raise LineageConflictError(
                    f"immutable run metadata already differs: {path}"
                )
        try:
            directory = os.open(path.parent, os.O_RDONLY)
        except OSError:
            directory = None
        if directory is not None:
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)
