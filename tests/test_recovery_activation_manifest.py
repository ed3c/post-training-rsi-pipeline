from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "docs" / "recovery-activation-manifest.json"

EXPECTED_FIELDS = {
    "schema_version",
    "record_type",
    "pr",
    "branch",
    "parent_pr",
    "parent_branch",
    "status",
    "dependencies",
    "owned_paths",
    "commands",
    "state_machine",
    "invariants",
    "artifacts",
    "evidence_boundary",
    "rollback_subject",
    "human_owned_operations",
    "git_town",
}


def _manifest() -> dict[str, object]:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_recovery_activation_manifest_has_exact_identity_and_fields() -> None:
    manifest = _manifest()

    assert set(manifest) == EXPECTED_FIELDS
    assert manifest["schema_version"] == (
        "post-training-rsi.recovery-activation-slice/v1"
    )
    assert manifest["record_type"] == "recovery_activation_planning_slice"
    assert manifest["pr"] == 14
    assert manifest["branch"] == "feat/recovery-activation-plan"
    assert manifest["parent_pr"] == 13
    assert manifest["parent_branch"] == "feat/forensic-recovery-bundle"
    assert manifest["status"] == "DRAFT_IMPLEMENTED_COMPONENT"


def test_recovery_activation_owned_paths_exist() -> None:
    manifest = _manifest()
    owned_paths = manifest["owned_paths"]
    assert isinstance(owned_paths, list)
    assert owned_paths

    missing: list[str] = []
    for item in owned_paths:
        assert isinstance(item, str)
        assert not item.startswith("/")
        assert ".." not in Path(item).parts
        if not (ROOT / item).exists():
            missing.append(item)
    assert not missing, f"recovery activation owned paths are missing: {missing}"


def test_recovery_activation_state_machine_stops_before_execution() -> None:
    manifest = _manifest()
    transitions = manifest["state_machine"]
    assert isinstance(transitions, list)
    assert transitions

    states: set[str] = set()
    normalized: list[tuple[str, str]] = []
    for transition in transitions:
        assert isinstance(transition, dict)
        assert set(transition) == {"from", "to"}
        source = transition["from"]
        target = transition["to"]
        assert isinstance(source, str)
        assert isinstance(target, str)
        states.update({source, target})
        normalized.append((source, target))

    assert normalized[0] == ("REQUEST_RECEIVED", "AUTHORITY_BOUND")
    assert normalized[-1] == (
        "PREFLIGHT_VERIFIED",
        "READY_FOR_HUMAN_EXECUTION",
    )
    forbidden = {"ACTIVATE", "APPLY", "SWITCH", "RESUME", "ACTIVE"}
    assert states.isdisjoint(forbidden)


def test_recovery_activation_commands_and_git_town_fail_closed() -> None:
    manifest = _manifest()
    commands = manifest["commands"]
    assert commands == ["build", "verify", "preflight"]
    assert set(commands).isdisjoint(
        {"activate", "apply", "switch", "resume", "rollback"}
    )

    git_town = manifest["git_town"]
    assert isinstance(git_town, dict)
    assert set(git_town) == {"enabled", "reason"}
    assert git_town["enabled"] is False
    assert isinstance(git_town["reason"], str)
    assert git_town["reason"]


def test_recovery_activation_document_declares_non_execution_boundary() -> None:
    document = (ROOT / "docs" / "recovery-activation-plan.md").read_text(
        encoding="utf-8"
    )
    required_fragments = {
        "READY_FOR_HUMAN_EXECUTION",
        '"executed": false',
        "There is deliberately no `ACTIVATE`",
        "The CLI has no `activate` command",
        "Git Town remains unconfigured and fail closed",
        "does not establish",
    }
    missing = sorted(fragment for fragment in required_fragments if fragment not in document)
    assert not missing, f"recovery activation document is missing: {missing}"
