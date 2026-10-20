# PostgreSQL 資料庫備份操作手冊

**適用環境：** `sit@10.20.31.40` — WiFi YFP Dashboard  
**資料庫容器：** `wifitest-db`  
**資料庫名稱：** `wifitest`  
**資料庫使用者：** `qc`  
**備份目錄：** `/mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/`

---

## 備份

### 一次性備份（SQL 明文格式）

```bash
mkdir -p /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup

docker exec wifitest-db pg_dump -U qc -d wifitest \
  > /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/wifitest_$(date +%Y%m%d_%H%M%S).sql
```

### 一次性備份（壓縮格式，建議用於正式備份）

```bash
mkdir -p /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup

docker exec wifitest-db pg_dump -U qc -d wifitest -Fc \
  > /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/wifitest_$(date +%Y%m%d_%H%M%S).dump
```

> `-Fc` 為 PostgreSQL custom format，壓縮率高、支援選擇性還原，還原時使用 `pg_restore`。

### 確認備份檔案

```bash
ls -lh /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/
```

---

## 還原

### 從 SQL 明文格式還原

```bash
# 將 <filename> 替換為實際備份檔名
docker exec -i wifitest-db psql -U qc -d wifitest \
  < /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/<filename>.sql
```

### 從壓縮格式（.dump）還原

```bash
docker exec -i wifitest-db pg_restore -U qc -d wifitest -Fc \
  < /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/<filename>.dump
```

> 還原前建議先確認容器正常運行：`docker compose ps`

---

## 定期自動備份（cron）

SSH 進遠端後，編輯 crontab：

```bash
crontab -e
```

加入以下排程（每日 02:00 自動備份，保留 30 天）：

```cron
0 2 * * * mkdir -p /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup && docker exec wifitest-db pg_dump -U qc -d wifitest -Fc > /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/wifitest_$(date +\%Y\%m\%d).dump && find /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/ -name "*.dump" -mtime +30 -delete
```

確認 crontab 已生效：

```bash
crontab -l
```

---

## 手動清理舊備份

```bash
# 刪除 30 天前的備份
find /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/ -name "*.dump" -mtime +30 -delete
find /mnt/md127/WIFI_YFP_DashBoard/docker-data/backup/ -name "*.sql"  -mtime +30 -delete
```

---

## 快速驗證備份內容

```bash
# 確認備份的 test_record 筆數
docker exec wifitest-db psql -U qc -d wifitest -c "SELECT COUNT(*) FROM test_record;"
```
