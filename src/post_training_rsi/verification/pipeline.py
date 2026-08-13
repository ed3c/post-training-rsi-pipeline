from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Iterable

from ..config import VerificationConfig
from ..models import SyntheticExample, VerificationBatch, VerificationRecord
from .code import PythonStaticVerifier
from .decontamination import BenchmarkIndex
from .lexical import lexical_metrics
from .safety import RuleSafetyClassifier
from .semantic import NoveltyIndex, TokenJaccardNoveltyIndex


class VerificationPipeline:
    """Evidence-producing data gate used by synthesis and trajectory harvesting."""

    def __init__(
        self,
        config: VerificationConfig,
        *,
        benchmark_texts: Iterable[str] = (),
        novelty_index: NoveltyIndex | None = None,
    ) -> None:
        self.config = config
        self.benchmark_index = BenchmarkIndex(
            tuple(benchmark_texts), ngram_size=config.benchmark_ngram_size
        )
        self.novelty_index = novelty_index or TokenJaccardNoveltyIndex()
        self.safety_classifier = RuleSafetyClassifier()
        self.code_verifier = PythonStaticVerifier(config.allowed_python_imports)
        self._accepted_hashes: set[str] = set()

    @property
    def config_hash(self) -> str:
        payload = json.dumps(asdict(self.config), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def verify(self, examples: Iterable[SyntheticExample]) -> VerificationBatch:
        accepted: list[SyntheticExample] = []
        quarantined: list[SyntheticExample] = []
        records: list[VerificationRecord] = []
        for example in examples:
            reasons: list[str] = []
            metrics: dict[str, float | str | bool] = {}
            normalized_hash = hashlib.sha256(example.text.strip().encode("utf-8")).hexdigest()
            if normalized_hash in self._accepted_hashes:
                reasons.append("EXACT_DUPLICATE")

            lexical = lexical_metrics(example.text)
            metrics.update(lexical)
            if lexical["entropy"] < self.config.min_entropy:
                reasons.append("LOW_ENTROPY")
            if lexical["distinct_2"] < self.config.min_distinct_2:
                reasons.append("LOW_DISTINCT_2")
            if lexical["type_token_ratio"] < self.config.min_type_token_ratio:
                reasons.append("LOW_TYPE_TOKEN_RATIO")

            semantic_similarity = self.novelty_index.max_similarity(example.text)
            metrics["semantic_similarity"] = round(semantic_similarity, 6)
            if semantic_similarity > self.config.max_semantic_similarity:
                reasons.append("SEMANTIC_DUPLICATE")

            overlap = self.benchmark_index.max_overlap(example.text)
            lcs = self.benchmark_index.max_lcs_ratio(example.text)
            metrics["benchmark_ngram_overlap"] = round(overlap, 6)
            metrics["benchmark_lcs_ratio"] = round(lcs, 6)
            if overlap > self.config.max_benchmark_overlap:
                reasons.append("BENCHMARK_CONTAMINATION_NGRAM")
            if lcs > self.config.max_lcs_ratio:
                reasons.append("BENCHMARK_CONTAMINATION_LCS")

            safety = self.safety_classifier.classify(example.prompt, example.response)
            metrics["safety_safe"] = safety.safe
            if not safety.safe:
                reasons.extend(f"SAFETY_{category}" for category in safety.categories)

            code = self.code_verifier.verify(example.code)
            if not code.safe:
                reasons.extend(code.reasons)

            unique_reasons = sorted(set(reasons))
            is_accepted = not unique_reasons
            records.append(
                VerificationRecord(
                    example_id=example.example_id,
                    accepted=is_accepted,
                    reasons=unique_reasons,
                    metrics=metrics,
                )
            )
            if is_accepted:
                accepted.append(example)
                self._accepted_hashes.add(normalized_hash)
                self.novelty_index.add(example.example_id, example.text)
            else:
                quarantined.append(example)
        return VerificationBatch(accepted=accepted, quarantined=quarantined, records=records)
