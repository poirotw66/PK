# 完整 Demo 與臨場賽務操作

## 兩份完整資料

| 入口 | 內容 |
|---|---|
| `output/full-event-demo/index.html` | 原稿閱讀版：147 筆報名、114 個姓名、17 組、232 筆公布賽程及全部 25 頁原稿對戰圖。提供原 PDF、Excel 及來源差異報告。歷史球場未載，不推定原始分配。 |
| `output/full-demo-four-courts/index.html` | 真實名單模擬版：完整 147 筆報名與 114 位選手，重新示範抽籤、產生 279 場並排入場地A～D；G4 與獨立排程檢查通過。 |

使用者已確認「完全同名跨組視為同一位選手」及四個場地名稱。其他時間與賽制前提記於模擬版 `assumptions.json`：兩個原日期 08:00–22:00、比賽 10 分鐘＋緩衝 5 分鐘、選手休息 15 分鐘。這些是 DEMO 假設，並非已核定的正式規則。男子A、女子A、女子B均只有一筆報名，保留組別與資格，不虛構對手。

模擬版的可操作工作區為 `output/full-demo-four-courts/tournament`。前端提供閱讀與查詢；寫入、核定與重新排程目前透過 CLI。

## 先查資格與狀態

以下命令在 `squash-scheduler` 目錄執行。`<entry_id>` 等尖括號內容要換成實際回傳值。

```powershell
$ws = 'output\full-demo-four-courts\tournament'
.venv\Scripts\python.exe -m squash_scheduler entries list --workspace $ws --json
.venv\Scripts\python.exe -m squash_scheduler deferred-draw list --workspace $ws --json
.venv\Scripts\python.exe -m squash_scheduler validate --gate G4 --workspace $ws --json
```

## 退賽、遞補與重新排程

先輸出只讀的影響提案；此步不更動賽事。

```powershell
.venv\Scripts\python.exe -m squash_scheduler entries propose --entry <entry_id> --kind withdraw --reason 'DEMO 測試退賽，非選手實際退賽' --output output\withdraw-proposal.json --workspace $ws --actor Justin-demo --json
```

遞補候選需是已有來源、身分已確認、同組且尚未排入對戰的資格。可將這筆未使用資格記為候補，再提出遞補：

```powershell
.venv\Scripts\python.exe -m squash_scheduler entries reserve --entry <reserve_entry_id> --reason '已確認且尚未排入的候補資格' --workspace $ws --actor Justin-demo --json
.venv\Scripts\python.exe -m squash_scheduler entries propose --entry <outgoing_entry_id> --kind replace --replacement <reserve_entry_id> --reason 'DEMO 測試遞補' --output output\replace-proposal.json --workspace $ws --actor Justin-demo --json
```

套用後回傳 `change_id`、`approval_hash` 及受影響場次；核定的是此份精確提案與變更，不是籠統同意任何後續修改。

```powershell
.venv\Scripts\python.exe -m squash_scheduler entries apply --file output\withdraw-proposal.json --workspace $ws --actor Justin-demo --json
.venv\Scripts\python.exe -m squash_scheduler review approve --target entry_change:<change_id> --hash <approval_hash> --reason '已核對受影響場次及退賽處置' --workspace $ws --actor Justin-demo --json
.venv\Scripts\python.exe -m squash_scheduler schedule replan --change <change_id> --time-limit 30 --workspace $ws --actor Justin-demo --json
.venv\Scripts\python.exe -m squash_scheduler export preview --workspace $ws --actor Justin-demo --json
```

退賽依明確棄權規則記錄 walkover，不產生假局分。既有結果、場次 ID 與場號保留。單組退出不會退出該選手其他組別的資格。遞補不得重寫受影響的已開打／已記錄結果場次，亦不得違反對手或抽籤鎖；已參加其他組別的選手可以作為本組候補，重新排程仍檢查同一人的跨組休息與重疊。

遇到尚無法確定棄權對手、雙方都退出或未完成的新抽位宣告，系統要求另行賽務審核，不會自行決定晉級者。排名保留退出者歷史積分；池排名晉級資格排除已退出資格，但不略過尚未解決的同分。

## 現場抽位

流程為：宣告完整候選與抽位程序 → 核定 → 綁定場次 → 在來源成績／名次確認後執行 → 核定執行證據 → 啟用結果。

```powershell
.venv\Scripts\python.exe -m squash_scheduler deferred-draw propose --file declaration.json --reason '宣告現場抽位程序' --workspace $ws --actor Justin-demo --json
.venv\Scripts\python.exe -m squash_scheduler review approve --target deferred_draw:<draw_id> --hash <approval_hash> --reason '已核對候選、時點及位置限制' --workspace $ws --actor Justin-demo --json
.venv\Scripts\python.exe -m squash_scheduler deferred-draw bind --file bindings.json --reason '綁定核定抽位到場次' --workspace $ws --actor Justin-demo --json
.venv\Scripts\python.exe -m squash_scheduler deferred-draw execute --file execution.json --reason '記錄現場簽名抽位單' --workspace $ws --actor Justin-demo --json
.venv\Scripts\python.exe -m squash_scheduler review approve --target deferred_draw:<execution_id> --hash <execution_approval_hash> --reason '已核對現場抽位證據' --workspace $ws --actor Justin-demo --json
.venv\Scripts\python.exe -m squash_scheduler deferred-draw activate --execution <execution_id> --reason '啟用核定抽位' --workspace $ws --actor Justin-demo --json
```

宣告 JSON 欄位：`division_id`、`slots`、`procedure`、`allowed_positions`。每個 slot 有 `slot_id` 及完整 `candidate_sides`，來源只能是 `entry`、`pool_rank`、`match_winner`、`match_loser`。`procedure` 包含描述、`execution_trigger: "after_sources_confirmed"`、`duration_minutes`、帶時區的 `earliest_at`（或明確 null）。

位置限制可明確設為 `{"mode":"disabled","constraints":[]}`；有特殊抽位時設 `mode: "provided"`，每筆限制列出 `candidate_side` 與容許的 `slot_ids`。例如小組第一僅可抽指定位置，需要逐一寫出各池第一的限制，不從原稿文字自動代填。

`bindings.json` 包含 `draw_run_id` 與 `bindings: [{match_id, side: "side_a" 或 "side_b", slot_id}]`。`execution.json` 包含原宣告 `draw_run_id`、逐一指定的 `assignments: [{slot_id, entry_id}]`、帶時區的完成時間 `executed_at` 及 `evidence`。未解抽位先以完整候選集合保守排程，加入核分與抽籤時間；核定啟用後才縮小候選。

## 重建成果

```powershell
.venv\Scripts\python.exe -X utf8 examples\build_full_event_demo.py --output output\my-published-demo
.venv\Scripts\python.exe -X utf8 examples\run_full_roster_demo.py --output output\my-roster-demo --time-limit 60
```

第二個命令需要真正求解；僅在 FEASIBLE／OPTIMAL 且獨立驗證通過時匯出成功草案。不得把 UNKNOWN 或 INFEASIBLE 當成已排好。`package_full_roster_demo.py` 可將已驗證的模擬成果建立成最新閱讀介面與 CLI 工作區，不必重算舊成果，也不覆寫既有版本。

## 選手每日場數與最低休息

使用者於 2026-10-29 確認：同一位選手跨組合計每天最多 **4 場**，前場打完至下一場開打至少 **60 分鐘**。新設定為全賽事 `player_workload`：

```json
{"minimum_rest_minutes": 60, "max_matches_per_day": 4}
```

同一人以 participant 身分合併，日期依賽事時區計算。每日上限依排定場次保守預留，包含各組正賽及名次賽；輪空、已取消、已排除場次不占額度，棄權不自動釋出排定額度。休息從 `play_end_at` 算起，場地緩衝不替代休息；跨場另加換場時間。全賽事休息底線不會被舊場次快照或較短的組別設定降低，較長的個人／組別休息規則仍有效。跨午夜不會重設休息要求。

排程器、獨立 G4 驗證及現場開賽共用此政策。延誤跨日後，現場每日額度改按已有實際開賽紀錄的日期核對；缺少紀錄時使用原排定日期。完成時間含秒時向上取整，避免不足 60 分鐘被視為足額。尚未設定此欄位的舊工作區保留原行為，不會被默默改成新規則。

先做唯讀稽核，不改工作區或建立新版本：

```powershell
.venv\Scripts\python.exe -m squash_scheduler schedule audit --workspace output/operator-demo-final/full-roster --minimum-rest 60 --max-matches-per-day 4 --output output/my-workload-audit.json --json
```

有違規時退出碼為 3，仍會產生完整報告。報告保留「已確認位置的排定場數」及「原始候選上界」，另增加每人每日 `maximum_path_count`、路徑場號與全賽事 `path_analysis`。原始候選仍含互斥分支，不能拿來當實際場數。設定全賽事負荷政策後，求解與獨立驗證均按相容資格路徑計數；未設定政策的舊工作區保留原先保守策略。

同一場的勝者／敗者共享同一個勝敗決定，同一池的第 1／2 名共享同一排名決定，現場抽位的一位選手只能占一個籤位；抽位邊線也必須可延伸成完整合法排列。已確認結果、正式排名及已啟用抽位會縮小路徑。每日上限對每一條相容路徑都成立，休息只檢查能由同一人同時參加的兩場，不替排程挑選有利勝者。尚未核定的池內名次仍保留可能性，這不是預測未來局分。分析超過資源上限或遇到不支援的結構時明確失敗，不能截斷路徑後宣稱安全。

重建完整名單的稽核、CSV 及不寫入的求解試算：

```powershell
.venv\Scripts\python.exe examples/audit_full_player_workload.py --output output/my-workload-report
```

最新 [相容路徑報告](../output/workload-paths-final/index.html) 核對 147 筆報名、114 位選手及 279 場，未排對戰者仍列出零場，單人組兼報其他組別者仍合併計算。已確認對手場次的舊排程最短休息 15 分鐘、單日最多 7 場，33 筆選手／日期超過 4 場，21 對相鄰已確認場次休息不足 60 分鐘。前次只用原始候選的報告保留於 `output/workload-4x60-final/`。

排除互斥分支後，仍有 30 位選手存在超過兩天總額度 8 場的路徑。最高為SyntheticPlayer046：男子 E 組 7 場＋國中男子組 6 場，合計 13 場。不是宣稱選手一定打滿；但要保證所有合法晉級情境都符合每天 4 場，目前兼報／賽制不成立。30 人的具體反例都在記憶體複本輸入假想成績，通過既有成績／排名規則，確認確實進入列出的場次；重播結果不會套用到正式資料。未來若來源已有成績或抽位尚未執行，重播工具會明示無法證實的範圍，不假裝通過。

固定原有兩天的重新求解回報 `INFEASIBLE`，可由具體路徑總數超過額度的必要條件判定，不是求解超時。沒有放寬規則、增加日期、改兼報／賽制或覆寫原賽程，來源雜湊維持相同。現有閱讀頁仍呈現原模擬排程，尚不能宣稱已符合新標準。報告提供 `paths.csv`、逐日 CSV、`qualification-paths.json`、重排診斷及可展開的場號明細。

要在可寫的演練工作區真正重排並保存政策，可用 `schedule solve --minimum-rest 60 --max-matches-per-day 4`，另帶既有 `--workspace`、`--actor`。只有求解及獨立驗證成功才寫入新版本；失敗保留原政策及場次，也不自行解除鎖定。尚未新增前端政策設定表單。

本次 125 項相關 pytest 通過，包含獨立枚舉四人／八人淘汰賽所有勝敗、同池名次互斥、男子 F 一人一籤位及啟用後縮小、決賽／季軍同時排於不同場地、全路徑額度保障、真實成績規則重播及原有跨組／鎖定／不覆寫邊界；結果在 `output/verification/qualification-paths/pytest.xml`。Ruff、139 份格式及 50 個來源模組 mypy 通過。報告已在 1440／768／390px 檢查 114 位選手、30 筆反例及 13 場明細，沒有整頁水平溢出。這是排程功能增量驗證，沒有重跑完整 PDF 回歸。
