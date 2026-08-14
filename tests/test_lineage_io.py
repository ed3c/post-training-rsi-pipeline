from __future__ import annotations

import os
from pathlib import Path

import pytest

from post_training_rsi.lineage import LineageIntegrityError
from post_training_rsi.lineage._io import sha256_path


def test_directory_artifact_hash_is_deterministic_and_path_sensitive(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    first.mkdir()
    (first / "b.bin").write_bytes(b"two")
    (first / "a.bin").write_bytes(b"one")

    second = tmp_path / "second"
    second.mkdir()
    (second / "a.bin").write_bytes(b"one")
    (second / "b.bin").write_bytes(b"two")

    assert sha256_path(first) == sha256_path(second)

    (second / "nested").mkdir()
    (second / "nested/a.bin").write_bytes((second / "a.bin").read_bytes())
    (second / "a.bin").unlink()
    assert sha256_path(first) != sha256_path(second)


def test_artifact_hash_rejects_missing_paths(tmp_path: Path) -> None:
    with pytest.raises(LineageIntegrityError, match="does not exist"):
        sha256_path(tmp_path / "missing")


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink support unavailable")
def test_artifact_hash_rejects_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.bin"
    target.write_bytes(b"weights")
    link = tmp_path / "link.bin"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable in this environment")

    with pytest.raises(LineageIntegrityError, match="symlink"):
        sha256_path(link)

    directory = tmp_path / "artifact-tree"
    directory.mkdir()
    nested_link = directory / "weights.bin"
    nested_link.symlink_to(target)
    with pytest.raises(LineageIntegrityError, match="contains symlink"):
        sha256_path(directory)
