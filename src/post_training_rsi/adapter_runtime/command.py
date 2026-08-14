from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import AdapterExecutionError, AdapterResultError

ADAPTER_RESULT_SCHEMA_VERSION = "post-training-rsi.adapter/v1"


@dataclass(frozen=True, slots=True)
class CommandSpec:
    command: tuple[str, ...]
    timeout_seconds: float
    max_attempts: int = 1
    initial_backoff_seconds: float = 0.0
    max_result_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("command cannot be empty")
        for argument in self.command:
            if not isinstance(argument, str) or not argument.strip():
                raise ValueError("command arguments must be non-empty strings")
            if "\x00" in argument:
                raise ValueError("command arguments must not contain NUL bytes")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must be non-negative")
        if self.max_result_bytes < 1:
            raise ValueError("max_result_bytes must be positive")


def run_json_command(
    spec: CommandSpec,
    *,
    result_type: str,
    result_path: Path,
    idempotency_key: str,
    expected_fields: set[str],
    environment: Mapping[str, str],
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Run one no-shell command and load an exact, idempotent JSON result."""

    _validate_token(result_type, "result_type")
    _validate_token(idempotency_key, "idempotency_key")
    result_path = result_path.resolve()
    result_path.parent.mkdir(parents=True, exist_ok=True)

    base_env = os.environ.copy()
    base_env.update(environment)
    base_env.update(
        {
            "RSI_ADAPTER_SCHEMA_VERSION": ADAPTER_RESULT_SCHEMA_VERSION,
            "RSI_ADAPTER_RESULT_TYPE": result_type,
            "RSI_IDEMPOTENCY_KEY": idempotency_key,
        }
    )

    last_failure: str | None = None
    for attempt in range(1, spec.max_attempts + 1):
        _remove_stale_result(result_path)
        env = dict(base_env)
        env["RSI_ADAPTER_ATTEMPT"] = str(attempt)
        try:
            completed = subprocess.run(
                spec.command,
                env=env,
                check=False,
                timeout=spec.timeout_seconds,
                capture_output=True,
                text=True,
            )
        except subprocess.TimeoutExpired:
            last_failure = (
                f"{result_type} command timed out after "
                f"{spec.timeout_seconds:.3f} seconds"
            )
        except OSError as exc:
            last_failure = (
                f"{result_type} command could not start: {type(exc).__name__}"
            )
        else:
            if completed.returncode == 0:
                return _load_result(
                    result_path,
                    result_type=result_type,
                    idempotency_key=idempotency_key,
                    expected_fields=expected_fields,
                    max_result_bytes=spec.max_result_bytes,
                )
            last_failure = (
                f"{result_type} command exited with status "
                f"{completed.returncode}"
            )

        if attempt < spec.max_attempts:
            sleeper(spec.initial_backoff_seconds * (2 ** (attempt - 1)))

    raise AdapterExecutionError(last_failure or f"{result_type} command failed")


def _remove_stale_result(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise AdapterResultError(
            f"result path must be a regular file location: {path}"
        )
    path.unlink()


def _load_result(
    path: Path,
    *,
    result_type: str,
    idempotency_key: str,
    expected_fields: set[str],
    max_result_bytes: int,
) -> dict[str, Any]:
    if not path.exists():
        raise AdapterResultError(f"{result_type} command did not create {path}")
    if path.is_symlink() or not path.is_file():
        raise AdapterResultError(f"{result_type} result must be a regular file")
    size = path.stat().st_size
    if size > max_result_bytes:
        raise AdapterResultError(
            f"{result_type} result exceeds {max_result_bytes} bytes"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AdapterResultError(
            f"{result_type} result is not valid UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise AdapterResultError(f"{result_type} result must be a JSON object")

    required = {
        "schema_version",
        "result_type",
        "idempotency_key",
    } | expected_fields
    actual = set(value)
    missing = sorted(required - actual)
    unknown = sorted(actual - required)
    if missing or unknown:
        raise AdapterResultError(
            f"{result_type} fields mismatch: "
            f"missing={missing}, unknown={unknown}"
        )
    if value["schema_version"] != ADAPTER_RESULT_SCHEMA_VERSION:
        raise AdapterResultError(
            f"unsupported adapter schema: {value['schema_version']!r}"
        )
    if value["result_type"] != result_type:
        raise AdapterResultError(f"result_type must be {result_type!r}")
    if value["idempotency_key"] != idempotency_key:
        raise AdapterResultError("adapter idempotency_key mismatch")
    return value


def _validate_token(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    if any(ord(character) < 32 for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
