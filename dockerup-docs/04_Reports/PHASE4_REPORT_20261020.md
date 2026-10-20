# 階段 4 完成報告:帳號、產品辨認庫、網頁上傳 Log(含 AI 上傳助理)

日期:2026-10-20 ・ 驗證環境:本機 `wifi-local` 堆疊(http://10.88.90.136:8000)・ **未部署到正式機**

設計依據:`UPLOADER_MERGE_STRATEGY_20261020.md`(所有決策已確認)。本報告說明做了什麼、怎麼用、測了什麼、部署前要準備什麼。

---

## 1. 交付內容

### 1.1 帳號與權限(取代原本的 PostgreSQL 帳密登入與 `ADMIN_TOKEN`)

| 角色 | 來源 | 能做什麼 |
|---|---|---|
| **管理者** | 伺服器環境變數 `TWEAK_USER` / `TWEAK_PASS`(**預設 `qc` / `qcpass`,與 PostgreSQL 帳密相同,和原本 DB Tweak 的登入一致**) | 資料庫維護(瀏覽/刪除)、帳號管理、產品辨認庫(新增/停用/備註)、上傳 Log(看得到所有人的批次並可撤銷)、`/api/ingest` |
| **上傳者** | 管理者在「帳號管理」頁建立(存在 `app_users`,密碼 PBKDF2-SHA256) | 上傳 Log(只看得到、只能撤銷自己的批次)、新增產品、修改自己的密碼 |

- 登入一次,四個管理頁共用(`POST /api/auth/login` → session token,8 小時;只存 SHA-256,放在瀏覽器 sessionStorage,關分頁即登出)。
- 登入/登出/失敗紀錄在 `auth_log`;帳號變更、產品變更、**資料庫維護的刪除**都寫 `account_audit`(帳號管理頁「變更紀錄」可查)。
- 帳號不可刪除、只能停用(停用立即登出);管理者可重設上傳者密碼。
- 側邊欄「帳號管理」只在管理者登入時顯示;上傳者直接打網址會看到「僅限管理者」。

### 1.2 產品辨認庫(`product_registry`)

- 由現有 `test_record.product_model` 自動建立 **11 筆**(migration 一次性 seed)。
- 比對規則(§3.4):`TN73_PICO_IMX6`、`tn-73_pico-imx6`、`TN-175_PICO-IMX8M_MINI`、全形字、只打編號 `86` → 都對應到正式名稱;
  **TN 編號已屬於其他產品的名稱一律擋下(R16)** 並列出該產品與相近產品一鍵改選(例:`TN-86_PICO-IMX6` → 建議 `TN-86_PICO-IMX7`;截斷的 `TN-99_EDM1`、`TN-99_` 同樣擋下)。
- 新產品(格式正確、TN 編號沒用過):上傳者與管理者都可直接建立(上傳時勾選 R4 警告確認,或在「產品名稱」頁新增)。**若該批被撤銷且該產品已無任何資料,辨認庫的那筆一併移除。**
- 「產品名稱」頁:清單(台數、工單數、最後測試、來源、備註)、**檢查名稱**(即時顯示一個名稱會對應成什麼)、新增;管理者可停用/啟用、改備註。停用的產品不出現在選單,歷史資料不動。
- 選擇清單由資料庫提供:`GET /api/products/registry`(表單下拉、AI 對話的可點選按鈕)。

### 1.3 上傳 Log(`Management → Log Upload`)

流程與桌面版 `wifi_stress_log_analyzer.py` 的 Database Upload 對齊:**`log_parser.ingest_file()` 逐檔解析、產品/工單以使用者輸入為準(不從路徑推斷)、`INSERT … ON CONFLICT (file_hash) DO NOTHING`(重複一律略過、不覆蓋)、每檔結果 inserted / duplicate / error / cancelled**。差異:每 50 檔 commit 一次(桌面版每檔),結果清單存在伺服器(`upload_log_item`)可下載與桌面版相同欄位的 CSV / TXT(`Index, Filename, Status, Message`)。

| 步驟 | 內容 |
|---|---|
| ① 選擇 log | 三種輸入:**桌面版交付 zip**(工單資料夾 + 內層產品 zip,巢狀 ≤ 2 層)、**直接一包 log 的 zip**、**選資料夾**(瀏覽器以每批 ≤ 500 檔 / 50 MB 分批上傳,伺服器端展開資料夾內的 zip)。安全上限:單次 512 MB、解壓 ≤ 1 GB、≤ 20,000 檔、壓縮比 ≤ 50、拒絕 `..`/絕對路徑/符號連結。非 log 檔(CSV、報表、不符檔名格式)列入 ℹ 忽略清單,不算錯誤。顯示:log 數、不重複 SN、PASS/FAIL、已在資料庫、測試期間、**由檔名推斷的工單/產品(只用來提示與交叉檢查)**。 |
| ② 確認三項 | **AI 助理**(預設,LLM 在線時)或 **表單**,同一套伺服器規則 R1–R16。三項缺一不可:產品(辨認庫)、工單號(`^(\d{4}\|NOWO)-YYMMDD\d{3}$` + 日曆檢查 + 流水號 ≠ 000;**輸入 `NOWO` 自動補成 `NOWO-<今日>001/002…`**,全域鎖內取號)、計畫測試數量(1–99,999;> 10,000 警告)。❌ 不能上傳;⚠ 需勾「我已確認」;ℹ 只顯示。**數量對齊卡**:計畫 / 本批不重複 SN / 上傳後工單累計 / 差異(達成、尚缺、超出只用醒目色,不擋)。 |
| ③ 上傳 | NDJSON 進度串流(每 50 檔一筆)、**取消**(在下一個 50 檔邊界停止,已寫入的保留並可撤銷)、結果四格(新增/重複/錯誤/取消)、NUL 清除統計、重複策略說明、結果清單 CSV/TXT 下載、**撤銷此批**(只刪本批 `upload_id` 的資料、還原前一個計畫數、必要時移除新產品)。上傳完成立即 `REFRESH mv_unit`,看板與篩選清單馬上看得到。 |
| 紀錄 | 最近上傳(管理者可依上傳者篩選;帳號管理頁「看紀錄」直接跳轉):時間、工單、產品(新產品標記)、計畫數、來源、新增/重複/錯誤、狀態、上傳者、CSV、撤銷。 |

**AI 上傳助理的防呆做法(§3.3–3.4)**
- 問句全部來自伺服器模板(產品 → 工單號(附「沒有工單號請輸入 NOWO」)→ 計畫數量),LLM 不負責記格式。
- 擷取用 json_schema 結構化輸出:`product` 欄位是辨認庫 enum + `__NEW__`;**伺服器逐字核對**:工單號先用 regex 從使用者原文取,LLM 給的值必須是原文子字串;產品以伺服器從原文讀到的 TN 字串為準(所以 `TN-86_PICO-IMX6` 這種打錯會被 R16 擋下,LLM 無法「順手」改成 IMX7);數量只接受原文出現的數字,文字換算(「四千台」)需使用者確認。
- 口語「沒有工單」不會靜默當成 NOWO,助理會請使用者輸入 `NOWO` 確認。
- LLM 的回覆送出前掃描:出現本輪伺服器不知道的產品名稱或工單號 → 整句改用模板。LLM 離線或逾時 → 自動退回表單,已取得的值保留。

### 1.4 其他

- 看板「Dashboard」篩選到單一工單時,左上大數字下方顯示 **計畫 / 已測 / 差異**(來源 `wo_plan`,由網頁上傳設定)。
- 資料庫維護改為管理者 session(不再把 PostgreSQL 帳密放在瀏覽器);記錄欄多了「上傳批次」(來自網頁上傳的資料顯示批次 id)。
- `/api/ingest`、`/api/admin/backfill-metadata` 改為管理者 session;`ADMIN_TOKEN` 移除。
- 舊的 `/legacy/` 單頁看板仍可開,但其 DB Tweak 登入已失效(階段 6 一併移除)。

---

## 2. 資料庫變更(只新增,不改既有欄位)

| 物件 | 用途 |
|---|---|
| `test_record.upload_id`(nullable)+ index | 哪個網頁批次寫入該筆;桌面版寫入的資料為 NULL |
| `app_users`、`app_sessions`、`auth_log`、`account_audit` | 帳號、session、登入紀錄、變更紀錄 |
| `product_registry` | 產品辨認庫(正式名稱、比對鍵、TN 編號、別名、狀態、來源) |
| `wo_plan` | 每張工單的計畫測試數量(由上傳設定;撤銷時還原) |
| `upload_log`、`upload_log_item` | 每批上傳與每檔結果 |

migration 在 API 啟動時自動執行(advisory lock 序列化兩個 worker)。

---

## 3. 驗證結果(全部在本機 10.88.90.136:8000)

| 測試 | 結果 |
|---|---|
| `python3 tests/acceptance_upload.py … ad4792af-5101-251216015.zip`(API 驗收,93 項) | **93/93 通過** |
| `python3 tests/golden.py check`(舊資料 API 不變) | **40/40 一致** |
| `node tests/js/i18n_keys.mjs`(488 個字串,中英同鍵) | OK |
| `python3 tests/e2e_phase4.py …`(Playwright 瀏覽器流程) | **0 個 console error / 0 個 HTTP ≥ 400** |

驗收腳本涵蓋的情境(策略文件 §8 的 A–K):
- 帳號:錯誤密碼 401、未登入 401、上傳者打帳號管理/資料庫維護/ingest → 403、建立/重設上傳者、相同新密碼被擋。
- 辨認庫:12 組解析案例(含 `TN73_PICO_IMX6`、`tn-175_pico-imx8m_mini`、`86`、`TN-86_PICO-IMX6` ❌、`TN-99_EDM1` ❌、`TN-88_NEW_BOARD` → 新產品 `TN-88_NEW-BOARD`、`TNXXX-XXXX-XXXXXX`、`TN-99_` ❌);新增同 TN 產品被擋、新增既有產品 409。
- 範例 zip 分析:252 log / 252 SN / 全 PASS、9 個非 log 檔忽略、兩層 zip 展開、推斷工單 `5101-251216015`、產品 `TN-99_EDM1-IMX6P`、252 筆已在庫。
- 規則:A 同工單同產品 → 無錯誤、R10「都已上傳過」、對齊達成;B 換工單 → R9+R8 ⚠;C 打錯產品 → R16 ❌;D 新產品 → R4 ⚠;E 產品空白 ❌;F `NOWO` → `NOWO-261020001` + R8;G 七種工單格式錯誤(月份 13、2 月 30、流水號 000、少一位、缺破折號、`NOWO-`、空白)各自的原因碼;H 全形/小寫正規化;I 任意 4 位前綴不警告;J 數量空白/非數字 ❌;K 20,000 ⚠;L 300 → 尚缺 48;M 200 → 超出 52(只顯示);N `1,200` → 1200。
- 寫入:重複批次 → 新增 0 / 略過 252,CSV 252 行 duplicate,工單計畫 252 顯示在 overview,撤銷後計畫移除;**改內容後的 zip 以新產品 + 新工單上傳 → 未勾確認 400、勾選後 252 筆寫入(3 秒)**,新產品進辨認庫、overview 顯示計畫 300 / 已測 252 / 尚缺 48、篩選清單出現新工單;撤銷 → 刪 252、新產品移除、統計消失。
- NOWO 批次實際寫入為 `NOWO-261020001`;取消請求在 50 檔邊界生效;資料夾模式(multipart 分批 + 伺服器展開內層 zip)得到與 zip 相同的事實;AI 對話 7 回合(產品 → 工單 → 數量 → 改成打錯的產品被 R16 擋下 → 只打 `86` → 口語「沒有工單」不接受 → `NOWO` 自動補號),每回合 4–6 秒。

截圖:`dockerup-docs/04_Reports/screenshots/phase4/`(登入、帳號管理三個分頁、產品辨認庫、資料庫維護、上傳 Log 各步驟、AI 對話、R16 擋下、對齊卡、完成畫面、淺色中文版)。

本機測試帳號:管理者 `qc` / `qcpass`(`dockerup-essential/.env`),上傳者 `test_uploader`、`e2e_uploader`(由測試腳本建立,可在帳號管理頁停用)。

---

## 4. 部署前要準備的(尚未部署)

1. 管理者登入預設 **`qc` / `qcpass`**(compose 預設值);要改就在正式機 `dockerup-essential/.env` 設 `TWEAK_USER` / `TWEAK_PASS`,可選 `SESSION_TTL_HOURS`(預設 8)。
2. API 映像需重建(新增 `python-multipart`):`docker compose build api`。
3. compose 新增 volume `${STORAGE_PATH}/docker-data/uploads:/app/uploads`(上傳暫存,每批 commit 後清除;超過 1 小時的殘留自動清掉)。
4. 第一次啟動會自動建表、加 `test_record.upload_id`、seed 辨認庫;不需要停機維護。
5. 上傳者帳號由管理者登入後在「帳號管理」建立。

---

## 5. 已知限制 / 後續

- 瀏覽器「選資料夾」在 Chrome/Edge 可用(`webkitdirectory`);Firefox 亦支援,但不支援拖放整個資料夾(拖放只收 zip)。
- 每批上傳全域只能一個在跑(advisory lock);第二個人會收到「已有其他上傳正在進行」。
- AI 助理每回合約 4–6 秒(Qwen3.6-35B,thinking 關閉);離線時自動退回表單。
- 階段 5(結構化 AI 報告)與階段 6(測試/CI、deploy.sh、移除 `/legacy/`、文件)尚未開始。
