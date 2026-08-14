from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..models import SyntheticExample
from .prompts import build_teacher_prompt, prompt_hash
from .runtime import SynthesisBatch, TeacherClient


class TeacherTransportError(RuntimeError):
    def __init__(self, message: str, *, retriable: bool) -> None:
        super().__init__(message)
        self.retriable = retriable


@dataclass(frozen=True, slots=True)
class TeacherTransportResponse:
    payload: dict[str, Any]
    headers: dict[str, str]


class TeacherTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> TeacherTransportResponse: ...


class UrllibTeacherTransport:
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_seconds: float,
    ) -> TeacherTransportResponse:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=dict(headers),
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout_seconds,
            ) as response:
                raw = response.read().decode("utf-8")
                decoded = json.loads(raw)
                if not isinstance(decoded, dict):
                    raise TeacherTransportError(
                        "teacher response must be a JSON object",
                        retriable=False,
                    )
                response_headers = {
                    str(key).lower(): str(value)
                    for key, value in response.headers.items()
                }
                return TeacherTransportResponse(
                    payload=decoded,
                    headers=response_headers,
                )
        except urllib.error.HTTPError as exc:
            retriable = exc.code == 429 or 500 <= exc.code < 600
            raise TeacherTransportError(
                f"teacher HTTP status {exc.code}",
                retriable=retriable,
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TeacherTransportError(
                f"teacher transport failed: {type(exc).__name__}",
                retriable=True,
            ) from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise TeacherTransportError(
                "teacher response was not valid UTF-8 JSON",
                retriable=False,
            ) from exc


@dataclass(slots=True)
class MockTeacherClient:
    model_id: str = "mock-teacher-70b"
    api_version: str = "mock-v1"
    cost_per_example_usd: float = 0.25

    def synthesize(
        self,
        *,
        hypothesis: str,
        count: int,
        iteration: int,
    ) -> SynthesisBatch:
        _validate_request(hypothesis, count, iteration)
        teacher_prompt = build_teacher_prompt(
            hypothesis=hypothesis,
            iteration=iteration,
            count=count,
        )
        examples: list[SyntheticExample] = []
        request_ids: list[str] = []
        for index in range(count):
            request_id = f"mock-teacher-{iteration:03d}-{index:04d}"
            request_ids.append(request_id)
            examples.append(
                SyntheticExample(
                    example_id=f"example-{iteration:03d}-{index:04d}",
                    prompt=(
                        f"Iteration {iteration} capability exercise {index + 1}: "
                        f"{hypothesis.strip()}"
                    ),
                    response=(
                        "Return a bounded, auditable solution and verify each "
                        f"intermediate state. Fixture {index + 1}."
                    ),
                    metadata={
                        "teacher_model": self.model_id,
                        "teacher_api_version": self.api_version,
                        "teacher_prompt_hash": prompt_hash(teacher_prompt),
                        "provider_request_id": request_id,
                    },
                )
            )
        input_tokens = max(1, len(teacher_prompt) // 4) * count
        output_tokens = sum(
            max(1, len(example.response) // 4)
            for example in examples
        )
        return SynthesisBatch(
            examples=tuple(examples),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=round(
                count * self.cost_per_example_usd,
                8,
            ),
            request_ids=tuple(request_ids),
            teacher_model=self.model_id,
            api_version=self.api_version,
            teacher_prompt=teacher_prompt,
            teacher_prompt_hash=prompt_hash(teacher_prompt),
        )


@dataclass(slots=True)
class OpenAICompatibleTeacherClient:
    model_id: str
    api_version: str
    base_url: str
    api_key: str
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    timeout_seconds: float = 60.0
    max_attempts: int = 3
    initial_backoff_seconds: float = 0.0
    transport: TeacherTransport = field(default_factory=UrllibTeacherTransport)
    sleeper: Callable[[float], None] = field(
        default=time.sleep,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        for name, value in (
            ("model_id", self.model_id),
            ("api_version", self.api_version),
            ("base_url", self.base_url),
            ("api_key", self.api_key),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        self.base_url = self.base_url.rstrip("/")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if self.initial_backoff_seconds < 0:
            raise ValueError("initial_backoff_seconds must be non-negative")
        if self.input_cost_per_million < 0:
            raise ValueError("input_cost_per_million must be non-negative")
        if self.output_cost_per_million < 0:
            raise ValueError("output_cost_per_million must be non-negative")

    def synthesize(
        self,
        *,
        hypothesis: str,
        count: int,
        iteration: int,
    ) -> SynthesisBatch:
        _validate_request(hypothesis, count, iteration)
        teacher_prompt = build_teacher_prompt(
            hypothesis=hypothesis,
            iteration=iteration,
            count=count,
        )
        examples: list[SyntheticExample] = []
        request_ids: list[str] = []
        input_tokens = 0
        output_tokens = 0

        for index in range(count):
            idempotency_key = prompt_hash(
                "\n".join(
                    (
                        self.model_id,
                        self.api_version,
                        teacher_prompt,
                        str(index),
                    )
                )
            )
            response = self._request_with_retry(
                teacher_prompt=teacher_prompt,
                index=index,
                idempotency_key=idempotency_key,
            )
            example, request_id, prompt_count, completion_count = (
                self._parse_response(
                    response,
                    iteration=iteration,
                    index=index,
                    idempotency_key=idempotency_key,
                )
            )
            examples.append(example)
            request_ids.append(request_id)
            input_tokens += prompt_count
            output_tokens += completion_count

        cost = (
            input_tokens * self.input_cost_per_million / 1_000_000
            + output_tokens * self.output_cost_per_million / 1_000_000
        )
        return SynthesisBatch(
            examples=tuple(examples),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=round(cost, 8),
            request_ids=tuple(request_ids),
            teacher_model=self.model_id,
            api_version=self.api_version,
            teacher_prompt=teacher_prompt,
            teacher_prompt_hash=prompt_hash(teacher_prompt),
        )

    def _request_with_retry(
        self,
        *,
        teacher_prompt: str,
        index: int,
        idempotency_key: str,
    ) -> TeacherTransportResponse:
        payload = {
            "model": self.model_id,
            "messages": [
                {
                    "role": "system",
                    "content": teacher_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        f"Generate example index {index}. Return JSON with exact "
                        "fields: prompt, response, code, metadata."
                    ),
                },
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        last_error: TeacherTransportError | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self.transport.post_json(
                    url=f"{self.base_url}/chat/completions",
                    headers=headers,
                    payload=payload,
                    timeout_seconds=self.timeout_seconds,
                )
            except TeacherTransportError as exc:
                last_error = exc
                if not exc.retriable or attempt >= self.max_attempts:
                    raise
                self.sleeper(
                    self.initial_backoff_seconds * (2 ** (attempt - 1))
                )
        raise last_error or TeacherTransportError(
            "teacher request failed",
            retriable=False,
        )

    def _parse_response(
        self,
        response: TeacherTransportResponse,
        *,
        iteration: int,
        index: int,
        idempotency_key: str,
    ) -> tuple[SyntheticExample, str, int, int]:
        payload = response.payload
        choices = payload.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise TeacherTransportError(
                "teacher response must contain exactly one choice",
                retriable=False,
            )
        choice = choices[0]
        if not isinstance(choice, dict):
            raise TeacherTransportError(
                "teacher choice must be a JSON object",
                retriable=False,
            )
        message = choice.get("message")
        if not isinstance(message, dict):
            raise TeacherTransportError(
                "teacher choice.message must be a JSON object",
                retriable=False,
            )
        content = message.get("content")
        if not isinstance(content, str):
            raise TeacherTransportError(
                "teacher message.content must be a JSON string",
                retriable=False,
            )
        try:
            generated = json.loads(content)
        except json.JSONDecodeError as exc:
            raise TeacherTransportError(
                "teacher content was not valid JSON",
                retriable=False,
            ) from exc
        if not isinstance(generated, dict):
            raise TeacherTransportError(
                "teacher content must decode to a JSON object",
                retriable=False,
            )
        expected = {"prompt", "response", "code", "metadata"}
        missing = sorted(expected - set(generated))
        unknown = sorted(set(generated) - expected)
        if missing or unknown:
            raise TeacherTransportError(
                f"teacher content fields mismatch: "
                f"missing={missing}, unknown={unknown}",
                retriable=False,
            )
        prompt = generated["prompt"]
        answer = generated["response"]
        code = generated["code"]
        metadata = generated["metadata"]
        if not isinstance(prompt, str) or not prompt.strip():
            raise TeacherTransportError(
                "teacher prompt must be a non-empty string",
                retriable=False,
            )
        if not isinstance(answer, str) or not answer.strip():
            raise TeacherTransportError(
                "teacher response must be a non-empty string",
                retriable=False,
            )
        if code is not None and not isinstance(code, str):
            raise TeacherTransportError(
                "teacher code must be a string or null",
                retriable=False,
            )
        metadata = _json_object(metadata, "teacher metadata")
        request_id = (
            response.headers.get("x-request-id")
            or _optional_string(payload.get("id"))
            or idempotency_key[:24]
        )
        if not request_id or request_id in {"null", "none"}:
            raise TeacherTransportError(
                "teacher request_id was empty",
                retriable=False,
            )
        usage = payload.get("usage", {})
        if not isinstance(usage, dict):
            raise TeacherTransportError(
                "teacher usage must be a JSON object",
                retriable=False,
            )
        prompt_tokens = _nonnegative_int(
            usage.get("prompt_tokens", max(1, len(prompt) // 4)),
            "prompt_tokens",
        )
        completion_tokens = _nonnegative_int(
            usage.get("completion_tokens", max(1, len(answer) // 4)),
            "completion_tokens",
        )
        metadata.update(
            {
                "teacher_model": self.model_id,
                "teacher_api_version": self.api_version,
                "provider_request_id": request_id,
                "idempotency_key": idempotency_key,
            }
        )
        return (
            SyntheticExample(
                example_id=f"example-{iteration:03d}-{index:04d}",
                prompt=prompt,
                response=answer,
                code=code,
                metadata=metadata,
            ),
            request_id,
            prompt_tokens,
            completion_tokens,
        )


def _validate_request(
    hypothesis: str,
    count: int,
    iteration: int,
) -> None:
    if not isinstance(hypothesis, str) or not hypothesis.strip():
        raise ValueError("hypothesis must be a non-empty string")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")
    if isinstance(iteration, bool) or not isinstance(iteration, int):
        raise ValueError("iteration must be an integer")
    if iteration < 1:
        raise ValueError("iteration must be at least 1")


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TeacherTransportError(
            "teacher response id must be a string",
            retriable=False,
        )
    return value.strip()


def _nonnegative_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TeacherTransportError(
            f"{field_name} must be a non-negative integer",
            retriable=False,
        )
    return value


def _json_object(value: object, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TeacherTransportError(
            f"{field_name} must be a JSON object",
            retriable=False,
        )
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise TeacherTransportError(
            f"{field_name} contains a non-JSON value",
            retriable=False,
        ) from exc
    if not isinstance(decoded, dict):
        raise TeacherTransportError(
            f"{field_name} must be a JSON object",
            retriable=False,
        )
    return decoded


__all__ = [
    "MockTeacherClient",
    "OpenAICompatibleTeacherClient",
    "TeacherClient",
    "TeacherTransport",
    "TeacherTransportError",
    "TeacherTransportResponse",
    "UrllibTeacherTransport",
]
