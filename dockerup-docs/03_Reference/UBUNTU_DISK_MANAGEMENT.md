# Ubuntu Linux /dev/sdb 磁盤管理與部署指南

## 1. 磁盤準備

### 1.1 檢查可用磁盤

```bash
# 列出所有磁盤
sudo lsblk

# 詳細信息
sudo fdisk -l

# 預期輸出示例：
# NAME   MAJ:MIN RM  SIZE RO TYPE MOUNTPOINTS
# sda      8:0    0  100G  0 disk
# ├─sda1   8:1    0    1G  0 part /boot
# └─sda2   8:2    0   99G  0 part /
# sdb      8:16   0  500G  0 disk
# └─sdb1   8:17   0  500G  0 part
```

### 1.2 使用自動部署腳本（推薦）

```bash
# 下載並執行部署腳本
cd /tmp
wget https://your-repo/ubuntu-deploy.sh
sudo bash ubuntu-deploy.sh

# 或直接複製並執行本地文件
sudo bash /path/to/ubuntu-deploy.sh
```

**此腳本會自動執行：**
- 安裝 Docker 和 Docker Compose
- 格式化 /dev/sdb（如需要）
- 掛載 /dev/sdb 到 /mnt/wifi-storage
- 創建 Docker 數據目錄
- 配置防火牆

### 1.3 手動配置 /dev/sdb（如果不使用自動腳本）

#### 步驟 A：檢查磁盤

```bash
# 檢查 /dev/sdb 是否存在
lsblk | grep sdb

# 檢查磁盤狀態
sudo smartctl -a /dev/sdb  # 需要安裝 smartmontools
```

#### 步驟 B：清除舊分區表

```bash
sudo dd if=/dev/zero of=/dev/sdb bs=512 count=1
sudo partprobe /dev/sdb
```

#### 步驟 C：創建分區

**方法 1：使用 parted（推薦）**
```bash
# 創建 GPT 分區表
sudo parted -s /dev/sdb mklabel gpt

# 創建單個分區使用整個磁盤
sudo parted -s /dev/sdb mkpart primary ext4 1 100%

# 驗證分區
sudo parted -l /dev/sdb
```

**方法 2：使用 fdisk**
```bash
sudo fdisk /dev/sdb
# 輸入以下命令：
# n - 新建分區
# p - 主分區
# 1 - 分區號
# Enter - 默認起始扇區
# Enter - 默認結束扇區
# w - 寫入並退出
```

#### 步驟 D：格式化分區

```bash
# 等待分區創建完成
sleep 2

# 格式化為 ext4
sudo mkfs.ext4 -F /dev/sdb1

# 驗證格式化
sudo blkid /dev/sdb1
```

#### 步驟 E：掛載分區

```bash
# 創建掛載點
sudo mkdir -p /mnt/wifi-storage

# 掛載分區
sudo mount /dev/sdb1 /mnt/wifi-storage

# 驗證掛載
mount | grep sdb
df -h /mnt/wifi-storage
```

#### 步驟 F：設置開機自動掛載

```bash
# 獲取分區 UUID
sudo blkid /dev/sdb1

# 編輯 /etc/fstab
sudo nano /etc/fstab

# 添加以下行（替換 UUID 為實際值）：
# UUID=12345678-1234-1234-1234-123456789012 /mnt/wifi-storage ext4 defaults,nofail 0 2

# 或直接添加：
echo "/dev/sdb1 /mnt/wifi-storage ext4 defaults,nofail 0 2" | sudo tee -a /etc/fstab

# 測試 fstab 配置
sudo mount -a

# 驗證
df -h /mnt/wifi-storage
```

### 1.4 設置目錄結構

```bash
# 創建 Docker 數據目錄
sudo mkdir -p /mnt/wifi-storage/docker-data/{pgdata,grafana-data,app-logs}

# 設置權限
# PostgreSQL 用戶 UID:999，GID:999
sudo chown -R 999:999 /mnt/wifi-storage/docker-data/pgdata
sudo chmod 700 /mnt/wifi-storage/docker-data/pgdata

# Grafana 用戶 UID:472，GID:472
sudo chown -R 472:472 /mnt/wifi-storage/docker-data/grafana-data
sudo chmod 700 /mnt/wifi-storage/docker-data/grafana-data

# 應用日誌
sudo mkdir -p /mnt/wifi-storage/docker-data/app-logs
sudo chown 1000:1000 /mnt/wifi-storage/docker-data/app-logs
sudo chmod 755 /mnt/wifi-storage/docker-data/app-logs

# 創建應用部署目錄
sudo mkdir -p /mnt/wifi-storage/wifi-dashboard
sudo chown $USER:$USER /mnt/wifi-storage/wifi-dashboard
sudo chmod 755 /mnt/wifi-storage/wifi-dashboard

# 驗證目錄結構
tree /mnt/wifi-storage
```

---

## 2. 磁盤監控與管理

### 2.1 查看磁盤使用情況

```bash
# 實時查看磁盤使用
df -h

# 查看具體分區
df -h /mnt/wifi-storage

# 查看目錄大小
du -sh /mnt/wifi-storage/*
du -sh /mnt/wifi-storage/docker-data/*

# 實時監控磁盤 I/O
iostat -x 1 10  # 需要安裝 sysstat

# 查看磁盤讀寫速度
iotop  # 需要安裝 iotop
```

### 2.2 磁盤空間警告

```bash
# 檢查 inode 使用率
df -i /mnt/wifi-storage

# 查找大文件
find /mnt/wifi-storage -type f -size +1G

# 查找舊日誌並清理
find /mnt/wifi-storage/docker-data/app-logs -name "*.log" -mtime +30 -delete
```

### 2.3 磁盤性能優化

#### 啟用寫緩存

```bash
# 檢查當前設置
sudo hdparm -W /dev/sdb

# 啟用寫緩存（加快性能）
sudo hdparm -W1 /dev/sdb

# 永久設置（需要重啟）
sudo bash -c 'echo "vm.dirty_ratio = 15" >> /etc/sysctl.conf'
sudo bash -c 'echo "vm.dirty_background_ratio = 5" >> /etc/sysctl.conf'
sudo sysctl -p
```

#### 調整 I/O 調度器

```bash
# 查看當前 I/O 調度器
cat /sys/block/sdb/queue/scheduler

# 更改為 noop（延遲最小）
echo noop | sudo tee /sys/block/sdb/queue/scheduler

# 或更改為 deadline（固態硬碟推薦）
echo deadline | sudo tee /sys/block/sdb/queue/scheduler

# 永久設置（編輯 Grub）
sudo nano /etc/default/grub
# 找到 GRUB_CMDLINE_LINUX_DEFAULT 行，添加：elevator=noop
```

---

## 3. Docker 部署配置

### 3.1 環境變數設置

```bash
# 進入應用目錄
cd /mnt/wifi-storage/wifi-dashboard

# 複製 .env 示例文件
cp .env.example .env

# 編輯環境變數
nano .env

# 確保以下重要變數已設置：
# STORAGE_PATH=/mnt/wifi-storage
# DB_PASSWORD=your-secure-password
# GRAFANA_ADMIN_PASSWORD=your-grafana-password
```

### 3.2 選擇正確的 docker-compose.yml

```bash
# 使用 Ubuntu Linux 專用配置
cp docker-compose-ubuntu.yml docker-compose.yml

# 驗證配置（會顯示 docker-compose.yml 內容和環境變數替換）
docker-compose config
```

### 3.3 構建並啟動容器

```bash
# 構建鏡像
docker-compose build

# 後台啟動容器
docker-compose up -d

# 驗證容器狀態
docker-compose ps

# 查看容器日誌
docker-compose logs -f

# 查看特定容器日誌
docker logs -f wifitest-api
docker logs -f wifitest-db
```

---

## 4. 防火牆配置

### 4.1 UFW（Ubuntu 默認防火牆）

```bash
# 檢查防火牆狀態
sudo ufw status

# 啟用防火牆
sudo ufw enable

# 開放 SSH（重要！避免被鎖定）
sudo ufw allow 22/tcp

# 開放 WiFi Dashboard 端口
sudo ufw allow 8000/tcp comment "WiFi API"
sudo ufw allow 3000/tcp comment "Grafana"
sudo ufw allow 5432/tcp comment "PostgreSQL"

# 驗證規則
sudo ufw status numbered

# 刪除規則（如需要）
sudo ufw delete allow 3000/tcp
```

### 4.2 允許特定 IP 訪問

```bash
# 只允許特定 IP 訪問 API
sudo ufw allow from 10.88.88.33 to any port 8000
sudo ufw allow from 10.88.88.33 to any port 3000

# 驗證
sudo ufw status numbered
```

---

## 5. 備份與恢復

### 5.1 手動備份

```bash
# 備份 PostgreSQL 數據
docker exec wifitest-db pg_dump -U qc -d wifitest \
    > /mnt/wifi-storage/backups/wifitest_$(date +%Y%m%d_%H%M%S).sql

# 備份整個 docker-data 目錄
tar -czf /mnt/wifi-storage/backups/docker-data_$(date +%Y%m%d_%H%M%S).tar.gz \
    /mnt/wifi-storage/docker-data/

# 列出備份文件
ls -lh /mnt/wifi-storage/backups/
```

### 5.2 自動備份腳本

```bash
# 創建備份腳本
sudo nano /usr/local/bin/wifi-backup.sh
```

文件內容：
```bash
#!/bin/bash

BACKUP_DIR="/mnt/wifi-storage/backups"
RETENTION_DAYS=30
DATE=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# 備份 PostgreSQL
docker exec wifitest-db pg_dump -U qc -d wifitest \
    | gzip > $BACKUP_DIR/db_$DATE.sql.gz

# 備份 Grafana 配置
tar -czf $BACKUP_DIR/grafana_$DATE.tar.gz \
    /mnt/wifi-storage/docker-data/grafana-data/

# 刪除超期備份
find $BACKUP_DIR -name "*.sql.gz" -mtime +$RETENTION_DAYS -delete
find $BACKUP_DIR -name "*.tar.gz" -mtime +$RETENTION_DAYS -delete

echo "✓ 備份完成: $DATE"
```

設置權限並添加 Cron 計畫：
```bash
sudo chmod +x /usr/local/bin/wifi-backup.sh

# 每天凌晨 2 點執行備份
sudo crontab -e
# 添加行：0 2 * * * /usr/local/bin/wifi-backup.sh
```

### 5.3 恢復備份

```bash
# 恢復 PostgreSQL 備份
gunzip < /mnt/wifi-storage/backups/db_YYYYMMDD_HHMMSS.sql.gz | \
    docker exec -i wifitest-db psql -U qc -d wifitest

# 恢復 Grafana 配置
docker-compose down
tar -xzf /mnt/wifi-storage/backups/grafana_YYYYMMDD_HHMMSS.tar.gz -C /
docker-compose up -d
```

---

## 6. 故障排除

### 故障 1：磁盤掛載失敗

```bash
# 檢查錯誤信息
sudo dmesg | tail -20

# 檢查分區
sudo fdisk -l

# 修復文件系統
sudo fsck -y /dev/sdb1

# 重新掛載
sudo mount -a
```

### 故障 2：權限被拒

```bash
# 檢查UID/GID對應
id postgres
id grafana
id $USER

# 修復所有權
sudo chown -R 999:999 /mnt/wifi-storage/docker-data/pgdata
sudo chown -R 472:472 /mnt/wifi-storage/docker-data/grafana-data
```

### 故障 3：磁盤滿

```bash
# 檢查使用情況
du -sh /mnt/wifi-storage/*

# 清理 Docker 日誌
docker system prune -a

# 清理舊備份
find /mnt/wifi-storage/backups -mtime +30 -delete

# 擴展分區（如有未分配空間）
sudo parted -s /dev/sdb resizepart 1 100%
sudo resize2fs /dev/sdb1
```

---

## 7. 常用命令速查表

| 任務 | 命令 |
|------|------|
| 檢查磁盤狀態 | `df -h /mnt/wifi-storage` |
| 展開目錄大小 | `du -sh /mnt/wifi-storage/*` |
| 查看容器日誌 | `docker-compose logs -f` |
| 重啟容器 | `docker-compose restart` |
| 停止容器 | `docker-compose down` |
| 啟動容器 | `docker-compose up -d` |
| 查看磁盤 I/O | `iostat -x 1 10` |
| 查看進程 I/O | `iotop` |
| 修復文件系統 | `sudo fsck -y /dev/sdb1` |
| 掛載磁盤 | `sudo mount /dev/sdb1 /mnt/wifi-storage` |

---

## 8. 參考資源

- [Ubuntu 官方文檔](https://ubuntu.com/server/docs)
- [Docker 官方文檔](https://docs.docker.com/)
- [PostgreSQL 性能調優](https://www.postgresql.org/docs/16/performance.html)
- [Linux 磁盤管理](https://linux.die.net/man/8/fdisk)
