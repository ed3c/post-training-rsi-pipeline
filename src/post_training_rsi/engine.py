from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import PipelineConfig
from .cost import CostLedger
from .evaluation.adapter import DeterministicEvaluator, Evaluator
from .generation import DeterministicGenerator
from .lineage.store import ArtifactStore
from .models import IterationOutcome, RSIRunResult
from .serving.adapter import LocalArtifactServingAdapter, ServingAdapter
from .training.adapter import MockTrainer, Trainer
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

    def run_once(self, *, iteration: int, hypothesis: str) -> IterationOutcome:
        batch = self.dependencies.generator.generate(
            hypothesis=hypothesis,
            count=self.config.rsi.examples_per_iteration,
            iteration=iteration,
        )
        self.dependencies.ledger.charge(
            batch.estimated_cost_usd,
            iteration=iteration,
            category="generation",
        )
        verification = self.dependencies.verifier.verify(batch.examples)
        dataset_path, dataset_hash = self.dependencies.store.write_iteration_bundle(
            iteration=iteration,
            raw_examples=batch.examples,
            verification=verification,
            synthesis_manifest=batch.manifest(),
        )
        if not verification.accepted:
            return IterationOutcome(
                iteration=iteration,
                status="data_rejected",
                hypothesis=hypothesis,
                raw_count=len(batch.examples),
                accepted_count=0,
                rejected_count=len(verification.quarantined),
                acceptance_rate=verification.acceptance_rate,
                reason="no examples passed the data gates",
            )
        training = self.dependencies.trainer.train(
            examples=verification.accepted,
            dataset_path=dataset_path,
            dataset_hash=dataset_hash,
            model_id=self.config.model_id,
            parent_checkpoint_id=None,
            iteration=iteration,
            output_root=self.dependencies.store.root / "checkpoints",
        )
        endpoint = self.dependencies.serving.deploy(training)
        evaluation = self.dependencies.evaluator.evaluate(
            checkpoint=training,
            iteration=iteration,
            benchmark_id=self.config.rsi.benchmark_id,
        )
        return IterationOutcome(
            iteration=iteration,
            status="evaluated",
            hypothesis=hypothesis,
            raw_count=len(batch.examples),
            accepted_count=len(verification.accepted),
            rejected_count=len(verification.quarantined),
            acceptance_rate=verification.acceptance_rate,
            checkpoint_id=training.checkpoint_id,
            candidate_score=evaluation.score,
            peak_score=evaluation.score,
            promoted=True,
            cost_usd=self.dependencies.ledger.iteration_total(iteration),
            reason=f"evaluation endpoint: {endpoint}",
        )

    def run(self) -> RSIRunResult:
        outcome = self.run_once(
            iteration=1,
            hypothesis="Improve tool-state verification and boundary handling.",
        )
        result = RSIRunResult(
            status="completed",
            peak_checkpoint_id=outcome.checkpoint_id,
            peak_score=float(outcome.peak_score or self.config.rsi.initial_score),
            total_cost_usd=self.dependencies.ledger.total_charged_usd,
            outcomes=[outcome],
        )
        self.dependencies.store.write_report("rsi-run-summary.json", result.to_dict())
        return result


def build_default_engine(config: PipelineConfig, *, workspace: str | Path) -> RSIEngine:
    store = ArtifactStore(workspace)
    dependencies = EngineDependencies(
        generator=DeterministicGenerator(model=config.teacher_model),
        verifier=VerificationPipeline(
            config.verification,
            benchmark_texts=config.benchmark_texts,
        ),
        trainer=MockTrainer(),
        evaluator=DeterministicEvaluator(),
        serving=LocalArtifactServingAdapter(),
        store=store,
        ledger=CostLedger(config.budget),
    )
    return RSIEngine(config, dependencies)
