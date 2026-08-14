from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from post_training_rsi.audit import AuditStatus
from post_training_rsi.config import BudgetConfig, PipelineConfig
from post_training_rsi.control_plane.validation import canonical_json
from post_training_rsi.preflight import (
    DESTINATION_AUTHORIZATION_SCHEMA_VERSION,
    PROVIDER_PREFLIGHT_SCHEMA_VERSION,
    DestinationAuthorization,
    PreflightContractError,
    PreflightTarget,
    ProviderPreflight,
)

FIXED_TIME = "2026-08-14T15:00:00Z"
ORIGIN = "https://teacher.example.com"


def _config(**overrides: object) -> PipelineConfig:
    value: dict[str, object] = {
        "benchmark_texts": ["reference benchmark item"],
        "adapters": {
            "teacher": {
                "backend": "openai_compatible",
                "base_url": f"{ORIGIN}/v1",
                "api_key_env": "TEACHER_API_KEY",
                "input_cost_per_million": 1.0,
                "output_cost_per_million": 2.0,
            },
            "training": {"backend": "mock"},
            "evaluation": {"backend": "deterministic"},
            "serving": {"backend": "local"},
        },
        "approval": {
            "dataset_review_required": True,
            "checkpoint_review_required": True,
            "harness_review_required": True,
        },
    }
    value.update(overrides)
    return PipelineConfig.from_mapping(value)


def _sha256(config: PipelineConfig) -> str:
    return hashlib.sha256(
        canonical_json(config.to_dict()).encode("utf-8")
    ).hexdigest()


def _preflight(
    config: PipelineConfig,
    workspace: Path,
    *,
    environment: dict[str, str] | None = None,
    resolve: object = None,
) -> ProviderPreflight:
    return ProviderPreflight(
        config,
        workspace=workspace,
        clock=lambda: FIXED_TIME,
        environment={"TEACHER_API_KEY": "unit-test-value"}
        if environment is None
        else environment,
        resolve_executable=(lambda name: f"/usr/bin/{name}")
        if resolve is None
        else resolve,
    )


def _authorization(config: PipelineConfig, **overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": DESTINATION_AUTHORIZATION_SCHEMA_VERSION,
        "authorization_id": "auth-teacher-001",
        "approved_by": "release-manager",
        "approved_at": "2026-08-14T00:00:00Z",
        "expires_at": "2026-08-15T00:00:00Z",
        "stage": "teacher",
        "origin": ORIGIN,
        "data_classes": ["synthetic-prompt", "synthetic-completion"],
        "config_sha256": _sha256(config),
    }
    value.update(overrides)
    return value


def _check(report, check_id: str):
    matches = [item for item in report.checks if item.check_id == check_id]
    assert matches, check_id
    return matches[-1]


def test_reference_target_passes_with_default_config(tmp_path: Path) -> None:
    report = _preflight(PipelineConfig(), tmp_path, environment={}).run(
        target=PreflightTarget.REFERENCE,
        write_report=False,
    )

    assert report.status is AuditStatus.PASS
    assert report.exit_code == 0
    assert report.to_dict()["schema_version"] == PROVIDER_PREFLIGHT_SCHEMA_VERSION
    assert report.config_sha256 == _sha256(PipelineConfig())


def test_reference_target_rejects_an_external_adapter(tmp_path: Path) -> None:
    report = _preflight(_config(), tmp_path).run(
        target=PreflightTarget.REFERENCE,
        write_report=False,
    )

    assert report.status is AuditStatus.FAIL
    assert _check(report, "preflight-adapter-inventory").status is AuditStatus.FAIL


def test_teacher_target_passes_with_a_bound_authorization(tmp_path: Path) -> None:
    config = _config()
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(config),
        write_report=False,
    )

    assert report.status is AuditStatus.PASS
    assert _check(report, "preflight-authorization").status is AuditStatus.PASS


def test_missing_secret_name_fails(tmp_path: Path) -> None:
    config = _config()
    report = _preflight(config, tmp_path, environment={}).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(config),
        write_report=False,
    )

    assert report.status is AuditStatus.FAIL
    assert _check(report, "preflight-secret-names").status is AuditStatus.FAIL


def test_empty_secret_value_fails_like_an_absent_one(tmp_path: Path) -> None:
    config = _config()
    report = _preflight(
        config, tmp_path, environment={"TEACHER_API_KEY": "   "}
    ).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(config),
        write_report=False,
    )

    assert _check(report, "preflight-secret-names").status is AuditStatus.FAIL


@pytest.mark.parametrize(
    ("base_url", "problem"),
    [
        ("http://teacher.example.com/v1", "scheme must be https"),
        (
            "https://user:secret@teacher.example.com/v1",
            "URL must not embed credentials",
        ),
        ("https://teacher.example.com/v1?key=abc", "URL must not carry a query string"),
        ("https://teacher.example.com/v1#frag", "URL must not carry a fragment"),
    ],
)
def test_unsafe_teacher_urls_fail(
    tmp_path: Path, base_url: str, problem: str
) -> None:
    config = _config(
        adapters={
            "teacher": {
                "backend": "openai_compatible",
                "base_url": base_url,
                "api_key_env": "TEACHER_API_KEY",
            }
        }
    )
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(config),
        write_report=False,
    )

    check = _check(report, "preflight-teacher-url")
    assert check.status is AuditStatus.FAIL
    assert problem in check.details["problems"]


def test_unsafe_url_never_reaches_the_report_verbatim(tmp_path: Path) -> None:
    """A credential embedded in the URL must not be copied into evidence."""
    config = _config(
        adapters={
            "teacher": {
                "backend": "openai_compatible",
                "base_url": "https://user:hunter2@teacher.example.com/v1",
                "api_key_env": "TEACHER_API_KEY",
            }
        }
    )
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(config),
        write_report=False,
    )

    serialized = json.dumps(report.to_dict())
    assert "hunter2" not in serialized
    assert "unit-test-value" not in serialized


def test_missing_authorization_fails_external_transmission(tmp_path: Path) -> None:
    report = _preflight(_config(), tmp_path).run(
        target=PreflightTarget.TEACHER,
        write_report=False,
    )

    assert report.status is AuditStatus.FAIL
    assert _check(report, "preflight-authorization").status is AuditStatus.FAIL


def test_expired_authorization_fails(tmp_path: Path) -> None:
    config = _config()
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(
            config,
            approved_at="2026-08-01T00:00:00Z",
            expires_at="2026-08-02T00:00:00Z",
        ),
        write_report=False,
    )

    check = _check(report, "preflight-authorization")
    assert check.status is AuditStatus.FAIL
    assert "authorization has expired" in check.details["problems"]


def test_config_substitution_invalidates_authorization(tmp_path: Path) -> None:
    config = _config()
    stale = _authorization(config)
    moved = _config(seed=99)

    report = _preflight(moved, tmp_path).run(
        target=PreflightTarget.TEACHER,
        authorization=stale,
        write_report=False,
    )

    check = _check(report, "preflight-authorization")
    assert check.status is AuditStatus.FAIL
    assert "config_sha256 does not match this configuration" in check.details["problems"]


def test_origin_substitution_invalidates_authorization(tmp_path: Path) -> None:
    config = _config()
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(config, origin="https://elsewhere.example.com"),
        write_report=False,
    )

    check = _check(report, "preflight-authorization")
    assert check.status is AuditStatus.FAIL
    assert (
        "origin does not match the configured Teacher destination"
        in check.details["problems"]
    )


def test_unresolvable_command_fails(tmp_path: Path) -> None:
    config = _config(
        adapters={
            "teacher": {"backend": "mock"},
            "training": {"backend": "command", "command": ["definitely-not-installed"]},
        }
    )
    report = _preflight(config, tmp_path, resolve=lambda name: None).run(
        target=PreflightTarget.TRAINING,
        write_report=False,
    )

    assert report.status is AuditStatus.FAIL
    assert _check(report, "preflight-commands").status is AuditStatus.FAIL


def test_missing_worker_script_warns_and_strict_fails(tmp_path: Path) -> None:
    config = _config(
        adapters={
            "teacher": {"backend": "mock"},
            "training": {
                "backend": "command",
                "command": ["python", "/opt/workers/train.py"],
            },
        }
    )
    normal = _preflight(config, tmp_path).run(
        target=PreflightTarget.TRAINING,
        write_report=False,
    )
    strict = _preflight(config, tmp_path).run(
        target=PreflightTarget.TRAINING,
        strict=True,
        write_report=False,
    )

    assert _check(normal, "preflight-commands").status is AuditStatus.WARN
    assert normal.status is AuditStatus.WARN
    assert normal.exit_code == 0
    assert strict.status is AuditStatus.FAIL
    assert strict.exit_code == 2


@pytest.mark.parametrize(
    "serving",
    [
        {"backend": "local", "deploy_command": ["deploy.sh"]},
        {"backend": "command", "deploy_command": ["deploy.sh"]},
        {"backend": "command", "undeploy_command": ["undeploy.sh"]},
    ],
)
def test_unpaired_serving_commands_are_rejected(serving: dict[str, object]) -> None:
    """A deploy without a teardown leaves a paid endpoint with no exit path."""
    with pytest.raises(ValueError):
        _config(adapters={"teacher": {"backend": "mock"}, "serving": serving})


def test_serving_pairing_is_recorded_as_admission_evidence(tmp_path: Path) -> None:
    config = _config(
        adapters={
            "teacher": {"backend": "mock"},
            "serving": {
                "backend": "command",
                "deploy_command": ["deploy.sh"],
                "undeploy_command": ["undeploy.sh"],
            },
        }
    )
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.TRAINING,
        write_report=False,
    )

    check = _check(report, "preflight-serving-commands")
    assert check.status is AuditStatus.PASS
    assert check.details["deploy_configured"] is True
    assert check.details["undeploy_configured"] is True


def test_external_artifact_path_escape_fails_for_production_targets(
    tmp_path: Path,
) -> None:
    config = _config(
        adapters={
            "teacher": {"backend": "mock"},
            "training": {"backend": "mock", "allow_external_artifact_path": True},
        }
    )
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.TRAINING,
        write_report=False,
    )

    assert _check(report, "preflight-artifact-path").status is AuditStatus.FAIL


def test_incomplete_approvals_fail_per_target(tmp_path: Path) -> None:
    config = _config(
        approval={
            "dataset_review_required": True,
            "checkpoint_review_required": True,
            "harness_review_required": False,
        }
    )
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.END_TO_END_COEVOLUTION,
        authorization=_authorization(config),
        write_report=False,
    )

    check = _check(report, "preflight-approvals")
    assert check.status is AuditStatus.FAIL
    assert check.details["missing"] == ["harness_review_required"]


def test_missing_benchmarks_fail_end_to_end_targets(tmp_path: Path) -> None:
    config = _config(benchmark_texts=[])
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.END_TO_END_RSI,
        authorization=_authorization(config),
        write_report=False,
    )

    assert _check(report, "preflight-benchmarks").status is AuditStatus.FAIL


def test_unvalidatable_config_fails_before_any_other_conclusion(
    tmp_path: Path,
) -> None:
    """A config that cannot revalidate must fail admission, not be re-decided."""
    config = replace(
        _config(),
        budget=BudgetConfig(
            total_limit_usd=10.0,
            per_iteration_limit_usd=25.0,
            max_consecutive_api_failures=3,
        ),
    )
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(config),
        write_report=False,
    )

    check = _check(report, "preflight-config")
    assert check.status is AuditStatus.FAIL
    assert "per-iteration budget cannot exceed total budget" in check.details["error"]
    assert report.status is AuditStatus.FAIL


def test_report_is_deterministic_and_written_only_to_its_own_path(
    tmp_path: Path,
) -> None:
    config = _config()
    workspace = tmp_path / "workspace"
    first = _preflight(config, workspace).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(config),
    )
    second = _preflight(config, workspace).run(
        target=PreflightTarget.TEACHER,
        authorization=_authorization(config),
    )

    assert first == second
    assert first.generated_at == FIXED_TIME
    written = sorted(
        path.relative_to(workspace).as_posix()
        for path in workspace.rglob("*")
        if path.is_file()
    )
    assert written == ["reports/provider-preflight.json"]
    payload = json.loads(
        (workspace / "reports/provider-preflight.json").read_text(encoding="utf-8")
    )
    assert payload["schema_version"] == PROVIDER_PREFLIGHT_SCHEMA_VERSION
    assert payload["inventory"]["mutations_allowed"] == [
        "reports/provider-preflight.json"
    ]


def test_preflight_opens_no_socket_and_starts_no_subprocess(
    tmp_path: Path, monkeypatch
) -> None:
    """The whole point of the boundary is that it costs nothing to fail here."""
    import socket
    import subprocess

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("preflight must not touch the network or spawn a process")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)

    config = _config()
    report = _preflight(config, tmp_path).run(
        target=PreflightTarget.END_TO_END_COEVOLUTION,
        authorization=_authorization(config),
    )

    assert report.status is AuditStatus.PASS


def test_authorization_receipt_rejects_malformed_records() -> None:
    config = _config()
    with pytest.raises(PreflightContractError):
        DestinationAuthorization.from_mapping(
            {**_authorization(config), "schema_version": "wrong/v9"}
        )
    with pytest.raises(PreflightContractError):
        broken = _authorization(config)
        del broken["expires_at"]
        DestinationAuthorization.from_mapping(broken)
    with pytest.raises(PreflightContractError):
        DestinationAuthorization.from_mapping(
            {**_authorization(config), "expires_at": "2026-08-13T00:00:00Z"}
        )
    with pytest.raises(PreflightContractError):
        DestinationAuthorization.from_mapping(
            {**_authorization(config), "data_classes": []}
        )
