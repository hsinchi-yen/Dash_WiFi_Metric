# 舊資料庫相容性驗證報告

日期:2026-10-21 ・ 資料:正式機備份 `wifitest_20261020_145811.dump`(64,181 筆,雜湊與備份清單相符)・ 環境:本機獨立的暫存 PostgreSQL 容器(不碰 `wifitest-db`,跑完自動移除)

## 1. 結論

**相容,不需要撤銷。** 目前所有修改(階段 1–4)套到正式機的舊資料庫上之後:

- 舊系統的每個使用者照常運作,結果與未升級的資料庫**逐位元組相同**:桌面版上傳工具、舊 API(看板讀取 40 支與 ingest / DB Tweak 寫入)、Grafana 5 個面板、3 個舊 view。
- 舊資料一筆不變:`test_record` 64,181 筆在 37 個舊欄位上的雜湊與升級前相同。
- 新 API 在舊資料庫上啟動會自動升級(2 秒內),40 支資料 API 與重構前完全相同。
- 可以撤銷:`rollback_web_additions.sql` 執行後,資料庫結構與舊資料庫**完全相同**(`pg_dump` 結構文字一模一樣),舊 API 結果不變;之後再部署新版也能重新升級。

驗證過程另外找到一個**部署會失敗的問題**(與資料庫無關),已修正,見 §3。

## 2. 「相容」驗證了什麼

| # | 方向 | 怎麼驗 | 結果 |
|---|---|---|---|
| 1 | 舊結構沒被改 | 逐一比對舊資料庫的每個物件:欄位(名稱、型別、NOT NULL、預設值、順序)、限制、索引、view 定義、trigger、function、extension | 唯一變動是 `test_record` 多一個**可為空、無預設值**的欄位 `upload_id` 與它的索引;沒有外鍵指向舊表、沒有 trigger |
| 2 | 舊資料沒被改 | `test_record` 37 個舊欄位逐列雜湊;3 個舊 view 全部結果雜湊 | 相同(64,181 / 223 / 56,694 / 77 列) |
| 3 | 桌面版上傳工具 | 用**正式機部署的** `log_parser.py` 原碼對兩個資料庫寫入同一份 log:新增、重複偵測、覆寫(upsert) | 兩邊行為與寫入內容相同;新列 `upload_id` 為空;`mv_unit` 刷新後看得到;測完刪除 |
| 4 | Grafana | 儀表板 5 個面板的 SQL 在兩個資料庫上執行 | 結果相同 |
| 5 | 舊 API 讀取 | **正式機部署的舊 `app.py`** 分別接兩個資料庫,40 支看板 API 比對 | 40/40 相同 |
| 6 | 舊 API 寫入 | 同上,`/api/ingest`(試跑、匯入、重複匯入)、DB Tweak 登入 / 列表 / 刪除、backfill 試跑 | 兩邊回應相同 |
| 7 | 新 API 在舊資料庫上 | 以**正式部署方式**(建好的映像、不掛原始碼)接一份新還原的舊資料庫,啟動時自動升級 | 升級成功;40/40 與重構前相同;登入與網頁正常 |
| 8 | 撤銷 | 對升級後的資料庫執行 `rollback_web_additions.sql` | 結構與舊資料庫完全相同;資料雜湊相同;舊 API 40/40 相同 |
| 9 | 撤銷後再部署 | 撤銷後的資料庫再讓新 API 升級一次 | 成功;所有檢查再次通過 |

升級新增的物件(全部是新的,舊系統看不到也不會用到):`mv_unit`(物化檢視)、`app_meta`、`ai_reports`、`app_users`、`app_sessions`、`auth_log`、`account_audit`、`product_registry`、`wo_plan`、`upload_log`、`upload_log_item`。

**撤銷時會失去的**:網頁上傳的批次紀錄與「哪些列是哪一批上傳的」(`upload_id`)、帳號、AI 報告、產品辨認庫。網頁上傳寫入的測試資料**會保留**,它們和桌面版寫入的格式相同。腳本開頭有先匯出這兩份對照的指令。

## 3. 驗證中發現並修正的部署問題

開發用的 `docker-compose.dev.yml` 把原始碼掛進容器時,Docker 在 `api/` 底下留下兩個**空白**的掛載點檔案 `api/log_parser.py`、`api/wifi_dashboard.html`,而且被誤提交進 git。`Dockerfile` 先複製共用的 `log_parser.py`,再複製 `api/`,空檔就蓋掉了真的檔案。

- 影響:**本機開發環境正常**(有掛原始碼),但**正式部署方式**(只用映像)啟動就當掉:`AttributeError: module 'log_parser' has no attribute 'FILENAME_RE'`。已用舊映像實際重現。
- 修正:`Dockerfile` 改成先複製 `api/`、再複製共用檔;兩個空檔移出 git,加入 `.gitignore` 與 `.dockerignore`。重建後的映像在舊資料庫上啟動正常(上表第 7 項)。

## 4. 之後每次改資料庫都要守的規則

1. `test_record` 與 3 個舊 view **不改、不刪、不加限制**;新資料一律放新表。`upload_id` 是唯一例外,已驗證。
2. 新表**不得**用外鍵指向 `test_record`(舊 DB Tweak 直接刪列,外鍵會擋住它),`test_record` 上**不得**加 trigger。
3. 每加一張新表,同時把它加進 `rollback_web_additions.sql`;`tests/db_compat.sh` 的撤銷檢查會抓出漏掉的。
4. 部署前跑一次 `tests/db_compat.sh <最新正式機備份>`,必須顯示 `COMPATIBLE`。

## 5. 重跑方式

```
tests/db_compat.sh DB_backups/wifitest_20261020_145811.dump
```

約 5 分鐘。第二個參數可指定舊系統的部署目錄,預設用 `_remote_snapshot/` 裡最新的快照。只用 127.0.0.1 上的暫存容器與暫存資料夾,結束自動清除。

撤銷步驟(只在決定退回舊版 API 時):

```
docker compose stop api
docker exec -i wifitest-db psql -U qc -d wifitest -v ON_ERROR_STOP=1 < dockerup-docs/02_Maintenance/scripts/rollback_web_additions.sql
```
