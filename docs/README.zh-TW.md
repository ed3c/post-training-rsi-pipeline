<!-- i18n-key: DOCS_INDEX; locale: zh-TW; reviewed: 2026-08-16 -->
[English](README.md) · [繁體中文](README.zh-TW.md)

# Post-Training RSI Pipeline 文件

先閱讀 [專案 README](../README.zh-TW.md)。其中說明支援的 Entrypoint、目前成熟度、Evidence boundary、Quick start 與 Non-goals。

## 技術文件

- [Implementation status](implementation-status.md) — Supported、Component-only、Planned 與 Unverified state。
- [Architecture](architecture.md) — System component 與 Ownership。
- [Machine-readable architecture manifest](architecture-manifest.json) — 供 Agent 與 Test 使用的精確 State Machine、Directory ownership、Validation index、PR graph 與 Non-claims。
- [State Machine](state-machine.md) — RSI state、Guard 與 Evidence。
- [RSI convergence](rsi-convergence.md) — Resume、Promotion、Rejection、Rollback 與 Stop rule。
- [Control-plane contracts](control-plane-contracts.md) — Typed control record 與 Authority。
- [Integration contracts](integration-contracts.md) — Cross-layer integration requirement。
- [Adapter runtime](adapter-runtime.md) — Provider 與 Process lifecycle boundary。
- [Lineage runtime](lineage-runtime.md) — Transaction、Checkpoint bundle 與 Peak continuity。
- [HITL approval](hitl-approval.md) — Immutable human review authority。
- [Provider preflight](provider-preflight.md) — Destination、Credential、Budget 與 Approval admission。
- [Harness outer loop](harness-outer-loop.md) — Harness mutation 與 Evaluation flow。
- [Model inner loop](model-inner-loop.md) — Model update 與 Evaluation flow。
- [Co-Evolution convergence](coevolution-convergence.md) — Bounded Model/Harness convergence。
- [Audit and recovery](coevolution-audit-recovery.md) — Read-only integrity 與 Recovery boundary。

## 專案與社群文件

- [文件語言政策](I18N.zh-TW.md)
- [Open-source readiness checklist](OPEN_SOURCE_CHECKLIST.zh-TW.md)
- [參與貢獻](../CONTRIBUTING.zh-TW.md)
- [安全政策](../SECURITY.zh-TW.md)
- [支援](../SUPPORT.zh-TW.md)
- [治理](../GOVERNANCE.zh-TW.md)
- [Maintainers](../MAINTAINERS.zh-TW.md)
- [行為準則](../CODE_OF_CONDUCT.zh-TW.md)
- [變更紀錄](../CHANGELOG.zh-TW.md)
- [Release process](../RELEASING.zh-TW.md)

## Source of truth 順序

文件不一致時，依下列順序判定：

```text
已合併 Code 與 Repository policy
> 目前 Machine-readable contracts 與 Tests
> 目前 Implementation/status ledger
> Architecture 與 Runbooks
> README summaries
> Issues、Pull Requests 與 Conversational summaries
```

Open Pull Request、Configured workflow、Example、Fixture、Generated report 或 Signed receipt，都不能單獨提升 `main` 的 Implementation 或 Verification state。
