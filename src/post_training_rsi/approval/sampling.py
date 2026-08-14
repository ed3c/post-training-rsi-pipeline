from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

from ..control_plane import DecisionSubject, JSONValue
from ..control_plane.validation import (
    normalize_json_object,
    validate_id,
    validate_sha256,
)
from .contracts import ApprovalSampleItem, ApprovalSampleManifest
from .errors import ApprovalContractError
from .policy import ApprovalPolicy


@dataclass(frozen=True, slots=True)
class ApprovalCandidate:
    item_id: str
    content_sha256: str
    metadata: dict[str, JSONValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "item_id", validate_id(self.item_id, "item_id"))
        validate_sha256(self.content_sha256)
        object.__setattr__(
            self,
            "metadata",
            normalize_json_object(self.metadata, "metadata"),
        )


def build_sample_manifest(
    *,
    request_id: str,
    run_id: str,
    iteration: int,
    subject_type: DecisionSubject,
    subject_id: str,
    candidates: tuple[ApprovalCandidate, ...],
    policy: ApprovalPolicy,
    selection_seed: str,
    created_at: str,
) -> ApprovalSampleManifest:
    """Select a deterministic hash-ranked sample without embedding raw content."""

    if not candidates:
        raise ApprovalContractError(
            "approval sampling requires at least one candidate"
        )
    candidate_ids = tuple(candidate.item_id for candidate in candidates)
    if len(candidate_ids) != len(set(candidate_ids)):
        raise ApprovalContractError("approval candidate IDs must be unique")
    selection_seed = validate_id(selection_seed, "selection_seed")
    target_count = min(
        len(candidates),
        policy.max_sample_items,
        max(
            policy.min_sample_items,
            math.ceil(len(candidates) * policy.sample_rate),
        ),
    )
    ranked = sorted(
        candidates,
        key=lambda candidate: _rank_key(
            selection_seed=selection_seed,
            subject_id=subject_id,
            candidate=candidate,
        ),
    )
    selected = tuple(
        ApprovalSampleItem(
            item_id=candidate.item_id,
            content_sha256=candidate.content_sha256,
            metadata=candidate.metadata,
        )
        for candidate in ranked[:target_count]
    )
    return ApprovalSampleManifest(
        request_id=request_id,
        run_id=run_id,
        iteration=iteration,
        subject_type=subject_type,
        subject_id=subject_id,
        selection_algorithm="sha256-rank-v1",
        selection_seed=selection_seed,
        sample_rate=policy.sample_rate,
        population_count=len(candidates),
        selected_count=len(selected),
        items=selected,
        created_at=created_at,
        metadata={
            "min_sample_items": policy.min_sample_items,
            "max_sample_items": policy.max_sample_items,
            "raw_content_embedded": False,
        },
    )


def _rank_key(
    *,
    selection_seed: str,
    subject_id: str,
    candidate: ApprovalCandidate,
) -> str:
    payload = "\x00".join(
        (
            selection_seed,
            subject_id,
            candidate.item_id,
            candidate.content_sha256,
        )
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
