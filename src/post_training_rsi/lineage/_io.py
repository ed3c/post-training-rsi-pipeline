from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..control_plane import JSONValue
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
    """Hash a file or a directory deterministically without following symlinks."""

    target = Path(path)
    if target.is_symlink():
        raise LineageIntegrityError(f"artifact path must not be a symlink: {target}")
    if target.is_file():
        return sha256_bytes(target.read_bytes())
    if not target.is_dir():
        raise LineageIntegrityError(f"artifact path does not exist: {target}")

    digest = hashlib.sha256()
    files = sorted(item for item in target.rglob("*") if item.is_file())
    for item in files:
        if item.is_symlink():
            raise LineageIntegrityError(f"artifact tree contains symlink: {item}")
        relative = item.relative_to(target).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        content = item.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


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
