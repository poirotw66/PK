# 鼓勵賽資格

使用者於 2026-10-28 確認：

- 僅國中男子組、國中女子組設鼓勵賽。
- 種子球員不得參加鼓勵賽，即使第一輪落敗亦不具資格。
- 僅籤表第一輪實際出賽落敗的非種子球員具資格。
- 首輪輪空者不得參加，即使下一輪第一次出賽就落敗。
- 棄權、未到場判敗者不得參加。

資格以本次報名組別判斷；不得以兼報其他組別取得資格。受傷中途退賽另列待審，不自行視為一般完賽敗者。

## 簡章匯出前試排

用已確認籤表、已完成主賽排程且尚未登錄成績的工作區試排。工具會在記憶體複本模擬首輪，優先讓非種子選手落敗，以測試該籤表可產生的最多鼓勵賽。預設在各組最後一場首輪比賽結束 15 分鐘後，才允許排入該組鼓勵賽。

```sh
.venv/bin/python scripts/check_encouragement_capacity.py --workspace "/path/to/workspace"
.venv/bin/python scripts/check_encouragement_capacity.py --workspace "/path/to/workspace" --replan-main
```

第一個指令固定主賽時間與場地。若回報 `infeasible`，第二個指令可在複本中一起重排主賽與鼓勵賽，再固定新主賽，重演並檢查鼓勵賽時段。`timeout` 表示在求解時間內未找到解。輸出包含場數、時段與移動的主賽場數，工作區只讀取。

使用 `--seed` 重跑不同的模擬賽果和鼓勵賽抽籤，用 `--confirmation-delay-minutes` 測試延後確認成績。每次結果只代表該情境和工作區的規則，正式成績確認後仍須核對資格與排程。

## 資格報告

資格報告使用原有種子設定與已確認成績，不重抽、不修改賽程，也不直接將「第一輪敗者」全部加入鼓勵賽。種子未確認、首輪尚未結束或來源無法確認時列為待確認。

```powershell
.venv\Scripts\python.exe -m squash_scheduler encouragement report --workspace <賽事工作區> --division <國中組別ID> --json
```

輸出 `eligible_entry_ids` 為符合資格的名單，`pending_entry_ids` 為未定名單，`entries` 含逐人判斷理由。

## 建立與確認抽籤

主賽必須為已有有效賽制／抽籤核准的單淘汰賽，籤表第一輪全部成績須確認。種子名單或首輪結果不明時，先處理資格資料；不推定輪空者或棄權者可參加。

```powershell
.venv\Scripts\python.exe -m squash_scheduler encouragement propose --workspace <賽事工作區> --division <國中組別ID> --seed 20261028 --reason "首輪成績確認後抽籤" --output proposal.json --actor <操作者> --json
```

產生 `proposal.json` 與 `proposal.review.html`，不改動工作區。審閱頁列出姓名、資格判定、對戰與晉級、輪空位置及提案雜湊。相同資格資料與 seed 會重現同一籤位；JSON 與審閱頁均不覆寫既有檔案。

```powershell
.venv\Scripts\python.exe -m squash_scheduler encouragement apply --workspace <賽事工作區> --file proposal.json --hash <proposal_hash> --reason "確認審閱提案" --actor <確認者> --json
.venv\Scripts\python.exe -m squash_scheduler schedule solve --workspace <賽事工作區> --actor <操作者> --json
```

套用會重查目前版本、資格與提案內容，保存確認者、原因及核准證據，再生成鼓勵賽。籤位或資料被修改、提案過期、重複套用時均拒絕，原工作區不變。後續排程依第一輪來源與鼓勵賽晉級關係檢查休息時間；PDF、Excel 與前端輸出前同樣檢查資格／抽籤證據。

| 合格人數 | 處理 |
|---|---|
| 0、1 人 | 記錄不成賽，不產生虛構比賽，不自動授予冠軍或獎項 |
| 2 人 | 一場決賽 |
| 3 人以上 | 隨機排列後以足夠的二次方籤表單淘汰，輪空分配給不同選手，不產生輪空比賽；共 N−1 場 |

尚未開打、沒有任何成績且沒有鎖定的鼓勵賽可重新提案確認；舊抽籤保留為 superseded。重抽只清除原鼓勵賽排程，保留主賽排程。任何鼓勵賽已開打、有結果或已鎖定時，拒絕重抽。

首輪結果更正、種子或資格資料變更後，既有抽籤會失效並阻擋排程／匯出，需重新提案確認。受影響下游已開打時，沿用既有成績更正保護，交由賽務處置。

舊版直接寫入 `format_plan.placement_matches` 的鼓勵賽定義會被攔截；須先透過賽制審核移除，不能將其自動視為已確認抽籤。其他一般名次賽仍支援明確來源 DAG。其餘正式簡章條文維持待補。
