from __future__ import annotations

import json
from pathlib import Path

from post_training_rsi.__main__ import _parser
from post_training_rsi.control_plane import ControlState, EvidenceKind

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/architecture-manifest.json"


def _load() -> dict[str, object]:
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_architecture_manifest_has_exact_top_level_contract() -> None:
    value = _load()
    assert set(value) == {
        "schema_version",
        "repository",
        "branch",
        "runtime_status",
        "read_order",
        "state_machines",
        "directory_ownership",
        "pr_graph",
        "artifact_index",
        "validation_index",
        "non_claims",
    }
    assert value["schema_version"] == "post-training-rsi.architecture-manifest/v1"
    assert value["repository"] == "ed3c/post-training-rsi-pipeline"
    assert value["branch"] == "main"


def test_manifest_read_order_and_owned_paths_exist() -> None:
    value = _load()
    read_order = value["read_order"]
    assert isinstance(read_order, list)
    assert read_order[0] == "AGENTS.md"
    for path in read_order:
        assert isinstance(path, str)
        assert (ROOT / path).exists(), path

    ownership = value["directory_ownership"]
    assert isinstance(ownership, list)
    assert ownership
    for entry in ownership:
        assert isinstance(entry, dict)
        assert set(entry) == {
            "path",
            "owner",
            "states",
            "inputs",
            "outputs",
            "evidence",
            "must_not_own",
        }
        path = entry["path"]
        assert isinstance(path, str)
        assert (ROOT / path).exists(), path
        assert isinstance(entry["owner"], str) and entry["owner"]
        for field in ("states", "inputs", "outputs", "evidence", "must_not_own"):
            items = entry[field]
            assert isinstance(items, list)
            assert all(isinstance(item, str) and item for item in items)


def test_manifest_states_and_evidence_use_control_plane_vocabulary() -> None:
    value = _load()
    known_states = {item.value for item in ControlState}
    known_evidence = {item.value for item in EvidenceKind}

    machines = value["state_machines"]
    assert isinstance(machines, dict)
    assert set(machines) == {"rsi", "coevolution"}
    for machine in machines.values():
        assert isinstance(machine, dict)
        states = machine["states"]
        assert isinstance(states, list)
        assert set(states) <= known_states
        controller = machine["controller"]
        assert isinstance(controller, str)
        assert (ROOT / controller).is_file()

    ownership = value["directory_ownership"]
    assert isinstance(ownership, list)
    for entry in ownership:
        assert isinstance(entry, dict)
        states = entry["states"]
        evidence = entry["evidence"]
        assert isinstance(states, list)
        assert isinstance(evidence, list)
        assert set(states) <= known_states
        for kind in evidence:
            if kind.endswith(".json") or "*" in kind or "/" in kind:
                continue
            assert kind in known_evidence or kind == "canonical JSON", kind


def test_manifest_supported_commands_match_parser() -> None:
    value = _load()
    runtime_status = value["runtime_status"]
    assert isinstance(runtime_status, dict)
    commands = runtime_status["supported_local_commands"]
    assert isinstance(commands, list)

    parser = _parser()
    registered: set[str] = set()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            registered.update(choices)
    assert set(commands) == registered
    assert runtime_status["deterministic_reference_only"] is True
    assert runtime_status["production_ready"] is False
    assert runtime_status["git_town_configured"] is False


def test_manifest_pr_graph_is_complete_acyclic_and_matches_stack() -> None:
    value = _load()
    graph = value["pr_graph"]
    assert isinstance(graph, list)
    assert [entry["pr"] for entry in graph] == list(range(1, 13))

    by_pr = {entry["pr"]: entry for entry in graph}
    for entry in graph:
        assert isinstance(entry, dict)
        assert set(entry) == {"pr", "branch", "parent_pr", "status"}
        # PR #1-#12 landed on main; the graph records ancestry, not open work.
        assert entry["status"] == "merged"
        parent = entry["parent_pr"]
        if parent is not None:
            assert parent in by_pr
            assert parent < entry["pr"]

    chain: list[int] = []
    current: int | None = 12
    while current is not None:
        assert current not in chain
        chain.append(current)
        current = by_pr[current]["parent_pr"]
    assert chain == [12, 11, 10, 9, 8, 7, 3, 2, 1]

    stack = (ROOT / "docs/stacked-pr-plan.md").read_text(encoding="utf-8")
    for pr in range(7, 13):
        assert f"PR #{pr}" in stack or f"#{pr}" in stack


def test_manifest_validation_files_and_non_claims_are_present() -> None:
    value = _load()
    validation = value["validation_index"]
    assert isinstance(validation, list)
    assert validation
    for path in validation:
        assert isinstance(path, str)
        assert (ROOT / path).is_file(), path

    non_claims = value["non_claims"]
    assert isinstance(non_claims, list)
    assert len(non_claims) >= 8
    assert "production readiness" in non_claims
    assert "Git Town configuration" in non_claims


def test_manifest_is_linked_from_agent_facing_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs_index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
    assert "architecture-manifest.json" in readme
    assert "architecture-manifest.json" in docs_index
