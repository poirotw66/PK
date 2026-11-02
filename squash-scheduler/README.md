# 壁球賽事編排工具

依 `SQUASH-SPEC-001` 建立的本機賽務工具，包含前端操作台、Word 名單匯入、抽籤與排程、成績／排名，以及共用資料產生的 PDF、Excel 與離線閱讀前端。

目前版本更新至 **2026-10-30**：完整名單示範包含 **147 筆報名、114 位選手、17 組、279 場**。最新功能為草案自動保存與恢復、選手負荷／衝突看板，以及從問題場次直接進入批次賽程調整。手機支援清單與明細切換。

即時閱讀頁支援比分、淘汰賽晉級、循環積分與排名；三角、四角與五角對戰圖會在確認成績後填入勝局數，點選即可查看逐局比分。[側邊章節導覽](docs/live-reader.md#側邊章節導覽) 可按組別／階段跳轉、標示閱讀位置，並使用章節網址重新開啟；手機改為可收合導覽。

多小組循環賽提供 [對戰圖總覽](docs/live-reader.md#多小組循環賽總覽)：上方集中全部小組圖形，下方逐組呈現積分、名次與比賽明細，手機可放大圖表。公開資料與操作資料分開，可設定不同的公開 JSON 來源；使用方式見 [即時閱讀頁](docs/live-reader.md)、[積分與特殊賽制驗收](docs/reader-standings.md)，後續範圍見 [待辦規劃](docs/live-spectator-roadmap.md)。

## 先查看成果

在檔案總管開啟本專案的 `squash-scheduler` 資料夾，雙擊 [START-HERE.html](START-HERE.html)，即可查看成果總入口。已產生的 PDF 與閱讀頁可直接開啟；賽務操作台需要啟動本機服務。

| 想查看的成果 | 開啟方式 | 內容 |
|---|---|---|
| 完整名單操作台 | 雙擊 [START-FULL-ROSTER-OPERATOR.cmd](START-FULL-ROSTER-OPERATOR.cmd)，再開啟 <http://127.0.0.1:8766/> | 147 筆報名、114 位選手、17 組、279 場模擬編排 |
| 選手負荷與衝突 | 啟動完整名單服務後開啟 <http://127.0.0.1:8766/#workload> | 每日場數、休息間隔、確定／可能晉級問題，可選取相關場次調整 |
| 批次調整與草案恢復 | 啟動完整名單服務後開啟 <http://127.0.0.1:8766/#schedule> | 多選平移、換場、兩場互換、自動保存／恢復及前後對照 |
| 現場賽務演練 | 雙擊 [START-LIVE-OPERATOR.cmd](START-LIVE-OPERATOR.cmd)，再開啟 <http://127.0.0.1:8768/> | 8 位正賽選手＋1 位候補的獨立虛構演練，場地 A–D |
| 即時比分與晉級圖表 | 啟動上述本機服務後開啟 <http://127.0.0.1:8768/live/> | 與操作台雙視窗連動；每 3 秒檢查版本，確認成績後更新下一輪 |
| 小型賽程調整演練 | 啟動上述服務後開啟 <http://127.0.0.1:8768/#schedule> | 練習草案保存、原／新日期時間場地、衝突比較及確認套用 |
| 種子、成績及鼓勵賽 | 雙擊 [START-OPERATOR.cmd](START-OPERATOR.cmd)，再開啟 <http://127.0.0.1:8765/> | 八人虛構國中組，供練習抽籤、登錄與匯出 |
| 完整名單即時閱讀頁 | 啟動完整名單操作台後開啟 <http://127.0.0.1:8766/live/> | 側邊導覽、多小組對戰圖總覽、38 張積分表及晉級圖；三個單人組保留姓名 |
| 最新 PDF 2.2 與閱讀前端 | [新版簡章入口](output/booklet-v2-2-reviewed/index.html) | 50 頁新編草稿、48 頁原稿重建及 Excel |
| PDF 與原版逐頁比較 | [逐頁對照](output/booklet-v2-2-reviewed/page-comparison/index.html) | 原版全部 36 頁對應新版，含 25 原圖、232 筆時間表及 QR 核對 |
| 完整名單四場地閱讀頁 | [四場地完整 Demo](output/full-demo-four-courts/index.html) | 搜尋姓名、日期／組別／場地篩選、279 場模擬賽程與對戰圖 |
| 原稿資料重建 | [完整原稿前端](output/full-event-demo/index.html) | 25 頁原稿圖、230 個編號場次、2 筆表演賽與完整名冊 |

啟動操作台後請保留命令視窗；關閉視窗或按 Ctrl+C 可停止服務。重新啟動同一工作區會保留已儲存的操作。若網址已能開啟，直接使用即可，不必重複啟動同一連接埠。這些服務僅供本機使用，綁定 `127.0.0.1`。

服務或前端更新後，按 **Ctrl＋F5** 載入新版本；若找到已保存草案，可在「賽程調整」選擇恢復。即時閱讀頁會自動同步已確認的賽況；PDF、Excel 與離線網站則保留匯出當時的版本，修改後須重新匯出。

上述 `output/` 成品與 `.venv/` 保存在目前本機，未納入 Git；只取得程式碼時，須先安裝依賴並建立示範資料。現場示範的建立方式見下節，其餘成品重建見 [新版簡章說明](docs/booklet-v2.md)、[臨場賽務操作](docs/live-operations.md) 與 [現場演練說明](docs/live-operator-demo.md)。

## 賽務操作與負荷檢查

新增 [批次賽程調整](docs/operator-ui.md#人工調整與重排變更預覽)：桌面四場地時間表、手機勾選清單，可一次平移多場、改日期／場地或互換兩場安排，並復原上一步。固定底列提供變更檢查；自動／局部重排也顯示前後對照與已排除、新增、仍存在的衝突。有衝突不能套用，確認後才更新閱讀頁；已套用的對照可在重啟後查看並下載公告文字。草案會自動保存在同一瀏覽器及服務網址，重新載入後可明確選擇恢復；過期版本僅可檢視或丟棄，多分頁修改會停止自動覆寫。此功能沿用工作區既有規則，不增加賽期或放寬已設定的 4 場／60 分鐘限制。

[選手負荷與衝突看板](docs/operator-ui.md#選手負荷與衝突總覽) 位於 <http://127.0.0.1:8766/#workload>，以同一選手跨組合併，列出每日已確定／相容晉級路徑最大場數、休息間隔與問題場次。可切換目前規則與不寫入的 4 場／60 分鐘試算，從問題直接選取多場前往調整。手機提供選手清單與獨立明細。

看板依已儲存版本分析；尚未確認的草案透過「檢查變更」查看衝突改善結果。每日路徑上界已排除互斥的晉級分支，可能問題不表示選手一定會遇到。分析未完成或有未排場次時會明示範圍。

草案保存限同一瀏覽器、服務網址及工作區，保存成功狀態會顯示在賽程頁上方。恢復後仍須預覽並確認；清除瀏覽器資料會移除本機草案。保存不包含復原歷程，也不會同步到其他裝置。詳見 [保存與恢復](docs/operator-ui.md#草案自動保存與恢復)。

選手負荷規則支援全賽事最低休息及每日場數上限（跨組合併），目前要求固定兩天、每天最多 4 場、至少休息 60 分鐘。已依相容資格路徑排除勝／敗者、池內名次及抽位互斥誤加；[最新路徑與試算報告](output/workload-paths-final/index.html) 列出全部 114 位選手的場數界線。30 位選手有超過兩天 8 場額度的路徑，全部已透過既有成績規則重播證實；最高為SyntheticPlayer046的男子 E 組 7 場＋國中男子組 6 場。現有兼報／賽制無法保證所有晉級情境符合上限，尚無可套用的新賽程，原工作區未改。命令與限制見 [負荷稽核說明](docs/live-operations.md#選手每日場數與最低休息)。

| 功能 | 已完成的操作 |
|---|---|
| 草案保存 | 自動保存、明確恢復、離頁提醒、過期／缺失來源只讀、多分頁衝突保護 |
| 選手負荷 | 跨組合併、零場者、每日場數及相容路徑上界、休息問題與精準選場跳轉 |
| 批次調整 | 四場地時間表、多場平移／改日期／換場、兩場互換、復原及衝突前後對照 |
| 場地看板 | 場地 A–D、人工叫號與開賽、實際開賽時間、延誤、場地與選手衝突檢查 |
| 操作歷程 | 時間、操作者、原因、姓名／場次／時間等前後差異；復原會建立新版本並保留歷史 |
| 匯出紀錄與備份 | 重啟後保留匯出紀錄、檔案完整性檢查、ZIP 資料備份、獨立還原與明確切換工作區 |
| 候補與異動 | 登記候補、退賽或替補提案、影響核准、精確預覽與局部重排 |

每次異動先填操作者與原因，再預覽確認。時間經過不會自動開賽；開賽預覽超過兩分鐘須重新產生。已有開賽、成績或鎖定的版本須依成績更正與局部重排流程處理。

備份包含賽事資料、完整版本、來源檔案、受控字型與操作紀錄，**不含 PDF、Excel 及閱讀網站等匯出文件**。還原會建立獨立工作區，不覆寫原資料；需要文件時可重新匯出。詳細步驟見 [本地操作說明](docs/operator-ui.md)。

建議先在 8768 演練：候補替補提案 → 核准影響 → 局部重排 → 看板叫號／開賽 → 檢視歷程 → 備份與還原。先開賽可能使受影響場次無法再替補。

現有現場演練工作區為 `output/live-operator-demo-final`，其原始示範時段為台北時間 **2026-10-29 10:45–18:45**。若要練習當下的人工開賽，請用新資料夾建立案例；先停止原 8768 服務，再啟動新工作區：

```powershell
.venv\Scripts\python.exe examples\build_live_operator_demo.py --output output\live-operator-demo-new
.venv\Scripts\python.exe -m squash_scheduler serve --workspace output\live-operator-demo-new --port 8768
```

輸出資料夾必須尚不存在；再次演練時換一個新名稱。此建置器使用本機既有乾淨國中組資料，來源需求與操作步驟見 [現場演練說明](docs/live-operator-demo.md)。

## PDF、鼓勵賽與完整資料

最新 PDF 採 `reference-v2` 模板 **2.2.0**，統一圖形比例、姓名與場號字級、積分表及留白，保留實際頁碼目錄與書籤。五角圖顯示「勝:2分 敗:1分 棄:0分」。原版 36 頁已有完整逐頁對照；這是依來源資料重新排版，非原稿幾何位置一比一復刻。見 [新版簡章說明](docs/booklet-v2.md)。

鼓勵賽僅限國中男、女組，排除種子、籤表首輪輪空者，以及因棄權或未到場判敗者；須為籤表第一輪實際出賽的敗者。資格、抽籤提案、確認套用與排程已接通。見 [資格與操作](docs/encouragement-rules.md) 及 [8 人 11 場虛構成品](output/consolation-reviewed-workflow/booklet/index.html)。

閱讀前端提供全文搜尋、日期／組別／場地篩選、對戰圖及下載，支援桌機與手機。設計與字型授權見 [前端設計紀錄](docs/frontend-design.md)。現場延後抽位宣告與核定另有 CLI，見 [臨場賽務操作](docs/live-operations.md)。

完全同名跨組視為同一位選手、四個場地名稱已由使用者確認。真實名單模擬中的抽籤、時間與賽制仍為示範假設；正式簡章未提供的條文依使用者決定保留「待補」。目前交付可供本機操作與審閱，尚不代表當屆正式賽程或主辦發布核准。完整證據與待辦見 [實作狀態](docs/implementation-status.md)。

## 安裝與執行

使用 Python 3.13，精確依賴版本在 `uv.lock`。在此目錄執行：

```powershell
uv sync --frozen --extra dev --system-certs --cache-dir .uv-cache
.venv\Scripts\python.exe -m squash_scheduler --help
.venv\Scripts\python.exe examples\run_demo.py --output output\my-demo
```

範例會建立匿名 Word、合成設定及身分修正檔，透過真正的 CLI 執行匯入 → 明確確認身分 → 核准合成賽制 → 抽籤／核准 → 產生場次 → 求解 → 獨立 G4 驗證 → Excel／PDF 草案。輸出目錄必須尚不存在，避免覆寫之前的檔案。只驗證排程可加 `--no-pdf`。

結果位置記錄在 `<輸出目錄>/demo-report.json`，其中 `preview` 指向可直接開啟的賽事前端 `index.html`、列印審查 `review.html`、`schedule.xlsx`、`booklet.pdf`、逐頁 PNG、SVG、`render-model.json`、`qa-report.json` 與 SHA-256 manifest。前端提供搜尋、日期／組別／球場篩選及對戰圖閱讀；不需要伺服器，亦不會修改賽事資料。9 人原型應有 9 場池賽、5 場淘汰主線及 1 場季殿賽，共 15 場；第六場淘汰階段賽事只是合成示範。

原稿風格可設定 `publication.layout_template_id: "reference-v1"`，採 A4 橫式分組多邊形／由下往上淘汰樹，以及 A4 直式每日賽程。舊 `standard-v1` 仍可使用。原稿風格範例可執行：

```powershell
.venv\Scripts\python.exe -X utf8 examples\render_reference_comparison.py --output output\my-layout-test
```

此範例會匯入本機楷體字型，產生匿名 4 頁 PDF、原稿／舊版／新版對照，以及歷史公布賽程的閱讀草案。兩份草案均附可離線開啟的查詢前端。歷史草案保留 230 筆編號場次及 2 筆未編號表演賽，男子 F 圖依原稿印出的線條重繪，抽位保留占位，不核准真實身分或推定球場。

Windows 預設使用已安裝的 Microsoft Edge 無頭列印；其他環境須先安裝 Playwright Chromium：

```powershell
.venv\Scripts\python.exe -m playwright install chromium
```

`.docx` 可直接解析；二進位 `.doc`／RTF 需要另外安裝 LibreOffice。找不到轉檔工具時回傳 `IMPORT_CONVERTER_UNAVAILABLE`，不會把空名單當作成功。

此次原始 Word 已用本機 Microsoft Word 16.0 與既有 pywin32 做唯讀歷史核對轉檔，停用巨集與開啟時連結更新，並驗證原始 SHA-256 不變。該輔助工具是 `scripts/convert_legacy_word.py`；它需要安裝 Word 與 pywin32 的 Python，並非主 CLI 的預設轉檔器。實際附件核對結果與 147 筆待審資料在 `output/historical-inventory/`，尚未核准身分與賽程。

橫排名單可在 `registration.parser_profiles` 設定來源 `source_sha256`、`layout: "horizontal_whitespace"`、`join_single_cjk_pairs` 與 `ignored_paragraphs`。設定只適用該檔案；姓名與原始行、字元範圍一併保留。中文單字配對屬待審候選，英文姓名的單一空白不拆開。解析設定的雜湊會記入批次；變更設定會產生可追溯的新暫存，已套用報名不會自動覆寫。

## 建立自己的草案

複製上層的 `squash-event-config.template.json`，填入已確認的資料。原始模板中的 `null` 與 `unconfigured` 會原樣保留；程式會依操作閘門列出缺項。Q01～Q14 政策不能透過賽事輸入改寫。

```powershell
.venv\Scripts\python.exe -m squash_scheduler init --config event.json --workspace tournaments\new-event --actor organizer --json
.venv\Scripts\python.exe -m squash_scheduler roster import --file roster.docx --workspace tournaments\new-event --json
.venv\Scripts\python.exe -m squash_scheduler review build --workspace tournaments\new-event --json
```

所有變更需 `--actor` 或設定檔的 `event.actor_id`。身分對應檔包含 `base_revision_id`、`batch_id`、`actor_id`、`reason` 及 `resolutions`。每個候選列明確選擇既有 `participant_id` 或 `create_participant`，同名不會自動合併。完整範例由 `run_demo.py` 產生。

一般變更檔包含 `base_revision_id`、`actor_id`、`reason`、`operations`。支援 `set_config`、`set_format`、`set_stage_rules`、`transfer_entry`、`exclude_match`、`withdraw_entry`、`set_lock`、`reschedule_match`、`resolve_division_count` 及 `set_ranking_decision`；先使用 `changes apply --dry-run`。任一操作失敗，整份變更不套用。

場次產生時會保存展開後的比賽／時間規則與繼承來源。規則修正透過設定或階段規則 patch 產生新草案；已開賽場次保留原規則。Excel、PDF 與結果驗證使用固定規則，重新產生不會遺失階段覆寫。

`review build` 回傳各 target 的內容雜湊。`review approve --target TYPE:ID --hash HASH --reason TEXT` 只核准指定內容；賽制、抽籤、排除、兩人組處置、人工排名與整體版本各自審核。重抽必須有理由，舊紀錄保留；舊 patch 或舊核准無法覆蓋新資料。

## 正式文件與交付

賽前文件只需要 G1～G5，不要求決賽先有成績。已知來源的池排名、勝者與敗者參照可保留到未來比賽。

正式輸出須設定完整規則、球場／時間、版型（最新為 `layout_template_id: "reference-v2"`，亦保留 `standard-v1` 與 `reference-v1`）、紙張方向、QR 啟用或停用、贊助提供或不使用，以及受控本機字型。先匯入可嵌入且涵蓋所需文字的字型，回傳的 SHA-256 asset ID 填入 `publication.font_asset_id`：

```powershell
.venv\Scripts\python.exe -m squash_scheduler assets import --kind font --file C:\Windows\Fonts\msjh.ttc --workspace tournaments\new-event --json
```

圖片以 `assets import --kind image` 匯入；贊助圖只能引用受控 `asset_id`。字型／圖片都驗證內容雜湊，HTML/PDF 不請求遠端素材；字型涵蓋、嵌入限制、版本及實際使用的字型識別會被檢查。使用本機字型的權利仍由使用者負責確認。

核准整體 revision 後，`release build --revision ID` 建立完整的 `qa_pending` 本機交付包並回傳 exit 4，保留每頁 PNG 供版面審核；不更新最新 ready。PDF 或 Excel 失敗時整包 failed，既有 ready 不變。

完成逐頁檢視後，建立以下審核 JSON，再執行 `release finalize --file visual-review.json`。`manifest_hash` 是該 manifest 的 canonical SHA-256，可用 `squash_scheduler.domain.models.canonical_hash` 計算；不可使用過期雜湊。所有頁碼與檢查項目都需明確確認：

```json
{
  "release_id": "完整的交付包 UUID",
  "manifest_hash": "manifest 的 canonical SHA-256",
  "actor_id": "organizer",
  "reason": "逐頁檢查結果與必要說明",
  "reviewed_pages": [1, 2, 3],
  "checks": {
    "all_pages": true,
    "glyphs": true,
    "no_clipping": true,
    "no_overlap": true,
    "black_and_white_readable": true
  }
}
```

完成流程會建立新的不可變 ready 包、保留原審核包，並原子更新 `releases/latest-ready.json`。這些操作只產生本機檔案，不會上傳、通知或對外公告。

## 驗證

2026-10-30 草案保存與負荷看板的相關回歸為 **117 項 Python、17 項 Node 測試通過**，Ruff、125 份格式檢查與 52 個來源模組 mypy 通過。本輪未重跑 PDF 全套。

| 驗證範圍 | 證據 |
|---|---|
| 排程、操作台、負荷、版本快取與公開資料回歸 | [117 項測試結果](output/verification/draft-workload/pytest.xml) |
| 草案恢復、過期、多分頁、工作區隔離、正式確認清除與保存失敗重試 | [7 條 Edge 流程、4 張截圖](output/draft-storage-final/report.json) |
| 完整名單負荷、篩選、手機明細、問題跳轉與草案保存 | [9 張操作截圖與報告](output/operator-workload-final/report.json) |
| 多小組總覽、38 小組／211 場循環賽、圖上比分及更正 | [循環賽驗收](output/rr-overview-final/report.json) |
| 側邊章節導覽、返回／前進與章節網址 | [導覽回歸](output/rr-overview-navigation/report.json) |

2026-10-29 循環積分、特殊賽制與完整名單版本的完整驗證為 **552 項通過、2 項跳過**，包含實際 PDF 回歸，開始與結束程式雜湊一致。紀錄在 [前次完整驗證](output/verification/reader-standings-formats/state.json)。[三階段瀏覽器驗收](output/rf-final/report.json) 包含同分裁定與更正、男子 F 抽位啟用、鼓勵賽輪空與種子排除、季軍賽，以及完整 147 筆報名、17 組、279 場、38 張積分表；33 個桌機／平板／手機畫面無水平溢出或 JavaScript 錯誤。

前次即時閱讀頁第一版為 545 項通過、2 項跳過，保留於 [第一版驗證](output/verification/live-reader-first/state.json)。比分、更正、斷線恢復、工作區切換及替換靜態 JSON 來源的證據在 [雙視窗驗收](output/lr-qa/report.json)。

前次四項賽務功能驗證為 531 項通過、2 項跳過，保留於 [前次驗證結果](output/verification/live-operator-four-features-final/state.json)。備份還原、替補核准、局部重排、延誤、版本復原及人工開賽的瀏覽器證據在 `output/live-operator-browser-qa/`。驗證數字均對應各次版本。

需要重新執行完整檢查並保存結果時，使用新的輸出路徑：

```powershell
.venv\Scripts\python.exe scripts\verify.py --output output\verification\my-check --pdf
```

亦可單獨執行：

```powershell
.venv\Scripts\python.exe -m pytest -q
$env:SQUASH_PDF_SMOKE = '1'
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check src tests examples
.venv\Scripts\ruff.exe format --check src tests examples
.venv\Scripts\mypy.exe src
.venv\Scripts\python.exe -m squash_scheduler schema --output schemas
```

開啟 `SQUASH_PDF_SMOKE` 的測試會真正啟動瀏覽器、產生 PDF、檢查逐場列內容、掃描全部頁面與解碼 QR；平常的單元測試不依賴瀏覽器。Windows 沙箱可能限制 pytest 暫存目錄或瀏覽器程序，需在有正常本機暫存與程序權限的終端執行。

效能量測入口為 `examples/benchmark.py`；以量測報告中的實際 solver 狀態、版本、時間及記憶體為準，不把 `UNKNOWN` 說成無解或成功。

程式參照 [Playwright PDF 介面](https://playwright.dev/python/docs/api/class-page#page-pdf) 與 [Pydantic JSON Schema](https://pydantic.dev/docs/validation/latest/api/pydantic/json_schema/)。合成示範使用匿名資料；真實名單驗收使用獨立工作區，並核對原來源內容雜湊。

## 本機產物與清理紀錄

`output/` 同時保存運作中的賽事資料、重建來源、匯出成品與驗收報告，未納入 Git。現在使用的工作區是 `output/operator-demo-final/` 與 `output/live-operator-demo-final/`；`historical-inventory/`、`full-event-demo/`、`full-roster-simulation-delivery/` 仍被部分重建流程使用。

2026-10-30 已清理 **37 個目標、7,905 個檔案，檔案大小合計約 485 MiB**，包含測試／下載快取、一次性修改腳本及已有最終證據取代的舊測試嘗試。[清理摘要](output/cleanup-20261030/summary.json) 記錄結果，[原始清單](output/cleanup-20261030/original-plan.json) 列出清理路徑。工作區與最新 PDF 雜湊核對一致，8766／8768 服務已恢復。

`.venv/`、原始附件、最新成品與最終驗收證據保留。測試工具會按需要重建快取；`.uv-cache/` 已移除，下次安裝依賴時可能重新下載。清理紀錄與既有成品均僅存在目前本機，從 Git 重新取得專案時需依前述流程建立資料。
