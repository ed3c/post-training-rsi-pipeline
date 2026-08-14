# Repository Agent Instructions

<!-- BEGIN SHARED RUNTIME IDENTITY -->
## Shared runtime identity and dual-forge preflight

Canonical contract: `ed3c/skills-shared` → `skills/dual-forge-repository-loop/references/runtime-identity-contract.md` and `SKILL.md`.

Before mutating repository, forge, worktree, CI, issue, PR, or publication state, classify the current runtime from evidence, not model name:

```text
CHATGPT_GITHUB_CONNECTOR
GITHUB_ACTIONS
CLAUDE_CODE_LOCAL
CODEX_CLI_LOCAL
CHATGPT_DESKTOP_WORKTREE
UNKNOWN
```

Precedence: trusted launcher override (`AGENT_RUNTIME`/`AGENT_HOST`) → `GITHUB_ACTIONS=true` plus run identity → observed local checkout/shell/git plus launcher identity → connector-only GitHub capability → `UNKNOWN`.

Hard boundaries:
- ChatGPT GitHub connector is not a GitHub Actions runner and does not prove local shell, checkout, worktree, or Forgejo.
- GitHub Actions is CI evidence on its exact checked-out SHA, not a developer worktree or local-Forgejo authority.
- Claude Code/Codex CLI may claim local execution only after observing the checkout, remotes, branch, and HEAD; Forgejo requires a resolved local binding.
- ChatGPT Desktop requires an actually created Desktop worktree with bound path/branch/HEAD; opening the app or pre-filling a prompt is insufficient.
- `UNKNOWN` fails closed for irreversible delivery actions.
- Runtime identity, model family, and forge authority are separate facts.
- One mutable branch has one writer. Evidence does not transfer across runtime or HEAD changes without rebinding.

Delivery order for dual-forge repos:
`runtime bind → GitHub ingress → local/Forgejo issue + isolated worktree → verified Forgejo PR → local main → re-observe/reconcile GitHub main + relevant PRs/issues → exact-head GitHub Actions → GitHub publication/merge policy`.

Three qualifying failures against the same target trigger the shared fresh-diagnosis/new-worktree escalation; no fourth blind patch.
<!-- END SHARED RUNTIME IDENTITY -->

Read the repository README, architecture, tests, workflows, and nearest local instructions before implementation. Preserve repository-specific evidence and authority boundaries.