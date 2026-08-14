from __future__ import annotations

from typing import Protocol

from .lexical import tokenize


class NoveltyIndex(Protocol):
    def max_similarity(self, text: str) -> float: ...

    def add(self, item_id: str, text: str) -> None: ...


class TokenJaccardNoveltyIndex:
    """Dependency-free semantic novelty baseline for CI and small experiments."""

    def __init__(self) -> None:
        self._items: list[tuple[str, set[str]]] = []

    def max_similarity(self, text: str) -> float:
        candidate = set(tokenize(text))
        if not candidate or not self._items:
            return 0.0
        best = 0.0
        for _, historical in self._items:
            union = candidate | historical
            similarity = len(candidate & historical) / len(union) if union else 0.0
            best = max(best, similarity)
        return best

    def add(self, item_id: str, text: str) -> None:
        self._items.append((item_id, set(tokenize(text))))

    def __len__(self) -> int:
        return len(self._items)


class SentenceTransformerNoveltyIndex:
    """Optional dense-vector index; imports heavy dependencies only when instantiated."""

    def __init__(self, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> None:
        try:
            import numpy as np
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("install the 'semantic' extra for dense novelty checks") from exc
        self._np = np
        self._encoder = SentenceTransformer(model_name)
        self._ids: list[str] = []
        self._vectors = None

    def max_similarity(self, text: str) -> float:
        vector = self._encoder.encode([text], convert_to_numpy=True, normalize_embeddings=True)
        if self._vectors is None:
            return 0.0
        similarities = vector @ self._vectors.T
        return float(self._np.max(similarities))

    def add(self, item_id: str, text: str) -> None:
        vector = self._encoder.encode([text], convert_to_numpy=True, normalize_embeddings=True)
        self._vectors = vector if self._vectors is None else self._np.vstack([self._vectors, vector])
        self._ids.append(item_id)
