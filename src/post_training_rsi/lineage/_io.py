from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..adapter_runtime.errors import AdapterIntegrityError
from ..adapter_runtime.integrity import sha256_path as canonical_artifact_sha256
from ..control_plane.validation import canonical_json, normalize_json_object


class LineageStoreError(RuntimeError):
    """Base error for local lineage persistence."""


class LineageConflictError(LineageStoreError):
    """Raised when an immutable ID is reused with different content."""


class LineageIntegrityError(LineageStoreError):
    """Raised when committed bytes no longer match their manifest."""


class LineageLockTimeout(LineageStoreError):
    """Raised when a local persistence lock cannot be acquired safely."""


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    normalized = normalize_json_object(payload, "payload")
    return (canonical_json(normalized) + "\n").encode("utf-8")


def read_json_object(path: str | Path) -> dict[str, object]:
    target = Path(path)
    try:
        with target.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise LineageIntegrityError(f"cannot read JSON object from {target}") from exc
    if not isinstance(value, dict):
        raise LineageIntegrityError(f"expected JSON object in {target}")
    return value


def write_immutable(path: str | Path, content: bytes) -> None:
    """Create immutable content or accept an exact idempotent retry."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        existing = target.read_bytes()
        if existing != content:
            raise LineageConflictError(f"immutable content conflict at {target}")
        return
    _atomic_replace(target, content)


def replace_atomic(path: str | Path, content: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    _atomic_replace(target, content)


def replace_directory_atomic(source: str | Path, destination: str | Path) -> None:
    source_path = Path(source)
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(source_path, destination_path)
    _fsync_directory(destination_path.parent)


def verify_file_hash(path: str | Path, expected_sha256: str) -> None:
    target = Path(path)
    try:
        actual = sha256_bytes(target.read_bytes())
    except OSError as exc:
        raise LineageIntegrityError(f"missing committed file {target}") from exc
    if actual != expected_sha256:
        raise LineageIntegrityError(
            f"hash mismatch for {target}: expected {expected_sha256}, got {actual}"
        )


def sha256_path(path: str | Path) -> str:
    """Use the adapter/runtime artifact hash as the repository-wide canonical hash."""

    target = Path(path)
    try:
        return canonical_artifact_sha256(target)
    except (AdapterIntegrityError, OSError) as exc:
        message = str(exc)
        if isinstance(exc, OSError) or "regular file or directory" in message or "does not exist" in message:
            message = f"artifact path does not exist: {target}"
        elif "contains a symlink" in message:
            message = message.replace("a symlink", "symlink")
        raise LineageIntegrityError(message) from exc


@contextmanager
def exclusive_lock(
    path: str | Path,
    *,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> Iterator[None]:
    """Portable fail-closed lock using O_EXCL; stale locks require human cleanup."""

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_seconds
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(
                lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            if time.monotonic() >= deadline:
                raise LineageLockTimeout(f"timed out acquiring lock {lock_path}") from exc
            time.sleep(poll_interval_seconds)
    try:
        os.write(descriptor, f"pid={os.getpid()}\n".encode("ascii"))
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _atomic_replace(path: Path, content: bytes) -> None:
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp"
    )
    descriptor = os.open(
        temp_path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _fsync_directory(path.parent)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(directory: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
