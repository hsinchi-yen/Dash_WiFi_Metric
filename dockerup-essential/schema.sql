-- WiFi Production Test Database Schema
-- Run once on your PostgreSQL instance (v14+)
-- Optional: install TimescaleDB for hypertable time-series queries

-- ─── Core test record table ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS test_record (
    id                  SERIAL PRIMARY KEY,

    -- Identity (from filename + path)
    product_model       TEXT,
    work_order          TEXT,
    sn                  TEXT NOT NULL,
    wifi_mac            TEXT,
    bt_mac              TEXT,
    test_time           TIMESTAMP NOT NULL,
    result              BOOLEAN NOT NULL,       -- TRUE = PASS

    -- 2.4G WiFi KPIs (warmup-excluded iperf [SUM] lines)
    avg_24g             FLOAT,
    min_24g             FLOAT,
    max_24g             FLOAT,
    std_24g             FLOAT,                  -- stability indicator
    samples_24g         INT,                    -- how many data points
    rssi_before_24g     FLOAT,
    rssi_after_24g      FLOAT,
    rssi_delta_24g      FLOAT,                  -- negative = RF degraded during test
    attempt_count_24g   INT DEFAULT 1,          -- >1 = borderline unit
    band_result_24g     BOOLEAN,

    -- 5G WiFi KPIs
    avg_5g              FLOAT,
    min_5g              FLOAT,
    max_5g              FLOAT,
    std_5g              FLOAT,
    samples_5g          INT,
    rssi_before_5g      FLOAT,
    rssi_after_5g       FLOAT,
    rssi_delta_5g       FLOAT,
    attempt_count_5g    INT DEFAULT 1,
    band_result_5g      BOOLEAN,

    -- Bluetooth KPIs (l2ping)
    bt_avg_latency      FLOAT,                  -- ms
    bt_min_latency      FLOAT,
    bt_max_latency      FLOAT,
    bt_loss_rate        FLOAT,                  -- percent (0-100)
    bt_result           BOOLEAN,

    -- Optional: add these when station tracking is available
    -- tester_id        TEXT,
    -- station_id       TEXT,
    -- fw_version       TEXT,

    -- Housekeeping
    raw_log             TEXT,
    file_hash           TEXT UNIQUE,            -- SHA256, prevents duplicate ingest
    source_file         TEXT,
    created_at          TIMESTAMP DEFAULT NOW()
);

-- ─── Indexes (critical for dashboard query performance) ─────────────────────
CREATE INDEX IF NOT EXISTS idx_test_time    ON test_record(test_time);
CREATE INDEX IF NOT EXISTS idx_model        ON test_record(product_model);
CREATE INDEX IF NOT EXISTS idx_work_order   ON test_record(work_order);
CREATE INDEX IF NOT EXISTS idx_result       ON test_record(result);
CREATE INDEX IF NOT EXISTS idx_sn           ON test_record(sn);

-- Composite for the most common dashboard query pattern
CREATE INDEX IF NOT EXISTS idx_wo_time      ON test_record(work_order, test_time);
CREATE INDEX IF NOT EXISTS idx_model_time   ON test_record(product_model, test_time);

-- ─── Optional: convert to TimescaleDB hypertable ─────────────────────────────
-- Uncomment if TimescaleDB extension is installed:
-- SELECT create_hypertable('test_record', 'test_time', if_not_exists => TRUE);

-- ─── Useful views for Grafana ────────────────────────────────────────────────

-- Overall yield per work order
CREATE OR REPLACE VIEW v_yield_by_wo AS
SELECT
    work_order,
    product_model,
    COUNT(*)                                                            AS total,
    COUNT(*) FILTER (WHERE result = TRUE)                               AS passed,
    COUNT(*) FILTER (WHERE result = FALSE)                              AS failed,
    ROUND(COUNT(*) FILTER (WHERE result = TRUE) * 100.0 / COUNT(*), 2) AS yield_pct,
    ROUND(AVG(avg_24g)::numeric, 1)                                     AS avg_24g,
    ROUND(AVG(avg_5g)::numeric, 1)                                      AS avg_5g,
    ROUND(
        COUNT(*) FILTER (WHERE attempt_count_24g > 1 OR attempt_count_5g > 1)
        * 100.0 / COUNT(*), 2
    )                                                                   AS retry_rate_pct,
    MIN(test_time)                                                      AS first_test,
    MAX(test_time)                                                      AS last_test
FROM test_record
GROUP BY work_order, product_model;

-- Units with high std deviation (RF stability alert)
CREATE OR REPLACE VIEW v_unstable_units AS
SELECT
    test_time, sn, work_order,
    std_5g, avg_5g,
    rssi_delta_5g,
    attempt_count_5g
FROM test_record
WHERE std_5g > 5 OR rssi_delta_5g < -20
ORDER BY test_time DESC;

-- Fail analysis
CREATE OR REPLACE VIEW v_fail_detail AS
SELECT
    test_time, sn, work_order, product_model,
    band_result_24g, band_result_5g, bt_result,
    avg_24g, avg_5g,
    rssi_before_24g, rssi_after_24g, rssi_delta_24g,
    rssi_before_5g, rssi_after_5g, rssi_delta_5g,
    bt_avg_latency, bt_loss_rate
FROM test_record
WHERE result = FALSE
ORDER BY test_time DESC;

-- ─── Sample Grafana queries ──────────────────────────────────────────────────

-- 1. Overall yield in time window (Grafana Stat panel)
-- SELECT ROUND(COUNT(*) FILTER (WHERE result=true) * 100.0 / COUNT(*), 1) AS yield
-- FROM test_record WHERE $__timeFilter(test_time)

-- 2. Yield trend by hour (Grafana Time series)
-- SELECT
--   date_trunc('hour', test_time) AS time,
--   ROUND(AVG(CASE WHEN result THEN 1 ELSE 0 END) * 100, 2) AS yield
-- FROM test_record
-- WHERE $__timeFilter(test_time)
-- GROUP BY 1 ORDER BY 1

-- 3. Throughput percentiles (Grafana Table)
-- SELECT
--   PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY avg_5g)  AS p50_5g,
--   PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY avg_5g)  AS p90_5g,
--   PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY avg_5g) AS p95_5g
-- FROM test_record WHERE $__timeFilter(test_time)

-- 4. High std-dev alert (Grafana Alert rule)
-- SELECT COUNT(*) FROM test_record
-- WHERE test_time > NOW() - INTERVAL '1 hour' AND std_5g > 5
