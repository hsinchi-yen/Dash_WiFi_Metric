# WiFi YFP Dashboard

英文版請見: [README.md](README.md)

## 專案應用目的

本專案用於 WiFi 產品生產測試（Factory QA）資料的可視化與追蹤，目標是把分散的測試 log 轉成可即時監控、可回溯分析、可維運管理的儀表板系統，協助產線與工程團隊快速發現良率風險並縮短問題定位時間。

## 主要解決的問題

1. 測試資料分散在文字 log，人工查找與彙整耗時。
2. 難以即時掌握 Work Order 的良率、吞吐量與重測風險。
3. 重啟或換機後，部署與資料還原流程容易不一致。
4. 現場值班需要一套可持續運作、可快速復原的監控機制。

## 核心能力

1. 產線資料整合：解析測試 log 並匯入 PostgreSQL。
2. 即時 Dashboard：顯示整體良率、失敗清單、吞吐趨勢與 Work Order 指標。
3. 自動輪播展示：可在展示模式下自動切換 Dashboard / Work orders / Throughput。
4. 維運工具鏈：提供備份、還原、掛載、權限修復與開機自啟腳本。
5. 容器化部署：以 Docker Compose 啟動 API、PostgreSQL 與 Grafana。

## 適用場景

1. 製造端生產測試線的即時監看。
2. 製程異常時的快速回查（Fail list / Retry 行為）。
3. 每日/每月品質報告的數據來源。
4. 新機搬遷與災難復原（Backup/Restore SOP）。

## 專案結構

- `dockerup-essential/`: 執行系統核心（API、Dashboard、Compose、Schema、Parser）
- `dockerup-docs/`: 部署與維運文件、腳本與 SOP
- `DB_backups/`: 備份檔存放（不建議提交到 Git）

## 非目標與限制

1. 本專案不取代完整 MES/ERP 流程，只聚焦測試資料觀測與分析。
2. Dashboard 指標品質依賴測試 log 正確性與資料匯入完整性。
3. 生產環境請搭配備份策略與權限控管（見 `dockerup-docs/`）。

## 建議成功指標

1. 問題工單定位時間（MTTD/MTTR）下降。
2. 產線日常巡檢由人工比對轉為儀表板監看。
3. 系統重啟後可在預期時間內自動恢復服務。
4. 換機後可依 SOP 在可控時間內完成還原與上線。
