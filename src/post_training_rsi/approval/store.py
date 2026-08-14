from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeVar

from ..control_plane.validation import canonical_json, validate_id
from .contracts import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalSampleManifest,
)
from .errors import ApprovalConflictError, ApprovalIntegrityError

_RecordT = TypeVar("_RecordT")
_MAX_RECORD_BYTES = 4 * 1024 * 1024


class ApprovalStore:
    """Immutable local approval store with exact replay semantics."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve() / "approvals"
        self.samples_dir = self.root / "samples"
        self.requests_dir = self.root / "requests"
        self.decisions_dir = self.root / "decisions"
        for directory in (
            self.samples_dir,
            self.requests_dir,
            self.decisions_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def sample_path(self, request_id: str) -> Path:
        request_id = validate_id(request_id, "request_id")
        return self.samples_dir / f"{request_id}.json"

    def request_path(self, request_id: str) -> Path:
        request_id = validate_id(request_id, "request_id")
        return self.requests_dir / f"{request_id}.json"

    def decision_path(self, request_id: str) -> Path:
        request_id = validate_id(request_id, "request_id")
        return self.decisions_dir / f"{request_id}.json"

    def commit_request(
        self,
        *,
        sample: ApprovalSampleManifest,
        request: ApprovalRequest,
    ) -> ApprovalRequest:
        self._validate_request_links(sample=sample, request=request)
        _write_immutable(self.sample_path(request.request_id), sample.to_dict())
        _write_immutable(self.request_path(request.request_id), request.to_dict())
        return self.load_request(request.request_id)

    def commit_decision(
        self,
        decision: ApprovalDecision,
    ) -> ApprovalDecision:
        request = self.load_request(decision.request_id)
        expected_request_sha256 = record_sha256(request.to_dict())
        if decision.request_sha256 != expected_request_sha256:
            raise ApprovalIntegrityError("decision request_sha256 mismatch")
        for field_name in (
            "run_id",
            "iteration",
            "subject_type",
            "subject_id",
            "requested_action",
        ):
            if getattr(decision, field_name) != getattr(request, field_name):
                raise ApprovalIntegrityError(
                    f"decision {field_name} does not match request"
                )
        if decision.decided_at < request.requested_at:
            raise ApprovalIntegrityError(
                "decision timestamp precedes request timestamp"
            )
        if request.expires_at is not None and decision.decided_at > request.expires_at:
            raise ApprovalIntegrityError(
                "decision timestamp is later than request expiration"
            )
        _write_immutable(
            self.decision_path(decision.request_id),
            decision.to_dict(),
        )
        return self.load_decision(decision.request_id)

    def load_sample(self, request_id: str) -> ApprovalSampleManifest:
        path = self.sample_path(request_id)
        sample = _read_record(path, ApprovalSampleManifest.from_dict)
        if sample.request_id != request_id:
            raise ApprovalIntegrityError(
                "sample request_id does not match its filename"
            )
        return sample

    def load_request(self, request_id: str) -> ApprovalRequest:
        path = self.request_path(request_id)
        request = _read_record(path, ApprovalRequest.from_dict)
        if request.request_id != request_id:
            raise ApprovalIntegrityError(
                "request_id does not match its filename"
            )
        sample = self.load_sample(request_id)
        self._validate_request_links(sample=sample, request=request)
        return request

    def load_decision(self, request_id: str) -> ApprovalDecision:
        path = self.decision_path(request_id)
        decision = _read_record(path, ApprovalDecision.from_dict)
        if decision.request_id != request_id:
            raise ApprovalIntegrityError(
                "decision request_id does not match its filename"
            )
        request = self.load_request(request_id)
        if decision.request_sha256 != record_sha256(request.to_dict()):
            raise ApprovalIntegrityError("stored decision request hash mismatch")
        for field_name in (
            "run_id",
            "iteration",
            "subject_type",
            "subject_id",
            "requested_action",
        ):
            if getattr(decision, field_name) != getattr(request, field_name):
                raise ApprovalIntegrityError(
                    f"stored decision {field_name} mismatch"
                )
        return decision

    def has_request(self, request_id: str) -> bool:
        return self.request_path(request_id).is_file()

    def has_decision(self, request_id: str) -> bool:
        return self.decision_path(request_id).is_file()

    def list_request_ids(self) -> tuple[str, ...]:
        request_ids: list[str] = []
        for path in sorted(self.requests_dir.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                raise ApprovalIntegrityError(
                    f"invalid request entry: {path.name}"
                )
            request_ids.append(path.stem)
        return tuple(request_ids)

    def _validate_request_links(
        self,
        *,
        sample: ApprovalSampleManifest,
        request: ApprovalRequest,
    ) -> None:
        for field_name in (
            "request_id",
            "run_id",
            "iteration",
            "subject_type",
            "subject_id",
        ):
            if getattr(sample, field_name) != getattr(request, field_name):
                raise ApprovalIntegrityError(
                    f"sample {field_name} does not match request"
                )
        if request.sample_count != sample.selected_count:
            raise ApprovalIntegrityError(
                "request sample_count does not match sample manifest"
            )
        if request.sample_sha256 != record_sha256(sample.to_dict()):
            raise ApprovalIntegrityError("request sample_sha256 mismatch")
        expected_uri = self.sample_path(request.request_id).as_uri()
        if request.sample_uri != expected_uri:
            raise ApprovalIntegrityError("request sample_uri mismatch")


def record_sha256(value: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _write_immutable(path: Path, value: Mapping[str, object]) -> None:
    serialized = canonical_json(value) + "\n"
    encoded = serialized.encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        _assert_exact_existing(path, serialized)
        return

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary_path, path)
        except FileExistsError:
            _assert_exact_existing(path, serialized)
        _fsync_directory(path.parent)
    finally:
        temporary_path.unlink(missing_ok=True)


def _assert_exact_existing(path: Path, expected: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise ApprovalConflictError(
            f"immutable approval path is not a regular file: {path}"
        )
    try:
        existing = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ApprovalIntegrityError(
            f"cannot read immutable approval record: {path}"
        ) from exc
    if existing != expected:
        raise ApprovalConflictError(
            f"immutable approval record already exists with different bytes: {path}"
        )


def _read_record(
    path: Path,
    parser: Callable[[Mapping[str, object]], _RecordT],
) -> _RecordT:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.is_symlink() or not path.is_file():
        raise ApprovalIntegrityError(
            f"approval record must be a regular file: {path}"
        )
    if path.stat().st_size > _MAX_RECORD_BYTES:
        raise ApprovalIntegrityError(
            f"approval record exceeds {_MAX_RECORD_BYTES} bytes: {path}"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApprovalIntegrityError(
            f"approval record is not valid UTF-8 JSON: {path}"
        ) from exc
    if not isinstance(value, Mapping):
        raise ApprovalIntegrityError(
            f"approval record must be a JSON object: {path}"
        )
    try:
        return parser(value)
    except (TypeError, ValueError) as exc:
        raise ApprovalIntegrityError(
            f"approval record violates its schema: {path}"
        ) from exc


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
