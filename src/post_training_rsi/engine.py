from __future__ import annotations

from dataclasses import dataclass

from .config import PipelineConfig
from .cost import CostLedger
from .evaluation.adapter import Evaluator
from .generation import DeterministicGenerator
from .lineage.store import ArtifactStore
from .serving.adapter import ServingAdapter
from .training.adapter import Trainer
from .verification.pipeline import VerificationPipeline


@dataclass(slots=True)
class EngineDependencies:
    generator: DeterministicGenerator
    verifier: VerificationPipeline
    trainer: Trainer
    evaluator: Evaluator
    serving: ServingAdapter
    store: ArtifactStore
    ledger: CostLedger


class RSIEngine:
    """Five-stage recursive data experiment controller."""

    def __init__(self, config: PipelineConfig, dependencies: EngineDependencies) -> None:
        self.config = config
        self.dependencies = dependencies
