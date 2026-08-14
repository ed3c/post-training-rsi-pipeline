from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from post_training_rsi.__main__ import main
from post_training_rsi.config import PipelineConfig
from post_training_rsi.control_plane.validation import canonical_json
from post_training_rsi.preflight import (
    DESTINATION_AUTHORIZATION_SCHEMA_VERSION,
    PROVIDER_PREFLIGHT_SCHEMA_VERSION,
)

ORIGIN = "https://teacher.example.com"


def _config_file(tmp_path: Path, **overrides: object) -> tuple[Path, PipelineConfig]:
    value: dict[str, object] = {
        "benchmark_texts": ["reference benchmark item"],
        "adapters": {
            "teacher": {
                "backend": "openai_compatible",
                "base_url": f"{ORIGIN}/v1",
                "api_key_env": "TEACHER_API_KEY",
            }
        },
        "approval": {
            "dataset_review_required": True,
            "checkpoint_review_required": True,
            "harness_review_required": True,
        },
    }
    value.update(overrides)
    path = tmp_path / "preflight-config.json"
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path, PipelineConfig.from_mapping(value)


def _authorization_file(tmp_path: Path, config: PipelineConfig) -> Path:
    digest = hashlib.sha256(
        canonical_json(config.to_dict()).encode("utf-8")
    ).hexdigest()
    path = tmp_path / "authorization.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": DESTINATION_AUTHORIZATION_SCHEMA_VERSION,
                "authorization_id": "auth-cli-001",
                "approved_by": "release-manager",
                "approved_at": "2026-08-14T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "stage": "teacher",
                "origin": ORIGIN,
                "data_classes": ["synthetic-prompt"],
                "config_sha256": digest,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_reference_target_passes_and_writes_only_its_report(
    tmp_path: Path, capsys
) -> None:
    workspace = tmp_path / "workspace"
    exit_code = main(
        ["--workspace", str(workspace), "provider-preflight", "--target", "reference"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["schema_version"] == PROVIDER_PREFLIGHT_SCHEMA_VERSION
    assert payload["status"] == "PASS"
    assert payload["target"] == "reference"
    written = sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file()
    )
    assert written == ["reports/provider-preflight.json"]


def test_missing_authorization_returns_structured_exit_two(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("TEACHER_API_KEY", "cli-test-value")
    config_path, _ = _config_file(tmp_path)
    exit_code = main(
        [
            "--config",
            str(config_path),
            "--workspace",
            str(tmp_path / "workspace"),
            "provider-preflight",
            "--target",
            "teacher",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "FAIL"
    assert any(
        check["check_id"] == "preflight-authorization" and check["status"] == "FAIL"
        for check in payload["checks"]
    )


def test_bound_authorization_passes_and_redacts_the_credential(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("TEACHER_API_KEY", "cli-secret-value")
    config_path, config = _config_file(tmp_path)
    authorization = _authorization_file(tmp_path, config)

    exit_code = main(
        [
            "--config",
            str(config_path),
            "--workspace",
            str(tmp_path / "workspace"),
            "provider-preflight",
            "--target",
            "teacher",
            "--authorization-file",
            str(authorization),
        ]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert json.loads(output)["status"] == "PASS"
    assert "cli-secret-value" not in output


def test_strict_promotes_a_warning_to_exit_two(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("TEACHER_API_KEY", "cli-test-value")
    config_path, config = _config_file(
        tmp_path,
        adapters={
            "teacher": {"backend": "mock"},
            "training": {
                # An absolute interpreter path resolves on every host, so the
                # only finding is the worker script this host does not carry.
                "backend": "command",
                "command": [sys.executable, "/opt/workers/train.py"],
            },
        },
    )
    workspace = tmp_path / "workspace"

    normal = main(
        [
            "--config",
            str(config_path),
            "--workspace",
            str(workspace),
            "provider-preflight",
            "--target",
            "training",
        ]
    )
    normal_payload = json.loads(capsys.readouterr().out)
    strict = main(
        [
            "--config",
            str(config_path),
            "--workspace",
            str(workspace),
            "provider-preflight",
            "--target",
            "training",
            "--strict",
        ]
    )
    strict_payload = json.loads(capsys.readouterr().out)

    assert normal == 0
    assert normal_payload["status"] == "WARN"
    assert strict == 2
    assert strict_payload["status"] == "FAIL"


def test_help_labels_the_command_read_only_and_offers_strict(capsys) -> None:
    try:
        main(["provider-preflight", "--help"])
    except SystemExit as exc:
        assert exc.code == 0
    output = capsys.readouterr().out.lower()
    assert "no network call" in output
    assert "strict" in output
    assert "end-to-end-coevolution" in output
