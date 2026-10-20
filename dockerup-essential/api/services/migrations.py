"""Schema setup at API startup.

Rule: test_record keeps its columns (the desktop uploader writes it with a fixed column
list). Everything new lives in separate objects: app_meta and the mv_unit materialized view.

mv_unit — one row per SN: that SN's latest test (test_time DESC, id DESC) plus sn_runs,
the number of tests the SN has in the whole table. It is exactly the `latest` CTE the
endpoints used to compute on every request (a window sort over the full table), minus
raw_log. Refreshed by services.stats.refresh().
"""
import logging
from datetime import datetime, timezone

import config
import db

log = logging.getLogger("wifi.migrations")

# Bump when the view definition changes; startup then rebuilds it.
MV_VERSION = "1"

# test_record columns except raw_log (44 KB per row; nothing in the statistics reads it).
MV_UNIT_COLUMNS = (
    "id, product_model, work_order, sn, wifi_mac, bt_mac, test_time, result, "
    "avg_24g, min_24g, max_24g, std_24g, samples_24g, rssi_before_24g, rssi_after_24g, rssi_delta_24g, "
    "attempt_count_24g, band_result_24g, "
    "avg_5g, min_5g, max_5g, std_5g, samples_5g, rssi_before_5g, rssi_after_5g, rssi_delta_5g, "
    "attempt_count_5g, band_result_5g, "
    "bt_avg_latency, bt_min_latency, bt_max_latency, bt_loss_rate, bt_result, "
    "file_hash, source_file, created_at"
)

MV_UNIT_SQL = f"""
CREATE MATERIALIZED VIEW mv_unit AS
SELECT {MV_UNIT_COLUMNS}, sn_runs
FROM (
    SELECT {MV_UNIT_COLUMNS},
           ROW_NUMBER() OVER (PARTITION BY sn ORDER BY test_time DESC, id DESC) AS rn,
           COUNT(*) OVER (PARTITION BY sn) AS sn_runs
    FROM test_record
) r
WHERE rn = 1
"""

MV_UNIT_INDEXES = (
    "CREATE UNIQUE INDEX mv_unit_sn_idx ON mv_unit (sn)",            # required by REFRESH … CONCURRENTLY
    "CREATE INDEX mv_unit_time_idx ON mv_unit (test_time)",
    "CREATE INDEX mv_unit_wo_time_idx ON mv_unit (work_order, test_time)",
    "CREATE INDEX mv_unit_pm_time_idx ON mv_unit (product_model, test_time)",
    "CREATE INDEX mv_unit_result_idx ON mv_unit (result)",
)

REFRESHED_AT_KEY = "mv_unit_refreshed_at"


def meta_get(cur, key):
    cur.execute("SELECT value FROM app_meta WHERE key = %s", (key,))
    row = cur.fetchone()
    return row["value"] if row else None


def meta_set(cur, key, value):
    cur.execute("""
        INSERT INTO app_meta (key, value, updated_at) VALUES (%s, %s, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
    """, (key, value))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run():
    conn = db.connect()
    try:
        cur = conn.cursor()
        # Both uvicorn workers start together; serialise so CREATE … cannot race.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (config.MIGRATION_LOCK_KEY,))
        cur.execute("""
            CREATE TABLE IF NOT EXISTS app_meta (
                key        TEXT PRIMARY KEY,
                value      TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        # AI reports: one row per generation (queued → running → done | failed); the newest
        # done row whose data_hash still matches the work order's numbers is the current report.
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ai_reports (
                id            BIGSERIAL PRIMARY KEY,
                work_order    TEXT NOT NULL,
                product_model TEXT NOT NULL DEFAULT '',
                lang          TEXT NOT NULL,
                status        TEXT NOT NULL,
                priority      INT NOT NULL DEFAULT 0,
                data_hash     TEXT,
                text          TEXT,
                model         TEXT,
                error         TEXT,
                requested_at  TIMESTAMPTZ DEFAULT NOW(),
                started_at    TIMESTAMPTZ,
                finished_at   TIMESTAMPTZ,
                elapsed_sec   REAL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS ai_reports_key_idx ON ai_reports (work_order, product_model, lang, requested_at DESC)")
        cur.execute("CREATE INDEX IF NOT EXISTS ai_reports_queue_idx ON ai_reports (priority DESC, requested_at) WHERE status = 'queued'")
        # A worker that died mid-generation leaves 'running' rows behind; re-queue them.
        cur.execute("UPDATE ai_reports SET status = 'queued', started_at = NULL WHERE status = 'running'")

        cur.execute("SELECT 1 FROM pg_matviews WHERE schemaname = current_schema() AND matviewname = 'mv_unit'")
        exists = cur.fetchone() is not None
        if not exists or meta_get(cur, "mv_unit_version") != MV_VERSION:
            cur.execute("DROP MATERIALIZED VIEW IF EXISTS mv_unit")
            cur.execute(MV_UNIT_SQL)
            for ddl in MV_UNIT_INDEXES:
                cur.execute(ddl)
            cur.execute("ANALYZE mv_unit")
            meta_set(cur, "mv_unit_version", MV_VERSION)
            meta_set(cur, REFRESHED_AT_KEY, now_iso())
            log.info("mv_unit (version %s) created", MV_VERSION)
        conn.commit()
    finally:
        conn.close()
