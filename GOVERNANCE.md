<!-- i18n-key: GOVERNANCE; locale: en; reviewed: 2026-08-15 -->
[English](GOVERNANCE.md) · [繁體中文](GOVERNANCE.zh-TW.md)

# Governance

## Model

Post-Training RSI Pipeline currently uses a maintainer-led governance model.

- `@ed3c` is the repository owner and final maintainer.
- Contributors propose changes through Issues and Pull Requests.
- Evidence, tests, security boundaries, licensing, compatibility, and documentation are reviewed before merge.
- No Agent, model, automation, workflow, or external reviewer has merge, release, deployment, legal, or policy authority unless a human maintainer explicitly delegates that authority in a repository-controlled record.

## Decision process

Routine changes are accepted through review and passing required checks. Material decisions should record:

1. the problem and affected users;
2. alternatives and tradeoffs;
3. trust, privacy, security, compatibility, and operational effects;
4. evidence required for acceptance;
5. rollback and migration plan;
6. explicit non-goals;
7. the human decision owner.

Maintainers may reject a technically correct change when it expands authority, weakens evidence, creates unsustainable maintenance cost, violates licensing, or conflicts with the project direction.

## Roles

| Role | Responsibilities | Authority |
|---|---|---|
| Repository owner | Direction, access, security response, release and final merge | Final |
| Maintainer | Triage, review, release preparation, policy enforcement | Delegated and recorded |
| Contributor | Issues, code, tests, docs, evidence, review feedback | Proposal |
| Automation / Agent | Bounded analysis or execution under repository policy | No independent governance authority |

## Releases

A release requires an explicit human decision, versioned source, documented changes, passing required checks, and review of security and compatibility impact. A signed artifact proves provenance or integrity only within its stated policy; it does not independently prove correctness or production fitness.

## Conflicts of interest

Reviewers should disclose personal, employment, financial, or vendor interests that could affect a decision. A conflicted reviewer should recuse when another qualified reviewer is available.

## Policy changes

Changes to governance, security, licensing, contribution terms, or the project trust model require a dedicated Pull Request and clear migration impact.
