from __future__ import annotations

from post_training_rsi.config import VerificationConfig
from post_training_rsi.models import SyntheticExample
from post_training_rsi.verification.pipeline import VerificationPipeline


def test_verification_accepts_and_then_detects_exact_duplicate() -> None:
    verifier = VerificationPipeline(
        VerificationConfig(min_entropy=1.0, min_distinct_2=0.1, min_type_token_ratio=0.1)
    )
    example = SyntheticExample(
        example_id="sample-1",
        prompt="Explain an idempotent event handler with one database invariant.",
        response="Persist a unique event identifier before the side effect and commit atomically.",
    )
    first = verifier.verify([example])
    second = verifier.verify([example])
    assert len(first.accepted) == 1
    assert second.records[0].reasons == ["EXACT_DUPLICATE"]


def test_verification_detects_benchmark_overlap_and_static_code_violation() -> None:
    verifier = VerificationPipeline(
        VerificationConfig(
            min_entropy=0.0,
            min_distinct_2=0.0,
            min_type_token_ratio=0.0,
            benchmark_ngram_size=2,
            max_benchmark_overlap=0.5,
        ),
        benchmark_texts=("alpha beta gamma delta epsilon",),
    )
    example = SyntheticExample(
        example_id="sample-2",
        prompt="alpha beta gamma delta epsilon",
        response="A copied benchmark fixture.",
        code="import os\nvalue = 1\n",
    )
    result = verifier.verify([example])
    assert result.quarantined
    assert "BENCHMARK_CONTAMINATION_NGRAM" in result.records[0].reasons
    assert "DISALLOWED_IMPORT:os" in result.records[0].reasons
