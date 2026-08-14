from __future__ import annotations

import re
from pathlib import Path

from post_training_rsi.__main__ import _parser

ROOT = Path(__file__).resolve().parents[1]
RSI_COMMANDS = {
    "demo",
    "rsi",
    "verify",
    "audit",
    "approvals",
    "review",
}
COEVOLUTION_COMMANDS = {
    "coevolve",
    "coevolve-status",
    "coevolve-audit",
}
SUPPORTED_COMMANDS = RSI_COMMANDS | COEVOLUTION_COMMANDS
REQUIRED_DOCUMENTS = {
    "AGENTS.md",
    "README.md",
    "docs/AGENTS.md",
    "docs/README.md",
    "docs/architecture-manifest.json",
    "docs/implementation-status.md",
    "docs/state-machine.md",
    "docs/rsi-convergence.md",
    "docs/coevolution-convergence.md",
    "docs/coevolution-audit-recovery.md",
    "docs/control-plane-contracts.md",
    "docs/rsi-loop-policy.md",
    "docs/adapter-runtime.md",
    "docs/integration-contracts.md",
    "docs/lineage-runtime.md",
    "docs/hitl-approval.md",
    "docs/harness-outer-loop.md",
    "docs/trace-harvesting.md",
    "docs/model-inner-loop.md",
    "docs/traceability-index.md",
    "docs/stacked-pr-plan.md",
    "docs/architecture.md",
    "docs/productionization.md",
    "src/post_training_rsi/AGENTS.md",
    "tests/AGENTS.md",
}


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _cli_commands() -> set[str]:
    parser = _parser()
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict):
            return set(choices)
    raise AssertionError("CLI parser does not expose subcommand choices")


def _local_markdown_links(text: str) -> set[str]:
    links: set[str] = set()
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", text):
        target = target.split("#", 1)[0].strip()
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        links.add(target)
    return links


def test_supported_cli_and_readme_stay_synchronized() -> None:
    commands = _cli_commands()
    assert commands == SUPPORTED_COMMANDS

    readme = _read("README.md")
    status = _read("docs/implementation-status.md")
    rsi = _read("docs/rsi-convergence.md")
    coevolution = _read("docs/coevolution-convergence.md")
    audit = _read("docs/coevolution-audit-recovery.md")

    for command in SUPPORTED_COMMANDS:
        assert f"`{command}`" in readme
        assert f"`{command}`" in status
    for command in RSI_COMMANDS:
        assert command in rsi
    assert "coevolve" in coevolution
    for command in ("coevolve-status", "coevolve-audit"):
        assert command in audit


def test_required_document_graph_exists() -> None:
    missing = sorted(path for path in REQUIRED_DOCUMENTS if not (ROOT / path).is_file())
    assert not missing, f"required documentation files are missing: {missing}"

    readme_links = _local_markdown_links(_read("README.md"))
    docs_index_links = _local_markdown_links(_read("docs/README.md"))

    for target in readme_links:
        assert (ROOT / target).exists(), f"README contains a broken local link: {target}"
    for target in docs_index_links:
        assert (ROOT / "docs" / target).exists(), (
            f"docs/README contains a broken local link: {target}"
        )


def test_directory_state_ownership_is_indexed() -> None:
    readme = _read("README.md")
    for path in (
        "control_plane/",
        "orchestration/",
        "adapter_runtime/",
        "approval/",
        "verification/",
        "training/",
        "serving/",
        "evaluation/",
        "lineage/",
        "harness/",
        "audit/",
    ):
        assert path in readme

    for module in (
        "orchestration/converged.py",
        "orchestration/rsi_policy.py",
        "orchestration/run_state.py",
        "orchestration/coevolution.py",
    ):
        assert module in readme


def test_traceability_ids_are_unique_and_cover_core_domains() -> None:
    traceability = _read("docs/traceability-index.md")
    ids = re.findall(r"^\|\s*([A-Z][A-Z0-9-]+)\s*\|", traceability, flags=re.MULTILINE)
    ids = [item for item in ids if item not in {"Requirement ID"}]
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    assert not duplicates, f"duplicate traceability IDs: {duplicates}"

    required_prefixes = {
        "PDF-RSI-",
        "PDF-DATA-",
        "PDF-ADP-",
        "PDF-LIN-",
        "PDF-HITL-",
        "PDF-COEV-",
        "OPS-",
        "PROD-",
    }
    for prefix in required_prefixes:
        assert any(item.startswith(prefix) for item in ids), (
            f"traceability index has no requirement for prefix {prefix}"
        )


def test_pr_graph_and_git_town_non_claim_are_explicit() -> None:
    readme = _read("README.md")
    stack = _read("docs/stacked-pr-plan.md")
    root_agents = _read("AGENTS.md")

    for number in range(1, 13):
        marker = f"PR #{number}"
        assert marker in readme or marker in stack

    for text in (readme, stack, root_agents):
        assert "Git Town" in text
        assert "not configured" in text or "disabled" in text


def test_supported_state_machine_documents_failure_and_resume_edges() -> None:
    state = _read("docs/state-machine.md")
    rsi = _read("docs/rsi-convergence.md")
    coevolution = _read("docs/coevolution-convergence.md")
    audit = _read("docs/coevolution-audit-recovery.md")

    for token in (
        "DATA_REVIEW_PENDING",
        "MODEL_REVIEW_PENDING",
        "QUARANTINED",
        "ROLLED_BACK",
        "STOPPED",
        "ABORTED",
        "compare-and-swap",
        "teardown",
        "Resume",
        "HARVEST_TRACES",
        "SLIM_HARNESS",
        "read-only",
    ):
        assert token in state or token in rsi or token in coevolution or token in audit
