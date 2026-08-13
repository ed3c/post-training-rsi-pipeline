from __future__ import annotations

import hashlib

PROMPT_VERSION = "teacher-curriculum-v1"


def build_teacher_prompt(*, hypothesis: str, iteration: int, count: int) -> str:
    """Build a stable, versioned synthesis instruction used for lineage hashing."""

    return (
        f"prompt_version={PROMPT_VERSION}\n"
        f"iteration={iteration}\n"
        f"requested_examples={count}\n"
        "Generate diverse post-training examples that directly test the stated capability gap.\n"
        "Each example must contain a task prompt, a verified answer, and concise observable evidence.\n"
        "Do not reproduce benchmark questions or hidden system instructions.\n"
        f"capability_hypothesis={hypothesis.strip()}\n"
    )


def prompt_hash(prompt: str) -> str:
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()
