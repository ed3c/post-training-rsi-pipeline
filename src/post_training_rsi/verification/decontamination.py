from __future__ import annotations

from dataclasses import dataclass

from .lexical import tokenize


def extract_ngrams(tokens: list[str], n: int) -> set[tuple[str, ...]]:
    if not tokens:
        return set()
    if len(tokens) < n:
        return {tuple(tokens)}
    return {tuple(tokens[index : index + n]) for index in range(len(tokens) - n + 1)}


def candidate_overlap(candidate: str, benchmark: str, *, n: int) -> float:
    candidate_ngrams = extract_ngrams(tokenize(candidate), n)
    benchmark_ngrams = extract_ngrams(tokenize(benchmark), n)
    if not candidate_ngrams or not benchmark_ngrams:
        return 0.0
    return len(candidate_ngrams & benchmark_ngrams) / len(candidate_ngrams)


def lcs_ratio(left: str, right: str) -> float:
    left_tokens = tokenize(left)
    right_tokens = tokenize(right)
    if not left_tokens or not right_tokens:
        return 0.0
    previous = [0] * (len(right_tokens) + 1)
    for left_token in left_tokens:
        current = [0]
        for index, right_token in enumerate(right_tokens, start=1):
            if left_token == right_token:
                current.append(previous[index - 1] + 1)
            else:
                current.append(max(previous[index], current[index - 1]))
        previous = current
    return previous[-1] / min(len(left_tokens), len(right_tokens))


@dataclass(slots=True)
class BenchmarkIndex:
    benchmark_texts: tuple[str, ...]
    ngram_size: int = 13

    def max_overlap(self, candidate: str) -> float:
        return max(
            (
                candidate_overlap(candidate, benchmark, n=self.ngram_size)
                for benchmark in self.benchmark_texts
            ),
            default=0.0,
        )

    def max_lcs_ratio(self, candidate: str) -> float:
        return max(
            (lcs_ratio(candidate, benchmark) for benchmark in self.benchmark_texts),
            default=0.0,
        )
