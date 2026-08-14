from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from post_training_rsi.adapter_runtime.command import (
    ADAPTER_RESULT_SCHEMA_VERSION,
    CommandSpec,
    run_json_command,
)
from post_training_rsi.adapter_runtime.errors import (
    AdapterConfigurationError,
    AdapterIntegrityError,
    AdapterLifecycleError,
    AdapterResultError,
)
from post_training_rsi.adapter_runtime.factory import (
    AdapterRuntime,
    build_adapter_runtime,
)
from post_training_rsi.adapter_runtime.integrity import sha256_path
from post_training_rsi.adapter_runtime.lifecycle import (
    evaluate_checkpoint_with_serving,
)
from post_training_rsi.config import PipelineConfig
from post_training_rsi.control_plane import EvidenceKind
from post_training_rsi.models import SyntheticExample, TrainingResult
from post_training_rsi.serving.adapter import (
    ServingDeployment,
    ServingTeardown,
)
from post_training_rsi.synthesis.teacher import (
    OpenAICompatibleTeacherClient,
    TeacherTransportError,
    TeacherTransportResponse,
)
from post_training_rsi.training.adapter import CommandTrainer, MockTrainer

FIXED_TIME = "2026-08-14T03:04:05Z"


def _write_script(path: Path, source: str) -> tuple[str, ...]:
    path.write_text(source, encoding="utf-8")
    return (sys.executable, str(path))


def _dataset(tmp_path: Path) -> tuple[Path, list[SyntheticExample]]:
    dataset_path = tmp_path / "accepted.jsonl"
    dataset_path.write_text(
        json.dumps(
            {
                "example_id": "example-001",
                "prompt": "prompt",
                "response": "response",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return dataset_path, [
        SyntheticExample("example-001", "prompt", "response")
    ]


def _checkpoint(tmp_path: Path) -> TrainingResult:
    dataset_path, examples = _dataset(tmp_path)
    return MockTrainer().train(
        examples=examples,
        dataset_path=dataset_path,
        dataset_hash=sha256_path(dataset_path),
        model_id="model-001",
        parent_checkpoint_id=None,
        iteration=1,
        output_root=tmp_path / "checkpoints",
    )


def test_default_factory_selects_mock_runtime_and_emits_safe_evidence(
    tmp_path: Path,
) -> None:
    runtime = build_adapter_runtime(
        PipelineConfig.load(None),
        workspace=tmp_path,
        clock=lambda: FIXED_TIME,
    )
    batch = runtime.teacher.synthesize(
        hypothesis="Improve state verification.",
        count=3,
        iteration=1,
    )
    evidence = runtime.evidence.synthesis(
        batch,
        run_id="run-001",
        iteration=1,
    )

    assert len(batch.examples) == 3
    assert len(set(batch.request_ids)) == 3
    assert evidence.kind is EvidenceKind.SYNTHESIS_MANIFEST
    assert evidence.metadata["example_count"] == 3
    assert "api_key" not in evidence.to_json().lower()
    assert "authorization" not in evidence.to_json().lower()


def test_factory_fails_closed_when_selected_teacher_secret_is_missing(
    tmp_path: Path,
) -> None:
    config = PipelineConfig.from_mapping(
        {
            "teacher_model": "teacher-001",
            "adapters": {
                "teacher": {
                    "backend": "openai_compatible",
                    "model_id": "teacher-001",
                    "api_version": "provider-v1",
                    "base_url": "https://inference.invalid/v1",
                    "api_key_env": "MISSING_TEACHER_KEY",
                }
            },
        }
    )

    with pytest.raises(AdapterConfigurationError, match="MISSING_TEACHER_KEY"):
        build_adapter_runtime(
            config,
            workspace=tmp_path,
            environment={},
        )


class _RetryingTransport:
    def __init__(self) -> None:
        self.calls = 0
        self.idempotency_keys: list[str] = []

    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> TeacherTransportResponse:
        assert url.endswith("/chat/completions")
        assert timeout_seconds == 2.0
        assert payload["model"] == "teacher-001"
        self.calls += 1
        self.idempotency_keys.append(headers["Idempotency-Key"])
        if self.calls == 1:
            raise TeacherTransportError("transient", retriable=True)
        content = json.dumps(
            {
                "prompt": "Verify a boundary state.",
                "response": "Check the state before the final action.",
                "code": None,
                "metadata": {"quality": "verified"},
            }
        )
        return TeacherTransportResponse(
            payload={
                "id": "provider-response-001",
                "choices": [{"message": {"content": content}}],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 17,
                },
            },
            headers={"x-request-id": "provider-request-001"},
        )


def test_openai_teacher_retries_only_retriable_failures_with_same_key() -> None:
    transport = _RetryingTransport()
    sleeps: list[float] = []
    client = OpenAICompatibleTeacherClient(
        model_id="teacher-001",
        api_version="provider-v1",
        base_url="https://inference.invalid/v1",
        api_key="secret-value",
        input_cost_per_million=1.0,
        output_cost_per_million=2.0,
        timeout_seconds=2.0,
        max_attempts=2,
        initial_backoff_seconds=0.25,
        transport=transport,
        sleeper=sleeps.append,
    )

    batch = client.synthesize(
        hypothesis="Improve boundary validation.",
        count=1,
        iteration=2,
    )

    assert transport.calls == 2
    assert transport.idempotency_keys[0] == transport.idempotency_keys[1]
    assert sleeps == [0.25]
    assert batch.request_ids == ("provider-request-001",)
    assert batch.input_tokens == 11
    assert batch.output_tokens == 17
    assert batch.estimated_cost_usd == pytest.approx(0.000045)
    assert "secret-value" not in json.dumps(batch.manifest())


def test_command_runner_removes_stale_result_before_process(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    result_path.write_text(
        json.dumps(
            {
                "schema_version": ADAPTER_RESULT_SCHEMA_VERSION,
                "result_type": "fixture",
                "idempotency_key": "fixture:stale",
                "value": "stale",
            }
        ),
        encoding="utf-8",
    )
    command = _write_script(tmp_path / "no_result.py", "pass\n")

    with pytest.raises(AdapterResultError, match="did not create"):
        run_json_command(
            CommandSpec(command, timeout_seconds=5.0),
            result_type="fixture",
            result_path=result_path,
            idempotency_key="fixture:fresh",
            expected_fields={"value"},
            environment={},
        )
    assert not result_path.exists()


def test_command_runner_retries_nonzero_exit_and_validates_identity(
    tmp_path: Path,
) -> None:
    counter = tmp_path / "counter.txt"
    result_path = tmp_path / "result.json"
    command = _write_script(
        tmp_path / "retry.py",
        """import json, os
from pathlib import Path
counter = Path(os.environ['COUNTER'])
value = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(value))
if value == 1:
    raise SystemExit(7)
payload = {
    'schema_version': os.environ['RSI_ADAPTER_SCHEMA_VERSION'],
    'result_type': os.environ['RSI_ADAPTER_RESULT_TYPE'],
    'idempotency_key': os.environ['RSI_IDEMPOTENCY_KEY'],
    'value': 'ok',
}
Path(os.environ['RESULT']).write_text(json.dumps(payload), encoding='utf-8')
""",
    )

    payload = run_json_command(
        CommandSpec(
            command,
            timeout_seconds=5.0,
            max_attempts=2,
            initial_backoff_seconds=0.0,
        ),
        result_type="fixture",
        result_path=result_path,
        idempotency_key="fixture:retry",
        expected_fields={"value"},
        environment={
            "COUNTER": str(counter),
            "RESULT": str(result_path),
        },
        sleeper=lambda _: None,
    )

    assert payload["value"] == "ok"
    assert counter.read_text(encoding="utf-8") == "2"


def test_training_rejects_dataset_hash_mismatch_before_worker(tmp_path: Path) -> None:
    dataset_path, examples = _dataset(tmp_path)

    with pytest.raises(AdapterIntegrityError, match="dataset_hash"):
        MockTrainer().train(
            examples=examples,
            dataset_path=dataset_path,
            dataset_hash="a" * 64,
            model_id="model-001",
            parent_checkpoint_id=None,
            iteration=1,
            output_root=tmp_path / "checkpoints",
        )


def test_command_training_rejects_artifact_path_escape(tmp_path: Path) -> None:
    dataset_path, examples = _dataset(tmp_path)
    command = _write_script(
        tmp_path / "escape.py",
        """import json, os
from pathlib import Path
outside = Path(os.environ['RSI_OUTPUT_DIR']).parent / 'escaped-artifact'
outside.mkdir(parents=True, exist_ok=True)
(outside / 'weights.bin').write_bytes(b'escaped')
payload = {
    'schema_version': os.environ['RSI_ADAPTER_SCHEMA_VERSION'],
    'result_type': os.environ['RSI_ADAPTER_RESULT_TYPE'],
    'idempotency_key': os.environ['RSI_IDEMPOTENCY_KEY'],
    'checkpoint_id': 'ckpt-escape',
    'checkpoint_path': str(outside),
    'model_id': os.environ['RSI_MODEL_ID'],
    'parent_checkpoint_id': None,
    'dataset_hash': os.environ['RSI_DATASET_HASH'],
    'iteration': int(os.environ['RSI_ITERATION']),
    'final_loss': 0.1,
    'artifact_sha256': None,
    'metadata': {},
}
Path(os.environ['RSI_TRAIN_RESULT_PATH']).write_text(json.dumps(payload), encoding='utf-8')
""",
    )

    with pytest.raises(AdapterIntegrityError, match="escaped"):
        CommandTrainer(command).train(
            examples=examples,
            dataset_path=dataset_path,
            dataset_hash=sha256_path(dataset_path),
            model_id="model-001",
            parent_checkpoint_id=None,
            iteration=1,
            output_root=tmp_path / "checkpoints",
        )


def test_command_training_rejects_worker_artifact_hash_mismatch(
    tmp_path: Path,
) -> None:
    dataset_path, examples = _dataset(tmp_path)
    command = _write_script(
        tmp_path / "bad_hash.py",
        """import json, os
from pathlib import Path
out = Path(os.environ['RSI_OUTPUT_DIR']) / 'ckpt-bad-hash'
out.mkdir(parents=True, exist_ok=True)
(out / 'weights.bin').write_bytes(b'actual')
payload = {
    'schema_version': os.environ['RSI_ADAPTER_SCHEMA_VERSION'],
    'result_type': os.environ['RSI_ADAPTER_RESULT_TYPE'],
    'idempotency_key': os.environ['RSI_IDEMPOTENCY_KEY'],
    'checkpoint_id': 'ckpt-bad-hash',
    'checkpoint_path': 'ckpt-bad-hash',
    'model_id': os.environ['RSI_MODEL_ID'],
    'parent_checkpoint_id': None,
    'dataset_hash': os.environ['RSI_DATASET_HASH'],
    'iteration': int(os.environ['RSI_ITERATION']),
    'final_loss': 0.1,
    'artifact_sha256': 'b' * 64,
    'metadata': {},
}
Path(os.environ['RSI_TRAIN_RESULT_PATH']).write_text(json.dumps(payload), encoding='utf-8')
""",
    )

    with pytest.raises(AdapterIntegrityError, match="artifact_sha256 mismatch"):
        CommandTrainer(command).train(
            examples=examples,
            dataset_path=dataset_path,
            dataset_hash=sha256_path(dataset_path),
            model_id="model-001",
            parent_checkpoint_id=None,
            iteration=1,
            output_root=tmp_path / "checkpoints",
        )


def test_default_lifecycle_passes_endpoint_tears_down_and_emits_evidence(
    tmp_path: Path,
) -> None:
    runtime = build_adapter_runtime(
        PipelineConfig.load(None),
        workspace=tmp_path,
        clock=lambda: FIXED_TIME,
    )
    checkpoint = _checkpoint(tmp_path)

    result = evaluate_checkpoint_with_serving(
        runtime,
        checkpoint=checkpoint,
        run_id="run-001",
        iteration=1,
        benchmark_id="benchmark-001",
    )

    assert result.teardown.stopped is True
    assert [record.kind for record in result.evidence] == [
        EvidenceKind.SERVING_ENDPOINT,
        EvidenceKind.EVALUATION_RESULT,
        EvidenceKind.SERVING_TEARDOWN,
    ]
    assert result.evidence[1].metadata["endpoint"] == result.deployment.endpoint


class _FailingEvaluator:
    def evaluate(
        self,
        *,
        checkpoint: TrainingResult,
        iteration: int,
        benchmark_id: str,
        endpoint: str | None = None,
    ) -> Any:
        del checkpoint, iteration, benchmark_id, endpoint
        raise RuntimeError("evaluation failed")


class _RecordingServing:
    def __init__(self, *, teardown_fails: bool = False) -> None:
        self.teardown_fails = teardown_fails
        self.teardown_called = False

    def deploy(self, checkpoint: TrainingResult) -> str:
        return self.deploy_handle(checkpoint).endpoint

    def deploy_handle(self, checkpoint: TrainingResult) -> ServingDeployment:
        return ServingDeployment(
            checkpoint_id=checkpoint.checkpoint_id,
            deployment_id="deployment-001",
            endpoint="mock://recording",
            idempotency_key="serving:recording",
        )

    def undeploy(self, checkpoint: TrainingResult, endpoint: str) -> None:
        deployment = ServingDeployment(
            checkpoint_id=checkpoint.checkpoint_id,
            deployment_id="deployment-001",
            endpoint=endpoint,
            idempotency_key="serving:recording",
        )
        self.undeploy_handle(checkpoint, deployment)

    def undeploy_handle(
        self,
        checkpoint: TrainingResult,
        deployment: ServingDeployment,
    ) -> ServingTeardown:
        self.teardown_called = True
        if self.teardown_fails:
            raise RuntimeError("teardown failed")
        return ServingTeardown(
            checkpoint_id=checkpoint.checkpoint_id,
            deployment_id=deployment.deployment_id,
            endpoint=deployment.endpoint,
            stopped=True,
        )


def test_lifecycle_runs_teardown_when_evaluation_raises(tmp_path: Path) -> None:
    default = build_adapter_runtime(
        PipelineConfig.load(None),
        workspace=tmp_path,
        clock=lambda: FIXED_TIME,
    )
    serving = _RecordingServing()
    runtime = AdapterRuntime(
        teacher=default.teacher,
        trainer=default.trainer,
        evaluator=_FailingEvaluator(),
        serving=serving,
        evidence=default.evidence,
    )

    with pytest.raises(RuntimeError, match="evaluation failed"):
        evaluate_checkpoint_with_serving(
            runtime,
            checkpoint=_checkpoint(tmp_path),
            run_id="run-001",
            iteration=1,
            benchmark_id="benchmark-001",
        )
    assert serving.teardown_called is True


def test_lifecycle_preserves_evaluation_and_teardown_failures(
    tmp_path: Path,
) -> None:
    default = build_adapter_runtime(
        PipelineConfig.load(None),
        workspace=tmp_path,
        clock=lambda: FIXED_TIME,
    )
    runtime = AdapterRuntime(
        teacher=default.teacher,
        trainer=default.trainer,
        evaluator=_FailingEvaluator(),
        serving=_RecordingServing(teardown_fails=True),
        evidence=default.evidence,
    )

    with pytest.raises(AdapterLifecycleError) as captured:
        evaluate_checkpoint_with_serving(
            runtime,
            checkpoint=_checkpoint(tmp_path),
            run_id="run-001",
            iteration=1,
            benchmark_id="benchmark-001",
        )

    assert [failure.stage for failure in captured.value.failures] == [
        "evaluation",
        "serving_teardown",
    ]
