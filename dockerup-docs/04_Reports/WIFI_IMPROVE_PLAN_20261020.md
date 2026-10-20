# WiFi YFP Dashboard 改善計畫(對齊 Solo PIXI 看板)

- 日期:2026-10-20。**狀態:提案,尚未動工。**
- 藍本:`Solo_PIXI_MP_Monitor_DashBoard/improve_plan.md`(14 項改善、已實作並驗證)與其 `web/` 版面(v3.1.0 截圖)。
- 搭配文件:`UPLOADER_MERGE_STRATEGY_20261020.md`(上傳 + AI 協助 + 帳號,設計已定案,本文直接引用)、`ALIGNMENT_REPORT_20261020.md`(現況基準)。
- 目標(你的要求):**使用者更容易讀、容易懂;管理者更容易看到看板的呈現結果**;測試在本機;部署自動化。

---

## 0. 原則(沿用 pixi 已確認的原則,加上本專案的決定)

1. **既有資料表不動**:`test_record` 只允許新增索引、新增資料表、materialized view、改寫查詢;桌面版 exe 照常上傳。
2. **版面以產線大電視(1920×1080)遠距離閱讀為優先**:字大、字少、同一個數字只出現一次、首頁不捲動;手機能看就好。
3. **主題只留 Dark(預設)/ Light**。這取代 `implementation_plan.md` 的 Cyber Metal 三主題方向(pixi 當初也有 Cyber/Space,最後為了可讀性砍掉)。
4. **語言:英文預設、側欄一鍵切中文**(pixi v3.1.0 的決定:主管是外國人;產線用中文)。技術名詞 PASS / FAIL / MAC / Mbps 保留英文。
5. 正式環境(10.20.31.40)任何部署:本機驗證 → 備份 → **經你同意** → `deploy.sh` 自動執行。
6. 每個階段:本機實作與驗證 → 回報 → 你手動看過 → 同意後才進下一階段。

---

## 1. 現況(本機實測,還原庫 64,181 筆)

### 1.1 規模
| 項目 | 數字 |
|---|---|
| `wifi_dashboard.html` | 4,560 行(CSS 1,633、JS 2,363),175 KB,單一檔案;127 個 inline style、59 個寫死的色碼;3 套主題 |
| 頁面 | 6 頁 + 終端機風格 AI 報告視窗;12 張圖、10 張 KPI 卡、105 個 DOM id |
| `api/app.py` | 1,519 行單檔,27 個端點;無 logging、無 pydantic 回應模型、無 `try/finally` |
| 語言 | 英文介面混 28 處中文(AI 報告、提示);無 i18n |
| 外部相依 | Google Fonts、cdnjs 的 Chart.js 與 marked——**內網離線時版面與圖表會壞** |

### 1.2 效能(uvicorn 2 workers,PostgreSQL 參數同正式環境)
| 項目 | 實測 |
|---|---|
| 一次重新整理 | **15 個 API 並行**,每個 110–310 ms,伺服器合計 2.8 秒 CPU、86 KB JSON;**每 10 秒一次、每個開著的分頁各一次** |
| 根本原因 | `LATEST_SN_CTE`(每個 SN 取最新一筆的 window function)在 `app.py` 出現 **23 次**,每次對全表 `SELECT *` 排序(EXPLAIN:76 ms);每個請求開新 DB 連線(7 處 `psycopg2.connect`) |
| 首頁 HTML | 175 KB 未壓縮(gzip 後 34 KB);無快取標頭 |
| 對照 pixi 改善後 | 首頁 6 API → 1 API,0.37 s → 0.20 s(之後 4 ms);開啟看板 2.3 s → 0.66 s |

### 1.3 與 pixi 同類的既有問題(本機已確認)
| # | 問題 | WiFi 位置 |
|---|---|---|
| W1 | API 失敗時 `apiGetOr` 靜默回傳預設值,畫面只剩舊數字或 0,沒有提示(= pixi B4) | `wifi_dashboard.html` `apiGetOr` |
| W2 | 排程輪播每 10 分鐘強制換頁,會打斷正在操作的人(pixi 已取消,改為閒置 5 分鐘才輪播且不覆蓋篩選) | `_schedCarouselInterval` |
| W3 | 查詢出錯連線不關閉(無 `try/finally`),累積後撞 `max_connections`(= B8) | `app.py` 各端點 |
| W4 | `/api/ingest`、`/api/admin/backfill-metadata` 不用登入就能寫/改整表(= B9) | `app.py:1280`、`:1385` |
| W5 | LLM 金鑰、模型名稱寫死在程式;模型是否存在未驗證 | `app.py:1437-1439` |
| W6 | 三個 view(`v_unstable_units` 等)無人使用;`attempt_count` 全為 1,`/api/retries` 永遠空 | schema / 資料 |
| W7 | AI 報告為「打字機終端」視窗,文字不能複製、無中英切換、無保存(= pixi B1/B2/C1) | `openLatestWoTerminalReport` |

---

## 2. 版面規格:照 pixi 的佈局,換成 WiFi 的內容

**直接沿用 pixi 的 `web/` 骨架(index.html + css/theme/base/pages + js/core + js/pages),只換頁面內容與 API。** 兩個專案同一組人維護,骨架相同之後互相搬功能的成本最低。

### 2.1 外殼
| 區塊 | 內容 |
|---|---|
| 側欄 | 品牌 `TN` / **WiFi YFP** / 「WiFi Module on SoM Test」;時鐘;三組導覽(見 2.2);底部:登入者資訊、**Light / 中文** 兩顆切換鈕、`API OK` / `AI ready` 狀態燈 |
| 頂列 | 頁名 + 目前範圍(例「Work order 5101-251216015」);篩選列:**All / Today / This Week / This Month / Custom + 工單下拉 + 產品下拉**(WiFi 比 pixi 多一個產品維度);右側 `Updated hh:mm` + `Refresh` |
| 預設範圍 | **最新一張工單**(標題直接寫工單號與日期範圍),要看別的用篩選列 |
| 資料更新 | 取代現在的 10 秒輪詢:畫面顯示「Updated hh:mm」,每 60 秒自動更新,`Refresh` 立即更新;伺服器端統計每 5 分鐘刷新 + 上傳完成立即刷新 |
| 失敗/空資料 | 該區塊顯示「Load failed · Retry」或「No data in this range」,不再整頁壞掉或顯示 0;狀態燈反映最近一次請求 |
| 閒置輪播 | 閒置 5 分鐘後,首頁 ↔ 工單頁每 30 秒輪播;不覆蓋使用者的篩選;**取消現在的排程強制換頁與 AI 報告輪播** |

### 2.2 導覽(從 6 頁 → 9 頁;pixi 相同結構)
| 群組 | 頁 | 內容 | 權限 |
|---|---|---|---|
| Overview | 📊 Dashboard | 2.3 | — |
| | 📋 Work Orders | 單一表格:工單、產品、最後測試、台數、PASS、FAIL、良率(色塊)、重測率、平均測試時間、**每小時產出**、AI 報告按鈕;搜尋框 | — |
| | ⚠️ Fail List | 伺服器端分頁的失敗清單:時間、工單、產品、SN、MAC、失敗原因(5G/2.4G/BT)、下載 raw log | — |
| Analysis | ✨ AI Report (Latest) | 單一報告視窗(2.5) | — |
| | 📡 RF Analysis | 頁籤 **2.4G / 5G / BT**:吞吐量直方圖、RSSI 前後差、P50/P95、標準差前 20 名;資料只抓一次 | — |
| | ⚡ Production | 現有 Advanced 頁的內容(產出速率、單片時間分布、最快/最慢批次) | — |
| Management | 🔧 DB Maintenance | 現有 DB Tweak(改用登入 token) | 管理者 |
| | ⚖️ Plan Alignment | 工單計畫數 vs 實測(來自上傳時填的計畫測試數量,`wo_plan`) | 看:—;改:登入 |
| | 📤 Log Upload | 三步驟:① 選檔 ② 確認(AI 助理 / 表單)③ 進度與結果;最近上傳表(可撤銷)——規格見上傳策略文件 | 登入 |
| | 👥 Accounts | 帳號列表 / 異動紀錄 / 登入紀錄 | 管理者(其他人不顯示) |

### 2.3 首頁(管理者一眼看懂):一列 7 個數字 + 4 張圖,填滿 1080p 不捲動
| 位置 | 數字 | 說明 |
|---|---|---|
| 主數字(76px) | **Yield** | 顏色:綠 ≥ 98%、黃 95–98%、紅 < 95%(全站共用同一份門檻;工單表、AI 報告同色) |
| 主數字副行 | **Plan 達成** `tested / planned` | 有計畫數才顯示(上傳時填的);沒有就顯示「No plan」 |
| 2 | Units Tested | 副行:test runs(含重測) |
| 3 | PASS(綠) | |
| 4 | FAIL(紅) | |
| 5 | Retry Rate | 副行:units retested(目前資料全為 0,會顯示 0%) |
| 6 | Avg 2.4G | Mbps(WiFi 專屬,取代 pixi 的 STOP) |
| 7 | Avg 5G | Mbps |

4 張圖:**Yield Trend**(每日良率線 + 台數柱)、**PASS / FAIL Split**(圓環)、**Units per Month**、**Throughput Distribution**(2.4G 與 5G 直方圖疊在同一張)。現在的「最新工單每小時產出」移到 Work Orders 列的展開/Production 頁。**同一個良率只出現一次。**

### 2.4 可讀性細節(對使用者)
- 數字用 `tabular-nums`,千分位,百分比固定兩位。
- 表格:表頭固定、斑馬列、數字右對齊、良率與重測率用色塊徽章(pixi 截圖同款)。
- 所有文字從字典取(`strings.en.js` / `strings.zh.js`),英文短詞不換行;中文由產線切換。
- 不用 Google Fonts / CDN:字型用系統字,Chart.js 放 `vendor/`,**內網離線可開**。
- `prefers-reduced-motion`、列印樣式、手機排版沿用 pixi。

### 2.5 AI 報告(對管理者)
- 單一視窗:標題列顯示工單、產生時間、**中/EN 切換(兩版一起產生)**;按鈕:複製、下載 Markdown、重新產生、和上一份比較、追問;文字可選取;Esc 關閉。
- 伺服器先算「事實表」(台數、PASS/FAIL、良率、重測率、2.4G/5G 平均與 P5、RSSI 衰減、失敗原因前 5、**計畫數 vs 實測**、測試時間範圍、和最近 3 張工單比較),LLM 只寫總評/3 發現/3 建議(json_schema),**數字由程式填**,輸出後檢查數字必須存在於事實表。
- 存進 `ai_reports`(資料雜湊不變就直接回舊報告);上傳完成後自動產生;每天 18:00 批次。
- thinking 關閉(pixi 實測快 15 倍);模型與金鑰改環境變數,P4 前先確認模型存在。

---

## 3. 改善項目(對應 pixi 的 14 項)

| 區 | 項目 | WiFi 的做法 | 效益 |
|---|---|---|---|
| A1 | 首頁精簡 | 2.3 的 7 數字 + 4 圖;新增 `GET /api/overview` 一次回傳 | 15 個請求 → 1 個 |
| A2 | 主題 | Dark / Light,所有顏色含圖表走 CSS 變數;刪 cyber/space 與 Google Fonts | CSS 1,633 行 → 約 400 行 |
| A3 | 語言 | 英文預設 + 中文字典;`data-i18n` | 不再中英混雜 |
| A4 | 失敗/空資料提示 | 統一 `apiGet()`;區塊級錯誤;狀態燈 | 修 W1 |
| A5 | 篩選與輪播 | 時間範圍 + 工單 + 產品;閒置輪播;取消排程輪播 | 修 W2 |
| B1 | 伺服器端分箱 | 吞吐量直方圖、P50/P95 在 SQL 用 `width_bucket()` 算完 | RF 頁只傳幾 KB |
| B2 | 日期範圍查詢 | 篩選改 `test_time >= :from AND < :to`,用現有 `idx_test_time` | 索引生效 |
| B3 | 去重統計集中 | 新增 materialized view `mv_unit`(每 SN 最新一筆 + 是否曾 PASS + 嘗試次數),取代 23 處 `LATEST_SN_CTE`;每 5 分鐘 `REFRESH CONCURRENTLY` + 上傳/撤銷/刪除後立即刷新 | 76 ms 全表排序 → 毫秒級 |
| B4 | 連線池、錯誤處理、傳輸 | psycopg2 連線池、`with db()`、統一錯誤格式 `{error, error_en, code}`;nginx gzip + 靜態檔快取(見 §5) | 修 W3;首頁傳輸量 −70% |
| C1–C4 | AI 報告 | 2.5 | 修 W7 |
| D1 | 前端拆檔 | 沿用 pixi `web/` 骨架;每頁一個模組;無 build 工具 | |
| D2 | 後端分模組 | `routers/`(analytics、auth、admin、tweak、upload、reports、system)+ `services/` + `db.py` + `config.py` + `errors.py`;pydantic 模型;logging(含慢查詢 > 500 ms);`/health` 回 DB 延遲、LLM 狀態、view 更新時間、版本 | |
| D3 | 測試與 CI | 單元(含 i18n 字典一致、無 inline style、無字典外中文)、API 整合(golden 比對)、Playwright E2E 進 `tests/`;GitHub Actions | |
| D4 | Repo 清理 | `.gitattributes`、刪明文密碼腳本、`build/`/`dist/` 忽略、舊文件歸檔、README 改寫 | |
| D5 | 部署 | `scripts/deploy.sh`(§5) | |
| E | 小修 | W4 端點僅管理者;W5 LLM 設定環境變數;W6 刪未用 view(或修門檻);錯誤訊息不回傳例外原文 | |

**上傳 / AI 協助 / 帳號 / 產品辨認庫 / 計畫數量對齊**:完全依 `UPLOADER_MERGE_STRATEGY_20261020.md`(R1–R16、§3.3–3.6),這裡不重複;UI 放進 2.2 的 Log Upload / Accounts / Plan Alignment 三頁,版面照 pixi 的三步驟畫面。

---

## 4. 執行順序與估時

依 pixi 的經驗調整:**先把骨架換成 pixi 的,再在新骨架上加功能**,避免同一份 4,560 行的檔案同時被改版和搬移。

| 階段 | 內容 | 驗收(出口條件) | 估時 |
|---|---|---|---|
| **1 基準與清理** | `.gitattributes` + EOL 正規化(單獨 commit,tag `baseline-20261020`);移除明文密碼腳本、忽略 `build/`/`dist/`;修 `/mnt/md0`、uid 999→70;W4 端點加管理者檢查(暫以環境變數 token);本機測試環境 §5.1 | `git diff` 乾淨;本機 compose 起得來、看板可開 | 1.5 天 |
| **2 後端分模組 + 效能** | D2 拆模組、連線池、logging、`/health`;B2 範圍查詢;B3 `mv_unit`;B1 分箱;A1 的 `/api/overview`;錯誤格式 | **golden 比對**:拆分前先錄 14 支資料端點輸出,改寫後數字完全一致(pixi 做了 210 項);首頁 1 個 API < 0.2 s | 5 天 |
| **3 新前端(pixi 骨架)** | 移植 `web/`(theme/base/pages css、core js、i18n、filter、freshness、theme、idle rotation);Dashboard、Work Orders、Fail List、RF Analysis、Production、DB Maintenance 六頁;舊 html 保留為 `legacy/` 直到驗收 | 1080p 不捲動;Dark/Light;EN/中文;Playwright 截圖;**你在本機看過** | 6 天 |
| **4 帳號 + 上傳 + AI 協助** | 策略文件 P2–P4:帳號/session/稽核、analyze/commit/revert、產品辨認庫、計畫數量、NDJSON 進度、AI 對話;Log Upload / Accounts / Plan Alignment 三頁 | 策略文件 §8 的回放比對 A–L、權限矩陣 A1–A12、測試帳號實際走完 | 9 天 |
| **5 AI 報告改版** | C1–C4:事實表、json_schema、`ai_reports`、單一視窗、自動產生、比較、追問 | 3 張工單新舊報告對照;數字檢查 0 錯 | 3 天 |
| **6 測試、CI、部署** | D3 測試進 repo + GitHub Actions;`deploy.sh` + `--local` 演練;文件(README、操作手冊、AGENTS.md) | 單元/整合/E2E 全過;`deploy.sh --local` 成功;**正式部署另待你同意** | 2.5 天 |
| | **合計** | | **約 27 天**(±30%) |

上傳策略文件原估 19–23 天只含 P0–P6;本計畫多了效能(B1–B3)、AI 報告改版與測試/CI,所以是 27 天。每階段結束回報並由你決定是否繼續。

---

## 5. 本機測試與自動部署

### 5.1 本機測試環境(所有測試都在這裡)
- compose 專案 **`wifi-local`**:`wifi-local-db`(127.0.0.1:**5435**)、`wifi-local-api`、`wifi-local-nginx`(**:8002**,本機 8000/8001/8003/8004/80/8443/5434 已被其他專案佔用)。`STORAGE_PATH=.`,資料目錄在專案內(已 gitignore)。
- 資料:`DB_backups/wifitest_20261020_145811.dump`(64,181 筆,與正式環境逐列相同)還原;上傳測試用你提供的 `5101-251216015.zip`(放 `rawlogs/`,gitignore)。
- 測試帳號只建在本機:`test_admin`(環境變數)、`test_uploader`(帳號頁建立),測完刪除。
- 瀏覽器測試:Playwright 1920×1080(大電視)+ 390×844(手機),截圖存 `docs/test-report/`。

### 5.2 nginx:加入(依「有效率提升就加入」)
pixi 實測:nginx 提供靜態檔 + gzip 後,開啟看板 2.3 s → 0.66 s(與 API 合併並行)。WiFi 的 HTML 175 KB → gzip 34 KB,每次重新整理 86 KB JSON → 約 20 KB;新前端拆成十幾個靜態檔後,由 nginx 直接提供並加 `Cache-Control`,API 只處理 `/api/`。**不做 TLS、不開新對外埠**:nginx 接手現在的 `:8000`,API 改為容器內部埠,對使用者的網址不變。設定直接複製 pixi 的 `nginx/`(含「上游不用 keepalive」這個他們踩過的坑)。

### 5.3 自動部署:`scripts/deploy.sh`(移植 pixi 的,已在他們正式環境用過)
`backup`(遠端檔案 tgz + `pg_dump`,存到 `oldbackup/<日期>/`)→ `rsync` 整個專案(排除 `.env`、資料目錄、備份)→ `docker compose up -d --build` → `/health` 檢查(DB 延遲、view 時間、版本)→ **失敗自動還原**上一版檔案與映像。`--local DIR` 在本機演練、`--dry-run` 只列出差異。SSH 用金鑰或 `SSHPASS` 環境變數,密碼不進檔案。**正式環境的每一次執行都先經你同意**(原則 5)。
- 部署前提:遠端能 `docker build`(需連 pip 來源,待驗證;不行就本機建映像再傳)。
- 部署後:全員重新登入一次;管理者在帳號頁建立上傳者。

---

## 6. 風險
| 風險 | 對策 |
|---|---|
| 改寫查詢後數字和現在不一致 | 階段 2 先錄 golden,逐項比對 |
| `mv_unit` 5 分鐘延遲讓桌面版剛傳的資料「還沒出現」 | 顯示「Updated hh:mm」+ Refresh 按鈕(觸發 view 刷新,10 秒內最多一次) |
| 新前端是重寫而非逐行搬移,行為可能漏掉 | 舊 html 留在 `legacy/` 並可從同一台 nginx 開(`/legacy/`)對照;E2E 逐頁比對關鍵數字 |
| 管理者不習慣大改後的版面 | 階段 3 結束先在本機讓你看(Dark/Light、EN/中文、1080p 截圖);舊版隨時可還原 |
| 遠端建映像失敗 | 階段 6 前驗證;備案本機建好映像再傳 |
| 7 個數字裡 Retry 目前永遠 0 | 保留(桌面版 log 未來若有重測就會出現),副行說明「no retests recorded」 |

---

## 7. 需要你決定的(不擋階段 1)
1. **首頁 7 個數字**是否同意 2.3 的選法(尤其用 Avg 2.4G / Avg 5G 取代 pixi 的 STOP)?
2. **良率門檻** 98 / 95 是否適用 WiFi(目前整體 99.65%)?
3. 「**自動雲端部署機器**」我解讀為 5.3 的 `deploy.sh`(自動備份 → 同步 → 建置 → 健康檢查 → 失敗還原,目標 10.20.31.40,執行前仍要你同意)。若你指的是別的(例如 GitHub Actions 直接部署、或雲端主機),請說明。
