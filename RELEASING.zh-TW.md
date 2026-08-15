<!-- i18n-key: RELEASING; locale: zh-TW; reviewed: 2026-08-15 -->
[English](RELEASING.md) · [繁體中文](RELEASING.zh-TW.md)

# 發布 Post-Training RSI Pipeline

Release 是由 Human governance 控制的 Publication event。Test 通過、Pull Request 合併、Signature 或 Artifact upload，都不會單獨授權 Release。

## 前置條件

1. 在受保護的 Release lineage 選定 Exact commit。
2. 確認 Version、Changelog、Compatibility impact、Migration 與 Rollback plan。
3. 執行 Repository gate：

```bash
make lint && make typecheck && make test
```

4. 執行 Release scope 所需的 Domain-specific smoke、Schema、Replay、Packaging 或 External-runtime check。
5. 審查 Dependency 與 License 變更、Generated artifact、Secret、Provenance 與 Security finding。
6. 更新英文與繁體中文公開 Release 文件。
7. 由具 Release authority 的 Maintainer 明確核准。

## 發布

- 只從已核准 Commit 建立 Annotated version tag。
- 在受控制的 Release workflow 從 Tagged source 建置 Artifact。
- Workflow 支援時，記錄 Artifact digest 與 Provenance。
- Release note 必須分離 Implemented behavior、Verified evidence、Known limitation 與 Planned work。
- 不得把 Fixture、Mock、Local、CI、Emulator、Sandbox 或 Production evidence 描述為可以互換。

## 發布後

在乾淨 Environment 驗證 Published artifact、Link、Package metadata 與 Installation instruction。若 Release 不安全或有重大錯誤，停止散布、發布 Advisory，並執行已記錄的 Rollback 或 Replacement process。
