<!-- i18n-key: DOCS_INDEX; locale: en; reviewed: 2026-08-15 -->
[English](README.md) · [繁體中文](README.zh-TW.md)

# Post-Training RSI Pipeline documentation

Start with the [project README](../README.md). It explains the supported entrypoints, current maturity, evidence boundaries, quick start, and non-goals.

## Technical documentation

- [Implementation status](implementation-status.md) — Supported, component-only, planned, and unverified states.
- [Architecture](architecture.md) — System components and ownership.
- [State Machine](state-machine.md) — RSI states, guards, and evidence.
- [RSI convergence](rsi-convergence.md) — Resume, promotion, rejection, rollback, and stop rules.
- [Control-plane contracts](control-plane-contracts.md) — Typed control records and authority.
- [Integration contracts](integration-contracts.md) — Cross-layer integration requirements.
- [Adapter runtime](adapter-runtime.md) — Provider and process lifecycle boundaries.
- [Lineage runtime](lineage-runtime.md) — Transactions, checkpoint bundles, and Peak continuity.
- [HITL approval](hitl-approval.md) — Immutable human review authority.
- [Provider preflight](provider-preflight.md) — Destination, credential, budget, and approval admission.
- [Harness outer loop](harness-outer-loop.md) — Harness mutation and evaluation flow.
- [Model inner loop](model-inner-loop.md) — Model update and evaluation flow.
- [Co-Evolution convergence](coevolution-convergence.md) — Bounded Model/Harness convergence.
- [Audit and recovery](coevolution-audit-recovery.md) — Read-only integrity and recovery boundary.

## Project and community documentation

- [Documentation language policy](I18N.md)
- [Open-source readiness checklist](OPEN_SOURCE_CHECKLIST.md)
- [Contributing](../CONTRIBUTING.md)
- [Security](../SECURITY.md)
- [Support](../SUPPORT.md)
- [Governance](../GOVERNANCE.md)
- [Maintainers](../MAINTAINERS.md)
- [Code of Conduct](../CODE_OF_CONDUCT.md)
- [Changelog](../CHANGELOG.md)
- [Release process](../RELEASING.md)

## Source-of-truth order

When documents disagree, use this order:

```text
merged code and repository policy
> current machine-readable contracts and tests
> current implementation/status ledger
> architecture and runbooks
> README summaries
> Issues, Pull Requests, and conversational summaries
```

An open Pull Request, configured workflow, example, fixture, generated report, or signed receipt cannot upgrade the implementation or verification state of `main` by itself.
