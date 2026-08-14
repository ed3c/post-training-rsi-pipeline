from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping, cast

SCHEMA_VERSION = "post-training-rsi.recovery-bundle/v1"
RECORD_TYPE = "recovery_bundle_manifest"
MANIFEST_NAME = "manifest.json"
BLOBS_DIRECTORY = "blobs"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_FILES = 100_000
DEFAULT_MAX_BYTES = 1 << 40

EntryKind = Literal["file", "directory"]


class RecoveryBundleError(RuntimeError):
    """Base class for deterministic recovery-bundle failures."""


class RecoveryBundleIntegrityError(RecoveryBundleError):
    """Raised when bytes, identities, or paths do not match the manifest."""


class RecoveryBundleConflictError(RecoveryBundleError):
    """Raised when a create or stage target is already owned by another writer."""


@dataclass(frozen=True, slots=True)
class BundleEntry:
    path: str
    kind: EntryKind
    size: int
    mode: int
    sha256: str | None

    def __post_init__(self) -> None:
        _validate_relative_path(self.path)
        if self.kind not in {"file", "directory"}:
            raise RecoveryBundleIntegrityError(f"unsupported entry kind: {self.kind!r}")
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            raise RecoveryBundleIntegrityError("entry size must be a non-negative integer")
        if isinstance(self.mode, bool) or not isinstance(self.mode, int):
            raise RecoveryBundleIntegrityError("entry mode must be an integer")
        if not 0 <= self.mode <= 0o777:
            raise RecoveryBundleIntegrityError("entry mode must contain permission bits only")
        if self.kind == "directory":
            if self.size != 0 or self.sha256 is not None:
                raise RecoveryBundleIntegrityError(
                    "directory entries must have size=0 and sha256=null"
                )
        else:
            _validate_sha256(self.sha256)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size": self.size,
            "mode": self.mode,
            "sha256": self.sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> BundleEntry:
        data = _exact_mapping(
            value,
            {"path", "kind", "size", "mode", "sha256"},
            "bundle entry",
        )
        path = _required_string(data, "path")
        kind_value = _required_string(data, "kind")
        if kind_value not in {"file", "directory"}:
            raise RecoveryBundleIntegrityError(
                f"bundle entry kind is unsupported: {kind_value!r}"
            )
        sha_value = data["sha256"]
        if sha_value is not None and not isinstance(sha_value, str):
            raise RecoveryBundleIntegrityError("bundle entry sha256 must be a string or null")
        return cls(
            path=path,
            kind=cast(EntryKind, kind_value),
            size=_required_integer(data, "size"),
            mode=_required_integer(data, "mode"),
            sha256=sha_value,
        )


@dataclass(frozen=True, slots=True)
class RecoveryBundleManifest:
    bundle_id: str
    source_label: str
    entries: tuple[BundleEntry, ...]
    file_count: int
    directory_count: int
    total_bytes: int

    def __post_init__(self) -> None:
        _validate_sha256(self.bundle_id)
        if not isinstance(self.source_label, str) or not self.source_label.strip():
            raise RecoveryBundleIntegrityError("source_label must be a non-empty string")
        if "\x00" in self.source_label or len(self.source_label) > 512:
            raise RecoveryBundleIntegrityError("source_label is invalid")
        expected_order = tuple(sorted(self.entries, key=lambda item: item.path))
        if self.entries != expected_order:
            raise RecoveryBundleIntegrityError("manifest entries must be sorted by path")
        paths = tuple(item.path for item in self.entries)
        if len(paths) != len(set(paths)):
            raise RecoveryBundleIntegrityError("manifest entry paths must be unique")
        expected_file_count = sum(item.kind == "file" for item in self.entries)
        expected_directory_count = sum(item.kind == "directory" for item in self.entries)
        expected_total_bytes = sum(
            item.size for item in self.entries if item.kind == "file"
        )
        if self.file_count != expected_file_count:
            raise RecoveryBundleIntegrityError("manifest file_count does not match entries")
        if self.directory_count != expected_directory_count:
            raise RecoveryBundleIntegrityError(
                "manifest directory_count does not match entries"
            )
        if self.total_bytes != expected_total_bytes:
            raise RecoveryBundleIntegrityError("manifest total_bytes does not match entries")
        if self.bundle_id != _manifest_identity(self.identity_payload()):
            raise RecoveryBundleIntegrityError("manifest bundle_id does not match its content")

    def identity_payload(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": RECORD_TYPE,
            "source_label": self.source_label,
            "entries": [entry.to_dict() for entry in self.entries],
            "file_count": self.file_count,
            "directory_count": self.directory_count,
            "total_bytes": self.total_bytes,
        }

    def to_dict(self) -> dict[str, object]:
        value = self.identity_payload()
        value["bundle_id"] = self.bundle_id
        return value

    @classmethod
    def create(
        cls,
        *,
        source_label: str,
        entries: tuple[BundleEntry, ...],
    ) -> RecoveryBundleManifest:
        ordered = tuple(sorted(entries, key=lambda item: item.path))
        payload: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "record_type": RECORD_TYPE,
            "source_label": source_label,
            "entries": [entry.to_dict() for entry in ordered],
            "file_count": sum(item.kind == "file" for item in ordered),
            "directory_count": sum(item.kind == "directory" for item in ordered),
            "total_bytes": sum(
                item.size for item in ordered if item.kind == "file"
            ),
        }
        return cls(
            bundle_id=_manifest_identity(payload),
            source_label=source_label,
            entries=ordered,
            file_count=cast(int, payload["file_count"]),
            directory_count=cast(int, payload["directory_count"]),
            total_bytes=cast(int, payload["total_bytes"]),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RecoveryBundleManifest:
        data = _exact_mapping(
            value,
            {
                "schema_version",
                "record_type",
                "bundle_id",
                "source_label",
                "entries",
                "file_count",
                "directory_count",
                "total_bytes",
            },
            "recovery bundle manifest",
        )
        if data["schema_version"] != SCHEMA_VERSION:
            raise RecoveryBundleIntegrityError("unsupported recovery bundle schema")
        if data["record_type"] != RECORD_TYPE:
            raise RecoveryBundleIntegrityError("unexpected recovery manifest record_type")
        entries_value = data["entries"]
        if not isinstance(entries_value, list):
            raise RecoveryBundleIntegrityError("manifest entries must be a JSON array")
        entries: list[BundleEntry] = []
        for item in entries_value:
            if not isinstance(item, Mapping):
                raise RecoveryBundleIntegrityError(
                    "every manifest entry must be a JSON object"
                )
            entries.append(BundleEntry.from_dict(item))
        return cls(
            bundle_id=_required_string(data, "bundle_id"),
            source_label=_required_string(data, "source_label"),
            entries=tuple(entries),
            file_count=_required_integer(data, "file_count"),
            directory_count=_required_integer(data, "directory_count"),
            total_bytes=_required_integer(data, "total_bytes"),
        )


@dataclass(frozen=True, slots=True)
class BundleVerificationReport:
    bundle_id: str
    source_label: str
    file_count: int
    directory_count: int
    total_bytes: int
    blob_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "verified",
            "schema_version": SCHEMA_VERSION,
            "bundle_id": self.bundle_id,
            "source_label": self.source_label,
            "file_count": self.file_count,
            "directory_count": self.directory_count,
            "total_bytes": self.total_bytes,
            "blob_count": self.blob_count,
        }


@dataclass(frozen=True, slots=True)
class StagedRestoreReport:
    bundle_id: str
    destination: str
    file_count: int
    directory_count: int
    total_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "staged",
            "schema_version": SCHEMA_VERSION,
            "bundle_id": self.bundle_id,
            "destination": self.destination,
            "file_count": self.file_count,
            "directory_count": self.directory_count,
            "total_bytes": self.total_bytes,
            "activated": False,
        }


def create_bundle(
    source: str | Path,
    bundle: str | Path,
    *,
    source_label: str | None = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> BundleVerificationReport:
    """Create a content-addressed bundle without mutating the source workspace."""

    source_root = _existing_directory(source, "source")
    bundle_root = Path(bundle).expanduser().absolute()
    _validate_limits(max_files=max_files, max_bytes=max_bytes)
    _reject_nested_path(source_root, bundle_root, "bundle output")
    label = source_label or source_root.name
    lock_path = bundle_root.parent / f".{bundle_root.name}.create.lock"
    staging_root = bundle_root.parent / f".{bundle_root.name}.tmp-{uuid.uuid4().hex}"
    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _acquire_lock(lock_path)
    try:
        if bundle_root.exists() or bundle_root.is_symlink():
            raise RecoveryBundleConflictError(f"bundle target already exists: {bundle_root}")
        staging_root.mkdir(mode=0o700)
        blobs_root = staging_root / BLOBS_DIRECTORY
        blobs_root.mkdir(mode=0o700)
        entries: list[BundleEntry] = []
        file_count = 0
        total_bytes = 0
        for candidate in sorted(
            source_root.rglob("*"),
            key=lambda item: item.relative_to(source_root).as_posix(),
        ):
            relative = candidate.relative_to(source_root).as_posix()
            metadata = candidate.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise RecoveryBundleIntegrityError(
                    f"source contains a symbolic link: {relative}"
                )
            mode = stat.S_IMODE(metadata.st_mode) & 0o777
            if stat.S_ISDIR(metadata.st_mode):
                entries.append(
                    BundleEntry(
                        path=relative,
                        kind="directory",
                        size=0,
                        mode=mode,
                        sha256=None,
                    )
                )
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise RecoveryBundleIntegrityError(
                    f"source contains an unsupported filesystem entry: {relative}"
                )
            file_count += 1
            total_bytes += metadata.st_size
            if file_count > max_files:
                raise RecoveryBundleIntegrityError(
                    f"source exceeds max_files={max_files}"
                )
            if total_bytes > max_bytes:
                raise RecoveryBundleIntegrityError(
                    f"source exceeds max_bytes={max_bytes}"
                )
            digest, copied_size = _copy_file_to_blob(candidate, blobs_root)
            if copied_size != metadata.st_size:
                raise RecoveryBundleIntegrityError(
                    f"source file changed while bundling: {relative}"
                )
            entries.append(
                BundleEntry(
                    path=relative,
                    kind="file",
                    size=copied_size,
                    mode=mode,
                    sha256=digest,
                )
            )
        manifest = RecoveryBundleManifest.create(
            source_label=label,
            entries=tuple(entries),
        )
        _write_json_exclusive(staging_root / MANIFEST_NAME, manifest.to_dict())
        _fsync_directory(staging_root)
        os.rename(staging_root, bundle_root)
        _fsync_directory(bundle_root.parent)
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
    return verify_bundle(bundle_root)


def load_manifest(bundle: str | Path) -> RecoveryBundleManifest:
    bundle_root = _existing_directory(bundle, "bundle")
    _reject_symlink(bundle_root, "bundle")
    manifest_path = bundle_root / MANIFEST_NAME
    _reject_symlink(manifest_path, "bundle manifest")
    try:
        size = manifest_path.stat().st_size
    except FileNotFoundError as exc:
        raise RecoveryBundleIntegrityError("bundle manifest is missing") from exc
    if size > MAX_MANIFEST_BYTES:
        raise RecoveryBundleIntegrityError("bundle manifest exceeds the size limit")
    try:
        raw = manifest_path.read_text(encoding="utf-8")
        value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RecoveryBundleIntegrityError("bundle manifest is not readable JSON") from exc
    if not isinstance(value, Mapping):
        raise RecoveryBundleIntegrityError("bundle manifest must be a JSON object")
    manifest = RecoveryBundleManifest.from_dict(value)
    if raw != _canonical_json(manifest.to_dict()) + "\n":
        raise RecoveryBundleIntegrityError("bundle manifest is not canonical JSON")
    return manifest


def verify_bundle(bundle: str | Path) -> BundleVerificationReport:
    """Verify the manifest, referenced blobs, and absence of unreferenced blobs."""

    bundle_root = _existing_directory(bundle, "bundle")
    manifest = load_manifest(bundle_root)
    blobs_root = bundle_root / BLOBS_DIRECTORY
    _reject_symlink(blobs_root, "bundle blobs directory")
    if not blobs_root.is_dir():
        raise RecoveryBundleIntegrityError("bundle blobs directory is missing")
    referenced: set[str] = set()
    for entry in manifest.entries:
        if entry.kind != "file":
            continue
        assert entry.sha256 is not None
        referenced.add(entry.sha256)
        blob_path = blobs_root / entry.sha256
        _reject_symlink(blob_path, f"blob {entry.sha256}")
        if not blob_path.is_file():
            raise RecoveryBundleIntegrityError(
                f"referenced blob is missing: {entry.sha256}"
            )
        actual_digest, actual_size = _hash_file(blob_path)
        if actual_digest != entry.sha256 or actual_size != entry.size:
            raise RecoveryBundleIntegrityError(
                f"blob integrity mismatch for {entry.path}"
            )
    actual_blob_names: set[str] = set()
    for item in blobs_root.iterdir():
        _reject_symlink(item, "bundle blob entry")
        if not item.is_file():
            raise RecoveryBundleIntegrityError(
                f"bundle blobs directory contains a non-file entry: {item.name}"
            )
        _validate_sha256(item.name)
        actual_blob_names.add(item.name)
    if actual_blob_names != referenced:
        missing = sorted(referenced - actual_blob_names)
        extra = sorted(actual_blob_names - referenced)
        raise RecoveryBundleIntegrityError(
            f"bundle blob set mismatch: missing={missing}, extra={extra}"
        )
    return BundleVerificationReport(
        bundle_id=manifest.bundle_id,
        source_label=manifest.source_label,
        file_count=manifest.file_count,
        directory_count=manifest.directory_count,
        total_bytes=manifest.total_bytes,
        blob_count=len(referenced),
    )


def stage_bundle(
    bundle: str | Path,
    destination: str | Path,
) -> StagedRestoreReport:
    """Restore into a new directory only; never activate or overwrite a live workspace."""

    bundle_root = _existing_directory(bundle, "bundle")
    verification = verify_bundle(bundle_root)
    manifest = load_manifest(bundle_root)
    destination_root = Path(destination).expanduser().absolute()
    _reject_nested_path(bundle_root, destination_root, "staged destination")
    lock_path = destination_root.parent / f".{destination_root.name}.stage.lock"
    staging_root = destination_root.parent / f".{destination_root.name}.tmp-{uuid.uuid4().hex}"
    destination_root.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = _acquire_lock(lock_path)
    try:
        if destination_root.exists() or destination_root.is_symlink():
            raise RecoveryBundleConflictError(
                f"staged destination already exists: {destination_root}"
            )
        staging_root.mkdir(mode=0o700)
        for entry in manifest.entries:
            if entry.kind != "directory":
                continue
            target = _safe_destination(staging_root, entry.path)
            target.mkdir(parents=True, exist_ok=False)
            os.chmod(target, entry.mode)
        blobs_root = bundle_root / BLOBS_DIRECTORY
        for entry in manifest.entries:
            if entry.kind != "file":
                continue
            assert entry.sha256 is not None
            target = _safe_destination(staging_root, entry.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_blob_to_target(blobs_root / entry.sha256, target, entry.mode)
        _verify_directory_against_manifest(staging_root, manifest)
        _fsync_directory(staging_root)
        os.rename(staging_root, destination_root)
        _fsync_directory(destination_root.parent)
    except Exception:
        shutil.rmtree(staging_root, ignore_errors=True)
        raise
    finally:
        os.close(lock_fd)
        lock_path.unlink(missing_ok=True)
    return StagedRestoreReport(
        bundle_id=verification.bundle_id,
        destination=str(destination_root),
        file_count=verification.file_count,
        directory_count=verification.directory_count,
        total_bytes=verification.total_bytes,
    )


def verify_staged_directory(
    bundle: str | Path,
    destination: str | Path,
) -> BundleVerificationReport:
    bundle_root = _existing_directory(bundle, "bundle")
    verification = verify_bundle(bundle_root)
    manifest = load_manifest(bundle_root)
    destination_root = _existing_directory(destination, "staged destination")
    _verify_directory_against_manifest(destination_root, manifest)
    return verification


def _verify_directory_against_manifest(
    root: Path,
    manifest: RecoveryBundleManifest,
) -> None:
    expected_paths = {entry.path for entry in manifest.entries}
    actual_paths: set[str] = set()
    for candidate in sorted(
        root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()
    ):
        relative = candidate.relative_to(root).as_posix()
        actual_paths.add(relative)
        metadata = candidate.lstat()
        if stat.S_ISLNK(metadata.st_mode):
            raise RecoveryBundleIntegrityError(
                f"staged directory contains a symbolic link: {relative}"
            )
    if actual_paths != expected_paths:
        missing = sorted(expected_paths - actual_paths)
        extra = sorted(actual_paths - expected_paths)
        raise RecoveryBundleIntegrityError(
            f"staged path set mismatch: missing={missing}, extra={extra}"
        )
    for entry in manifest.entries:
        candidate = _safe_destination(root, entry.path)
        metadata = candidate.lstat()
        if entry.kind == "directory":
            if not stat.S_ISDIR(metadata.st_mode):
                raise RecoveryBundleIntegrityError(
                    f"staged entry is not a directory: {entry.path}"
                )
            continue
        if not stat.S_ISREG(metadata.st_mode):
            raise RecoveryBundleIntegrityError(
                f"staged entry is not a regular file: {entry.path}"
            )
        actual_digest, actual_size = _hash_file(candidate)
        if actual_digest != entry.sha256 or actual_size != entry.size:
            raise RecoveryBundleIntegrityError(
                f"staged file integrity mismatch: {entry.path}"
            )


def _copy_file_to_blob(source: Path, blobs_root: Path) -> tuple[str, int]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(source, flags)
    temporary = blobs_root / f".tmp-{uuid.uuid4().hex}"
    output_descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    digest = hashlib.sha256()
    size = 0
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise RecoveryBundleIntegrityError(
                f"source entry is not a regular file: {source}"
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
            _write_all(output_descriptor, chunk)
        os.fsync(output_descriptor)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RecoveryBundleIntegrityError(
                f"source file changed while being read: {source}"
            )
    finally:
        os.close(descriptor)
        os.close(output_descriptor)
    hexdigest = digest.hexdigest()
    target = blobs_root / hexdigest
    if target.exists():
        actual_digest, actual_size = _hash_file(target)
        if actual_digest != hexdigest or actual_size != size:
            temporary.unlink(missing_ok=True)
            raise RecoveryBundleIntegrityError(
                f"existing content-addressed blob is inconsistent: {hexdigest}"
            )
        temporary.unlink(missing_ok=True)
    else:
        os.replace(temporary, target)
        os.chmod(target, 0o400)
        _fsync_directory(blobs_root)
    return hexdigest, size


def _copy_blob_to_target(blob: Path, target: Path, mode: int) -> None:
    _reject_symlink(blob, "source blob")
    if not blob.is_file():
        raise RecoveryBundleIntegrityError(f"source blob is missing: {blob.name}")
    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    source_descriptor = os.open(blob, source_flags)
    target_descriptor = os.open(
        target,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        while True:
            chunk = os.read(source_descriptor, 1024 * 1024)
            if not chunk:
                break
            _write_all(target_descriptor, chunk)
        os.fsync(target_descriptor)
    finally:
        os.close(source_descriptor)
        os.close(target_descriptor)
    os.chmod(target, mode)


def _hash_file(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    digest = hashlib.sha256()
    size = 0
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RecoveryBundleIntegrityError(f"not a regular file: {path}")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise RecoveryBundleIntegrityError("filesystem write made no progress")
        view = view[written:]


def _write_json_exclusive(path: Path, value: Mapping[str, object]) -> None:
    encoded = (_canonical_json(value) + "\n").encode("utf-8")
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        _write_all(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _manifest_identity(value: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _existing_directory(value: str | Path, label: str) -> Path:
    path = Path(value).expanduser()
    _reject_symlink(path, label)
    try:
        resolved = path.resolve(strict=True)
    except FileNotFoundError as exc:
        raise RecoveryBundleIntegrityError(f"{label} does not exist: {path}") from exc
    if not resolved.is_dir():
        raise RecoveryBundleIntegrityError(f"{label} must be a directory: {resolved}")
    return resolved


def _reject_symlink(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise RecoveryBundleIntegrityError(f"{label} must not be a symbolic link")
    except OSError as exc:
        raise RecoveryBundleIntegrityError(f"cannot inspect {label}: {path}") from exc


def _reject_nested_path(parent: Path, candidate: Path, label: str) -> None:
    parent_resolved = parent.resolve(strict=True)
    candidate_resolved = candidate.resolve(strict=False)
    try:
        candidate_resolved.relative_to(parent_resolved)
    except ValueError:
        return
    raise RecoveryBundleIntegrityError(f"{label} must be outside {parent_resolved}")


def _safe_destination(root: Path, relative: str) -> Path:
    _validate_relative_path(relative)
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    resolved_parent = candidate.parent.resolve(strict=False)
    root_resolved = root.resolve(strict=True)
    try:
        resolved_parent.relative_to(root_resolved)
    except ValueError as exc:
        raise RecoveryBundleIntegrityError(
            f"staged path escapes destination root: {relative}"
        ) from exc
    return candidate


def _validate_relative_path(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 4096:
        raise RecoveryBundleIntegrityError("entry path must be a non-empty relative path")
    if "\x00" in value or "\\" in value:
        raise RecoveryBundleIntegrityError("entry path contains forbidden characters")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RecoveryBundleIntegrityError(f"unsafe relative path: {value!r}")
    if path.as_posix() != value:
        raise RecoveryBundleIntegrityError(f"entry path is not canonical: {value!r}")


def _validate_sha256(value: str | None) -> None:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise RecoveryBundleIntegrityError(
            "SHA-256 values must contain 64 lowercase hexadecimal characters"
        )


def _validate_limits(*, max_files: int, max_bytes: int) -> None:
    if isinstance(max_files, bool) or not isinstance(max_files, int) or max_files < 0:
        raise RecoveryBundleIntegrityError("max_files must be a non-negative integer")
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 0:
        raise RecoveryBundleIntegrityError("max_bytes must be a non-negative integer")


def _exact_mapping(
    value: Mapping[str, object],
    expected: set[str],
    label: str,
) -> dict[str, object]:
    if any(not isinstance(key, str) for key in value):
        raise RecoveryBundleIntegrityError(f"{label} keys must be strings")
    actual = set(value)
    missing = sorted(expected - actual)
    unknown = sorted(actual - expected)
    if missing or unknown:
        raise RecoveryBundleIntegrityError(
            f"{label} fields mismatch: missing={missing}, unknown={unknown}"
        )
    return dict(value)


def _required_string(value: Mapping[str, object], field: str) -> str:
    item = value[field]
    if not isinstance(item, str):
        raise RecoveryBundleIntegrityError(f"{field} must be a string")
    return item


def _required_integer(value: Mapping[str, object], field: str) -> int:
    item = value[field]
    if isinstance(item, bool) or not isinstance(item, int):
        raise RecoveryBundleIntegrityError(f"{field} must be an integer")
    return item


def _acquire_lock(path: Path) -> int:
    try:
        return os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise RecoveryBundleConflictError(
            f"another recovery-bundle writer owns lock: {path}"
        ) from exc


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
