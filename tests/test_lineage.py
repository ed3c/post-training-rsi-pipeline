from __future__ import annotations

from pathlib import Path

from post_training_rsi.config import VerificationConfig
from post_training_rsi.lineage.manifest import LineageManifest
from post_training_rsi.lineage.store import ArtifactStore
from post_training_rsi.models import SyntheticExample
from post_training_rsi.training.adapter import MockTrainer
from post_training_rsi.verification.pipeline import VerificationPipeline


def test_lineage_manifest_and_artifact_store_round_trip(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    verifier = VerificationPipeline(
        VerificationConfig(min_entropy=1.0, min_distinct_2=0.1, min_type_token_ratio=0.1)
    )
    example = SyntheticExample(
        "lineage-1",
        "Define an atomic transfer invariant across two accounts.",
        "Debit and credit commit together while preserving total value except explicit fees.",
    )
    verified = verifier.verify([example])
    dataset_path, dataset_hash = store.write_iteration_bundle(
        iteration=1,
        raw_examples=[example],
        verification=verified,
        synthesis_manifest={"source_api_version": "mock-v1"},
    )
    training = MockTrainer().train(
        examples=verified.accepted,
        dataset_path=dataset_path,
        dataset_hash=dataset_hash,
        model_id="model",
        parent_checkpoint_id=None,
        iteration=1,
        output_root=store.root / "checkpoints",
    )
    manifest = LineageManifest.create(
        checkpoint_id=training.checkpoint_id,
        parent_checkpoint_id=None,
        dataset_commit_hash=dataset_hash,
        dataset_path=str(dataset_path),
        teacher_api_version="mock-v1",
        teacher_model="teacher",
        teacher_prompt_hash="prompt-hash",
        filter_config_version=verifier.config_hash,
        rejected_data_count=0,
        training_loss_final=training.final_loss,
        benchmark_score=0.61,
        model_id="model",
        iteration=1,
        status="PROMOTED",
        code_git_commit="test-commit",
    )
    checkpoint_dir = store.write_checkpoint(training, manifest, status="PROMOTED")
    store.write_peak(
        checkpoint_id=training.checkpoint_id,
        score=0.61,
        model_id="model",
        iteration=1,
    )
    store.mark_iteration_quarantined(
        iteration=1,
        checkpoint_id=training.checkpoint_id,
        reason="fixture regression",
    )
    restored = LineageManifest.from_dict(
        store.read_json(checkpoint_dir / "lineage_manifest.json")
    )
    assert restored.dataset_commit_hash == dataset_hash
    assert store.load_peak() == {
        "checkpoint_id": training.checkpoint_id,
        "iteration": 1,
        "model_id": "model",
        "score": 0.61,
    }
    assert store.read_jsonl(tmp_path / "iterations/iter-001/accepted.jsonl")[0][
        "example_id"
    ] == "lineage-1"
    assert (tmp_path / "iterations/iter-001/QUARANTINED.json").exists()
