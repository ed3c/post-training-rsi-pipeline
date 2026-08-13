from __future__ import annotations

from post_training_rsi.config import PipelineConfig
from post_training_rsi.verification import (
    BenchmarkIndex,
    PythonAstSafetyChecker,
    RuleSafetyClassifier,
    TokenJaccardIndex,
    VerificationPipeline,
)


def build_verifier(config: PipelineConfig | None = None) -> VerificationPipeline:
    config = config or PipelineConfig()
    benchmarks = BenchmarkIndex(
        ngram_size=config.decontamination.ngram_size,
        overlap_threshold=config.decontamination.overlap_threshold,
        lcs_threshold=config.decontamination.lcs_threshold,
    )
    benchmarks.extend(config.benchmark_texts)
    return VerificationPipeline(
        diversity=config.diversity,
        benchmark_index=benchmarks,
        safety_classifier=RuleSafetyClassifier(
            prompt_injection_patterns=config.safety_patterns,
        ),
        semantic_index=TokenJaccardIndex(),
        code_checker=PythonAstSafetyChecker(),
    )
