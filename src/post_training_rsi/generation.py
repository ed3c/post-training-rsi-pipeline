from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from .models import SyntheticExample


@dataclass(slots=True)
class GenerationBatch:
    examples: list[SyntheticExample]
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    request_id: str
    source_model: str
    api_version: str
    instruction: str
    instruction_hash: str

    def manifest(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "source_model": self.source_model,
            "source_api_version": self.api_version,
            "instruction_hash": self.instruction_hash,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "example_count": len(self.examples),
        }


class DeterministicGenerator:
    def __init__(self, model: str = "mock-source-70b", api_version: str = "mock-v1") -> None:
        self.model = model
        self.api_version = api_version

    def generate(self, *, hypothesis: str, count: int, iteration: int) -> GenerationBatch:
        instruction = f"iteration={iteration}; count={count}; capability={hypothesis.strip()}"
        instruction_hash = hashlib.sha256(instruction.encode("utf-8")).hexdigest()
        examples = [
            SyntheticExample(
                example_id=f"iter-{iteration:03d}-sample-{index + 1:04d}",
                prompt=f"Revision {iteration} capability exercise {index + 1}: {hypothesis[:72]}",
                response=f"Record one invariant and one boundary result for case {iteration}-{index + 1}.",
                metadata={"instruction_hash": instruction_hash, "iteration": iteration},
            )
            for index in range(count)
        ]
        input_tokens = max(1, len(instruction) // 4) + count * 12
        output_tokens = sum(max(1, len(example.text) // 4) for example in examples)
        return GenerationBatch(
            examples=examples,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost_usd=round((input_tokens * 0.80 + output_tokens * 0.88) / 1_000_000, 8),
            request_id=f"mock-{iteration:03d}-{instruction_hash[:8]}",
            source_model=self.model,
            api_version=self.api_version,
            instruction=instruction,
            instruction_hash=instruction_hash,
        )
