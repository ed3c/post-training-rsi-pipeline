from __future__ import annotations

import json
from pathlib import Path

from post_training_rsi.__main__ import main


def _config_path(tmp_path: Path) -> Path:
    path = tmp_path / "audit-config.json"
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


def _create_workspace(tmp_path: Path, capsys) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    config = _config_path(tmp_path)
    exit_code = main(
        [
            "--config",
            str(config),
            "--workspace",
            str(workspace),
            "--run-id",
            "audit-cli-run",
            "coevolve",
        ]
    )
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "STOPPED"
    return workspace, config


def test_status_and_audit_cli_emit_versioned_json(tmp_path: Path, capsys) -> None:
    workspace, config = _create_workspace(tmp_path, capsys)

    status_exit = main(
        [
            "--config",
            str(config),
            "--workspace",
            str(workspace),
            "coevolve-status",
            "--expect-run-id",
            "audit-cli-run",
        ]
    )
    status = json.loads(capsys.readouterr().out)
    audit_exit = main(
        [
            "--config",
            str(config),
            "--workspace",
            str(workspace),
            "coevolve-audit",
            "--expect-run-id",
            "audit-cli-run",
        ]
    )
    audit = json.loads(capsys.readouterr().out)

    assert status_exit == 0
    assert status["schema_version"] == "post-training-rsi.coevolution-status/v1"
    assert status["runtime_status"] == "STOPPED"
    assert status["state"] == "STOPPED"
    assert audit_exit == 0
    assert audit["schema_version"] == "post-training-rsi.coevolution-audit/v1"
    assert audit["status"] == "PASS"
    assert Path(audit["report_path"]).is_file()


def test_audit_cli_warns_nonstrict_and_fails_strict(tmp_path: Path, capsys) -> None:
    workspace, config = _create_workspace(tmp_path, capsys)
    orphan = workspace / "control/evidence/ev-cli-orphan.json"
    orphan.write_text('{"forensic":"orphan"}\n', encoding="utf-8")

    normal_exit = main(
        [
            "--config",
            str(config),
            "--workspace",
            str(workspace),
            "coevolve-audit",
        ]
    )
    normal = json.loads(capsys.readouterr().out)
    strict_exit = main(
        [
            "--config",
            str(config),
            "--workspace",
            str(workspace),
            "coevolve-audit",
            "--strict",
        ]
    )
    strict = json.loads(capsys.readouterr().out)

    assert normal_exit == 0
    assert normal["status"] == "WARN"
    assert strict_exit == 2
    assert strict["status"] == "FAIL"
    assert orphan.is_file()


def test_status_identity_error_returns_structured_exit_two(tmp_path: Path, capsys) -> None:
    workspace, config = _create_workspace(tmp_path, capsys)

    exit_code = main(
        [
            "--config",
            str(config),
            "--workspace",
            str(workspace),
            "coevolve-status",
            "--expect-run-id",
            "different-run",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["schema_version"] == "post-training-rsi.coevolution-status/v1"
    assert payload["runtime_status"] == "ERROR"
    assert "different Run ID" in payload["error"]


def test_missing_workspace_audit_returns_two_without_creation(tmp_path: Path, capsys) -> None:
    workspace = tmp_path / "missing"

    exit_code = main(
        [
            "--workspace",
            str(workspace),
            "coevolve-audit",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "FAIL"
    assert not workspace.exists()


def test_audit_help_labels_read_only_and_no_automatic_recovery(capsys) -> None:
    try:
        main(["coevolve-audit", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out.lower()
    assert "read-only" in output
    assert "strict" in output
    assert "repair" not in output
