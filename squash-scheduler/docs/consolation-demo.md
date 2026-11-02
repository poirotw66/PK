此案例僅用全虛構八人、合成時段與合成競賽規程，驗證正式 Domain Snapshot 的多輪鼓勵賽。真實賽事條文仍須主辦單位提供；本案例不修改真實資料，也不確認歷史原稿鼓勵賽的敗者來源。

2026-10-28 確認資格後，示範改為國中男子組，八人皆虛構、非種子且無輪空。資格與操作見 [鼓勵賽資格](encouragement-rules.md)。最新成品為 `output/consolation-reviewed-workflow/booklet/index.html`，抽籤審閱頁為 `output/consolation-reviewed-workflow/encouragement-proposal.review.html`。舊示範保留供比對，不代表目前已通過資格／確認流程。

從專案目錄使用既有 Python 環境執行：

```powershell
.venv\Scripts\python.exe examples\build_consolation_demo.py --output output\consolation-demo --font C:\Windows\Fonts\msjh.ttc
```

每次輸出必須使用新目錄，例如 `output/consolation-demo-2`；既有目錄會拒絕覆寫。`--no-documents` 可只驗證 CLI、快照與排程。字型必須是既有的受控本機中文字型，由真 CLI 匯入資產庫。

案例明訂八人主賽七場，主賽準決賽敗者打一場季軍賽。先排主賽並匯入四場合法虛構首輪成績，再由 `encouragement propose` 篩出四名合格敗者、生成隨機籤位及審閱頁，`encouragement apply` 以提案雜湊確認套用，接著重排並保留已完成首輪時間。鼓勵賽兩場準決賽、一場決賽，共十一場。來源為 typed `match_loser` / `match_winner`，不再預先將所有未知敗者寫入賽制。

四場地 A–D，2030-01-01 08:00–18:00 開放，12:00–13:00 停賽。每 15 分鐘起賽格點、25 分鐘預估比賽、5 分鐘場地緩衝、20 分鐘球員休息及換場另加 5 分鐘，全部由合成 config 明訂。

腳本使用真 CLI 建立工作區、匯入並確認虛構名冊、核准合成 format 與 draw、生成賽事、求解、通過 G4/G5 並核准精確合成版本。獨立 `validate_schedule` 再檢查排程，未取代或複製排程驗證器。輸出為：

- `snapshot.json`：正式模型的合成已核准版本，含 typed 籤位、規則、場地與時間。
- `match-audit.json`：每場 typed 入口、依賴、可能球員及每對潛在共用球員賽事的實際休息間隔。
- `independent-validation.json`、`report.json`：獨立驗證結果及 CLI 命令紀錄。
- `first-round.synthetic.json`、`encouragement-proposal.json`、`encouragement-proposal.review.html`：虛構首輪成績與已確認套用的抽籤提案。審閱頁保留當時內容，不代表可再次套用。
- `booklet/booklet.pdf`、`booklet/schedule.xlsx`、`booklet/index.html`：同一合成版本的 reference-v2 PDF、Excel 與前端，含三場鼓勵賽完整兩輪樹。
- `booklet/manifest.json`、`booklet/qa-report.json` 及逐頁圖片：實際文件 QA 與人工視覺檢查材料。

合成版本的技術核准不代表真實賽事核准。文件仍為示範閱讀版本，視覺檢查保留 required，不呼叫 release finalize 或更新 latest-ready。
