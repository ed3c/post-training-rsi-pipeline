from __future__ import annotations

import hashlib
from dataclasses import replace

from ...control_plane import JSONValue
from ...control_plane.validation import canonical_json
from .contracts import (
    HarnessContractError,
    HarnessMutationProposal,
    HarnessSpec,
    HarnessValidationResult,
)


class HarnessMutationError(HarnessContractError):
    """Raised when a mutation cannot be applied to its declared parent."""


class HarnessMutator:
    """Apply an explicit mutation proposal and derive a content-addressed Harness ID."""

    def apply(
        self,
        parent: HarnessSpec,
        proposal: HarnessMutationProposal,
    ) -> HarnessSpec:
        if proposal.parent_harness_id != parent.harness_id:
            raise HarnessMutationError(
                "mutation parent_harness_id does not match the active Harness"
            )

        tools = [tool for tool in parent.tools if tool not in proposal.remove_tools]
        for tool in proposal.add_tools:
            if tool not in tools:
                tools.append(tool)

        system_prompt = parent.system_prompt
        if proposal.prompt_appendix:
            system_prompt = f"{system_prompt.rstrip()}\n\n{proposal.prompt_appendix.strip()}"

        retry_policy = parent.retry_policy
        if proposal.max_attempts is not None:
            retry_policy = replace(retry_policy, max_attempts=proposal.max_attempts)

        timeout_seconds = (
            proposal.timeout_seconds
            if proposal.timeout_seconds is not None
            else parent.timeout_seconds
        )
        max_steps = proposal.max_steps if proposal.max_steps is not None else parent.max_steps

        metadata: dict[str, JSONValue] = dict(parent.metadata)
        metadata.update(proposal.metadata)
        metadata.update(
            {
                "mutation_id": proposal.mutation_id,
                "mutation_sha256": proposal.content_sha256,
            }
        )

        identity_payload: dict[str, JSONValue] = {
            "parent_harness_id": parent.harness_id,
            "version": parent.version + 1,
            "system_prompt": system_prompt,
            "tools": list(tools),
            "retry_policy": retry_policy.to_dict(),
            "timeout_seconds": timeout_seconds,
            "max_steps": max_steps,
            "metadata": metadata,
        }
        digest = hashlib.sha256(
            canonical_json(identity_payload).encode("utf-8")
        ).hexdigest()
        harness_id = f"harness-{digest[:24]}"

        return HarnessSpec(
            harness_id=harness_id,
            version=parent.version + 1,
            parent_harness_id=parent.harness_id,
            system_prompt=system_prompt,
            tools=tuple(tools),
            retry_policy=retry_policy,
            timeout_seconds=timeout_seconds,
            max_steps=max_steps,
            metadata=metadata,
        )


class HarnessValidator:
    """Static and policy validation before a Harness Candidate may be evaluated."""

    def __init__(
        self,
        *,
        allowed_tools: tuple[str, ...] | None = None,
        forbidden_prompt_fragments: tuple[str, ...] = (
            "ignore all previous instructions",
            "reveal secrets",
            "disable safety checks",
        ),
        max_prompt_chars: int = 32_000,
        max_tools: int = 32,
        max_attempts: int = 8,
        max_timeout_seconds: float = 300.0,
        max_steps: int = 256,
    ) -> None:
        if max_prompt_chars < 1:
            raise HarnessContractError("max_prompt_chars must be positive")
        if max_tools < 1:
            raise HarnessContractError("max_tools must be positive")
        if max_attempts < 1:
            raise HarnessContractError("max_attempts must be positive")
        if max_timeout_seconds <= 0:
            raise HarnessContractError("max_timeout_seconds must be positive")
        if max_steps < 1:
            raise HarnessContractError("max_steps must be positive")
        self.allowed_tools = frozenset(allowed_tools) if allowed_tools is not None else None
        self.forbidden_prompt_fragments = tuple(
            fragment.casefold() for fragment in forbidden_prompt_fragments
        )
        self.max_prompt_chars = max_prompt_chars
        self.max_tools = max_tools
        self.max_attempts = max_attempts
        self.max_timeout_seconds = max_timeout_seconds
        self.max_steps = max_steps

    def validate(
        self,
        candidate: HarnessSpec,
        *,
        evidence_ids: tuple[str, ...],
        validated_at: str,
    ) -> HarnessValidationResult:
        reasons: list[str] = []
        prompt = candidate.system_prompt.casefold()

        if candidate.parent_harness_id is None:
            reasons.append("MISSING_PARENT")
        if len(candidate.system_prompt) > self.max_prompt_chars:
            reasons.append("PROMPT_TOO_LONG")
        for fragment in self.forbidden_prompt_fragments:
            if fragment and fragment in prompt:
                reasons.append("FORBIDDEN_PROMPT_DIRECTIVE")
                break
        if len(candidate.tools) > self.max_tools:
            reasons.append("TOO_MANY_TOOLS")
        if self.allowed_tools is not None:
            unknown_tools = sorted(set(candidate.tools) - self.allowed_tools)
            if unknown_tools:
                reasons.append("TOOL_NOT_ALLOWED")
        if candidate.retry_policy.max_attempts > self.max_attempts:
            reasons.append("RETRY_LIMIT_EXCEEDED")
        if candidate.timeout_seconds > self.max_timeout_seconds:
            reasons.append("TIMEOUT_LIMIT_EXCEEDED")
        if candidate.max_steps > self.max_steps:
            reasons.append("STEP_LIMIT_EXCEEDED")

        unique_reasons = tuple(sorted(set(reasons)))
        metrics: dict[str, JSONValue] = {
            "prompt_chars": len(candidate.system_prompt),
            "tool_count": len(candidate.tools),
            "retry_max_attempts": candidate.retry_policy.max_attempts,
            "timeout_seconds": candidate.timeout_seconds,
            "max_steps": candidate.max_steps,
        }
        return HarnessValidationResult(
            candidate_harness_id=candidate.harness_id,
            valid=not unique_reasons,
            reasons=unique_reasons,
            evidence_ids=evidence_ids,
            validated_at=validated_at,
            metrics=metrics,
        )
