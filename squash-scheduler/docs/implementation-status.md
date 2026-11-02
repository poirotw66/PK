# 實作與驗收狀態

依據 `SQUASH-SPEC-001`（2026-10-27）開始實作。此文件區分已驗證的匿名工程案例與尚未取得證據的真實賽事資料；不代表主辦已核准實際賽程。

最新 PDF 為 `reference-v2` 模板 2.2.0，統一圖形比例、姓名／場號字級、積分表位置與留白；`output/booklet-v2-2-reviewed/` 提供 50 頁新編模擬版與 48 頁原稿重建，另有原版全部 36 頁逐頁對照，依 25 原圖、232 筆時間表及 QR 網址核對。這不是幾何位置一比一復刻，也不將 279 場新編模擬當作原公布賽程。其餘正式條文依使用者決定維持待補。

## 目前可執行的流程

2026-10-30 完成草案自動保存與選手負荷看板。草案按瀏覽器／服務網址／工作區保存最少安排、原因與來源版本，重新載入後明確恢復；過期、缺失來源與鎖定不相容草案只讀，多分頁停止自動覆寫，正式確認清除相應草案。負荷看板跨組合併身分、保留零場者，區分已確定與相容晉級路徑問題，提供不寫入的 4 場／60 分鐘試算，問題可精準選場轉入批次調整；手機採清單／明細切換。分析按工作區、版本、內容與政策快取，版本變動拒絕舊報告。

本輪 117 項 Python 回歸、17 項 Node 測試通過；Ruff、125 份格式與 52 模組 mypy 通過。Edge 草案驗收 7 條流程／4 張截圖在 `output/draft-storage-final/`；完整名單看板與批次草案串接 9 張截圖在 `output/operator-workload-final/`，原來源雜湊均不變。完整名單 114 人／279 場試算，目前列出 75 位問題選手、54 項已確定及 211 項可能問題；這是已排場次負荷結果，不等同先前跨全部賽果的兩天不可行性反例。新版看板 inspect 為 0 FAIL、0 WARN，見專案根目錄 `.taste-skill/draft-workload-ready/`。本輪未重跑 PDF 全套，前次 PDF 證據繼續保留。

2026-10-30 多小組循環賽改為先顯示全部對戰圖總覽，再列逐組積分、名次及可展開的賽果；單小組保留直接閱讀順序。圖形收緊留白、放大姓名；手機預設完整圖形，另可放大及左右捲動。總覽／明細雙向連結、小組側欄章節、圖上比分與更正同步皆已驗證。完整名單 38 小組、211 場循環賽對戰及 10 個多小組階段驗收通過，原來源雜湊未變；13 張截圖與比分證據在 `output/rr-overview-final/`，側邊導覽回歸在 `output/rr-overview-navigation/`。Node 12 項檢查通過；inspect 為 0 FAIL、1 WARN（未有賽果的圖表未顯示資料強調色）。

2026-10-30 即時閱讀頁新增側邊章節導覽：桌面按組別／階段跳轉與追蹤閱讀位置，手機／平板可收合；支援章節網址、返回／前進、重新載入、篩選空結果與跨分頁的賽程日期連結。輪詢保留閱讀位置與未改變的導覽結構。完整名單獨立複本驗收通過，5 張截圖、零 JavaScript 錯誤、零寫入請求、原來源雜湊不變，見 `output/reader-navigation-final/`；既有比分／晉級、更正、斷線重連與替換來源的瀏覽器回歸亦通過，見 `output/live-navigation-sync/`。另有 14 項 Python 及 12 項 Node 檢查通過。版面 inspect 為 0 FAIL、1 WARN（未有賽果的圖表未顯示資料強調色）。操作見 [側邊章節導覽](live-reader.md#側邊章節導覽)。

2026-10-30 完成以多場調整為主的「賽程調整」頁：桌面四場地時間表、手機清單，多選平移、批次改日期／場地、兩場互換、自動加入草案與最近 30 步復原，固定底列檢查變更。`manual_schedule` 與自動／局部重排共用前後對照、已排除／新增／仍存在的衝突及公告下載；有衝突不能套用，確認後才原子儲存並更新閱讀頁。時間、鎖定、已開賽、每日負荷與既定賽期規則繼續生效，原示範來源未變。草案保存已由後續增量支援（見本節開頭），原批次調整驗證證據繼續保留。

本輪 98 項相關回歸測試、Ruff、119 份格式及 51 模組 mypy 通過，見 `output/verification/schedule-ux/pytest.xml`。瀏覽器批次、手機、衝突阻擋、原子交換、版本過期、公告及即時同步驗證通過，15 張截圖及來源雜湊證據在 `output/schedule-ux-final/`。完整名單桌面／平板／手機 inspect 為 0 FAIL、1 WARN（資料色彩偵測未辨識青色時間文字；待套用變更另有色條），見專案根目錄 `.taste-skill/schedule-ux-final/`。操作見 [人工調整](operator-ui.md#人工調整與重排變更預覽)。

最新增量將負荷求解、G4 與稽核改為相容資格路徑，勝／敗支線、同池不同名次及延後抽位不再對同一人重複累加。固定兩天、每天 4 場／60 分鐘的完整名單試算仍為 `INFEASIBLE`：30 位選手存在超過總額度 8 場的合法結果情境，全部已在記憶體複本經既有成績／排名規則重播，最高 13 場（男子 E 組 7＋國中男子組 6）。報告涵蓋所有 114 位選手，未排對戰者仍列出零場，單人組兼報其他組別者仍合併計算；賽期、名單、賽制及原工作區不變，尚無符合條件的新排程。證據在 `output/workload-paths-final/`；125 項相關測試、Ruff、139 份格式與 50 模組 mypy 通過，詳細範圍見 [負荷稽核](live-operations.md#選手每日場數與最低休息)。前次 94 項負荷驗證保留於 `output/verification/player-workload/`；循環多角形即時比分已交付於 `b2267a4`。下方保留前次完整 PDF 驗證證據。

本輪已依序完成循環賽即時積分、特殊賽制連動及完整名單獨立驗收。公開資料增加可選的積分組與輪空資訊，舊 JSON 來源仍可使用；同分待核定、人工裁定及成績更正失效均沿用領域規則。男子 F 的虛構實際抽位須核准啟用，鼓勵賽顯示三人輪空並排除種子，季軍賽接準決賽敗者。完整 147 筆報名、17 組、279 場與 38 張積分表（144 組內參賽資料＋3 個單人組）已驗證。33 個瀏覽器畫面無溢出或 JavaScript 錯誤，來源工作區雜湊未變；證據位於 `output/rf-final/`，操作見 [三階段說明](reader-standings.md)。

最新完整回歸 `output/verification/reader-standings-formats/` 為 552 項通過、2 項跳過，Ruff、132 份格式檢查、47 個來源模組 mypy 與實際 PDF 通過，驗證開始／結束程式雜湊一致。循環積分頁官方 taste-skill lint／inspect 為 0 FAIL、0 WARN。雲端同步、正式部署、未定政策與歷史簽核維持後續範圍。

即時閱讀頁第一版新增 `/live/`、獨立公開 JSON 契約及唯讀 API，操作資料與公開資料以明確欄位清單分離；`serve --reader-config` 可替換本機或靜態 JSON 來源，圖表不需重寫。每 3 秒檢查版本，已實測確認成績與合法更正後的淘汰賽晉級、未確認預覽不更新、篩選／捲動保留、斷線恢復及同修訂不同工作區切換。另一個只提供靜態 JSON 的來源已透過設定直接呈現相同閱讀頁。前次瀏覽器證據位於 `output/lr-qa/`，完整回歸證據位於 `output/verification/live-reader-first/`，後續見 [即時閱讀頁待辦](live-spectator-roadmap.md)。

前次即時閱讀第一版驗證 `output/verification/live-reader-first/`：545 項通過、2 項跳過，Ruff、128 份檔案格式、47 個來源模組 mypy 與實際 PDF 回歸均通過，開始與結束程式雜湊一致。閱讀頁的雙視窗、替換靜態來源與工作區切換證據在 `output/lr-qa/`，既有操作台加入入口後也通過三種寬度的唯讀檢查。官方 taste-skill lint／inspect 為 0 FAIL、0 WARN。

前次完整驗證 `output/verification/live-operator-four-features-final/`：531 項通過、2 項跳過，Ruff、123 份檔案格式與 mypy 均通過，包含實際 PDF 回歸，驗證開始與結束程式雜湊一致。乾淨試用工作區 `output/live-operator-demo-final`（8 位正賽、1 位候補、場地 A–D，2026-10-29 10:45–18:45）另通過桌機／平板／手機唯讀瀏覽器檢查，未寫入成績或現場事件，詳見 `output/live-operator-final-readonly-qa/`。

2026-10-29 新增四項本地賽務功能：場地 A–D 人工叫號／開賽／延誤看板、操作歷程及前後差異與安全復原、持久化匯出紀錄及資料 ZIP 備份／獨立還原、候補退賽替補與核准後局部重排。入口 `START-LIVE-OPERATOR.cmd`，說明見 `docs/operator-ui.md` 與 `docs/live-operator-demo.md`。原始真實資料與正式條文待補狀態保持原樣。瀏覽器已實際驗證四個新頁面於 1440／768／390 三種寬度、備份下載上傳與切換工作區、替補核准及局部重排、叫號與輪詢保留輸入；另補測延誤儲存、局部重排、版本復原及人工開賽，證據在 `output/live-operator-browser-qa/`。本輪完整回歸證據另存 `output/verification/live-operator-four-features-final/`，下方舊驗證數字保留供追溯。

本輪全套驗證 `output/verification/operator-and-booklet-approved-checks/`：480 項通過、2 項跳過，Ruff、格式與 44 個來源模組 mypy 通過，開始／結束雜湊一致。之後補正示範重建的字型資產來源及舊版未編場號顯示相容，另通過 18 項重點回歸、2 項跳過，並重建兩份工作區核對受控字型；追蹤於同目錄 `follow-up.json`。實際瀏覽器流程已完成種子→主賽籤表→四場首輪成績→種子敗者排除→三人鼓勵賽輪空→排程→匯出。完整原名單也已從操作頁匯出 58 頁模板 2.2.0 草稿，文件 QA 通過，並在瀏覽器 UTC 時區確認仍顯示賽事 Asia/Taipei 時間；詳見 `output/operator-final-browser-qa/`。獨立簡章比較包使用其原有新編資料及短場號，為 50 頁；不能將兩套模擬來源的頁數混為相同輸出。

本地操作頁已支援種子設定、主賽抽籤確認、成績登錄／更正、鼓勵賽資格與排除原因、抽籤套用、排程及背景匯出。每次寫入先預覽，再核對精確版本確認；過期預覽、跨來源寫入與未完成匯出均有攔截。八人虛構工作區與 147 筆原名單示範分開保存於 `output/operator-demo-final/`，雙擊兩份 `START-*-OPERATOR.cmd` 即可啟動。操作見 `docs/operator-ui.md`。

2026-10-28 使用者確認鼓勵賽資格：僅國中男、女組，非種子且籤表第一輪實際出賽落敗；輪空、棄權與未到場者不得參加。現已完成 `encouragement report / propose / apply`：逐人資格報告、隨機籤位與明列輪空、離線審閱頁、精確雜湊確認及原子套用。處理 0／1 人不成賽、2 人決賽與奇數輪空；重抽保護已開打、有成績或鎖定的場次。生成、排程與文件匯出共用資格及抽籤證據檢查，手動預寫鼓勵賽來源會被攔截。最新 7 頁虛構示範在 `output/consolation-reviewed-workflow/booklet/`，已通過真 CLI、G4/G5、獨立排程與文件 QA；審閱頁桌機／手機無橫向溢出。操作與遷移限制見 `docs/encouragement-rules.md`。

一般多輪名次賽仍支援明確勝／敗者來源、同組跨階段依賴、循環與外組引用拒絕、重生及鎖定保護、四場地排程與完整樹圖。舊 `output/consolation-demo-fresh/` 與 `output/consolation-junior-rule/` 保留作為先前工程證據，不代表通過目前完整資格／抽籤確認流程。真實賽事的種子、成績與實際抽籤尚須依本流程確認，其餘正式簡章條文維持待補。

Python 3.13／`uv.lock`、Pydantic 契約與 Schema、Word 暫存匯入、明確身分對應、轉組、賽制／抽籤核准、對戰圖、CP-SAT 排程、成績與排名、不可變版本、Excel／PDF 共用資料及本機交付包均已有實作。

`examples/run_demo.py` 以真正 CLI 跑匿名 9 人男子 F 原型：9 場池賽＋5 場淘汰＋1 場季殿賽。`examples/run_formal_demo.py` 另驗證完整合成規則、本機中文字型嵌入、QR、G5、核准 revision 及 `qa_pending` 包；它不代替主辦核准，也不自動完成逐頁審核。

## 里程碑

| 里程碑 | 目前證據 | 未完成／限制 |
|---|---|---|
| M0 資料契約 | `domain/models.py`、`domain/config.py`、`schemas/`、`test_storage_review.py` | 部分輔助實體仍以受控 JSON 紀錄保存，尚非全部獨立 Pydantic 實體 |
| M1 名單 | Word 147 筆、17 組已完整帶入；使用者確認完全同名跨組為同一人，形成 114 位選手。原始文字、來源位置與報名資格分別保留，不合併不同寫法 | 同名不同人的例外可再修正；原 `.doc` 已由 Word 唯讀轉換並核對雜湊，主 CLI 的 LibreOffice 路徑仍需本機安裝 |
| M2 男子 F 原型 | `test_formats_draws.py`、兩份 `tests/fixtures/synthetic/`、實際 DRAFT SVG／HTML／Excel／PDF；實際 PDF 第 35 頁確認第 221 場為男子 F 季殿賽 | PDF 第 9 頁已確認小組第一抽 ①③⑥；實際抽位執行、記錄與時點仍待核對 |
| M3 排程審核 | `test_scheduling.py`、`test_solver_hints.py`、`test_cli_pipeline.py`：候選選手、跨組、緩衝、午休、休息、依賴、鎖定、局部範圍、UNKNOWN、獨立驗證；300 人／500 場／8 球場／3 天實測回 FEASIBLE，獨立驗證通過 | 保守候選模型；實測為 300 場循環與 200 場表演賽的直接參賽方案例，不能推論所有晉級圖的 SLA。首輪 UNKNOWN 報告仍保留 |
| M4 文件與閱讀前端 | 25 原圖已依逐場座標及連線證據全部轉成可編輯的來源模型，230 編號場與 2 表演賽完整保留。另有 279 場四場地模擬版、共用 Excel／PDF／前端、實際頁碼目錄及書籤；完整合成條文的正式流程已驗證 | 來源模型不是已核准參賽 Snapshot，未知身份／抽位不推定；新編版不是原籤位。真實條文、賽事參數及主辦視覺／實印驗收仍待完成 |
| M5 擴充與臨場變更 | 候補、退賽／替補提案、精確影響核定、結果保留及局部重排已接前端與 CLI；新增人工現場看板、操作歷程、安全復原與備份還原。延後抽位接上候選、依賴、核分與抽籤時間、核定執行及安全綁定；退出者不晉級，跨組資格獨立處理 | 延後抽位宣告與核定仍透過 CLI。替補不得重寫已開打／有結果的受影響場次；雙方退出、未定對手或鎖定抽位的異動需另行賽務處置。資料備份不含匯出文件 |
| M6 歷史驗收 | 三份附件已取得；Word 17 組 147 筆、Excel 28 表／0 公式、PDF 36 頁。25 圖與 230 號完整對應，循環配對／淘汰連線有實際座標證據，232 筆時間表保留原順序與欄序。共同數字場號有 26 筆 Excel／PDF 差異（8 筆人員、19 筆時間、交集 114） | 實際身份／別名差異、抽位執行、鼓勵獎入口對應、球場及主辦簽核仍待完成；重建是重新排版，非原版面一比一復刻。Excel 另有 98A／114A／129A，已另外保留 |

## AT-01～AT-34 對照

下表指向具體測試檔。測試案例覆蓋不代表相應真實賽事已驗收。

| 驗收項目 | 證據／範圍 |
|---|---|
| AT-01～07 | `test_roster.py`：Word 重匯、明確身分、轉檔不可用、人數門檻、方向／資格邊界及兼報轉組 |
| AT-08～09 | `test_formats_draws.py`：5 人 10 場、核准排除保留 9 場且重生不補回 |
| AT-10 | `test_rankings.py`、`test_review_decisions.py`：三種統計、人工完整名次、理由及目前 basis |
| AT-11～14 | `test_formats_draws.py`：15 場合成原型、6 人淘汰 5 場、重現抽籤、搜尋上限與硬限制 |
| AT-15～23 | `test_scheduling.py`：格點／占用／緩衝／休息、候選、午休、季殿依賴、表演賽、鎖定及各求解狀態 |
| AT-24～25 | `test_rankings.py`：三方同分人工處理、明確棄權積分／分母、不捏造比分；多方同分即使提供文字規則仍需人工核定 |
| AT-26 | `test_storage_review.py`、`test_review_decisions.py`：QR 更動內容雜湊、新 draft、不覆寫 approved、精確 revision ID 與 report hash |
| AT-27 | `test_documents.py`、`test_release_finalize.py`：PDF 失敗、完整包複製、過期／不完整審核拒絕、latest-ready 保留 |
| AT-28 | `test_documents.py` 與合成完整流程：決賽勝敗者參照可在賽前輸出，不需要未來結果 |
| AT-29 | `test_documents.py`、`test_assets.py`：長文字換行、同名短識別、HTML／Excel 注入防護、字型涵蓋與缺字阻擋 |
| AT-30～31 | `SQUASH_PDF_SMOKE=1`：真正 Edge PDF、每場每欄對照、跨頁表頭／列、逐頁渲染、QR 解碼、本機字型查驗；主辦視覺驗收仍需另做 |
| AT-32 | 新賽事拒用 historical_unresolved；SyntheticPlayer058實際原因未查明，尚無完整歷史結案證據 |
| AT-33 | `test_storage_review.py`、`test_review_decisions.py`：舊 patch、寫入鎖、過期核准及內容／稽核欄位損壞拒絕 |
| AT-34 | `test_rankings.py`：已有下游開賽時阻擋自動結果更正，保存舊結果 |

`test_rule_snapshots.py` 另外驗證重新生成保留階段規則、開賽後固定規則、首次結果使用已固定規則，以及設定變更只影響新草案。`test_gates_and_issue_display.py` 驗證 G6 不依賴 G5、未確認成績不通過晉級閘門，且已解決問題留在紀錄表而不再列為待處理。

## 操作邊界與下一步

1. 新賽事實際日期、球場、開放時段、規則、抽位、字型與發布素材仍需由主辦提供並核准；模板不會自行填成歷史或合成值。
2. 已完成可核定的候補、退賽／遞補與現場抽位 CLI；已開打、既有結果、鎖定、未決資格與雙方皆缺席等仍有人工審核界線。
3. 歷史驗收仍需特殊籤位實際執行／時點、未編賽原因及主辦逐頁驗收。完全同名跨組身分已依使用者確認合併，不同寫法仍保留差異，不推定別名。
4. 修改後應執行 README 的 lint、format、mypy 與相關 pytest。完整檢查可將 JUnit 與 stdout 保存在 `output/verification/`，以檔案內容為準，不以過去口頭通過數取代當前結果。

首輪工程交付可供本機試跑與審查；完整規格與真實歷史重現尚未全部完成。

## 已交付入口與續作邊界

直接開啟 `START-HERE.html`：完整原稿資料位於 `output/full-event-demo/`（25 圖、232 筆公布賽程）；四場地新編模擬位於 `output/full-demo-four-courts/`（147 筆報名、114 位選手、17 組、279 場及可操作 `tournament/` 工作區）。

最新完整驗證為 `output/verification/encouragement-reviewed-workflow/`，Ruff、格式、41 個來源模組的 mypy 與包含實際 PDF／瀏覽器的 468 項 pytest 均通過，另 2 項跳過，開始／結束程式雜湊相同。鼓勵賽另有完整 CLI 成品、資格／抽籤確認證據、獨立休息間隔稽核及桌機／手機審閱頁檢查，見 `output/consolation-reviewed-workflow/`。新版前端及交付包驗證見 `output/booklet-v2-1-browser-qa/`；原有驗證仍保留。50 頁新編草稿、48 頁全組原稿重建及逐組差異集中在 `output/booklet-v2-1-reviewed/`，五角圖積分更新在 `output/booklet-pentagon-scoring/`；其餘正式條文依使用者選擇保留待補。

操作與重建說明見 `docs/live-operations.md`。四場地名稱及完全同名跨組身分已確認；08:00–22:00 開放、10 分鐘比賽＋5 分鐘緩衝、15 分鐘休息、賽制與新抽籤均為 DEMO 假設，列於 `assumptions.json`。

本輪依使用者明確要求補齊資料、完整 demo 與新賽務流程；成果仍是可審查的本機版本，不代替正式規則、原籤位執行紀錄或發布核准。

## 效能實測證據

`output/benchmark-hinted/fair-result/report.json`：16 邏輯 CPU、約 64 GiB RAM、Python 3.13.13、OR-Tools 9.15.6755，單 solver worker。300 人、500 場（300 循環＋200 表演）、8 面球場、3 個日期。FEASIBLE 且 G4／獨立 validator 通過；求解含建模／驗證約 14.55 秒，完整匯入／驗證／HTML＋Excel 渲染約 38.12 秒，程序峰值 working set 約 458 MiB。本次未量測 500 場 PDF。初始解僅提供提示，實際 CP-SAT 狀態與獨立驗證決定是否提交；`test_solver_hints.py` 確認 UNKNOWN 不會被提示偽裝為成功。
