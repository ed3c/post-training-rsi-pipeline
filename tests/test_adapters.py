from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from post_training_rsi.evaluation.adapter import CommandEvaluator
from post_training_rsi.models import SyntheticExample
from post_training_rsi.serving.adapter import CommandServingAdapter
from post_training_rsi.training.adapter import CommandTrainer, MockTrainer


def _write_script(path: Path, source: str) -> list[str]:
    path.write_text(source, encoding="utf-8")
    return [sys.executable, str(path)]


def test_external_command_contracts(tmp_path: Path) -> None:
    trainer_command = _write_script(
        tmp_path / "trainer.py",
        """import json, os
from pathlib import Path
out = Path(os.environ['RSI_OUTPUT_DIR']) / 'external-ckpt'
out.mkdir(parents=True, exist_ok=True)
payload = {'checkpoint_id': 'external-ckpt', 'checkpoint_path': str(out), 'final_loss': 0.12}
Path(os.environ['RSI_TRAIN_RESULT_PATH']).write_text(json.dumps(payload))
""",
    )
    serving_command = _write_script(
        tmp_path / "serve.py",
        """import json, os
from pathlib import Path
payload = {'ready': True, 'endpoint': 'mock://external'}
Path(os.environ['RSI_SERVE_RESULT_PATH']).write_text(json.dumps(payload))
""",
    )
    evaluator_command = _write_script(
        tmp_path / "evaluate.py",
        """import json, os
from pathlib import Path
payload = {'score': 0.57, 'metrics': {'success': 0.57}, 'failure_traces': []}
Path(os.environ['RSI_EVAL_RESULT_PATH']).write_text(json.dumps(payload))
""",
    )
    dataset_path = tmp_path / "accepted.jsonl"
    dataset_path.write_text(json.dumps({"example_id": "x"}) + "\n", encoding="utf-8")
    example = SyntheticExample("x", "prompt", "response")
    training = CommandTrainer(trainer_command).train(
        examples=[example],
        dataset_path=dataset_path,
        dataset_hash="a" * 64,
        model_id="model",
        parent_checkpoint_id=None,
        iteration=1,
        output_root=tmp_path / "checkpoints",
    )
    endpoint = CommandServingAdapter(serving_command).deploy(training)
    evaluation = CommandEvaluator(evaluator_command).evaluate(
        checkpoint=training,
        iteration=1,
        benchmark_id="contract-test",
    )
    assert endpoint == "mock://external"
    assert evaluation.score == 0.57
    assert evaluation.metrics == {"success": 0.57}


def test_adapter_contract_failures_are_explicit(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty dataset"):
        MockTrainer().train(
            examples=[],
            dataset_path=tmp_path / "none.jsonl",
            dataset_hash="x",
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
        CommandServingAdapter([])
