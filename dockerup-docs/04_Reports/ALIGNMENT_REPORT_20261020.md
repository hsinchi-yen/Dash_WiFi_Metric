# WIFI_YFP_DashBoard 對齊比對報告

- 產生日期:2026-10-20
- 備份批次(TS):`20261020_145811`
- 範圍:本機工作目錄 ↔ 遠端部署 `sit@10.20.31.40:/mnt/md127/WIFI_YFP_DashBoard` ↔ 遠端線上資料庫 `wifitest`
- 本報告不含任何帳號密碼。

---

## 1. 結論摘要

| # | 結論 |
|---|---|
| 1 | **程式碼已經對齊。** 後端、前端、compose、schema、Grafana 設定等 14 個檔案(含 2 個樣本 log),本機與遠端**逐位元組相同**。git HEAD(2026-05-04)的內容 = 本機工作樹 = 遠端部署。 |
| 2 | `git status` 的 17 個 `M` **全是換行符號雜訊**(工作樹/遠端為 CRLF、HEAD 為 LF;`git diff --ignore-space-at-eol -w` 為空)。沒有「只存在於遠端、不在任何 commit」的程式碼。 |
| 3 | 實質差異只有 3 個檔案 + 1 個搬移後不同的檔案,影響都很小(見 §4)。其中**唯一遠端比本機新**的是 `fix_project_permissions.sh`。 |
| 4 | **線上 DB 已完整備份到本機並驗證可還原。** 64,181 筆,逐列內容雜湊(含 `raw_log`)與線上完全相同;備份期間零漂移。 |
| 5 | **DB 是 64,181 份原始 log 的唯一副本。** 兩端 `logs/`、`WiFiTestLogs/` 只有樣本;原始 log 只存在 `raw_log` 欄位。備份檔因此是重要資產。 |
| 6 | `schema.sql` 可原樣重建出與線上**完全相同**的 schema(欄位、型別、預設值、索引、約束、view 定義皆一致),沒有 schema 漂移。 |
| 7 | 真正需要處理的是**設定與文件的不一致**(md0/md127、uid 999/70、過期路徑、明文密碼、EOL),見 §6。 |

---

## 2. 環境事實

| 項目 | 內容 |
|---|---|
| 本機 | `/home/hermesrunner/ClaudeWorkSpace/TN_Work/WIFI_YFP_DashBoard`(Linux 區分大小寫;實際是 `TN_Work` 與 `DashBoard`) |
| 本機角色 | **測試環境。** `/mnt/md127` 以本機目錄替代,部署到遠端時再改回(見 §7) |
| 遠端 | `sit-wifi-metric-mon`,專案在 `/mnt/md127/WIFI_YFP_DashBoard`(`/dev/md127`,916G,用 1%) |
| 遠端 git | **不是 git repo**,只能逐檔比對 |
| 遠端 wifitest 服務 | `wifitest-db`(PG 16.13 alpine,5432)、`wifitest-api`(8000)、`wifitest-grafana`(3000);皆 `restart: unless-stopped` |
| 同機其他服務 | `pixi-test-*`(8001/5433)、`iw-solo-pixi-*`(8004/5434),與本專案無關,備份期間狀態未變 |
| 本機 PG | `postgres:16-alpine` = 16.15(遠端 16.13,同大版本,dump/restore 相容) |

---

## 3. 本輪執行紀錄

### 3.1 DB 備份(遠端 → 本機,串流,遠端零寫入)

`pg_dump -Fc` 與 `pg_dumpall --globals-only` 經 ssh 直接寫到本機,格式與 `02_Maintenance/scripts/backup_db_oneclick.sh` 相同,既有 `restore_db_oneclick.sh` 可直接使用。不停機、不用 sudo、遠端沒有新增任何檔案。

| 檔案(`DB_backups/`) | 大小 | SHA-256 |
|---|---|---|
| `wifitest_20261020_145811.dump` | 405,365,629 B | `1235d24d940001e7dfd95d8fa31843ab06d592b18703827c61a9be6c97b92126` |
| `globals_20261020_145811.sql` | 659 B | 見 `SHA256SUMS_20261020_145811.txt` |
| `db_backup_20261020_145811.tar.gz` | 397,975,637 B | 見 `SHA256SUMS_20261020_145811.txt` |

- 傳輸完整性:遠端邊傳邊算的 SHA-256 = 本機落地檔 SHA-256(一致)。
- 耗時:dump 93 秒。
- **備份前後漂移檢查**:筆數、`max(created_at)`、`file_hash` 內容雜湊(`d22d8ffe88b28d3e2b91f3cf85f1c030`)完全相同;遠端 9 個容器(含另外兩套無關服務)的狀態皆未變。
- 既有的 2026-03-25 備份未動。遠端 `DB_backups/` 內 3.6G 舊備份**未拉取**(最新也只到 2026-05-04,已過時)。
- ⚠ 備份檔含完整 `raw_log` 與 DB role 的密碼雜湊(globals),`DB_backups/` 已在 `.gitignore`,**不可提交、不可外傳**。

### 3.2 還原驗證(本機獨立容器)

- 容器 `wifitest-db-local`,綁 `127.0.0.1:55432`,資料目錄為 `dockerup-essential/docker-data/pgdata`(專案既有的 `STORAGE_PATH=.` 本機測試慣例,已在 `.gitignore`),角色/密碼沿用專案預設(`qc` / `qcpass`,僅本機回環位址)。
- `pg_restore -l`:18 個物件(1 表、3 view、7 index、2 constraint…)。`pg_restore`:**0 錯誤,57 秒**。
- 還原後大小 807MB(線上 817MB),差異為線上的 dead tuple / 索引膨脹,屬正常。

### 3.3 遠端內容快照

- `_remote_snapshot/20261020_145811/`:遠端專案 48 個檔案、1.2MB,與遠端**逐檔 md5 相同**。
- 排除:`docker-data/`(pgdata、grafana-data 由 DB 備份涵蓋;Grafana 只有 1 個 provisioned dashboard 與 1 個 read-only datasource,無 UI 手建內容)、`DB_backups/`、`backups/`、`.venv/`、`__pycache__/`。
- 以 `.git/info/exclude` 隔離,**未修改任何已追蹤檔案,未推送任何東西到遠端,未 commit**。

---

## 4. 目錄差異(遠端 48 檔 ↔ 本機範圍內 50 檔)

本機範圍 = `git ls-files -co --exclude-standard`,另排除 `.claude*/`、`.opencode/`、`build/`、`dist/`。遠端 docs 為平面式,本機已整理為 `01_Deployment / 02_Maintenance / 03_Reference`,比對時已套用路徑對映。

| 類別 | 數量 | 內容 |
|---|---|---|
| 同路徑、位元組相同 | 14 | `api/app.py`、`ai_summary_helper.py`、`Dockerfile`、`requirements.txt`、`docker-compose.yml`、`wifi_dashboard.html`、`log_parser.py`、`schema.sql`、`.env.production`、grafana 的 json/yaml ×3、`logs/` 樣本 ×2 |
| 搬移後相同 | 14 | `DBEAVER_SQL_OPERATIONS.md`、`UBUNTU_DISK_MANAGEMENT.md`、`ubuntu-deploy.sh`、`DB_RETURN.md`(= 本機 `DB_BACKUP_RESTORE_SOP.md`)、`scripts/*` 10 支 |
| **同路徑但不同** | 3 | 見下表 |
| **搬移後不同** | 1 | `fix_project_permissions.sh`(見下表) |
| 只在遠端 | 16 | 見下方 |
| 只在本機 | 18 | 見下方 |

### 4.1 內容不同的檔案

| 檔案 | 差異 | 較新/正確的一邊 |
|---|---|---|
| `dockerup-essential/wifi_stress_log_analyzer.py` | **僅第 19 行** `APP_VERSION`:本機 `2026.03.26`、遠端 `2026.03.20`(檔案大小相同) | 本機 |
| `dockerup-essential/.env.example` | `STORAGE_PATH`:本機 `/mnt/md127/WIFI_YFP_DashBoard`、遠端 `/mnt/md0/WIFI_YFP_DASHBOARD` | 本機(md127 是實際掛載) |
| `.gitignore`(根目錄) | 完全不同。遠端版忽略 `.env`、`*.sql`、`*.sql.gz` 等;本機版忽略 pgdata、`DB_backups/`、`*.dump`、`__pycache__` | 兩邊各有用途,需合併(§6) |
| `02_Maintenance/scripts/fix_project_permissions.sh` | 4 處 `/mnt/md127`(遠端)↔ `/mnt/md0`(本機) | **遠端**(md127 正確) |

### 4.2 只在遠端(16)——已存入快照,未併入追蹤樹

- 根目錄:`.env`(僅 `STORAGE_PATH=.`,compose 不會讀)、`.env.example`、`.env.production`(與 `dockerup-essential/.env.production` 不同)、`DeploySystemGUIDE.MD`
- 本機整理時未保留的舊文件(8):`DEPLOYMENT_GUIDE.md`、`DOCKER_COMPOSE_DEPLOYMENT.md`、`docker-compose-ubuntu.yml`、`Deployment_GuideLine.txt`、`MigrationGuide.txt`、`README_MIGRATION_QUICKSTART.md`、`UBUNTU_DEPLOYMENT_SUMMARY.md`、`UBUNTU_QUICK_START.md`
- 舊版/殘留(4):`oldbackup/app.py`、`oldbackup/wifi_dashboard.html`、`wifi_dashboard.html.bak`、1 個 `wifi_stress_report_…20260323_153527.csv`

### 4.3 只在本機(18)

- 新文件/資產:`01_Deployment/DOCKER_DEPLOY_SOP.md`、`01_Deployment/deploy_aiagent.md`、`03_Reference/system_architecture_overview.svg`、`backup_op.md`、`implementation_plan.md`、`README.md`、`README_CHT.md`
- 新程式/測試/建置:`api/test_ai_summary_prompt.py`、`test_parse_terminal_segments.js`、`new_wifi_stress_log_analyzer.py`(1,759 行,舊版 1,625 行)、`wifi_stress_log_analyzer.spec`、`build_linux_bin.sh`
- 根目錄部署腳本(6,**含明文密碼**):`deploy_script.py`、`deploy_script_docker_compose.py`、`deploy_fix.py`、`check_status.py`、`check_remote_db.py`、`check_remote_fix.py`
- 另有 `dockerup-essential/build/`(16M)、`dist/`(180M)PyInstaller 產物,未被忽略。

---

## 5. 資料庫比對

### 5.1 線上 DB ↔ 還原後本機 DB:**完全一致**

| 檢查項 | 線上 | 本機還原 | 結果 |
|---|---|---|---|
| `test_record` 筆數 | 64,181 | 64,181 | ✅ |
| distinct `file_hash` / `sn` / 工單 / 產品 | 64,181 / 63,881 / 77 / 11 | 同 | ✅ |
| `test_time` 範圍 | 2026-01-06 17:24:54 ~ 2026-10-01 16:59:31 | 同 | ✅ |
| `created_at` 範圍 | 2026-04-30 07:29:37 ~ 2026-10-06 06:05:31 | 同 | ✅ |
| `sum(length(raw_log))` | 2,852,907,043 | 同 | ✅ |
| 數值欄位加總(avg_24g/avg_5g/bt_loss…) | — | 同 | ✅ |
| 各月 / 各產品筆數與 PASS 數 | — | 同 | ✅ |
| **逐列內容雜湊** `md5(string_agg(md5(t::text) order by id))` | `8e34ab8775231d24f04fbe813cc9f470` | 同 | ✅ |
| Schema 簽章(欄位、索引、約束、view 定義) | 72 欄(表+view)/ 9 index / 2 constraint / 3 view | 同 | ✅ |

### 5.2 DB ↔ 工作目錄程式碼

| 比對 | 結果 |
|---|---|
| 線上 schema ↔ `dockerup-essential/schema.sql`(套用到全新庫後比對) | ✅ **完全一致**,無漂移 |
| `logs/…082710_513126033667…PASS.txt` 的 SHA-256 ↔ `file_hash` | ✅ 吻合(id 14295) |
| `logs/…182710_513126039999…PASS.txt` | 未入庫(日期/序號為測試用假資料,符合預期) |
| `source_file` 是否含路徑 | 0 筆;全是純檔名,**無法回推原始 log 的存放位置** |
| 原始 log 在磁碟上的副本 | 兩端皆無;DB 的 `raw_log` 是**唯一副本** |

### 5.3 資料概況(供 UI 重構參考)

- 各月筆數(PASS):01 月 7,582(7,382)、02 月 1,254、03 月 7,313、04 月 9,117、05 月 3,525、06 月 10,031、07 月 11,116、08 月 9,244、09 月 4,737、10 月 262(僅到 10/01)。
- 整體 PASS 63,958 / FAIL 223(99.65%)。FAIL 集中:TN-73_PICO-IMX6 103(1.36%)、TN-86_PICO-IMX7 90(0.43%)、TN-175 13、TN-167 9。01 月就有 200 筆 FAIL,之後幾乎沒有。
- 11 個產品:TN-86 20,950、TN-410 14,720、TN-175 10,862、TN-73 7,564、TN-99 3,242、TN-250 2,720、TN-111 2,034、TN-167 1,013、TN-228 601、TN-165 261、TN-112 214。
- 欄位缺值:`avg_24g` 32、`avg_5g` 26、`bt_avg_latency` 42、`rssi_before_24g` 931、`band_result_24g/5g` 928/921。
- **`attempt_count_24g/5g` 全為 1(0 筆重測)。** 任何依賴重測次數的分析(例如 `/api/retries`)目前不會有資料。
- 三個 view(`v_yield_by_wo` 77 列、`v_fail_detail` 223 列、`v_unstable_units` **56,694 列,占 88%**)**未被 `app.py`、`wifi_dashboard.html`、Grafana JSON 引用**。`v_unstable_units` 的條件 `std_5g > 5 OR rssi_delta_5g < -20` 對現有資料幾乎恆成立,門檻疑似未校準。
- 最近一筆測試資料 2026-10-01、最後入庫 2026-10-06(今天 10/20),入庫目前閒置。`grafana.db` 今天 14:52 仍有寫入(不代表一定有人操作,Grafana 內部狀態也會寫入)。

---

## 6. 不一致與風險清單

嚴重度:🔴 需優先處理 / 🟠 部署前處理 / 🟡 順手整理。「環境」欄指影響哪裡。

| # | 嚴重度 | 環境 | 問題 | 證據 |
|---|---|---|---|---|
| R1 | 🟠 | 遠端部署 | **`/mnt/md0` 與 `/mnt/md127` 混用。** 實際掛載與運行都是 md127。若把 `dockerup-essential/.env.production` 複製成 `.env` 部署,`STORAGE_PATH=/mnt/md0/WIFI_YFP_DASHBOARD`(連大小寫都不同)會**建出一個空的 pgdata**。本機測試環境以本機目錄替代,不受影響,**部署時再統一為 md127**。 | md0 殘留:`.env.production:3`、`fix_project_permissions.sh:4,18,20,60`、`wifi-md127-mount.sh:4`、`wifi-dashboard.service:6,10`、遠端 `.env.example`。`/etc/fstab` 同一 UUID 同時列在 `/mnt/md0` 與 `/mnt/md127`。 |
| R2 | 🟠 | 還原/部署文件 | **pgdata 擁有者寫 999:999,實際是 uid 70**(alpine 映像)。照 SOP 做 cold restore 後 postgres 起不來。 | `ubuntu-deploy.sh:101`、`restore_db_oneclick.sh:121`、`DB_BACKUP_RESTORE_SOP.md:110,140`、`UBUNTU_DISK_MANAGEMENT.md:144,429`。遠端與本機 pgdata 實測皆為 uid 70。 |
| R3 | 🔴 | 安全 | **6 支根目錄腳本含明文 SSH 密碼**,未被 `.gitignore` 忽略(目前 untracked,一個 `git add .` 就會進 repo)。此密碼也已出現在本次對話。`deploy_fix.py` 另含失效的 `d:\TN_Tool_Projects\…` 路徑。 | 6 支各 1 行 `password=`;值不在此列出。建議改 SSH key 並更換密碼。 |
| R4 | 🟠 | 安全 | compose 寫死 `qcpass`、Grafana `admin/admin`;5432/3000/8000 對區網全開(`0.0.0.0`)。`app.py` 有 `/api/db-tweak/*`(含 `delete-record`、`delete-workorder`)端點,**本輪未審查其驗證機制**。 | `docker-compose.yml:31,56,77`;`docker ps` 連接埠;`app.py:1087-1239` |
| R5 | 🟡 | 版本控管 | **無 `.gitattributes`**,CRLF/LF 混用,每個 diff 被 8,703 行雜訊淹沒。`.service` 檔為 CRLF,systemd 對 `\r` 不一定容忍。 | `git diff --stat`;`file` |
| R6 | 🟡 | 文件 | SOP 內路徑 `dockerup-docs/scripts/…`(已搬到 `02_Maintenance/scripts/`)與 `README_MIGRATION_QUICKSTART.md`(本機不存在)失效。 | `DB_BACKUP_RESTORE_SOP.md:17,18,34,48,66,85,103,174,175,177` |
| R7 | 🟡 | 版本控管 | `build/`、`dist/`(196MB)、6 支部署腳本、`implementation_plan.md` 等 untracked 且未忽略。 | `git status` |
| R8 | 🟡 | 部署 | 倉庫的 `wifi-dashboard.service`、`wifi-md127-mount.service` **並未安裝**到遠端(無對應 systemd 單元);重開機後靠 Docker `unless-stopped` 恢復,但前提是 `/mnt/md127` 先掛好。 | 遠端 `/etc/systemd/system` 無相關單元 |
| R9 | 🟡 | 資料 | `attempt_count` 全為 1、三個 view 未使用且 `v_unstable_units` 門檻失準、約 1.4% 的 RSSI/band 缺值。 | §5.3 |
| R10 | 🟡 | 維運 | 遠端 `DB_backups/` 累積 3.6G(含 1.4G、1.2G、0.7G 純文字 SQL),`backups/restore_tmp` 為 root 擁有。空間不是問題(1%),但可整理。是否有排程備份**未驗證**。 | 遠端 `ls`/`du` |

---

## 7. 本機測試環境使用說明

> 本機視為測試環境,`/mnt/md127` 以專案內本機目錄替代(`STORAGE_PATH=.` ⇒ `dockerup-essential/docker-data/`)。部署到遠端時,`STORAGE_PATH` 改回 `/mnt/md127/WIFI_YFP_DashBoard`(或不設,走 compose 預設值)。

**使用本機還原庫**(容器目前為**停止**狀態,避免與之後的 compose 同時佔用同一資料目錄):

```bash
docker start wifitest-db-local
psql postgresql://qc:qcpass@127.0.0.1:55432/wifitest      # 或 docker exec -it wifitest-db-local psql -U qc -d wifitest
docker stop wifitest-db-local
```

**改用完整 compose 堆疊在本機跑**(未實測;需先 `docker stop wifitest-db-local`,兩者不可同時使用同一個 pgdata):

```bash
cd dockerup-essential && STORAGE_PATH=. docker compose up -d
```

pgdata 已含還原資料,PG 偵測到非空資料目錄會跳過 `schema.sql` 初始化。

**重新備份線上 DB**(密碼以 `SSHPASS` 環境變數傳入,不要寫進檔案):

```bash
ssh sit@10.20.31.40 "docker exec wifitest-db pg_dump -U qc -d wifitest -Fc" > DB_backups/wifitest_$(date +%Y%m%d_%H%M%S).dump
```

**清理本機測試資料**(pgdata 擁有者為 uid 70,需 sudo 或透過容器):

```bash
docker rm -f wifitest-db-local
docker run --rm -v "$PWD/dockerup-essential/docker-data:/d" alpine rm -rf /d/pgdata
```

---

## 8. 給「修改對應策略」階段的輸入(尚未決策)

1. **建立乾淨基準**:先處理 R5(`.gitattributes` + EOL 正規化),再以單獨 commit/tag 標記「對齊完成基準」,之後重構的 diff 才讀得懂。
2. **前端現況**:`wifi_dashboard.html` 單檔 4,560 行、`app.py` 1,519 行。既有 `implementation_plan.md`(Cyber Metal 視覺 + Advanced Test Analysis 頁)聲明「僅動前端、不動後端 API 與 `wifi_stress_log_analyzer.py`」,可作為策略起點。
3. **「產出專案的功能」待確認**:是指 `wifi_stress_log_analyzer.py` / `new_wifi_stress_log_analyzer.py`(PyInstaller 打包的報表產出工具),還是 dashboard 內的匯出/AI 報告功能?兩者的資料來源與相依不同。
4. **可用資料**:§5.3 的概況可作為 UI 測資;若要測「重測」「不穩定單元」類畫面,現有資料沒有 `attempt_count>1`,需要另備測資或調整邏輯。
5. **部署前清單**:R1(md127 統一)、R2(uid 70)、R3(明文密碼)、R4(預設密碼與對外埠)。
6. **`fix_project_permissions.sh`** 遠端版較新,同意後可一行修正本機版。

---

## 9. 附錄:產出物清單

| 路徑 | 說明 | git |
|---|---|---|
| `DB_backups/wifitest_20261020_145811.dump` | pg_dump custom format | 忽略 |
| `DB_backups/globals_20261020_145811.sql` | roles(含密碼雜湊) | 忽略 |
| `DB_backups/db_backup_20261020_145811.tar.gz` | 上兩者打包,SOP 格式 | 忽略 |
| `DB_backups/SHA256SUMS_20261020_145811.txt` | 雜湊清單,`sha256sum -c` 可驗 | 忽略 |
| `_remote_snapshot/20261020_145811/` | 遠端專案原樣快照(48 檔) | `.git/info/exclude` |
| `dockerup-essential/docker-data/pgdata/` | 本機測試庫資料目錄(uid 70) | 忽略 |
| 容器 `wifitest-db-local`(已停止) | 127.0.0.1:55432 | — |
| `dockerup-docs/04_Reports/ALIGNMENT_REPORT_20261020.md` | 本報告 | untracked |
