# DBeaver + PostgreSQL 操作快速參考

## 快速查詢集合

### 1. 工單管理

#### 查詢所有活躍的工單
```sql
SELECT 
    work_order,
    product_model,
    COUNT(*) as record_count,
    MAX(test_time) as latest_update,
    ROUND(100.0 * SUM(CASE WHEN result = 'PASS' THEN 1 ELSE 0 END) / COUNT(*), 2) as pass_rate
FROM test_record
GROUP BY work_order, product_model
ORDER BY latest_update DESC;
```

**在 DBeaver 中執行：**
- Ctrl+N 開新 SQL 編輯器
- 貼上以上 SQL
- Ctrl+Enter 執行
- 結果會在下方 Result 面板顯示

#### 查詢特定工單的所有產品
```sql
SELECT DISTINCT product_model, COUNT(*) as count
FROM test_record
WHERE work_order = '2024-003'
GROUP BY product_model
ORDER BY count DESC;
```

---

### 2. 刪除操作（分級難度）

#### 🟢 初級：刪除單個工單
```sql
-- STEP 1: 先看要刪除多少筆記錄
SELECT COUNT(*) as records_to_delete
FROM test_record
WHERE work_order = '2024-001';

-- STEP 2: 查看具體記錄
SELECT id, product_model, test_time
FROM test_record
WHERE work_order = '2024-001'
LIMIT 10;

-- STEP 3: 確認無誤後執行刪除
DELETE FROM test_record
WHERE work_order = '2024-001';
```

**DBeaver UI 操作：**
1. 分開執行 SELECT 語句（看記錄）
2. 執行 DELETE（刪除）
3. 刪除後看 Notifications 面板顯示 "Affected: X rows"

#### 🟡 中級：有條件地刪除特定日期的記錄
```sql
-- 刪除2024年1月1日之前的所有記錄
DELETE FROM test_record
WHERE test_time < '2024-01-01'::timestamp;

-- 或刪除30天前的數據
DELETE FROM test_record
WHERE test_time < NOW() - INTERVAL '30 days';
```

#### 🔴 高級：安全的事務式刪除（推薦生產環境）
```sql
-- 開始事務
START TRANSACTION;

-- 備份要刪除的數據到臨時表
CREATE TEMP TABLE deleted_records AS
SELECT * FROM test_record
WHERE work_order = '2024-001';

-- 驗證臨時表有數據
SELECT COUNT(*) FROM deleted_records;  -- 應該 > 0

-- 執行刪除
DELETE FROM test_record
WHERE work_order = '2024-001';

-- 確認刪除成功
SELECT COUNT(*) FROM test_record
WHERE work_order = '2024-001';  -- 應該 = 0

-- 如果都正確，提交事務
COMMIT;

-- 如果有誤，執行此命令回滾
-- ROLLBACK;
```

---

### 3. 資料分析查詢

#### 計算每個產品的失敗率分析
```sql
SELECT 
    product_model,
    COUNT(*) as total_tests,
    SUM(CASE WHEN result = 'FAIL' THEN 1 ELSE 0 END) as fail_count,
    SUM(CASE WHEN result = 'PASS' THEN 1 ELSE 0 END) as pass_count,
    ROUND(100.0 * SUM(CASE WHEN result = 'FAIL' THEN 1 ELSE 0 END) / COUNT(*), 2) as fail_rate_percent,
    ROUND(100.0 * SUM(CASE WHEN result = 'PASS' THEN 1 ELSE 0 END) / COUNT(*), 2) as pass_rate_percent
FROM test_record
GROUP BY product_model
ORDER BY fail_rate_percent DESC;
```

**結果例示：**
```
product_model  | total_tests | fail_count | pass_count | fail_rate_percent | pass_rate_percent
iPhone14Pro    | 2500        | 75         | 2425       | 3.00              | 97.00
iPhone14       | 3200        | 128        | 3072       | 4.00              | 96.00
iPhone13Pro    | 1800        | 36         | 1764       | 2.00              | 98.00
```

#### WiFi 與 BT 結果分析
```sql
SELECT 
    product_model,
    work_order,
    COUNT(*) as total,
    SUM(CASE WHEN wifi_result = TRUE THEN 1 ELSE 0 END) as wifi_pass,
    SUM(CASE WHEN bt_result = TRUE THEN 1 ELSE 0 END) as bt_pass,
    SUM(CASE WHEN wifi_result = TRUE AND bt_result = TRUE THEN 1 ELSE 0 END) as both_pass,
    ROUND(100.0 * SUM(CASE WHEN wifi_result = TRUE THEN 1 ELSE 0 END) / COUNT(*), 2) as wifi_pass_rate,
    ROUND(100.0 * SUM(CASE WHEN bt_result = TRUE THEN 1 ELSE 0 END) / COUNT(*), 2) as bt_pass_rate
FROM test_record
GROUP BY product_model, work_order
ORDER BY work_order DESC, product_model;
```

#### 查詢異常測試（通過率異常低）
```sql
-- 找出最近24小時內通過率低於90%的工單
SELECT 
    work_order,
    product_model,
    SN,
    ROUND(100.0 * SUM(CASE WHEN result = 'PASS' THEN 1 ELSE 0 END) / COUNT(*), 2) as pass_rate,
    COUNT(*) as test_count,
    MAX(test_time) as latest_test
FROM test_record
WHERE test_time > NOW() - INTERVAL '24 hours'
GROUP BY work_order, product_model, SN
HAVING 100.0 * SUM(CASE WHEN result = 'PASS' THEN 1 ELSE 0 END) / COUNT(*) < 90
ORDER BY pass_rate ASC;
```

---

### 4. 批量操作

#### 清除特定工單的所有測試記錄並驗證
```sql
-- 使用 CTE 進行安全刪除
WITH deleted AS (
    DELETE FROM test_record
    WHERE work_order = '2024-001'
    RETURNING id, product_model, test_time
)
SELECT COUNT(*) as deleted_count, MIN(test_time), MAX(test_time)
FROM deleted;
```

**預期結果：**
```
deleted_count | min(test_time)      | max(test_time)
45            | 2024-03-20 08:15:30 | 2024-03-24 14:22:45
```

#### 按日期批量刪除舊測試資料
```sql
-- 每月執行一次：清理超過3個月的數據
DELETE FROM test_record
WHERE test_time < NOW() - INTERVAL '3 months'
AND work_order NOT IN (
    -- 保留最近5個工單
    SELECT DISTINCT work_order
    FROM test_record
    ORDER BY work_order DESC
    LIMIT 5
);
```

#### 清空整個表格（完整清理）
```sql
-- 方法1：刪除所有記錄，保留自增ID軌跡
DELETE FROM test_record;

-- 方法2：完全清空表格和重置自增ID（危險操作！）
TRUNCATE TABLE test_record RESTART IDENTITY;
```

---

## DBeaver UI 操作流程

### 流程 1：安全刪除一個工單的數據

```
【第1步】打開 DBeaver SQL 編輯器
  → File → New → SQL Script

【第2步】選擇目標連接
  → 右上角 Database 下拉選擇 wifitest

【第3步】執行 SELECT 查詢
  SELECT COUNT(*) FROM test_record WHERE work_order = '2024-001';
  → 按 Ctrl+Enter
  → 確認數字（如：45筆記錄）

【第4步】查看具體記錄
  SELECT * FROM test_record WHERE work_order = '2024-001' LIMIT 5;
  → 檢查 product_model、test_time 是否正確

【第5步】執行刪除
  DELETE FROM test_record WHERE work_order = '2024-001';
  → 底部 Notifications 顯示 "Affected: 45 rows"

【第6步】驗證刪除成功
  SELECT COUNT(*) FROM test_record WHERE work_order = '2024-001';
  → 應該返回 0
```

### 流程 2：使用 DBeaver 表格 UI 直接刪除

```
【第1步】展開 Databases → wifitest → Schemas → public → Tables

【第2步】右鍵點擊 test_record → View Records

【第3步】篩選記錄
  → 點擊 + Filter 按鈕
  → Column: work_order
  → Operator: =
  → Value: 2024-001
  → 點擊 OK

【第4步】選擇要刪除的行
  → 在左側勾選記錄前的 ☐
  → 或 Ctrl+A 全選所有篩選結果

【第5步】刪除選中記錄
  → 右鍵 → Delete rows
  → 確認彈出對話框
  → 點擊 Yes 確認

【第6步】驗證刪除
  → 表格中被選記錄應該消失
  → 或重新加載表格（F5）確認
```

### 流程 3：導出數據備份後再刪除

```
【第1步】右鍵 test_record → Export Table Data

【第2步】選擇導出格式
  → 格式: CSV 或 Excel
  → 位置: C:\Backup\test_record_backup.csv

【第3步】配置導出選項
  → Include column names: ✓
  → Number format: Default
  → 點擊 Finish

【第4步】執行導出
  → DBeaver 會保存文件到指定位置
  → 驗證文件大小不為 0 KB

【第5步】確認備份後再刪除
  → 現在可以安心執行 DELETE 命令
```

---

## 常見 SQL 操作模式

### Pattern 1：驗證後刪除
```sql
-- 三步節奏
EXPLAIN ANALYZE
DELETE FROM test_record
WHERE work_order = '2024-001';
-- 上行顯示會刪除多少行，不真正刪除

-- 確認無誤後執行真實刪除
DELETE FROM test_record
WHERE work_order = '2024-001';
```

### Pattern 2：批量標記後刪除
```sql
-- 先添加標記列
ALTER TABLE test_record ADD COLUMN marked_for_deletion BOOLEAN DEFAULT FALSE;

-- 標記要刪除的記錄
UPDATE test_record
SET marked_for_deletion = TRUE
WHERE work_order = '2024-001';

-- 驗證標記
SELECT COUNT(*) FROM test_record WHERE marked_for_deletion = TRUE;

-- 確認無誤後刪除
DELETE FROM test_record WHERE marked_for_deletion = TRUE;

-- 清理标记列
ALTER TABLE test_record DROP COLUMN marked_for_deletion;
```

### Pattern 3：保存刪除清單
```sql
-- 先導出刪除列表
CREATE TABLE deletion_log AS
SELECT id, work_order, product_model, test_time, NOW() as deletion_time
FROM test_record
WHERE work_order = '2024-001';

-- 執行刪除
DELETE FROM test_record
WHERE work_order = '2024-001';

-- 刪除後可從 deletion_log 查詢歷史
SELECT * FROM deletion_log;
```

---

## 故障排除 SQL 查詢

#### 連接測試
```sql
-- 如果此查詢執行成功，表示連接正常
SELECT NOW(), version(), current_user;
```

#### 檢查表統計
```sql
-- 查看表的大小和行數
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) as size,
    n_live_tup as row_count
FROM pg_stat_user_tables
WHERE tablename = 'test_record';
```

#### 查詢運行監控
```sql
-- 查看當前運行的長操作
SELECT pid, usename, application_name, query, state
FROM pg_stat_activity
WHERE state != 'idle';
```

---

## DBeaver 快速鍵

| 快速鍵 | 功能 |
|---------|------|
| Ctrl+N | 新建 SQL 編輯器 |
| Ctrl+Enter | 執行 SQL |
| Ctrl+Shift+Enter | 執行當前行 |
| Ctrl+F | 查詢 (Ctrl+H 取代) |
| Alt+B | 切換 SQL 編輯器/結果面板 |
| F5 | 刷新結果 |

---

## 參考資料

- PostgreSQL 官方文檔: https://www.postgresql.org/docs/16/
- DBeaver 官方文檔: https://dbeaver.io/docs/
- delete 語法: https://www.postgresql.org/docs/16/sql-delete.html
