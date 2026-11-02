# 現場演練版本

此工作區使用八位虛構正賽選手及一位虛構候補，提供四個場地 A–D 的八場淘汰與季軍賽。所有候補及棄權設定只供演練；真實名單、既有操作工作區及正式簡章條文均不會被改寫。

乾淨的試用工作區為 `output/live-operator-demo-final`，台北時間 2026-10-29 10:45–18:45，通過 G4。雙擊 `START-LIVE-OPERATOR.cmd` 或執行下方命令啟動。驗收使用的 `output/live-operator-demo-ready` 及其還原子工作區另行保留，已完成叫號、開賽、延誤、替補、核准、局部重排、版本復原與備份還原的瀏覽器操作測試。

```powershell
.venv\Scripts\python.exe -m squash_scheduler serve --workspace output/live-operator-demo-final --port 8768
```

在 `squash-scheduler` 目錄執行：

```powershell
.venv\Scripts\python.exe examples/build_live_operator_demo.py --output output/live-operator-demo-new
.venv\Scripts\python.exe -m squash_scheduler serve --workspace output/live-operator-demo-new --port 8768
```

開啟 <http://127.0.0.1:8768>。建置時間使用台北時區，第一個起賽格點為當下向下取整至 15 分鐘，場地可用時間向後延伸八小時。時間經過不會自動標記開賽，必須由操作員明確執行。

建議用不同工作區演練下列流程，避免前一步開始的比賽阻止替補操作：

1. 看板：填寫操作人與原因，預覽叫號並確認，再預覽開賽並確認；同一場地進行中的比賽會阻止另一場開賽。開賽預覽應在兩分鐘內確認。成績仍由成績頁輸入。
2. 候補與替補：在尚未開賽的第一輪選手中選擇一位，指定「虛構候補09」遞補，檢查影響範圍後確認提案，再核准影響、預覽並確認局部重排。
3. 延誤：對尚未開賽比賽設定延誤分鐘，檢視影響並局部重排；確認前檢查原排程與新排程。
4. 歷程與備份：檢視變更前後差異；下載「賽事資料備份（不含匯出文件）」後上傳，先預覽再還原至新的工作區，再明確切換查看。還原不會覆蓋目前工作區。已有成績、已開賽或鎖定的版本不得直接復原。

預設輸出資料夾只允許建立一次。隔日或已操作後重試，使用新路徑建立獨立案例：

```powershell
.venv\Scripts\python.exe examples/build_live_operator_demo.py --output output/live-operator-demo-2
```

`demo-report.json` 記錄演練日期、開始與結束時間、版本、候補 ID 及驗證結果。資料來源為既有乾淨的 `output/operator-demo-final/junior`；若尚未建立，先執行 `examples/build_operator_demo.py --output output/operator-demo-final`。
