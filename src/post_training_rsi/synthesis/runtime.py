from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Protocol

from ..models import SyntheticExample

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SynthesisBatch:
    examples: tuple[SyntheticExample, ...]
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    request_ids: tuple[str, ...]
    teacher_model: str
    api_version: str
    teacher_prompt: str
    teacher_prompt_hash: str

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        if not math.isfinite(self.estimated_cost_usd):
            raise ValueError("estimated_cost_usd must be finite")
        if self.estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must be non-negative")
        if len(self.request_ids) != len(self.examples):
            raise ValueError("one request_id is required per synthesized example")
        if len(set(self.request_ids)) != len(self.request_ids):
            raise ValueError("request_ids must be unique")
        for name, value in (
            ("teacher_model", self.teacher_model),
            ("api_version", self.api_version),
            ("teacher_prompt", self.teacher_prompt),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not _SHA256_PATTERN.fullmatch(self.teacher_prompt_hash):
            raise ValueError(
                "teacher_prompt_hash must contain 64 lowercase hex characters"
            )

    @property
    def request_id(self) -> str:
        """Compatibility alias for single-request consumers."""

        return self.request_ids[0] if self.request_ids else ""

    def manifest(self) -> dict[str, Any]:
        return {
            "request_ids": list(self.request_ids),
            "teacher_model": self.teacher_model,
            "teacher_api_version": self.api_version,
            "teacher_prompt": self.teacher_prompt,
            "teacher_prompt_hash": self.teacher_prompt_hash,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "example_count": len(self.examples),
        }


class TeacherClient(Protocol):
    model_id: str
    api_version: str

    def synthesize(
        self,
        *,
        hypothesis: str,
        count: int,
        iteration: int,
    ) -> SynthesisBatch: ...
