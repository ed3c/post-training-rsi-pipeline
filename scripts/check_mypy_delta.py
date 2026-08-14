#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

_ERROR = re.compile(
    r"^(.*?):\d+(?::\d+)?: error: (.*?)(?:\s+\[([^]]+)\])?$"
)


def _normalize_path(raw: str) -> str:
    value = raw.replace("\\", "/")
    for marker in ("/src/", "/tests/", "/scripts/"):
        if marker in value:
            tail = value.split(marker, 1)[1]
            return f"{marker.strip('/')}/{tail}"
    return value.removeprefix("./")


def _fingerprints(output: str) -> Counter[str]:
    result: Counter[str] = Counter()
    for line in output.splitlines():
        match = _ERROR.match(line)
        if match is None:
            continue
        path, message, code = match.groups()
        suffix = f" [{code}]" if code else ""
        result[f"{_normalize_path(path)}: {message}{suffix}"] += 1
    return result


def _run_mypy(cwd: Path) -> tuple[str, Counter[str]]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "src",
            "--show-error-codes",
            "--no-error-summary",
        ],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        print(completed.stdout)
        raise RuntimeError(
            f"mypy could not complete in {cwd}: exit {completed.returncode}"
        )
    return completed.stdout, _fingerprints(completed.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reject new mypy errors relative to the exact PR base ref."
    )
    parser.add_argument("--base-ref", required=True)
    args = parser.parse_args()

    repository = Path.cwd().resolve()
    temporary_root = Path(tempfile.mkdtemp(prefix="post-training-rsi-mypy-base-"))
    base_tree = temporary_root / "tree"
    try:
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(base_tree), args.base_ref],
            cwd=repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        )
        base_output, base_errors = _run_mypy(base_tree)
        head_output, head_errors = _run_mypy(repository)

        new_errors = head_errors - base_errors
        resolved_errors = base_errors - head_errors
        print(
            "mypy delta: "
            f"base={sum(base_errors.values())}, "
            f"head={sum(head_errors.values())}, "
            f"resolved={sum(resolved_errors.values())}, "
            f"new={sum(new_errors.values())}"
        )
        if new_errors:
            print("New mypy errors relative to the exact base ref:")
            for fingerprint, count in sorted(new_errors.items()):
                print(f"  {count} x {fingerprint}")
            print("\nHead mypy output:\n" + head_output)
            print("\nBase mypy output:\n" + base_output)
            return 1
        return 0
    finally:
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(base_tree)],
            cwd=repository,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        shutil.rmtree(temporary_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
