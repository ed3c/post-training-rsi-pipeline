from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN_PATTERN = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]")


def tokenize(text: str) -> list[str]:
    return _TOKEN_PATTERN.findall(text.lower())


def shannon_entropy(text: str) -> float:
    tokens = tokenize(text)
    if not tokens:
        return 0.0
    counts = Counter(tokens)
    total = len(tokens)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def distinct_n(text: str, n: int = 2) -> float:
    tokens = tokenize(text)
    if len(tokens) < n:
        return 0.0
    ngrams = [tuple(tokens[index : index + n]) for index in range(len(tokens) - n + 1)]
    return len(set(ngrams)) / len(ngrams) if ngrams else 0.0


def type_token_ratio(text: str) -> float:
    tokens = tokenize(text)
    return len(set(tokens)) / len(tokens) if tokens else 0.0


def lexical_metrics(text: str) -> dict[str, float]:
    return {
        "entropy": round(shannon_entropy(text), 6),
        "distinct_2": round(distinct_n(text, 2), 6),
        "type_token_ratio": round(type_token_ratio(text), 6),
    }
