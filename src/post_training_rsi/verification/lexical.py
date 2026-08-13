from __future__ import annotations

import math
import re
from collections import Counter
from typing import Sequence

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]|[^\s]", re.UNICODE)


def normalize_text(text: str) -> str:
    return " ".join(text.lower().strip().split())


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(normalize_text(text))


def shannon_entropy(tokens_or_text: Sequence[str] | str) -> float:
    tokens = tokenize(tokens_or_text) if isinstance(tokens_or_text, str) else list(tokens_or_text)
    if not tokens:
        return 0.0
    counts = Counter(tokens)
    total = len(tokens)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def distinct_n(tokens_or_text: Sequence[str] | str, n: int = 2) -> float:
    if n <= 0:
        raise ValueError("n must be positive")
    tokens = tokenize(tokens_or_text) if isinstance(tokens_or_text, str) else list(tokens_or_text)
    if len(tokens) < n:
        return 0.0
    ngrams = [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]
    return len(set(ngrams)) / len(ngrams)


def type_token_ratio(tokens_or_text: Sequence[str] | str) -> float:
    tokens = tokenize(tokens_or_text) if isinstance(tokens_or_text, str) else list(tokens_or_text)
    return len(set(tokens)) / len(tokens) if tokens else 0.0
