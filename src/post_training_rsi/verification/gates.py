from __future__ import annotations

from typing import Iterable

from ..config import VerificationConfig
from ..models import SyntheticExample, VerificationBatch


class VerificationPipeline:
    def __init__(self, config: VerificationConfig, *, benchmark_texts: Iterable[str] = ()) -> None:
        self.config = config
        self.benchmark_texts = tuple(benchmark_texts)

    @property
    def config_hash(self) -> str:
        return "pending"

    def verify(self, examples: Iterable[SyntheticExample]) -> VerificationBatch:
        raise NotImplementedError
