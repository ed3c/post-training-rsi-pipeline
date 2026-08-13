from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from ..models import SyntheticExample


@dataclass(slots=True)
class SynthesisBatch:
    examples: list[SyntheticExample]
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    request_id: str
    teacher_model: str
    api_version: str
    teacher_prompt: str
    teacher_prompt_hash: str

    def manifest(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
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
    def synthesize(self, *, hypothesis: str, count: int, iteration: int) -> SynthesisBatch: ...
