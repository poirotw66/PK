# 即時閱讀頁與公開資料來源

本機第一版已實作。操作台確認儲存成績後，即時閱讀頁會取得新版本，更新比分、勝方及確定的下一輪選手；資料來源可透過設定檔替換。

循環賽在「晉級圖表」直接顯示三角、四角或五角對戰圖。確認成績後，連線兩端填入各選手的勝局數；點選連線／比分可查看逐局明細。棄權、退賽以文字標示，成績更正後同步更新。手機可在圖內左右捲動，詳見 [對戰圖與驗收](reader-standings.md#循環賽對戰圖上的即時賽果)。

## 多小組循環賽總覽

同一循環賽階段有兩個以上小組時，上方「對戰圖總覽」集中呈現全部小組的圖形及已確認場數，下方才依序列出各小組積分、名次與可展開的比賽明細。對戰圖只出現一次，保留點選連線看比分、成績確認與更正後同步更新的功能。

桌面依可用寬度並排圖形；手機先顯示完整圖形，可點「放大圖表」看較長姓名或比分，再左右滑動。點「縮回總覽」可恢復完整圖形。各圖提供「查看小組明細」，各明細提供「回到對戰圖總覽」；側邊導覽也會列出各小組，連結可複製及重新開啟。

每組明細只列該小組的實際對戰，不混入其他小組。搜尋仍保留符合階段的完整籤表脈絡。單小組循環賽維持圖形、積分與成績的直接閱讀順序。

驗收指令：`.venv\Scripts\python.exe scripts/check_reader_polygons.py --output output/<新的驗證資料夾>`。完整名單包含 38 個小組、211 場循環賽對戰，其中 10 個階段有多小組；總覽順序、逐組場次歸屬、手機縮放及比分更新證據在 `output/rr-overview-final/`，導覽回歸在 `output/rr-overview-navigation/`。

## 側邊章節導覽

桌面左側提供固定的「頁內導覽」，按組別列出目前檢視中的循環賽、淘汰賽等階段；「積分與排名」列出積分區段，「賽程與成績」依目前已展開的賽程日期導覽。單人報名組別也有連結。點選只跳轉閱讀位置，不會改動篩選條件或賽事資料。

捲動時會標示目前章節，桌面側欄會將該連結移入可見範圍；手機與平板改為頂部可收合導覽，選取章節後自動收合。使用「回到頁首」可快速返回搜尋與檢視切換。

章節使用真正的頁內超連結，可複製含 `#reader-...` 的網址、開新分頁或透過瀏覽器上一頁／下一頁返回。重新開啟章節網址會恢復對應的檢視與位置；賽程日期超過最初 40 場時也會展開到該日期。網址不保存組別、搜尋、日期或場地篩選條件。

導覽依目前篩選與實際顯示內容產生，空結果會顯示提示；成績同步時保留既有閱讀位置、側欄捲動及未改變的導覽結構。已確認的勝方與圖表更新規則沿用原流程。

導覽驗收指令：`.venv\Scripts\python.exe scripts/check_reader_navigation.py --output output/<新的驗證資料夾>`。獨立完整名單驗收證據在 `output/reader-navigation-final/`；比分同步回歸另見 `output/live-navigation-sync/`。

## 開啟方式

在 `squash-scheduler` 目錄啟動既有工作區：

```powershell
.venv\Scripts\python.exe -m squash_scheduler serve --workspace output\live-operator-demo-final --port 8768
```

- 操作台：<http://127.0.0.1:8768/>
- 即時閱讀頁：<http://127.0.0.1:8768/live/>

操作台右上角也有「即時閱讀頁」連結，可開啟第二個視窗。首次讀取完整賽況後，每 3 秒檢查版本；版本不變時，不重新下載快照或重畫圖表。手動更新、重新連線及返回頁面時也會嘗試同步。

可先在獨立的 [虛構現場演練](live-operator-demo.md) 測試。輸入或預覽成績尚不影響公開資料；確認儲存後，下一次成功同步才更新。更正成績沿用既有領域規則，受保護的下游場次不會因閱讀頁而放寬限制。

## 架構邊界

```text
賽務操作 → 既有領域驗證與不可變版本
                     ↓
             公開資料投影 PublicSnapshot
                     ↓
      本機公開 API ／ 後續雲端公開 JSON
                     ↓
        HttpJsonSource → LiveReader → 閱讀與圖表
```

公開資料由 `public_feed.py` 明確逐欄建立，沒有呼叫或轉送操作台的 `/api/state`。只提供賽事名稱、公開工作區識別、版本、組別、階段、場地、對戰、已確認結果及人工現場狀態；不包含操作者、異動原因、核准紀錄、來源檔案、內部路徑、備份、匯出工作或操作憑證。

`PublicFeed` 依工作區及原子寫入的 `working/current.json` 檔案識別快取驗證結果。未變更的版本查詢只檢查檔案狀態，不重新讀取歷史或計算晉級。讀取時遇到跨版本變動會重試，錯誤回應不透露內部路徑。

公開籤表須通過既有 G3 檢查。種子或籤表變更尚待核准時，公開資料顯示「待賽務確認」，不把舊籤表繼續當作已確定對戰。`publication_status: ready` 僅代表此處的對戰資料檢查通過，不等同正式文件或主辦發布核准。

## 本機 API 與版本契約

| 路徑 | 用途 |
|---|---|
| `GET /live-config.json` | 閱讀頁資料來源及更新間隔，不含操作憑證 |
| `GET /public/version.json` | 小型版本描述，支援 `ETag` 與 `If-None-Match`；未變更回傳 304 |
| `GET /public/snapshot.json?version=...` | 該版本公開賽況；版本已改變回傳 409，供讀取端重新同步 |

公開讀取不需要 `X-Squash-Token`；操作台 API 繼續要求原有本機保護。服務仍只綁定 `127.0.0.1`，沒有新增對外登入或遠端操作權限。

完整快照契約見 [public-snapshot.schema.json](../schemas/public-snapshot.schema.json)。也可使用 `schema --output <資料夾>` 重新產生。

版本描述包含 `schema_version`、`version`、`workspace_id`、`revision_id`、`updated_at` 與 `snapshot_url`。`version` 是公開快照內容的 SHA-256（計算時排除 `version` 本身），其中包含工作區與修訂；它用於版本識別，不是來源簽章。不同還原工作區即使保留同一修訂 ID，也有不同公開版本鍵。投影格式或內容調整後也會得到新的版本。

閱讀端只接受相容 Schema，並核對完整快照與版本描述的版本、工作區及修訂；不一致時保留上次畫面，下一輪重試。輪詢不重疊，停用後的遲到回應不套用，同一工作區較舊的更新時間也不覆蓋新內容。

## 替換資料來源

閱讀與圖表程式只透過 `live-source.mjs` 的介面讀取資料：

```text
source.load(previousDescriptor, {signal})
  → {descriptor, snapshot}
```

未變更時 `snapshot` 為 `null`。`LiveReader` 負責更新間隔、逾時、版本次序及連線狀態，畫面程式只消費公開資料。請求使用 `credentials: omit`，不攜帶操作台 token。

目前的 `http-json` 來源同時支援本機 API 與靜態 JSON。未來已有雲端公開資料時，建立例如 `reader-config.json`：

```json
{
  "source": {
    "type": "http-json",
    "version_url": "https://scores.example.com/event/version.json"
  },
  "poll_interval_ms": 3000
}
```

再以設定檔啟動：

```powershell
.venv\Scripts\python.exe -m squash_scheduler serve --workspace output\live-operator-demo-final --port 8768 --reader-config reader-config.json
```

設定只改變閱讀頁來源，不修改賽務工作區；不需要改寫閱讀頁或圖表。服務會將設定的來源加入閱讀頁的連線政策。跨來源伺服器也必須設定允許公開讀取的 CORS，以及需要時的 `If-None-Match` 預檢；快照網址須與版本描述位於同一來源，網址不可包含帳密。

雲端資料須遵守相同契約。建議先上傳不可變的完整快照，再更新 `version.json` 指向它，例如 `snapshots/<version>.json`，避免讀取半份更新。此輪未實作雲端上傳、同步憑證或正式部署。

## 畫面與限制

- 淘汰賽以實際勝／敗者依賴連線，確認成績後帶入下一輪並標示勝方與局數；名次賽另列。
- 循環賽提供即時積分表、名次、勝敗棄退與局數；未完賽暫列、同分未裁定保留待核定，確定名次後才標示小組晉級。原稿多邊形圖尚未改為動態繪製。
- 輪空、棄權及未到場不捏造局分；排名與抽位未核定時，保留待確認。鼓勵賽須經既有資格與抽籤確認流程，不會僅因首輪落敗就自動生成。
- 篩選後的晉級圖保留該階段完整脈絡；「賽程與成績」依選定條件顯示場次。
- 自動更新保留搜尋、篩選、頁面及圖表捲動位置。工作區切換會明示新的公開工作區識別。
- 連線失敗時保留最後內容並標示可能過期；恢復後自動同步。
- PDF、Excel 與離線閱讀頁仍是各次匯出的快照，不因即時頁更新而被覆寫。

循環積分、男子 F 抽位、鼓勵賽輪空及完整名單的成果與操作，見 [三階段驗收說明](reader-standings.md)。

## 驗證方式

最新完整回歸為 552 項通過、2 項跳過，含實際 PDF；程式驗證前後雜湊一致。結果見 [完整驗證](../output/verification/reader-standings-formats/state.json)，循環積分、特殊賽制與完整名單見 [三階段驗收](../output/rf-final/report.json)。前次第一版的 545 項通過、2 項跳過與雙視窗／替換來源紀錄，保留於 [第一版驗證](../output/verification/live-reader-first/state.json) 及 [瀏覽器驗收](../output/lr-qa/report.json)。

後端隱私、HTTP 及瀏覽器資料來源模組測試：

```powershell
.venv\Scripts\python.exe -m pytest tests/test_public_feed.py tests/test_public_http.py tests/test_public_reader.py -q
```

實際雙視窗與替換來源驗收（使用新輸出資料夾，建立獨立虛構工作區）：

```powershell
.venv\Scripts\python.exe scripts/check_live_reader.py --output output/lr-check
```

腳本驗證未確認預覽、成績確認與更正、下一輪晉級、篩選及捲動保留、斷線恢復、還原工作區切換及三種螢幕寬度，另啟動只有靜態 JSON 的不同來源，證明同一份閱讀頁與圖表可透過設定直接替換資料來源。報告及截圖保存在指定輸出資料夾。Windows 請選擇較短的專案及輸出路徑，避免還原子工作區中的雜湊字型檔名超過系統路徑長度限制。
