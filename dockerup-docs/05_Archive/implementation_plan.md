# WiFi Dashboard — Deep UI Optimization & Advanced Test Analytics

## Overview

完整重新設計 `wifi_dashboard.html` 的視覺風格，參照 `solo_pixi_dashboard.html` 的科技金屬/Cyber Neon 設計語言，同時新增 **Advanced Test Analysis** 頁面，提供工單/產品的產出時間分析。

**不影響 `wifi_stress_log_analyzer.py` 及任何後端 API。** 所有改動僅限 HTML/CSS/JS 前端層。

---

## Code Review 發現項目

### 需優化的問題

| 項目 | 說明 |
|---|---|
| CSS 設計老舊 | 使用基礎暗色，缺乏 glassmorphism、ambient glow、漸層文字等現代效果 |
| 無頁面進場動畫 | 切換頁面時無過渡效果，體驗生硬 |
| font 使用系統預設 | 未引入 Google Fonts (Inter/Outfit) |
| 指示燈缺乏動態 | API 狀態 badge 為靜態顯示 |
| sidebar 設計簡單 | 缺少品牌識別圖標、無 icon、無 live pulse |
| metric-card 無互動 | KPI 卡片無 hover 效果 / 漸層背景 |
| 缺少 Advanced Analysis 頁面 | 缺少工單產出時間分析、最快/最慢/平均產出等 |
| chart theme bug | 多處重複 getCssVar 呼叫，可提取為常數 |
| 無 favicon/SEO title | 缺少品牌 favicon |

---

## Proposed Changes

### 1. CSS Design System 升級 (Cyber Metal Style)

- **色彩主題**：沿用 solo_pixi 的 `[data-theme="cyber"]`，加入多主題支援（Cyber Neon / Deep Space / TechNexion Blue）
- **背景動效**：加入 `body::before/after` ambient orb 動畫（藍/紫漸層浮動光球）
- **Glassmorphism**：所有 card/sidebar 加入 `backdrop-filter: blur(16px)`
- **漸層文字**：KPI 數值改為 `background-clip: text` 漸層效果
- **字型**：引入 Google Fonts `Inter` 替代系統字型
- **進場動畫**：`@keyframes fadeInUp` + `@keyframes ds-fadeUp` 給各區塊
- **Sidebar**：加入 brand-mark icon（TW 字樣），live-pulse 動態綠點，主題切換 chip
- **Nav item**：加入 emoji icon、active 左邊 glow bar（box-shadow 藍色光暈）

### 2. 新增 Advanced Test Analysis 頁面

新增 nav 入口 **"⚡ Advanced Analysis"** → `page-advanced`

分析內容（純前端計算，使用現有 `workOrders` 資料）：

#### 2.1 工單產出時間 KPI Cards
| 指標 | 說明 |
|---|---|
| 最快單片產出時間 | 全部 WO 中，最短的每片平均時間 (WO duration / units) |
| 最慢單片產出時間 | 全部 WO 中，最長的每片平均時間 |
| 平均單片產出時間 | 加權平均（總時長/總片數）|
| 每小時平均產出數量 | 各 WO 每小時單量的統計均值 |

#### 2.2 工單產出速率散佈圖 (Chart)
- 橫軸：各工單
- 縱軸：每小時產出數 (units/hr)
- 顏色：依良率深淺上色 (green→red gradient)
- 點擊工單 bar 可跳轉至對應 WO 的 work order page

#### 2.3 產出時間分布直方圖 (Chart)
- 展示各工單 per-unit-minutes 的分布
- 藍色 bar chart，X軸：時間分鐘bucket, Y軸：WO 數量

#### 2.4 工單詳細分析表格
展示每個工單的：
- Work Order
- Product Model
- 開始 / 結束時間
- 總時長
- 總測試數
- **每小時產出** (units/hr)
- **每片平均時間** (min/unit)
- **最快/最慢批次** (若有每小時資料)
- 良率

---

## Verification Plan

### 視覺驗證
- 用 browser 開啟 `file:///.../wifi_dashboard.html` 確認 cyber metal 風格
- 確認 ambient orb 動畫、glassmorphism card 效果
- 確認新 Advanced Analysis 頁面正確顯示

### 功能驗證
- 確認現有所有頁面（Dashboard、Work Orders、Fail List、RF Analysis、DB Tweak）功能無破壞
- 確認 API 端點呼叫完全不變
- 確認 Advanced Analysis 頁面數據計算邏輯正確（需有 WO 資料）

### 不影響後端確認
- `wifi_stress_log_analyzer.py` 完全不做修改
- API 調用方式不變，僅增加前端展示層
