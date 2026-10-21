# 切換新舊看板 / 還原

正式機 `/mnt/md127/WIFI_YFP_DashBoard/releases/`（部署腳本 `deploy.sh` 安裝，本檔在那裡叫 `README.md`）。
所有指令都在正式機上、以 `sit` 帳號執行，不需要密碼以外的任何東西，也不需要開發機。

## 一句話

```bash
cd /mnt/md127/WIFI_YFP_DashBoard
releases/switch.sh old      # 切回部署前的舊看板（約 1 分鐘）
releases/switch.sh new      # 再切回新看板（約 1 分鐘）
releases/switch.sh status   # 現在跑哪一版、有哪些版本可切
releases/switch.sh help     # 全部指令
```

切換只重建 api（新看板另有 nginx）。**PostgreSQL 與 Grafana 不重啟，資料庫不變**：舊看板可以直接在升級過的資料庫上跑（`tests/db_compat.sh` 驗證過）。在任何一版寫入的資料（桌面工具、批次指令、網頁上傳），切到另一版都看得到。

## 資料夾裡有什麼

| 項目 | 內容 |
|---|---|
| `releases/old/` | 第一次部署前正在跑的程式（原樣複製），API 映像 `dockerup-essential-api:rel-old` |
| `releases/new_<commit>/` | 每次部署的版本（該 commit 的 `dockerup-essential`），API 映像 `wifitest-api:rel-new_<commit>` |
| `releases/CURRENT` | 現在上線的版本 |
| `releases/switch.sh` | 切換腳本 |
| `releases/rollback_web_additions.sql` | 把資料庫結構還原成部署前的樣子 |
| `releases/switch.log` | 每次切換的紀錄 |
| `DB_backups/predeploy_<時間>/` | 每次部署前的完整備份：`wifitest.dump`（pg_dump）、`globals.sql`、`schema_only.sql`、`code_dockerup-essential.tgz`、`containers.json`、`compose_config.yml`、`api_image.tar.gz`（當時的 API 映像）、`SHA256SUMS.txt` |

開發機上的 `DB_backups/predeploy_<時間>/` 有同一份備份，已用 SHA256 核對過。

## 切換做了什麼

1. 檢查：版本資料夾、它的 compose 檔、它的 API 映像、資料庫容器在跑。`switch.sh check old` 只做這一步，不改任何東西。
2. 把該版本的程式複製到 `dockerup-essential/`。`docker-data`、`logs`、`WiFiTestLogs`、`build`、`dist`、`grafana`、`.env` 不動。
3. 重建 api，新看板另有 nginx；該版本沒有的容器會移除，例如切回舊版時的 nginx。
4. 等 `/health` 與首頁回應。**兩分鐘內沒起來就自動切回原來那一版**，並把 api 的最後幾行 log 印出來。

## 資料庫也要回到部署前的結構（通常不需要）

```bash
releases/switch.sh old --drop-web-tables
```

先切回舊看板，再把資料庫備份到 `DB_backups/before_drop_web_tables_<時間>.dump`，然後執行 `rollback_web_additions.sql`：
- 會刪掉新看板自己的表：帳號、上傳紀錄、計畫數、狀態一句話、結案報告、失敗分類與處置、週報 / 月報、LLM 用量。
- 也會刪掉 `test_record.upload_id` 欄。
- `test_record` 的每一列都保留。

之後再 `switch.sh new`，新看板會重建這些表（內容是空的，帳號要重建）。

## 整個資料庫回到部署前那一刻（只在資料庫本身壞掉時）

部署之後寫入的資料（桌面工具上傳的 log 等）會**消失**，請先確定真的需要。

```bash
cd /mnt/md127/WIFI_YFP_DashBoard
releases/switch.sh restore-db DB_backups/predeploy_<時間>/wifitest.dump    # 會要求輸入 yes；約 3 分鐘
```

依序執行：
1. 切回舊看板。
2. 把現在的資料庫備份到 `DB_backups/before_restore_<時間>.dump`；之後反悔，可以用同一個指令還原這份。
3. 執行 `rollback_web_additions.sql`。新看板的統計檢視 `mv_unit` 依賴 `test_record`，部署前的備份裡又沒有它，不先刪掉，`pg_restore --clean` 就刪不掉 `test_record`。
4. 用**單一交易**還原備份：任何一步出錯就整個不套用，不會留下一半的資料庫。
5. 印出還原後的筆數。

完成後是舊看板在跑；要用新看板就再 `switch.sh new`。

## 映像不見時

`switch.sh status` 若顯示某版本的映像 MISSING（例如有人清了 docker 映像），可從當次部署的備份載回：

```bash
gunzip -c DB_backups/predeploy_<時間>/api_image.tar.gz | docker load
docker tag $(cat DB_backups/predeploy_<時間>/api_image_id.txt) dockerup-essential-api:rel-old    # 舊版
```

新版的映像也可以在開發機上重新部署同一個 commit 產生：`deploy.sh --host sit@10.20.31.40`。

## 注意

- 切回舊看板後，舊看板的 AI 報告輪播會恢復，每 10 分鐘呼叫一次 LLM；這是舊版本身的行為。
- 舊看板的網址也是 `http://10.20.31.40:8000/`。新看板的大電視網址 `/?lang=zh&tv=1` 在舊版會顯示舊首頁。
- 兩版都不碰同一台主機上的其他服務（pixi-test、iw-solo-pixi）。
