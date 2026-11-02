# 正式簡章與秩序冊改版

本輪依 `demo1.pdf` 與 `2026中正盃20261023.pdf` 的逐頁對比實作。目標是讓核准的資料能產生可閱讀、可列印的競賽簡章及秩序冊；目前交付仍供主辦審閱，不是正式賽程核准。

## 開啟成品

最新版本為模板 2.2.0。開啟 `output/booklet-v2-2-reviewed/index.html`，集中提供：

- `full-event/representative-pages.pdf`：從完整新版取出的競賽辦法、規則、循環圖、男子 F 預決賽及每日四場地時間表。
- `full-event/booklet/booklet.pdf`：147 筆報名、114 位選手與 279 場四場地模擬的完整新版，含三個未編對戰單人組的名單說明。
- `full-event/booklet/index.html`、`schedule.xlsx`：使用同一份顯示資料的閱讀前端與 Excel。
- `source-samples/booklet.pdf`：48 頁全組原稿重建，涵蓋全部 25 原圖、230 個編號場次、2 筆表演賽、原通知 QR 及完整時間表。這是可編輯資料重新繪圖，不是直接貼入原 PDF 圖片。
- `source-samples/graph-catalog.html`、`graph-catalog.json`：逐場原圖編號、名稱位置、連線與時間表的座標證據，以及對應新 PDF 的實際頁碼；交付狀態以實際產出驗證，不沿用首版 47 場樣張的缺圖清單。
- `baseline.html`：重新驗證原 PDF 的 SHA 與 232 筆文字位置，再逐組列出新舊賽制、場次及來源差異。
- `page-comparison/index.html`：原版全部 36 頁逐頁對照；25 個圖頁依來源頁碼映射、232 筆時間表依原行與新場號映射，通知頁另驗證原 QR 網址。每頁可點圖放大，對照證據在 `page-comparison/mapping.json`。

2.2.0 放大單組循環圖，將積分表移至右下，保留「勝:2分 敗:1分 棄:0分」；姓名採一致的 20 SVG 單位、場號 18 單位，超長名稱沿用參照與完整附註，線寬統一。標題為 30pt，對戰圖可用高度提高至 145mm。多組同頁維持分欄間距，完整圖形總覽已逐頁檢視。這是正式排版整理，並非原稿幾何位置一比一復刻。

新編模擬與原稿樣張各有明確範圍。前者不重新執行抽籤或求解器，保留所有 match ID、對戰關係、固定規則及場地／時間安排；顯示場號依本版時間及場地重新編排，完整對照在 `full-event/source-link.json`。後者只重建原稿已畫出的關係，不將未知的鼓勵賽入籤來源指定為主賽敗者，也不替原稿補入場地 A～D。

## 已完成的版型與內容處理

`publication.layout_template_id = "reference-v2"` 使用黑白 A4 橫式對戰圖及直式前文／每日時間表。新增封面、實際頁碼目錄、未編對戰組別及競賽辦法；同組預賽、決賽依組別設定順序連續排列，不使用 UUID 排序。多頁的同組圖加上分頁序號，池別標示放在標題處，姓名前的數字只供積分表對照，不表示種子或名次。

目錄採兩輪列印：先讀取第一輪 PDF 的真實目的頁，再填入頁碼重印、核對分頁穩定並加入書籤。目錄與書籤均導向目的頁頂端，另存 `navigation-map.json`；QA 重新檢查實際 PDF 的頁碼、連結與書籤，不只信任記錄檔。

循環賽使用多邊形與完整積分紀錄，三個三人池可同頁。單池保留完整英文姓名；短參照放在圖旁，只有過長內容另頁。場號標示會避開姓名與其他場號；空間不足時使用完整積分矩陣及場次對照，不省略比賽。16 人淘汰圖採橫向展開，避免姓名擠在同一條底邊。SVG 檔名補零，使前端順序與文件一致。

相同比賽規則只列一次，保留所有適用組別／階段；不同規則分開列出。公開文件使用中文規則說明，保留「自動處理」等實際語義，不直接列印工程欄位。未知規則不默默省略；`demo_only` 條款保留示範證據及待確認警語。`synthetic_only` 僅標記完全虛構測試資料的來源，不等同未核准；原本合成發布整合測試仍保留。

每日時間表分為場次、預計時間、場地、組別及兩個選手欄。每頁重複日期、星期與表頭，換頁不拆列；正文與儲存格在實際 PDF 中驗證最低字級及內容。

## 填寫正式條款

`publication.document_profile` 有兩個值：

| 值 | 用途 |
|---|---|
| `order_book` | 原稿類型的秩序冊；保留既有專案相容性 |
| `prospectus` | 完整競賽簡章，必須提供並核准正式條款 |

複製 `examples/prospectus-content.template.json`，填寫主辦、協辦、參賽資格、報名、報到、競賽辦法、獎勵、申訴及聯絡資訊。欄位可為一段文字或文字陣列；確認無協辦時明確填「無」。未提供的內容維持 `null`，草稿顯示「待補」，不自行套入歷史或示範條款。

將內容放入賽事設定的 `publication.prospectus`，與原有字型、QR 及贊助設定一併保留。`approval_status` 為條文的主辦確認狀態，不能代替整體快照的內容雜湊核准。

正式 `G5` 及直接文件渲染都拒絕缺漏／未核准的完整簡章，填入「待補」「待確認」「TODO」並標 approved 也不能通過。真實名單的 `demo_only` 假設不能正式發布；新版中尚未轉成完整公開條文的有效規則也必須補齊。

## 重建

在專案根目錄執行，輸出目錄必須尚不存在：

```powershell
.venv\Scripts\python.exe examples\package_booklet_review.py --output output\my-booklet-review
```

帶入已填寫的條款，仍保留原模擬賽程的草稿性質：

```powershell
.venv\Scripts\python.exe examples\package_booklet_review.py --output output\my-booklet-with-terms --prospectus-content my-terms.json
```

單獨建立完整模擬版或歷史樣張：

```powershell
.venv\Scripts\python.exe examples\build_booklet_review.py --output output\my-full-booklet
.venv\Scripts\python.exe examples\render_historical_booklet_samples.py --output output\my-source-samples
.venv\Scripts\python.exe examples\render_historical_booklet_samples.py --all-groups --output output\my-all-source-groups
```

來源預設是已驗證的 `output/full-roster-simulation-delivery/`；此包含 snapshot 與受控字型素材。歷史樣張與逐組比較另外需要原 PDF 及既有歷史來源擷取資料，不會以猜測補替缺檔。

## 驗收與剩餘工作

自動 QA 核對 Excel 與 PDF 每一場、每一欄；新版另外確認每個 match ID 正好出現在一張實際對戰圖中。檢查包含字型載入、字級、溢出、空白頁、頁面邊界、目錄／書籤及 QR 實際解碼。完整回歸證據位於 `output/verification/booklet-v2-1-reviewed/`，以其中當前 `state.json` 與測試紀錄為準。

本輪完整驗證為 388 項測試通過、2 項跳過，Ruff、格式與 38 個來源模組的 mypy 通過，開始及結束的來源雜湊相同。包含真實 Edge／CLI 的正向正式簡章驗收：完整合成條文、G5、精確快照核准、PDF 與目錄全部通過，發布仍停在 `qa_pending` 等待人審，不自動更新 `latest-ready`。

最新交付為 50 頁新編草稿與 48 頁原稿重建。原稿重建保留圖內左右關係，同時以另外的來源欄序投影保留原時間表內容；例如第 25 場圖右葉是SyntheticPlayer057，原時間表卻先列該姓名，兩種呈現均保留，不改動賽事關係。男子 B 的第 195 場確實已列在原圖與時間表；未編號的是 SyntheticAlias007—SyntheticPlayer002圖線，單獨保留且不建立正式場次。先前 10 頁樣張的 195 說明已更正，舊包入口另留勘誤。

新版與原稿前端及交付入口檢查在 `output/booklet-v2-1-browser-qa/`。原稿 25 圖為 31 張分頁圖，再加 1 張獨立表演賽附錄；分池、積分表、國中淘汰樹方向等排版與原 36 頁不同，這是依來源重建，不能宣稱版面一比一復刻。

這些檢查不能代替主辦逐頁及黑白實印驗收。原 PDF 的 36 頁是版面參考，不是固定頁數限制；新增正式條款與場地資訊後，頁數會隨內容調整。

正式完成仍需主辦提供條款、確認當屆各組賽制及特殊抽位、實際場館／開放時間、局數／局分與休息等排程規則，再核准正式資料及全冊版面。原稿 232 筆與新編 279 場不同，尚未宣稱全部歷史籤位、身分及實際抽位執行紀錄已完成重現。
