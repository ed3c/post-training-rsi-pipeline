from __future__ import annotations

import json
from pathlib import Path

from post_training_rsi.__main__ import main


def _config_path(tmp_path: Path) -> Path:
    path = tmp_path / "coevolution-config.json"
    path.write_text(
        json.dumps(
            {
                "budget": {
                    "total_limit_usd": 50.0,
                    "per_iteration_limit_usd": 10.0,
                    "max_consecutive_api_failures": 3,
                },
                "verification": {
                    "min_entropy": 1.0,
                    "min_distinct_2": 0.05,
                    "min_type_token_ratio": 0.05,
                    "max_semantic_similarity": 0.99,
                    "benchmark_ngram_size": 5,
                    "max_benchmark_overlap": 1.0,
                    "max_lcs_ratio": 1.0,
                    "min_acceptance_rate": 0.25,
                },
                "co_evolution": {
                    "max_cycles": 1,
                    "max_outer_iterations": 3,
                    "plateau_patience": 1,
                    "target_traces": 4,
                    "harness_min_improvement": 0.005,
                    "model_min_improvement": 0.005,
                },
                "approval": {
                    "dataset_review_required": False,
                    "checkpoint_review_required": False,
                    "harness_review_required": False,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_coevolve_cli_runs_reference_runtime_and_resumes(
    tmp_path: Path,
    capsys,
) -> None:
    workspace = tmp_path / "workspace"
    config_path = _config_path(tmp_path)
    arguments = [
        "--config",
        str(config_path),
        "--workspace",
        str(workspace),
        "--run-id",
        "coevolution-cli-run",
        "coevolve",
    ]

    first_exit = main(arguments)
    first = json.loads(capsys.readouterr().out)
    second_exit = main(arguments)
    second = json.loads(capsys.readouterr().out)

    assert first_exit == 0
    assert second_exit == 0
    assert first == second
    assert first["status"] == "STOPPED"
    assert first["state"] == "STOPPED"
    assert first["completed_cycles"] == 1
    assert first["active_checkpoint_id"].startswith("model-candidate-")
    assert first["pending_approval_request_id"] is None
    assert (workspace / "reports/coevolution-run-summary.json").is_file()
    assert (workspace / "active_harness.json").is_file()
    assert (workspace / "peak_checkpoint.json").is_file()


def test_coevolve_help_labels_deterministic_reference_runtime(capsys) -> None:
    try:
        main(["coevolve", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out
    assert "deterministic" in output.lower()
    assert "reference" in output.lower()
