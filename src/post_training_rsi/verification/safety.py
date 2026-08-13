from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class SafetyResult:
    safe: bool
    category: str
    matched_pattern: str | None = None


class SafetyClassifier(Protocol):
    def classify(self, prompt: str, response: str) -> SafetyResult: ...


@dataclass(slots=True)
class RuleSafetyClassifier:
    prompt_injection_patterns: tuple[str, ...] = (
        "ignore previous instructions",
        "reveal the system prompt",
        "print your hidden prompt",
        "developer message",
    )
    unsafe_output_patterns: tuple[str, ...] = (
        "<unsafe-output>",
        "credential_dump=",
    )

    def classify(self, prompt: str, response: str) -> SafetyResult:
        combined = f"{prompt}\n{response}".lower()
        for pattern in self.prompt_injection_patterns:
            if pattern.lower() in combined:
                return SafetyResult(False, "PROMPT_INJECTION", pattern)
        for pattern in self.unsafe_output_patterns:
            if pattern.lower() in combined:
                return SafetyResult(False, "UNSAFE_OUTPUT", pattern)
        role_match = re.search(r"(?:^|\n)\s*(system|developer)\s*:\s*", combined)
        if role_match:
            return SafetyResult(False, "ROLE_INJECTION", role_match.group(0).strip())
        return SafetyResult(True, "SAFE", None)
