# 正式機部署報告

日期：2026-10-22 ・ 正式機 `sit@10.20.31.40:/mnt/md127/WIFI_YFP_DashBoard` ・ 部署版本 `new_32c6de9`（commit `32c6de9`）

## 1. 結論

新版看板已在正式機上線：`http://10.20.31.40:8000/`，大電視用 `/?lang=zh&tv=1`。
- 部署前做了完整備份，正式機與開發機各一份，已核對 SHA256。
- 部署前的舊看板原封不動保存在 `releases/old`。用 `releases/switch.sh` 可以在新舊兩版之間直接切換，**已在正式機實際來回切過一次**：切舊版 7.7 秒、切回新版 11.8 秒，兩邊的舊 API 數字都和錄製的相同（golden 40/40）。
- 資料庫 `test_record` 部署前後都是 64,181 筆。PostgreSQL、Grafana 沒有重啟；同一台主機上的 pixi-test、iw-solo-pixi 也沒有受影響（容器 ID 都沒變）。

## 2. 有問題時怎麼切回

在正式機上（`sit` 帳號）執行：

```bash
/mnt/md127/WIFI_YFP_DashBoard/releases/switch.sh old      # 切回部署前的舊看板（約 10 秒，資料庫不動）
/mnt/md127/WIFI_YFP_DashBoard/releases/switch.sh new      # 再切回新看板
/mnt/md127/WIFI_YFP_DashBoard/releases/switch.sh status   # 現在是哪一版
```

| 情況 | 指令 | 說明 |
|---|---|---|
| 新看板有問題，先用舊的 | `switch.sh old` | 舊看板直接在升級過的資料庫上跑（db_compat 驗證）；期間寫入的資料切回新版都看得到 |
| 也要資料庫結構回到部署前 | `switch.sh old --drop-web-tables` | 先自動備份，再刪新看板自己的表與 `upload_id` 欄；`test_record` 每一列保留 |
| 資料庫本身壞了，整個回到部署前 | `switch.sh restore-db DB_backups/predeploy_20261022_071959/wifitest.dump` | 要輸入 yes；先自動備份現況，再以單一交易還原（出錯就整個不套用）。部署後寫入的資料會消失 |

完整說明在正式機的 `releases/README.md`（= `01_Deployment/SWITCH_RELEASE.md`）。切換時會先檢查版本、映像、資料庫；新版兩分鐘內沒起來會**自動切回**原來那一版。

## 3. 部署前備份

| 位置 | 內容 |
|---|---|
| 正式機 `DB_backups/predeploy_20261022_071959/`（532 MB） | `wifitest.dump`（pg_dump，405,365,629 bytes）、`globals.sql`、`schema_only.sql`、`code_dockerup-essential.tgz`（部署前的程式）、`containers.json`、`compose_config.yml`、`api_image.tar.gz`（部署前的 API 映像）、`SHA256SUMS.txt` |
| 開發機 `DB_backups/predeploy_20261022_071959/` | 同一份，SHA256 全部核對通過 |
| 正式機與開發機 `DB_backups/predeploy_20261022_064327/` | 06:43 為演練先取的一份，資料相同（筆數、max id、最後寫入時間一致） |
| 正式機 `releases/old/`（756 KB）＋ 映像 `dockerup-essential-api:rel-old` | 部署前正在跑的程式與 API 映像（`sha256:bda738e3…`），`switch.sh old` 用的就是它 |

正式機原本的 `backups/` 資料夾屬於 root，`sit` 寫不進去。原腳本把程式快照放在那裡，所以改放 `DB_backups/` 與 `releases/`。

## 4. 部署過程（`deploy.sh --host sit@10.20.31.40`）

| 步驟 | 結果 |
|---|---|
| 0. 事前檢查 | `test_record` 64,181 列；compose 與正在跑的 PostgreSQL 指向同一個 pgdata（`/mnt/md127/…/docker-data/pgdata`） |
| 1. 備份 | 見 §3 |
| 2. 用這份備份跑 db_compat | **COMPATIBLE**：3 36/36、4 40/40、5 40/40 ＋ 8/8、6 36/36 ＋ 40/40、7 40/40 ＋ 36/36（紀錄 `DB_backups/db_compat_20261022_071959.log`） |
| 3. 版本 | `releases/old` 取自正在跑的程式。`releases/new_32c6de9` 是 `git archive` 出來的 commit 內容；它的 API 映像在開發機建置，再載入正式機 |
| 4. 切換 | 07:27:50 → 07:28:08 新版上線，約 18 秒 |
| 5. 檢查 | `/health`、`/api/meta`、首頁正常；`/legacy/` 回 301；筆數前後都是 64,181 |

新版第一次啟動時做的事：
- 資料庫升級：新增 `test_record.upload_id`；建立統計檢視 `mv_unit`；產品名稱表帶入 11 個產品。
- 失敗分類：223 筆 FAIL 全部由規則分類。
- LLM 呼叫共 15 次：7 個工單一句話、7 份結案報告、1 份期間報表，合計約 105 秒。之後回到平常的低用量。

## 5. 部署後在正式機上的驗證

| 項目 | 結果 |
|---|---|
| 舊 API 數字（golden，對正式機） | 40/40 相同 |
| 瀏覽器逐頁檢查（`tests/e2e_screens.py`，只瀏覽不寫入；趨勢、工單、詳情、失敗清單、RF、產出、上傳、產品、資料庫維護、帳號、設定；深 / 淺色、中 / 英、手機） | 19 張截圖，**0 個錯誤**（console、頁面錯誤、HTTP ≥ 400） |
| api log | 沒有 error / traceback |
| Grafana | `/api/health` 200；容器沒有重啟 |
| 其他服務 | pixi-test、iw-solo-pixi 的容器 ID 與部署前相同 |
| 切回舊版 | 7.7 秒；首頁是舊看板（TN-WiFi Metrics）；golden 40/40；nginx 容器移除；64,181 列 |
| 再切回新版 | 11.8 秒；golden 40/40；`/legacy/` 301 |

## 6. 正式部署前的演練

演練環境是一份完整的正式機複本，架在開發機上：今天的正式機備份、正式機上的舊程式（含 CRLF 與伺服器自己的檔案）、正式機的舊 API 映像，並用相同的連接埠。複本的 schema 與正式機逐行相同，舊看板 golden 40/40。

1. `deploy.sh --replica`：從舊版部署到新版，結束碼 0。
2. `tests/switch_rehearsal.sh`：第一次 41/48，抓到兩個問題，已修正：
   - **原本寫在文件裡的「整個資料庫還原」步驟是錯的。** 新看板的 `mv_unit` 依賴 `test_record`，`pg_restore --clean` 刪不掉 `test_record`，接著又把資料再寫一次，複本變成 128,363 列。現在改成先跑 `rollback_web_additions.sql`，再用單一交易還原，並做成 `switch.sh restore-db`，不必手打。
   - 測試自己寫入的一列沒有清掉，造成後面幾個 golden 比對不符；測試已改為寫入後立即清掉。
3. 用全新的複本重跑一次：**53/53 全部通過**。內容包括：
   - 新 ↔ 舊來回切換，切換時間 5–10 秒。
   - 舊版上線時用舊的 `log_parser.py` 寫入資料，切到新版看得到。
   - 一個起不來的版本會自動切回。
   - 沒有映像的版本會被拒絕，現有容器不受影響。
   - 同時只能跑一個切換。
   - `--drop-web-tables` 之後，schema 與部署前的正式機備份逐行相同。
   - `restore-db`：沒輸入 yes、或給的不是備份檔，都會拒絕；真正還原後筆數與 schema 都回到備份當時，golden 40/40。

## 7. 這次修改的檔案

- `dockerup-docs/01_Deployment/switch_release.sh`（新增）：正式機上的 `releases/switch.sh`。
- `dockerup-docs/01_Deployment/SWITCH_RELEASE.md`（新增）：切換與還原說明，正式機上叫 `releases/README.md`。
- `dockerup-docs/01_Deployment/deploy.sh`：
  - 備份改成在正式機上做，再複製到開發機核對。
  - 改用 `releases/` 與 `switch.sh` 部署。
  - 只部署已 commit 的內容（`git archive HEAD`）。
  - 新增 `--replica` 演練模式與 `SSH_OPTS`。
  - 原本的回復方式會把整個 `dockerup-essential` 資料夾搬走，已拿掉。
- `tests/switch_rehearsal.sh`（新增）、`tests/README.md`、`DOCKER_DEPLOY_SOP.md`、`06_Manuals/02_管理者.md`、`AGENTS.md`。

## 8. 接下來要做的（同交付報告 §6）

1. 管理者（qc / qcpass）在「帳號管理」建立上傳者與 RD 帳號。
2. 進行中的工單在工單詳情設定計畫數，預測頁才算得出完工時間。
3. 大電視開 `http://10.20.31.40:8000/?lang=zh&tv=1`。
4. 看一下設定頁的門檻、上班時間、上班日是否符合產線。

另外：
- 桌面上傳工具照常直接寫資料庫，不需要任何更改。
- 舊的 `POST /api/ingest` 現在要管理者登入。
- 切回舊看板時，舊版的 AI 報告輪播會恢復，每 10 分鐘呼叫一次 LLM。

## 9. 部署後的正式機驗證（08:00–08:35）

截圖報告：`PRODUCTION_VERIFICATION_20261022.pdf`（54 頁）。用合成資料與測試帳號在正式機上實際操作，驗證後全部刪除，資料庫回到 64,181 筆。

| 項目 | 結果 |
|---|---|
| 網頁流程：建立帳號 → 上傳者上傳 → 權限 → 自動分析 → RD → 撤銷 → 停用 | 16/16 |
| 舊方式上傳（桌面工具、批次指令） | 20/20 |
| 舊 API 數字（golden） | 40/40，新、舊版各 2 次 |
| 瀏覽器逐頁檢查 | 0 錯誤 |
| 頁面畫面出現時間 | 0.2–0.3 秒 |
| 再一次來回切換 | 舊版 6.0 秒、新版 12.5 秒 |

找到 2 個問題，已在本機修正並測試，**尚未部署**：

1. **每天 00:00–08:00，上傳精靈誤報「工單日期晚於今天」。** API 容器用 UTC，所以 NOWO 編號在這段時間也會拿到前一天的日期。已修正，commit `77e04c4`。
2. **新的一批資料會讓 LLM 重寫已完工工單仍正確的 AI 報告與一句話。**
   - 第一次啟動實際呼叫 42 次 LLM，不是 §4 估的 15–20 次；驗證期間又多了 14 次。
   - 修正後，數字仍正確的 AI 文字保留不重寫，commit `a8ba520`。
