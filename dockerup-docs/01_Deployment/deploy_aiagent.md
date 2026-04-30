# AI Report 功能部署 SOP

本文件說明 AI Report（ZH/EN 雙語報告）功能上線所需的檔案異動與部署步驟。

---

## 1. 功能異動摘要

| 功能 | 說明 |
|---|---|
| Work Orders 新增 AI Report 欄位 | 每筆工單顯示 **ZH**（繁體中文）與 **EN**（英文）兩顆按鈕 |
| 告警等級自動計算 | 依 Yield 計算等級：≥99.2% Normal、98.5-99.19% Warning、<98.5% ALARM |
| Modal 顯示語言與等級徽章 | 報告彈窗標題列顯示告警等級色塊與語言標籤 |
| 本地 LLM 中英文強制語言指令 | EN prompt 加入中文語言覆寫指令，解決中文微調 LLM 忽略英文指令的問題 |

---

## 2. 異動檔案清單

### 新增檔案

| 檔案路徑 | 說明 |
|---|---|
| `dockerup-essential/api/ai_summary_helper.py` | AI prompt 建構邏輯（純函數，可單元測試） |
| `dockerup-essential/api/test_ai_summary_prompt.py` | pytest 單元測試（僅開發用，**不需部署**） |

### 修改檔案

| 檔案路徑 | 異動內容 |
|---|---|
| `dockerup-essential/api/app.py` | 新增 `lang` 參數支援；改呼叫 `build_summary_messages()` |
| `dockerup-essential/docker-compose.yml` | api volumes 新增 `ai_summary_helper.py` 掛載 |
| `dockerup-essential/wifi_dashboard.html` | Work Orders 表格新增 AI Report 欄；ZH/EN 按鈕；Modal 告警等級顯示 |

---

## 3. 部署步驟

### 步驟 A：將異動檔案同步至遠端伺服器

```bash
# 方法一：git pull（建議，若遠端有設定 git）
cd /mnt/md127/WIFI_YFP_DashBoard
git pull

# 方法二：手動 scp（僅傳異動檔案）
scp dockerup-essential/api/app.py              user@remote:/mnt/md127/WIFI_YFP_DashBoard/dockerup-essential/api/
scp dockerup-essential/api/ai_summary_helper.py user@remote:/mnt/md127/WIFI_YFP_DashBoard/dockerup-essential/api/
scp dockerup-essential/docker-compose.yml      user@remote:/mnt/md127/WIFI_YFP_DashBoard/dockerup-essential/
scp dockerup-essential/wifi_dashboard.html     user@remote:/mnt/md127/WIFI_YFP_DashBoard/dockerup-essential/
```

### 步驟 B：重啟 API 容器

```bash
cd /mnt/md127/WIFI_YFP_DashBoard/dockerup-essential
docker-compose up -d --force-recreate api
```

> 只需重啟 `api` 服務，`postgres` 和 `grafana` 不受影響。

### 步驟 C：確認容器正常運行

```bash
# 確認容器狀態
docker-compose ps

# 查看啟動 log，確認無 ImportError
docker-compose logs api --tail=30
```

預期看到：
```
wifitest-api  | INFO:     Application startup complete.
```

若看到以下錯誤，代表 `ai_summary_helper.py` 未正確掛載：
```
ModuleNotFoundError: No module named 'ai_summary_helper'
```
→ 確認 `docker-compose.yml` 內 volumes 已包含：
```yaml
- ./api/ai_summary_helper.py:/app/ai_summary_helper.py
```

---

## 4. 告警等級門檻（前後端一致）

| Yield 範圍 | 等級 | 顏色 |
|---|---|---|
| ≥ 99.2% | Normal / 正常 | 綠色 |
| 98.5% ~ 99.19% | Warning / 警告 | 黃色 |
| < 98.5% | ALARM / 告警 | 紅色 |

---

## 5. 已知限制

- **本地 LLM 語言行為**：中文微調模型（如 Qwen、ChatGLM）可能仍偶爾輸出混合語言。
  目前 prompt 已採用「中文指示英文回覆」策略，若仍有問題需視 LLM 版本調整。
- **LLM 需保持連線**：ZH/EN 按鈕在 LLM 斷線時自動 disabled，無需額外處理。

---

## 6. 回滾方式

若需回滾此功能：

```bash
# git 回滾（回到前一個 commit）
git revert HEAD
git pull  # 同步到遠端後重啟 api
docker-compose up -d --force-recreate api
```

或手動還原：將 `app.py` 改回移除 `from ai_summary_helper import build_summary_messages` 的版本，並還原 `docker-compose.yml` 移除 `ai_summary_helper.py` 的 volume 行。



scp dockerup-essential/api/app.py             user@remote:/path/dockerup-essential/api/
scp dockerup-essential/api/ai_summary_helper.py user@remote:/path/dockerup-essential/api/
scp dockerup-essential/docker-compose.yml     user@remote:/path/dockerup-essential/
scp dockerup-essential/wifi_dashboard.html    user@remote:/path/dockerup-essential/