from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .lexical import tokenize


@dataclass(frozen=True, slots=True)
class ContaminationResult:
    contaminated: bool
    overlap_ratio: float
    lcs_ratio: float
    benchmark_id: str | None = None


@dataclass(slots=True)
class _BenchmarkRecord:
    benchmark_id: str
    tokens: tuple[str, ...]
    ngrams: frozenset[tuple[str, ...]]


@dataclass(slots=True)
class BenchmarkIndex:
    ngram_size: int = 13
    overlap_threshold: float = 0.70
    lcs_threshold: float = 0.80
    _records: list[_BenchmarkRecord] = field(default_factory=list)

    def add(self, benchmark_id: str, text: str) -> None:
        tokens = tuple(tokenize(text))
        self._records.append(
            _BenchmarkRecord(
                benchmark_id=benchmark_id,
                tokens=tokens,
                ngrams=frozenset(self._extract_ngrams(tokens)),
            )
        )

    def extend(self, texts: Iterable[str], prefix: str = "benchmark") -> None:
        for index, text in enumerate(texts):
            self.add(f"{prefix}-{index:04d}", text)

    def check(self, text: str) -> ContaminationResult:
        candidate_tokens = tuple(tokenize(text))
        if not candidate_tokens or not self._records:
            return ContaminationResult(False, 0.0, 0.0, None)
        candidate_ngrams = frozenset(self._extract_ngrams(candidate_tokens))
        best = ContaminationResult(False, 0.0, 0.0, None)
        for record in self._records:
            overlap = self._overlap_ratio(candidate_ngrams, record.ngrams)
            lcs = self._lcs_ratio(candidate_tokens, record.tokens)
            contaminated = overlap > self.overlap_threshold or lcs > self.lcs_threshold
            if contaminated or max(overlap, lcs) > max(best.overlap_ratio, best.lcs_ratio):
                best = ContaminationResult(
                    contaminated=contaminated,
                    overlap_ratio=overlap,
                    lcs_ratio=lcs,
                    benchmark_id=record.benchmark_id,
                )
        return best

    def _extract_ngrams(self, tokens: tuple[str, ...]) -> set[tuple[str, ...]]:
        if not tokens:
            return set()
        n = min(self.ngram_size, len(tokens))
        return {tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)}

    @staticmethod
    def _overlap_ratio(
        candidate: frozenset[tuple[str, ...]], benchmark: frozenset[tuple[str, ...]]
    ) -> float:
        return len(candidate & benchmark) / len(candidate) if candidate else 0.0

    @staticmethod
    def _lcs_ratio(left: tuple[str, ...], right: tuple[str, ...]) -> float:
        if not left or not right:
            return 0.0
        if len(left) > len(right):
            left, right = right, left
        previous = [0] * (len(left) + 1)
        for token_right in right:
            current = [0]
            for index, token_left in enumerate(left, 1):
                current.append(
                    previous[index - 1] + 1
                    if token_left == token_right
                    else max(previous[index], current[-1])
                )
            previous = current
        return previous[-1] / min(len(left), len(right))
