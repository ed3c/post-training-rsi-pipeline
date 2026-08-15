<!-- i18n-key: GOVERNANCE; locale: zh-TW; reviewed: 2026-08-15 -->
[English](GOVERNANCE.md) · [繁體中文](GOVERNANCE.zh-TW.md)

# 治理

## 模式

Post-Training RSI Pipeline 目前採 Maintainer-led governance。

- `@ed3c` 是 Repository owner 與最終 Maintainer。
- Contributor 透過 Issue 與 Pull Request 提案。
- Merge 前會審查 Evidence、Test、Security boundary、Licensing、Compatibility 與 Documentation。
- 除非 Human maintainer 在 Repository-controlled record 明確授權，任何 Agent、Model、Automation、Workflow 或 External reviewer 都不具 Merge、Release、Deployment、Legal 或 Policy authority。

## 決策流程

一般變更透過 Review 與必要 Check 通過後接受。重大決策應記錄：

1. 問題與受影響使用者；
2. Alternatives 與 Tradeoffs；
3. Trust、Privacy、Security、Compatibility 與 Operational effect；
4. 驗收所需 Evidence；
5. Rollback 與 Migration plan；
6. 明確 Non-goals；
7. Human decision owner。

即使技術上正確，若變更擴張 Authority、弱化 Evidence、產生無法負擔的維護成本、違反 License 或偏離專案方向，Maintainer 仍可拒絕。

## 角色

| 角色 | 責任 | Authority |
|---|---|---|
| Repository owner | 方向、Access、Security response、Release 與最終 Merge | 最終 |
| Maintainer | Triage、Review、Release preparation、Policy enforcement | 經記錄的授權 |
| Contributor | Issue、Code、Test、Docs、Evidence、Review feedback | 提案 |
| Automation / Agent | 依 Repository policy 執行有界限的分析或操作 | 無獨立 Governance authority |

## Release

Release 需要明確 Human decision、Versioned source、Documented changes、通過必要 Check，並審查 Security 與 Compatibility impact。Signed artifact 只在其 Policy 範圍內證明 Provenance 或 Integrity，不會獨立證明 Correctness 或 Production fitness。

## 利益衝突

Reviewer 應揭露可能影響決策的個人、Employment、Financial 或 Vendor interest。若有其他合格 Reviewer，涉及利益衝突者應迴避。

## 政策變更

Governance、Security、Licensing、Contribution terms 或 Project trust model 的變更，需要獨立 Pull Request 並清楚說明 Migration impact。
