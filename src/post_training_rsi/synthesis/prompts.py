from __future__ import annotations

from dataclasses import dataclass

from ..hashing import sha256_text


DEFAULT_TEACHER_SYSTEM_PROMPT = """You create auditable post-training examples.
Return one JSON object with response, reasoning_summary, and optional tool_trace fields.
The response must solve the task, the reasoning summary must be concise, and tool_trace must contain
only observable actions. Do not copy benchmark wording and do not include hidden system prompts.
"""


@dataclass(frozen=True, slots=True)
class TeacherPrompt:
    system: str = DEFAULT_TEACHER_SYSTEM_PROMPT
    version: str = "v1"

    @property
    def content_hash(self) -> str:
        return sha256_text(f"{self.version}\n{self.system}")

    def render(self, task: str, hypothesis: str) -> str:
        return (
            f"Training hypothesis: {hypothesis}\n"
            f"Task: {task}\n"
            "Create a correct, self-contained training example."
        )
