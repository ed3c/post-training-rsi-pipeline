from __future__ import annotations

import pytest

from post_training_rsi.config import PipelineConfig


def test_default_config_is_valid() -> None:
    config = PipelineConfig.load(None)
    assert config.model_id == "mock-student-8b"
    assert config.rsi.max_iterations >= 1


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
