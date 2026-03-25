# WiFi Dashboard 部署與還原標準作業程序 (SOP)

本文件指引使用者如何在全新的 Ubuntu 伺服器上建立 Docker 環境，並還原資料庫。

## 1. 環境需求
- **作業系統**: Ubuntu 20.04 LTS 或更新版本。
- **硬體建議**: i3 CPU, 16GB RAM, SSD 並掛載於 `/mnt/md127`。
- **必要軟體**: 
  - Docker (CE)
  - Docker Compose (V2+)

## 2. 快速部署步驟

### 步驟 A: 準備專案目錄
請將 `DB_backups`, `dockerup-docs`, `dockerup-essential` 複製到伺服器的以下路徑：
`/mnt/md127/WIFI_YFP_DashBoard`

### 步驟 B: 設定環境變數
1. 進入核心目錄：
   ```bash
   cd /mnt/md127/WIFI_YFP_DashBoard/dockerup-essential
   ```
2. 複製並建立 `.env` 檔案：
   ```bash
   cp .env.example .env
   ```
3. 確認 `.env` 中的 `STORAGE_PATH` 指向正確路徑：
   `STORAGE_PATH=/mnt/md127/WIFI_YFP_DashBoard`

### 步驟 C: 啟動 Docker 服務
執行以下指令構建並啟動所有服務 (PostgreSQL, FastAPI, Grafana)：
```bash
docker compose up -d
```
*注意：首次啟動時，PostgreSQL 將自動執行 `schema.sql` 初始化資料表格式。*

---

## 3. 資料庫還原步驟

如果您有舊的備份檔 (位於 `DB_backups/`)，請按照以下步驟還原：

### 使用自動化腳本 (推薦)
1. 進入維護目錄：
   ```bash
   cd /mnt/md127/WIFI_YFP_DashBoard/dockerup-docs/02_Maintenance/scripts
   ```
2. 執行還原腳本 (請替換為實際的備份檔路徑)：
   ```bash
   sudo ./restore_db_oneclick.sh ARCHIVE=/mnt/md127/WIFI_YFP_DashBoard/DB_backups/your_backup_file.tar.gz
   ```

### 手動還原方式 (備案)
如果腳本無法執行，可手動將 `.dump` 檔案匯入容器：
```bash
# 1. 將 dump 檔案複製到容器內
docker cp /path/to/backup.dump wifitest-db:/tmp/restore.dump

# 2. 執行 pg_restore
docker exec -it wifitest-db pg_restore -U qc -d wifitest --clean --if-exists /tmp/restore.dump
```

---

## 4. 服務驗證
啟動後，您可以透過以下連結訪問服務：
- **WiFi 儀表板**: `http://<伺服器IP>:8000`
- **Grafana 監控**: `http://<伺服器IP>:3000` (預設帳密: admin / admin)

**檢查健康狀態：**
```bash
curl http://localhost:8000/health
```

---

## 5. 目錄結構參考
- `dockerup-essential/`: Docker 核心配置與 API 程式。
- `dockerup-docs/`:
    - `01_Deployment/`: 部署指南與安裝腳本。
    - `02_Maintenance/`: 資料庫備份/還原腳本。
    - `03_Reference/`: 架構圖與技術參考。
- `DB_backups/`: 資料庫歷史備份存放處。
