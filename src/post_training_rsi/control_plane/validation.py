from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import StrEnum
from typing import TypeAlias, TypeVar

JSONScalar: TypeAlias = str | int | float | bool | None
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_EnumT = TypeVar("_EnumT", bound=StrEnum)


class ControlContractError(ValueError):
    """Raised when a record violates the versioned control-plane contract."""


def canonical_json(value: Mapping[str, JSONValue]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_id(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
        raise ControlContractError(
            f"{field_name} must match {_ID_PATTERN.pattern} and contain no path separators"
        )
    return value


def validate_text(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ControlContractError(f"{field_name} must be a non-empty string")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise ControlContractError(f"{field_name} must not contain control characters")
    return value


def validate_nonnegative_int(value: int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ControlContractError(f"{field_name} must be a non-negative integer")


def validate_finite_number(value: float | int, field_name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ControlContractError(f"{field_name} must be a finite number")
    if not math.isfinite(float(value)):
        raise ControlContractError(f"{field_name} must be a finite number")


def validate_nonnegative_number(value: float | int, field_name: str) -> None:
    validate_finite_number(value, field_name)
    if float(value) < 0:
        raise ControlContractError(f"{field_name} must be non-negative")


def normalize_timestamp(value: str) -> str:
    validate_text(value, "timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ControlContractError("timestamp must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ControlContractError("timestamp must include a UTC offset")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def validate_sha256(value: str | None) -> None:
    if value is not None and not _SHA256_PATTERN.fullmatch(value):
        raise ControlContractError("sha256 must contain exactly 64 lowercase hex characters")


def validate_id_tuple(values: Sequence[str], field_name: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ControlContractError(f"{field_name} must be a sequence of IDs")
    normalized = tuple(validate_id(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ControlContractError(f"{field_name} must not contain duplicate IDs")
    return normalized


def normalize_json_object(value: Mapping[str, object], field_name: str) -> dict[str, JSONValue]:
    if not isinstance(value, Mapping):
        raise ControlContractError(f"{field_name} must be a JSON object")
    keys = list(value)
    if any(not isinstance(key, str) for key in keys):
        raise ControlContractError(f"{field_name} keys must be strings")
    normalized: dict[str, JSONValue] = {}
    for key in sorted(keys):
        normalized[key] = normalize_json_value(value[key], f"{field_name}.{key}")
    return normalized


def normalize_json_value(value: object, field_name: str) -> JSONValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ControlContractError(f"{field_name} must not contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        return normalize_json_object(value, field_name)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [
            normalize_json_value(item, f"{field_name}[{index}]")
            for index, item in enumerate(value)
        ]
    raise ControlContractError(
        f"{field_name} contains a non-JSON value: {type(value).__name__}"
    )


def copy_json_object(value: Mapping[str, JSONValue]) -> dict[str, JSONValue]:
    return normalize_json_object(value, "metadata")


def validated_record_mapping(
    value: Mapping[str, object],
    record_type: str,
    expected_fields: set[str],
    schema_version: str,
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ControlContractError("record must be a mapping")
    keys = list(value)
    if any(not isinstance(key, str) for key in keys):
        raise ControlContractError("record keys must be strings")
    data = dict(value)
    actual_fields = set(keys)
    missing = sorted(expected_fields - actual_fields)
    unknown = sorted(actual_fields - expected_fields)
    if missing or unknown:
        raise ControlContractError(f"record fields mismatch: missing={missing}, unknown={unknown}")
    if data["schema_version"] != schema_version:
        raise ControlContractError(f"unsupported schema_version: {data['schema_version']!r}")
    if data["record_type"] != record_type:
        raise ControlContractError(
            f"record_type must be {record_type!r}, got {data['record_type']!r}"
        )
    return data


def required_str(data: Mapping[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str):
        raise ControlContractError(f"{key} must be a string")
    return value


def optional_str(data: Mapping[str, object], key: str) -> str | None:
    value = data[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ControlContractError(f"{key} must be a string or null")
    return value


def required_int(data: Mapping[str, object], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ControlContractError(f"{key} must be an integer")
    return value


def required_float(data: Mapping[str, object], key: str) -> float:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ControlContractError(f"{key} must be a number")
    return float(value)


def optional_float(data: Mapping[str, object], key: str) -> float | None:
    value = data[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ControlContractError(f"{key} must be a number or null")
    return float(value)


def required_enum(
    data: Mapping[str, object], key: str, enum_type: type[_EnumT]
) -> _EnumT:
    value = required_str(data, key)
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ControlContractError(f"{key} has unsupported value {value!r}") from exc


def optional_enum(
    data: Mapping[str, object], key: str, enum_type: type[_EnumT]
) -> _EnumT | None:
    value = data[key]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ControlContractError(f"{key} must be a string or null")
    try:
        return enum_type(value)
    except ValueError as exc:
        raise ControlContractError(f"{key} has unsupported value {value!r}") from exc


def required_id_tuple(data: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = data[key]
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ControlContractError(f"{key} must be an array of strings")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ControlContractError(f"{key} must contain only strings")
        values.append(item)
    return tuple(values)


def required_json_object(data: Mapping[str, object], key: str) -> dict[str, JSONValue]:
    value = data[key]
    if not isinstance(value, Mapping):
        raise ControlContractError(f"{key} must be a JSON object")
    return normalize_json_object(value, key)
