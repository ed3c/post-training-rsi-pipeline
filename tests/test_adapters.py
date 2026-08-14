from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from post_training_rsi.adapter_runtime.integrity import sha256_path
from post_training_rsi.evaluation.adapter import CommandEvaluator
from post_training_rsi.models import SyntheticExample
from post_training_rsi.serving.adapter import CommandServingAdapter
from post_training_rsi.training.adapter import CommandTrainer, MockTrainer


def _write_script(path: Path, source: str) -> tuple[str, ...]:
    path.write_text(source, encoding="utf-8")
    return (sys.executable, str(path))


def test_external_command_contracts_are_end_to_end_and_teardown(tmp_path: Path) -> None:
    trainer_command = _write_script(
        tmp_path / "trainer.py",
        """import json, os
from pathlib import Path
out = Path(os.environ['RSI_OUTPUT_DIR']) / 'external-ckpt'
out.mkdir(parents=True, exist_ok=True)
(out / 'weights.bin').write_bytes(b'trained-weights')
payload = {
    'schema_version': os.environ['RSI_ADAPTER_SCHEMA_VERSION'],
    'result_type': os.environ['RSI_ADAPTER_RESULT_TYPE'],
    'idempotency_key': os.environ['RSI_IDEMPOTENCY_KEY'],
    'checkpoint_id': 'external-ckpt',
    'checkpoint_path': 'external-ckpt',
    'model_id': os.environ['RSI_MODEL_ID'],
    'parent_checkpoint_id': None,
    'dataset_hash': os.environ['RSI_DATASET_HASH'],
    'iteration': int(os.environ['RSI_ITERATION']),
    'final_loss': 0.12,
    'artifact_sha256': None,
    'metadata': {'fixture': 'trainer'},
}
Path(os.environ['RSI_TRAIN_RESULT_PATH']).write_text(json.dumps(payload), encoding='utf-8')
""",
    )
    deploy_command = _write_script(
        tmp_path / "deploy.py",
        """import json, os
from pathlib import Path
checkpoint_id = os.environ['RSI_CHECKPOINT_ID']
payload = {
    'schema_version': os.environ['RSI_ADAPTER_SCHEMA_VERSION'],
    'result_type': os.environ['RSI_ADAPTER_RESULT_TYPE'],
    'idempotency_key': os.environ['RSI_IDEMPOTENCY_KEY'],
    'checkpoint_id': checkpoint_id,
    'deployment_id': 'deployment-001',
    'endpoint': 'mock://external-ckpt',
    'ready': True,
    'metadata': {'fixture': 'deploy'},
}
Path(os.environ['RSI_SERVE_RESULT_PATH']).write_text(json.dumps(payload), encoding='utf-8')
""",
    )
    undeploy_command = _write_script(
        tmp_path / "undeploy.py",
        """import json, os
from pathlib import Path
payload = {
    'schema_version': os.environ['RSI_ADAPTER_SCHEMA_VERSION'],
    'result_type': os.environ['RSI_ADAPTER_RESULT_TYPE'],
    'idempotency_key': os.environ['RSI_IDEMPOTENCY_KEY'],
    'checkpoint_id': os.environ['RSI_CHECKPOINT_ID'],
    'deployment_id': os.environ['RSI_DEPLOYMENT_ID'],
    'endpoint': os.environ['RSI_SERVING_ENDPOINT'],
    'stopped': True,
    'metadata': {'fixture': 'undeploy'},
}
Path(os.environ['RSI_UNSERVE_RESULT_PATH']).write_text(json.dumps(payload), encoding='utf-8')
""",
    )
    evaluator_command = _write_script(
        tmp_path / "evaluate.py",
        """import json, os
from pathlib import Path
payload = {
    'schema_version': os.environ['RSI_ADAPTER_SCHEMA_VERSION'],
    'result_type': os.environ['RSI_ADAPTER_RESULT_TYPE'],
    'idempotency_key': os.environ['RSI_IDEMPOTENCY_KEY'],
    'checkpoint_id': os.environ['RSI_CHECKPOINT_ID'],
    'benchmark_id': os.environ['RSI_BENCHMARK_ID'],
    'iteration': int(os.environ['RSI_ITERATION']),
    'endpoint': os.environ['RSI_SERVING_ENDPOINT'] or None,
    'score': 0.57,
    'metrics': {'success': 0.57},
    'failure_traces': [],
    'estimated_cost_usd': 0.01,
    'metadata': {'fixture': 'evaluation'},
}
Path(os.environ['RSI_EVAL_RESULT_PATH']).write_text(json.dumps(payload), encoding='utf-8')
""",
    )

    dataset_path = tmp_path / "accepted.jsonl"
    dataset_path.write_text(
        json.dumps({"example_id": "x"}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    example = SyntheticExample("x", "prompt", "response")
    training = CommandTrainer(trainer_command).train(
        examples=[example],
        dataset_path=dataset_path,
        dataset_hash=sha256_path(dataset_path),
        model_id="model",
        parent_checkpoint_id=None,
        iteration=1,
        output_root=tmp_path / "checkpoints",
    )
    serving = CommandServingAdapter(
        deploy_command,
        undeploy_command=undeploy_command,
    )
    deployment = serving.deploy_handle(training)
    evaluation = CommandEvaluator(evaluator_command).evaluate(
        checkpoint=training,
        iteration=1,
        benchmark_id="contract-test",
        endpoint=deployment.endpoint,
    )
    teardown = serving.undeploy_handle(training, deployment)

    assert training.metadata["artifact_sha256"] == sha256_path(
        training.checkpoint_path
    )
    assert deployment.endpoint == "mock://external-ckpt"
    assert evaluation.score == 0.57
    assert evaluation.metrics == {"success": 0.57}
    assert teardown.stopped is True


def test_adapter_contract_failures_are_explicit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty dataset"):
        MockTrainer().train(
            examples=[],
            dataset_path=tmp_path / "none.jsonl",
            dataset_hash="a" * 64,
            model_id="model",
            parent_checkpoint_id=None,
            iteration=1,
            output_root=tmp_path,
        )
    with pytest.raises(ValueError, match="command cannot be empty"):
        CommandTrainer([])
    with pytest.raises(ValueError, match="command cannot be empty"):
        CommandEvaluator([])
    with pytest.raises(ValueError, match="command cannot be empty"):
        CommandServingAdapter([], undeploy_command=("unused",))
    with pytest.raises(ValueError, match="command cannot be empty"):
        CommandServingAdapter(("unused",), undeploy_command=[])
