<!-- i18n-key: CONTRIBUTING; locale: zh-TW; reviewed: 2026-08-15 -->
[English](CONTRIBUTING.md) · [繁體中文](CONTRIBUTING.zh-TW.md)

# 參與 Post-Training RSI Pipeline

感謝你改善 Post-Training RSI Pipeline。所有貢獻會依正確性、安全性、證據品質、可維護性與文件清晰度進行審查。

## 開始修改前

1. 先搜尋既有 Issues 與 Pull Requests。
2. 非小型修改必須建立或引用 Issue，說明使用者可觀察的成果、受影響的信任邊界、驗收測試與明確不處理事項。
3. 不得將憑證、私有來源、客戶資料、專有 Repository 內容、Production artifact 放入 Issue、Prompt、Fixture、Log 或 Commit。
4. 漏洞請依 [SECURITY.zh-TW.md](SECURITY.zh-TW.md) 回報，不要建立公開 Issue。

## 開發環境

```bash
git clone https://github.com/ed3c/post-training-rsi-pipeline.git
cd post-training-rsi-pipeline
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

送審前執行 Repository gate：

```bash
make lint && make typecheck && make test
```

若受影響的子系統另有 Smoke、Schema、Replay 或 Integration gate，必須依該子系統文件執行精確命令。

## 變更設計

每個 Pull Request 應可獨立審查：

- 一個主要成果與明確 Rollback 邊界；
- 儘可能小的 Path lease；
- 視需要涵蓋成功、拒絕、竄改、逾時與復原路徑的測試；
- Claim 不得強於實際捕捉到的證據；
- 不得自動擴張權限、Network、Secret、Model authority、Release authority 或 Side effect；
- 公開文件的英文與繁體中文版本必須在同一個 Pull Request 更新。

Generated artifact 與 Evidence receipt 必須可重現、有界限、移除秘密，並與 Source code 清楚分離。

## Pull Request 必要內容

說明必須包含：

- 問題與預期成果；
- Scope 與 Non-goals；
- Architecture 或 State Machine 影響；
- Security、Privacy 與 Compatibility 影響；
- 已執行命令與觀察結果；
- Rollback plan；
- 文件與翻譯變更；
- 關聯 Issue。

Implementation 與 Evidence 尚未可審查前，優先使用 Draft Pull Request。Workflow 綠燈只證明該 Workflow，不代表 Production、Security、Model quality 或外部 Runtime 已通過驗證。

## AI 輔助貢獻

可使用 AI 工具協助分析、程式碼、測試或文件。提交者仍對所有內容負責，並且必須：

- 審查完整 Diff；
- 執行所宣告的驗證；
- 在 Pull Request 揭露具實質影響的 AI 協助；
- 防止私有或受限制資料傳送到未授權 Provider；
- 移除捏造引用、不可驗證 Claim 與由 Prompt 產生的虛假 Authority；
- 保留 Repository 專屬 Agent 與 Security contract。

## Commit 與審查紀律

Commit 訊息必須清楚。不得略過 Hook 或 Check。除非有必要且已說明耦合原因，不要把無關格式調整、Generated output、Dependency upgrade 與行為變更混在同一個 PR。

Maintainer 可在 Merge 前要求更小的切片、更強的 Negative control、更清楚的 Evidence 或更窄的 Claim。

## 授權

提交貢獻即表示你同意該貢獻可依本 Repository 的 License 發布，且你有權提交。第三方內容必須附上 License、Provenance 與必要 Notice。
