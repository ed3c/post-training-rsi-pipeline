from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import AdapterIntegrityError

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def canonical_sha256(value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def make_idempotency_key(stage: str, value: Mapping[str, Any]) -> str:
    if not stage or any(ord(character) < 32 for character in stage):
        raise ValueError("stage must be a non-empty printable string")
    return f"{stage}:{canonical_sha256(value)}"


def validate_sha256(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _SHA256_PATTERN.fullmatch(value):
        raise AdapterIntegrityError(
            f"{field_name} must contain 64 lowercase hex characters"
        )
    return value


def resolve_artifact_path(
    value: object,
    *,
    output_root: Path,
    allow_external: bool,
) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise AdapterIntegrityError(
            "checkpoint_path must be a non-empty string"
        )
    root = output_root.resolve()
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_symlink():
        raise AdapterIntegrityError("checkpoint artifact must not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise AdapterIntegrityError(
            f"checkpoint artifact does not exist: {candidate}"
        ) from exc
    if not allow_external:
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise AdapterIntegrityError(
                "checkpoint artifact escaped the configured output root"
            ) from exc
    if resolved.is_symlink():
        raise AdapterIntegrityError("checkpoint artifact must not be a symlink")
    if not resolved.is_file() and not resolved.is_dir():
        raise AdapterIntegrityError(
            "checkpoint artifact must be a regular file or directory"
        )
    return resolved


def sha256_path(path: Path) -> str:
    resolved = path.resolve(strict=True)
    if path.is_symlink() or resolved.is_symlink():
        raise AdapterIntegrityError("artifact symlinks are not allowed")
    if resolved.is_file():
        return _sha256_file(resolved)
    if not resolved.is_dir():
        raise AdapterIntegrityError(
            "artifact must be a regular file or directory"
        )

    digest = hashlib.sha256()
    files = sorted(
        (item for item in resolved.rglob("*") if item.is_file() or item.is_symlink()),
        key=lambda item: item.relative_to(resolved).as_posix(),
    )
    if not files:
        digest.update(b"empty-directory\x00")
    for item in files:
        if item.is_symlink():
            raise AdapterIntegrityError(
                f"artifact contains a symlink: {item.relative_to(resolved)}"
            )
        if not item.is_file():
            raise AdapterIntegrityError(
                f"artifact contains a non-regular entry: {item}"
            )
        relative = item.relative_to(resolved).as_posix().encode("utf-8")
        digest.update(b"file\x00")
        digest.update(relative)
        digest.update(b"\x00")
        digest.update(bytes.fromhex(_sha256_file(item)))
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
