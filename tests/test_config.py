from __future__ import annotations

from pathlib import Path

import pytest

from post_training_rsi.config import PipelineConfig

ROOT = Path(__file__).resolve().parents[1]


def test_default_config_is_valid() -> None:
    config = PipelineConfig.load(None)
    assert config.model_id == "mock-student-8b"
    assert config.rsi.max_iterations >= 1
    assert config.adapters.teacher.backend == "mock"
    assert config.adapters.serving.backend == "local"


def test_repository_example_config_is_strict_and_round_trips() -> None:
    config = PipelineConfig.load(ROOT / "configs" / "pipeline.example.json")
    restored = PipelineConfig.from_mapping(config.to_dict())

    assert restored == config
    assert restored.adapters.training.command == ()
    assert restored.verification.allowed_python_imports[0] == "collections"


def test_per_iteration_budget_cannot_exceed_total() -> None:
    with pytest.raises(ValueError, match="per-iteration budget"):
        PipelineConfig.from_mapping(
            {
                "budget": {
                    "total_limit_usd": 1.0,
                    "per_iteration_limit_usd": 2.0,
                }
            }
        )


def test_unknown_top_level_and_nested_fields_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown fields"):
        PipelineConfig.from_mapping({"surprise": True})

    with pytest.raises(ValueError, match="adapters.training"):
        PipelineConfig.from_mapping(
            {
                "adapters": {
                    "training": {
                        "backend": "mock",
                        "unexpected": "value",
                    }
                }
            }
        )


def test_command_must_be_an_argument_array_not_a_shell_string() -> None:
    with pytest.raises(TypeError, match="array of strings"):
        PipelineConfig.from_mapping(
            {
                "adapters": {
                    "training": {
                        "backend": "command",
                        "command": "python trainer.py",
                    }
                }
            }
        )


def test_string_boolean_is_rejected() -> None:
    with pytest.raises(TypeError, match="boolean"):
        PipelineConfig.from_mapping(
            {
                "adapters": {
                    "training": {
                        "allow_external_artifact_path": "false",
                    }
                }
            }
        )


def test_command_serving_requires_explicit_teardown_command() -> None:
    with pytest.raises(ValueError, match="deploy_command and undeploy_command"):
        PipelineConfig.from_mapping(
            {
                "adapters": {
                    "serving": {
                        "backend": "command",
                        "deploy_command": ["python", "deploy.py"],
                    }
                }
            }
        )


def test_openai_compatible_teacher_requires_url_and_matching_model() -> None:
    with pytest.raises(ValueError, match="base_url"):
        PipelineConfig.from_mapping(
            {
                "teacher_model": "teacher-001",
                "adapters": {
                    "teacher": {
                        "backend": "openai_compatible",
                        "model_id": "teacher-001",
                    }
                },
            }
        )

    with pytest.raises(ValueError, match="must match"):
        PipelineConfig.from_mapping(
            {
                "teacher_model": "teacher-001",
                "adapters": {
                    "teacher": {
                        "model_id": "teacher-002",
                    }
                },
            }
        )
