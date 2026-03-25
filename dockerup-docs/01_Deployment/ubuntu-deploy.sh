#!/bin/bash
# Ubuntu Linux WiFi Dashboard 磁盤和 Docker 部署腳本

set -e  # 任何命令失敗時退出

echo "=========================================="
echo "WiFi 儀表板 Ubuntu Linux 全自動部署"
echo "=========================================="
echo ""

# 檢查是否為 root
if [[ $EUID -ne 0 ]]; then
   echo "此腳本需要 root 權限。請使用 sudo 執行："
   echo "sudo bash $0"
   exit 1
fi

# 步驟 1: 安裝 Docker 和依賴
echo "[1/6] 安裝 Docker 和 Docker Compose..."
apt-get update -qq
apt-get install -y -qq \
    docker.io \
    docker-compose \
    lvm2 \
    curl \
    wget \
    nano \
    git

# 啟用 Docker 開機自啟
systemctl enable docker
systemctl start docker
echo "✓ Docker 安裝完成"

# 步驟 2: 掛載和格式化 /dev/sdb
echo ""
echo "[2/6] 配置存儲磁盤 (/dev/sdb)..."

# 檢查磁盤是否存在
if [ ! -e /dev/sdb ]; then
    echo "✗ 錯誤: /dev/sdb 不存在"
    echo "可用磁盤:"
    lsblk
    exit 1
fi

# 檢查磁盤是否已掛載
if grep -q /dev/sdb /proc/mounts; then
    echo "✓ /dev/sdb 已經掛載"
    STORAGE_PATH=$(mount | grep /dev/sdb | awk '{print $3}')
else
    echo "正在格式化 /dev/sdb..."
    
    # 清除舊的分區表
    dd if=/dev/zero of=/dev/sdb bs=512 count=1 2>/dev/null || true
    partprobe /dev/sdb 2>/dev/null || true
    
    # 創建新分區
    parted -s /dev/sdb mklabel gpt 2>/dev/null || true
    parted -s /dev/sdb mkpart primary ext4 1 100% 2>/dev/null || true
    
    # 等待分區創建完成
    sleep 2
    
    # 格式化分區
    if [ -e /dev/sdb1 ]; then
        mkfs.ext4 -F /dev/sdb1
        STORAGE_PATH="/mnt/wifi-storage"
    else
        # 如果分區不存在，使用整個磁盤
        mkfs.ext4 -F /dev/sdb
        STORAGE_PATH="/mnt/wifi-storage"
    fi
    
    # 創建掛載點
    mkdir -p $STORAGE_PATH
    
    # 掛載磁盤
    if [ -e /dev/sdb1 ]; then
        mount /dev/sdb1 $STORAGE_PATH
        # 添加到 /etc/fstab 以實現開機自動掛載
        echo "/dev/sdb1 $STORAGE_PATH ext4 defaults,nofail 0 2" | tee -a /etc/fstab
    else
        mount /dev/sdb $STORAGE_PATH
        echo "/dev/sdb $STORAGE_PATH ext4 defaults,nofail 0 2" | tee -a /etc/fstab
    fi
    
    echo "✓ /dev/sdb 已格式化並掛載至 $STORAGE_PATH"
fi

# 步驟 3: 創建 Docker 數據目錄
echo ""
echo "[3/6] 創建 Docker 數據目錄 ($STORAGE_PATH/docker-data)..."

mkdir -p $STORAGE_PATH/docker-data/pgdata
mkdir -p $STORAGE_PATH/docker-data/grafana-data
mkdir -p $STORAGE_PATH/docker-data/app-logs

# 設置權限
chmod -R 755 $STORAGE_PATH/docker-data
chown -R 999:999 $STORAGE_PATH/docker-data/pgdata  # PostgreSQL 用戶
chown -R 472:472 $STORAGE_PATH/docker-data/grafana-data  # Grafana 用戶

echo "✓ Docker 數據目錄已創建"

# 步驟 4: 部署應用程序代碼
echo ""
echo "[4/6] 部署應用程序代碼..."

# 檢查應用代碼是否已存在
if [ ! -d "$STORAGE_PATH/wifi-dashboard" ]; then
    mkdir -p $STORAGE_PATH/wifi-dashboard
    echo "已創建應用目錄: $STORAGE_PATH/wifi-dashboard"
    echo "請將以下文件複製到此目錄:"
    echo "  - docker-compose.yml"
    echo "  - schema.sql"
    echo "  - log_parser.py"
    echo "  - wifi_dashboard.html"
    echo "  - api/ (目錄)"
    echo "  - grafana/ (目錄)"
    echo "  - logs/ (目錄)"
    echo "  - WiFiTestLogs/ (目錄)"
    echo "  - .env (環境變數文件)"
    echo ""
    echo "使用 SCP 複製："
    echo "scp -r /path/to/files/* ubuntu@10.88.88.250:$STORAGE_PATH/wifi-dashboard/"
fi

chmod -R 755 $STORAGE_PATH/wifi-dashboard

# 步驟 5: 配置防火牆
echo ""
echo "[5/6] 配置 UFW 防火牆..."

# 啟用 UFW（如果未啟用）
ufw --force enable 2>/dev/null || true

# 打開所需端口
ufw allow 22/tcp comment "SSH"
ufw allow 5432/tcp comment "PostgreSQL"
ufw allow 8000/tcp comment "WiFi API"
ufw allow 3000/tcp comment "Grafana"

echo "✓ 防火牆已配置"

# 步驟 6: 啟動 Docker 容器
echo ""
echo "[6/6] 準備啟動 Docker 容器..."

# 創建非 root 用戶用於 Docker（如果不存在）
if ! id "ubuntu" &>/dev/null; then
    useradd -m -s /bin/bash ubuntu
    usermod -aG docker ubuntu
    echo "✓ 已創建用戶: ubuntu"
fi

# 添加現有用戶到 docker 組
usermod -aG docker ubuntu 2>/dev/null || true

echo ""
echo "=========================================="
echo "部署準備完成！"
echo "=========================================="
echo ""
echo "存儲位置: $STORAGE_PATH"
echo "應用目錄: $STORAGE_PATH/wifi-dashboard"
echo "數據目錄: $STORAGE_PATH/docker-data"
echo ""
echo "接下來的步驟："
echo "1. 複製應用文件到: $STORAGE_PATH/wifi-dashboard/"
echo "2. 編輯 .env 文件配置環境變數"
echo "3. 編輯 docker-compose.yml 更新卷路徑為: $STORAGE_PATH/docker-data"
echo "4. 執行: cd $STORAGE_PATH/wifi-dashboard && docker-compose up -d"
echo ""
echo "檢查磁盤使用情況："
echo "df -h | grep $STORAGE_PATH"
echo ""
