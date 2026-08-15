<!-- i18n-key: OSS_CHECKLIST; locale: zh-TW; reviewed: 2026-08-15 -->
[English](OPEN_SOURCE_CHECKLIST.md) · [繁體中文](OPEN_SOURCE_CHECKLIST.zh-TW.md)

# Open-source readiness checklist

此 Checklist 定義 Post-Training RSI Pipeline 的 Public repository baseline。文件存在只代表 Policy 已建立，不代表每個 External runtime、Release lane 或 Production control 都已實際執行。

## Project identity

- [x] `README.md` 清楚說明 Purpose、Audience、Status、Quick start、Architecture、Limitations 與 Non-goals
- [x] 英文與繁體中文 Public landing pages
- [x] 封裝型專案具有 Machine-readable package metadata
- [x] 記錄 Repository owner 與 Maintainers
- [x] 記錄 License status 與 Third-party content boundary

## Community health

- [x] `CONTRIBUTING.md`
- [x] `CODE_OF_CONDUCT.md`
- [x] `SUPPORT.md`
- [x] `GOVERNANCE.md`
- [x] Issue 與 Pull Request guidance
- [x] AI-assisted contribution accountability

## Security 與 Privacy

- [x] Private vulnerability reporting route
- [x] Supported-version policy
- [x] Secret 與 Private-data handling rules
- [x] Fail-closed evidence 與 Claim boundary
- [x] Docs index 連結 Project-specific threat 或 Trust documentation

## Engineering quality

- [x] 可重複的 Local validation command
- [x] CI entrypoint
- [x] Tests 與 Static checks
- [x] Exact implementation/evidence status 與 Roadmap 分離
- [x] Generated artifact 與 Provenance 不取代 Authoritative source
- [ ] 每個 Published artifact 都有 Reproducible release provenance — 啟用 Release channel 時必須完成
- [ ] 每個 Release artifact 發布 SBOM — 發布 Package release 時必須完成

## Documentation

- [x] Documentation index
- [x] State Machine 或 Architecture ownership documentation
- [x] English／Traditional Chinese language policy
- [x] CI 對 Maintained translation pairs 進行 Structural validation
- [x] Executable Agent contract 與 Immutable evidence 具有 Controlled exception
- [ ] 由第二位流利 Reviewer 進行 Semantic translation review — Stable release 前建議完成

## Release 與 Operations

- [x] Changelog policy
- [x] Release procedure
- [x] Human release authority
- [x] Rollback expectations
- [ ] Stable compatibility 與 Deprecation policy — 專案達到 Stable API release 前暫緩
- [ ] Production support commitment — 本 Open-source baseline 不提供

## Review rule

不得只因 File 或 Workflow 存在就勾選。只有當所述 Policy 或 Mechanism 可使用，且 Limitations 已清楚揭露時才能勾選。
