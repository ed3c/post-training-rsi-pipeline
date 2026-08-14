from __future__ import annotations

from post_training_rsi.config import PipelineConfig
from post_training_rsi.engine import build_default_engine


def test_default_engine_runs_end_to_end(tmp_path) -> None:
    config = PipelineConfig.from_mapping(
        {
            "rsi": {"examples_per_iteration": 4},
            "verification": {
                "min_entropy": 1.0,
                "min_distinct_2": 0.1,
                "min_type_token_ratio": 0.1,
            },
        }
    )
    result = build_default_engine(config, workspace=tmp_path).run()
    assert result.status == "completed"
    assert result.peak_checkpoint_id is not None
    assert result.peak_score == 0.58
    assert (tmp_path / "reports" / "rsi-run-summary.json").exists()
    assert (tmp_path / "iterations" / "iter-001" / "filter_audit.jsonl").exists()
