<!-- i18n-key: SECURITY; locale: zh-TW; reviewed: 2026-08-15 -->
[English](SECURITY.md) · [繁體中文](SECURITY.zh-TW.md)

# 安全政策

## 支援版本

Post-Training RSI Pipeline 目前屬於 pre-1.0 Research 或 Alpha software。安全修正套用於最新 `main`，以及 Maintainer 明確發布時的最新 Release。除非 Maintainer 另行聲明，舊 Commit、Fork、Experimental branch、Fixture 與 Archived evidence 不在支援範圍內。

## 漏洞回報

**不要**建立包含 Exploit 細節、Credential、Private data 或可執行 Proof of concept 的公開 Issue。

請使用 GitHub Private vulnerability reporting：

```text
https://github.com/ed3c/post-training-rsi-pipeline/security/advisories/new
```

若 Private reporting 尚未啟用，建立標題為 `Security contact request` 的公開 Issue，但不得包含漏洞細節。Maintainer 會建立私下聯絡管道。

請提供：

- 受影響 Version、Commit、Component 與 Configuration；
- 實際 Impact 與必要前提；
- 已移除秘密的最小 Reproduction 或 Evidence bundle；
- 問題是否跨越 Permission、Identity、Provenance、Sandbox、Approval、Network、Data 或 Release boundary；
- 已知時提供 Mitigation 建議。

## 安全範圍

Security report 包含 Dataset／Checkpoint substitution、Approval bypass、Parent／Peak lineage corruption、Provider destination 或 Credential leakage、Command-adapter injection、Artifact-hash confusion、Unsafe recovery、Budget bypass，以及任何未經宣告 Human authority 就 Promotion 或 Publish candidate 的路徑。

以下項目一律視為安全敏感：

- Command construction 與 Subprocess boundary；
- Path normalization、Symlink、Archive extraction 與 Workspace ownership；
- Credential、Token、Model provider、Network 與 Egress handling；
- Immutable identity、Digest、Signature、Approval、Replay 與 Lineage；
- Output、Time、Retry、Memory、Artifact 與 Cost budget；
- Release workflow、Dependency provenance 與 Generated evidence；
- 任何可能讓使用者授予超出實作能力之 Authority 的 Claim。

## 揭露與修正

Maintainer 會驗證報告、確認受影響邊界，並協調修正與揭露。修正完成或雙方同意揭露日期前，不要公開細節。

修正必須包含 Regression coverage，且不得靜默弱化 Fail-closed control。Security advisory 只描述已觀察到的 Scope 與 Limitations，不代表其他 Configuration 已被證明安全。

## 安全研究

歡迎不侵犯隱私、不造成服務中斷、資料毀損、Persistence、Credential access 或第三方 Targeting 的善意研究。若接觸到真實 Secret 或 Private data，請立即停止測試並回報。

## Secret 與私有資料

不得 Commit 或附加 Live secret。Credential 外洩後應撤銷，不應只依賴刪除。Public Git history、Actions log、Cache、Artifact、Package registry 與外部 Model provider 都必須視為 Disclosure surface。
