# 正式機修正報告：LLM 改用 vLLM 後 AI 功能失效

日期：2026-11-23 ・ 正式機 `sit@10.20.31.40:/mnt/md127/WIFI_YFP_DashBoard` ・ 版本仍為 `new_017f8a5`（只改設定，未換映像）

## 1. 結論

看板的「問看板」（Ask AI）從 11-23 起一律回「The AI could not answer. Please try again later.」。原因是 LLM 主機 10.20.30.23 改用 **vLLM** 部署，而 API 送出的模型名稱仍是舊的 `Qwen3.6-35B-A3B-Q6_K`。

- llama.cpp 會忽略請求裡的模型名稱，所以舊名稱一直能用；**vLLM 會檢查**，不存在的名稱直接回 404 `The model ... does not exist`。
- vLLM 目前提供 `Qwen3.8-27B-FP8`（另一個別名 `Qwen3.8-27B-UD-Q6_K_L`）。
- 狀態燈只檢查 `GET /v1/models` 是否回 200，不檢查模型名稱，所以燈號一直是綠色，掩蓋了問題。

已在正式機設定 `LLM_MODEL=Qwen3.8-27B-FP8` 並重建 api 容器，所有 AI 功能恢復。

## 2. 受影響範圍（依 `llm_usage` 與程式呼叫點）

| 功能 | 修正前 | 依據 |
|---|---|---|
| 問看板（`ask_dashboard`） | 故障 | 最後成功 11-19；11-23 5 次全失敗（`POST /api/ask → 503`） |
| 上傳助理：欄位抽取、回覆串流、批次解讀 | 會故障（近 60 天無使用紀錄） | 同一個 `services/llm.py` 呼叫路徑 |
| 背景 FAIL 分類、工單一句話、工單報告／問答、週報月報 | 會故障 | 同上；狀態燈為綠色，所以排程會照常呼叫 |
| DB Maintenance（`routers/tweak.py`、`routers/maintenance.py`） | **不受影響** | 不使用 LLM；上傳後的 `fail_classify.scan()` 是規則分類 |

## 3. 正式機變更（09:52）

| 項目 | 內容 |
|---|---|
| 新增 | `dockerup-essential/.env`：`LLM_MODEL=Qwen3.8-27B-FP8`（`switch.sh` 切換版本時保留 `.env`，不會被覆蓋） |
| 重建 | `docker compose up -d --no-build --no-deps api`，沿用映像 `wifitest-api:0.2.0`；約 6 秒恢復 healthy |
| 未動 | PostgreSQL、Grafana、nginx、pixi-test、iw-solo-pixi 都沒有重啟 |

切回原設定：刪除 `dockerup-essential/.env`，再執行同一個 `docker compose up -d --no-build --no-deps api`。

## 4. 冒煙測試（09:56）

| # | 項目 | 結果 |
|---|---|---|
| 1 | `GET /health` | 200，`llm.connected=true`，`model=Qwen3.8-27B-FP8` |
| 2 | `GET /api/llm-status` | 200，ok |
| 3 | `POST /api/ask`（中文：「最近一週的整體良率是多少？」） | 200，1.9 秒，查詢正確解析（上週 0 台，所以良率顯示 —） |
| 4 | `POST /api/ask`（英文：「overall yield last month」） | 200，1.0 秒，2026-10 良率 100.00%（841 台） |
| 5 | 上傳助理欄位抽取（json_schema） | 正確抽出工單 `5101-260810001`、計畫數 500 |
| 6 | 上傳助理回覆串流（SSE） | 正常 |
| 7 | 一般 chat（舊版報告路徑） | 正常 |
| 8 | api log | 重建後沒有錯誤 |

批次解讀（`/api/upload/{id}/reading/ai`）沒有實測，因為目前沒有狀態為 done 的上傳批次；它和第 5 項走同一個 `complete_json` 路徑。測試在 `llm_usage` 留下 `smoke_stream`、`smoke_chat` 各 1 筆。

## 5. 程式庫

`api/config.py`、`docker-compose.yml`、`.env.example` 的 `LLM_MODEL` 預設值改為 `Qwen3.8-27B-FP8`，與正式機一致。下次重建映像時不需要 `.env` 也是正確值。

## 6. 建議（尚未實作）

- `services/llm.py` 的 `probe()`：設定的 `LLM_MODEL` 不在 `/v1/models` 清單裡時，回報未連線，讓狀態燈直接顯示錯誤。
- LLM 主機再換模型時，先 `GET /v1/models` 確認，再更新正式機 `dockerup-essential/.env`。
