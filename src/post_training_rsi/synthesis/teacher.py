from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from ..domain import SynthesisBatch, SynthesisUsage, TrainingExample
from ..hashing import canonical_sha256
from .prompts import TeacherPrompt


class TeacherClient(Protocol):
    model_id: str
    api_version: str

    def synthesize(
        self,
        tasks: Sequence[str],
        *,
        hypothesis: str,
        iteration: int,
    ) -> SynthesisBatch: ...


@dataclass(slots=True)
class MockTeacherClient:
    model_id: str = "mock-teacher-70b"
    api_version: str = "mock-v1"
    cost_per_example_usd: float = 0.25
    include_adversarial_samples: bool = True
    prompt: TeacherPrompt = TeacherPrompt()

    def synthesize(
        self,
        tasks: Sequence[str],
        *,
        hypothesis: str,
        iteration: int,
    ) -> SynthesisBatch:
        examples: list[TrainingExample] = []
        for index, task in enumerate(tasks):
            examples.append(
                TrainingExample(
                    task_id=f"task-{iteration:03d}-{index:03d}",
                    prompt=task,
                    response=self._solve(task, hypothesis, iteration),
                    reasoning_summary="Apply the stated hypothesis and return an auditable answer.",
                    metadata={
                        "teacher_model": self.model_id,
                        "teacher_api_version": self.api_version,
                        "teacher_prompt_hash": self.prompt.content_hash,
                        "iteration": iteration,
                    },
                )
            )
        if self.include_adversarial_samples and tasks:
            examples.extend(
                [
                    TrainingExample(
                        task_id=f"task-{iteration:03d}-duplicate",
                        prompt=tasks[0],
                        response=self._solve(tasks[0], hypothesis, iteration),
                        reasoning_summary="Near duplicate fixture for the diversity gate.",
                        metadata={"fixture": "near_duplicate", "iteration": iteration},
                    ),
                    TrainingExample(
                        task_id=f"task-{iteration:03d}-loop",
                        prompt="Explain the next action.",
                        response="repeat repeat repeat repeat repeat repeat repeat repeat repeat",
                        reasoning_summary="Low-entropy fixture.",
                        metadata={"fixture": "low_entropy", "iteration": iteration},
                    ),
                ]
            )
        input_tokens = sum(max(1, len(task) // 4) for task in tasks)
        output_tokens = sum(max(1, len(item.response) // 4) for item in examples)
        usage = SynthesisUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(len(examples) * self.cost_per_example_usd, 8),
            provider_request_ids=tuple(f"mock-{iteration}-{i}" for i in range(len(examples))),
        )
        return SynthesisBatch(
            examples=tuple(examples),
            usage=usage,
            teacher_model=self.model_id,
            teacher_api_version=self.api_version,
            teacher_prompt_hash=self.prompt.content_hash,
        )

    @staticmethod
    def _solve(task: str, hypothesis: str, iteration: int) -> str:
        lowered = task.lower()
        if "4x + 10 = 26" in lowered:
            return "Subtract 10 from both sides to get 4x = 16, then divide by 4: x = 4."
        if "json" in lowered:
            return json.dumps(
                {"status": "ok", "iteration": iteration, "hypothesis": hypothesis},
                ensure_ascii=False,
                sort_keys=True,
            )
        if "retry" in lowered or "timeout" in lowered:
            return (
                "Use bounded retry with exponential backoff, jitter, an attempt cap, and a "
                "terminal error that preserves the last failure."
            )
        return f"Verified answer for: {task}. Applied hypothesis: {hypothesis}."


@dataclass(slots=True)
class OpenAICompatibleTeacherClient:
    model_id: str
    api_version: str
    base_url: str
    api_key: str | None = None
    input_cost_per_million: float = 0.0
    output_cost_per_million: float = 0.0
    timeout_seconds: float = 60.0
    max_workers: int = 4
    prompt: TeacherPrompt = TeacherPrompt()
    max_retries: int = 3

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.api_key = self.api_key or os.getenv("TEACHER_API_KEY")
        if not self.api_key:
            raise ValueError("api_key is required")

    def synthesize(
        self,
        tasks: Sequence[str],
        *,
        hypothesis: str,
        iteration: int,
    ) -> SynthesisBatch:
        examples: list[TrainingExample] = []
        request_ids: list[str] = []
        input_tokens = 0
        output_tokens = 0
        for index, task in enumerate(tasks):
            body, request_id = self._request(task, hypothesis)
            content = body["choices"][0]["message"]["content"]
            generated = json.loads(content)
            usage = body.get("usage", {})
            input_tokens += int(usage.get("prompt_tokens", max(1, len(task) // 4)))
            output_tokens += int(usage.get("completion_tokens", max(1, len(content) // 4)))
            request_ids.append(request_id)
            examples.append(
                TrainingExample(
                    task_id=f"task-{iteration:03d}-{index:03d}",
                    prompt=task,
                    response=str(generated["response"]),
                    reasoning_summary=generated.get("reasoning_summary"),
                    tool_trace=tuple(generated.get("tool_trace", [])),
                    metadata={
                        "teacher_model": self.model_id,
                        "teacher_api_version": self.api_version,
                        "teacher_prompt_hash": self.prompt.content_hash,
                        "provider_request_id": request_id,
                    },
                )
            )
        cost = (
            input_tokens * self.input_cost_per_million / 1_000_000
            + output_tokens * self.output_cost_per_million / 1_000_000
        )
        return SynthesisBatch(
            examples=tuple(examples),
            usage=SynthesisUsage(input_tokens, output_tokens, round(cost, 8), tuple(request_ids)),
            teacher_model=self.model_id,
            teacher_api_version=self.api_version,
            teacher_prompt_hash=self.prompt.content_hash,
        )

    def _request(self, task: str, hypothesis: str) -> tuple[dict[str, Any], str]:
        payload = {
            "model": self.model_id,
            "messages": [
                {"role": "system", "content": self.prompt.system},
                {"role": "user", "content": self.prompt.render(task, hypothesis)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
            request_id = response.headers.get("x-request-id") or canonical_sha256(body)[:20]
        return body, request_id
