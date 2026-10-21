"""Schema setup at API startup.

Rule: test_record keeps its columns (the desktop uploader writes it with a fixed column
list). The one addition is the nullable `upload_id` (which web upload inserted a row;
desktop rows stay NULL). Everything else new lives in separate objects:

  app_meta          key/value (view refresh time, versions)
  mv_unit           one row per SN (its latest test) — the statistics source
  ai_reports        generated AI reports
  app_users / app_sessions / auth_log / account_audit    accounts
  product_registry  the product names the dashboard accepts
  wo_plan           planned test quantity per work order
  upload_log / upload_log_item                           web uploads and their per-file results
  wo_status_note    L1 one-line status note per work order (settings live in app_meta "settings")
  wo_report         L3 structured final report per work order
  fail_taxonomy / fail_analysis                          5F failure categories and one classification per FAIL run
  llm_usage         one row per LLM completion (kept 90 days)

Old-database compatibility (tests/db_compat.sh must print COMPATIBLE before a deployment):
  - never alter, drop or constrain test_record or the old views v_yield_by_wo / v_unstable_units /
    v_fail_detail (upload_id is the one verified exception);
  - no foreign key to test_record and no trigger on it (the old DB Tweak deletes rows directly);
  - every new object is also dropped by dockerup-docs/02_Maintenance/scripts/rollback_web_additions.sql.
"""
import logging
from datetime import datetime, timezone

import config
import db
from services import products

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

TABLES = (
    """CREATE TABLE IF NOT EXISTS app_meta (
        key        TEXT PRIMARY KEY,
        value      TEXT,
        updated_at TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS ai_reports (
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
    )""",
    "CREATE INDEX IF NOT EXISTS ai_reports_key_idx ON ai_reports (work_order, product_model, lang, requested_at DESC)",
    "CREATE INDEX IF NOT EXISTS ai_reports_queue_idx ON ai_reports (priority DESC, requested_at) WHERE status = 'queued'",
    """CREATE TABLE IF NOT EXISTS app_users (
        id                  SERIAL PRIMARY KEY,
        username            TEXT NOT NULL UNIQUE,
        display_name        TEXT NOT NULL,
        department          TEXT,
        email               TEXT,
        password_hash       TEXT NOT NULL,
        enabled             BOOLEAN NOT NULL DEFAULT TRUE,
        created_at          TIMESTAMPTZ DEFAULT NOW(),
        created_by          TEXT,
        updated_at          TIMESTAMPTZ,
        password_changed_at TIMESTAMPTZ,
        last_login_at       TIMESTAMPTZ,
        last_login_ip       TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS app_sessions (
        token_hash  TEXT PRIMARY KEY,
        username    TEXT NOT NULL,
        role        TEXT NOT NULL,
        cred_fp     TEXT,
        created_at  TIMESTAMPTZ DEFAULT NOW(),
        expires_at  TIMESTAMPTZ NOT NULL,
        client_ip   TEXT,
        revoked_at  TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS app_sessions_username_idx ON app_sessions (username)",
    """CREATE TABLE IF NOT EXISTS auth_log (
        id          BIGSERIAL PRIMARY KEY,
        at          TIMESTAMPTZ DEFAULT NOW(),
        username    TEXT,
        event       TEXT,
        success     BOOLEAN,
        reason      TEXT,
        client_ip   TEXT,
        user_agent  TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS auth_log_at_idx ON auth_log (at DESC)",
    """CREATE TABLE IF NOT EXISTS account_audit (
        id          BIGSERIAL PRIMARY KEY,
        at          TIMESTAMPTZ DEFAULT NOW(),
        actor       TEXT,
        action      TEXT,
        target      TEXT,
        detail      JSONB,
        client_ip   TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS account_audit_at_idx ON account_audit (at DESC)",
    """CREATE TABLE IF NOT EXISTS product_registry (
        id                SERIAL PRIMARY KEY,
        canonical         TEXT NOT NULL UNIQUE,
        match_key         TEXT NOT NULL UNIQUE,
        tn_number         INT,
        aliases           TEXT[] NOT NULL DEFAULT '{}',
        status            TEXT NOT NULL DEFAULT 'active',
        source            TEXT,
        note              TEXT,
        created_by        TEXT,
        created_upload_id TEXT,
        created_at        TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS wo_plan (
        work_order    TEXT PRIMARY KEY,
        product_model TEXT,
        planned_qty   INT NOT NULL,
        updated_by    TEXT,
        updated_at    TIMESTAMPTZ DEFAULT NOW(),
        upload_id     TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS upload_log (
        id              TEXT PRIMARY KEY,
        created_at      TIMESTAMPTZ DEFAULT NOW(),
        finished_at     TIMESTAMPTZ,
        client_ip       TEXT,
        username        TEXT,
        user_role       TEXT,
        source_kind     TEXT,
        source_name     TEXT,
        source_bytes    BIGINT,
        work_order      TEXT,
        product_model   TEXT,
        inferred_wo     TEXT,
        inferred_product TEXT,
        planned_qty     INT,
        prev_plan       INT,
        new_product     BOOLEAN DEFAULT FALSE,
        unique_sn       INT,
        total_files     INT,
        facts           JSONB,
        validation      JSONB,
        chat_transcript JSONB,
        mode            TEXT,
        status          TEXT,
        uploaded        INT,
        skipped         INT,
        failed          INT,
        failures        JSONB,
        reverted        INT,
        reverted_by     TEXT,
        reverted_at     TIMESTAMPTZ,
        elapsed_sec     REAL,
        error           TEXT,
        cancel_requested BOOLEAN NOT NULL DEFAULT FALSE
    )""",
    "CREATE INDEX IF NOT EXISTS upload_log_created_idx ON upload_log (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS upload_log_username_idx ON upload_log (username, created_at DESC)",
    """CREATE TABLE IF NOT EXISTS upload_log_item (
        upload_id   TEXT NOT NULL,
        idx         INT NOT NULL,
        filename    TEXT,
        status      TEXT,
        message     TEXT,
        record_id   INT,
        PRIMARY KEY (upload_id, idx)
    )""",
    # L1: the one-line status note per work order (latest only; rewritten when its facts change)
    """CREATE TABLE IF NOT EXISTS wo_status_note (
        work_order    TEXT NOT NULL,
        product_model TEXT NOT NULL DEFAULT '',
        facts_hash    TEXT NOT NULL,
        state         TEXT,
        light         TEXT,
        facts         JSONB,
        text_zh       TEXT,
        text_en       TEXT,
        source        TEXT,
        model         TEXT,
        error         TEXT,
        rejected      JSONB,
        elapsed_sec   REAL,
        created_at    TIMESTAMPTZ DEFAULT NOW(),
        PRIMARY KEY (work_order, product_model)
    )""",
    # L3: the structured final report per work order (latest only; facts shown as they are, the LLM writes
    # summary / anomalies / possible causes / actions in both languages; numbers checked against the facts)
    """CREATE TABLE IF NOT EXISTS wo_report (
        work_order    TEXT NOT NULL,
        product_model TEXT NOT NULL DEFAULT '',
        facts_hash    TEXT,
        facts         JSONB,
        report        JSONB,
        source        TEXT,
        model         TEXT,
        error         TEXT,
        rejected      JSONB,
        elapsed_sec   REAL,
        created_at    TIMESTAMPTZ,
        requested_at  TIMESTAMPTZ,
        requested_by  TEXT,
        PRIMARY KEY (work_order, product_model)
    )""",
    # 5F: failure taxonomy (RD maintains it) and one classification per FAIL test run. record_id is a
    # test_record id WITHOUT a foreign key: the old DB Tweak deletes test rows directly (orphans are swept).
    """CREATE TABLE IF NOT EXISTS fail_taxonomy (
        code           TEXT PRIMARY KEY,
        name_zh        TEXT NOT NULL,
        name_en        TEXT NOT NULL,
        description_zh TEXT,
        description_en TEXT,
        severity       INT NOT NULL DEFAULT 5,
        likely_stage   TEXT,
        action_zh      TEXT,
        action_en      TEXT,
        enabled        BOOLEAN NOT NULL DEFAULT TRUE,
        sort           INT NOT NULL DEFAULT 100,
        updated_by     TEXT,
        updated_at     TIMESTAMPTZ DEFAULT NOW()
    )""",
    """CREATE TABLE IF NOT EXISTS fail_analysis (
        record_id          BIGINT PRIMARY KEY,
        sn                 TEXT,
        work_order         TEXT,
        product_model      TEXT,
        test_time          TIMESTAMP,
        category           TEXT NOT NULL,
        secondary          TEXT[] NOT NULL DEFAULT '{}',
        band               TEXT,
        mbps               REAL,
        threshold          REAL,
        rssi               REAL,
        attempts           INT,
        measures           JSONB,
        evidence           JSONB,
        station_port       TEXT,
        ssid_group         TEXT,
        target_ssid        TEXT,
        retest_outcome     TEXT,
        run_seq            TEXT,
        run_index          INT,
        classifier         TEXT NOT NULL,
        classifier_version INT,
        confidence         REAL,
        llm_model          TEXT,
        llm_tried_at       TIMESTAMPTZ,
        stage              TEXT,
        rd_status          TEXT NOT NULL DEFAULT 'new',
        rd_owner           TEXT,
        rd_note            TEXT,
        rd_by              TEXT,
        rd_at              TIMESTAMPTZ,
        created_at         TIMESTAMPTZ DEFAULT NOW(),
        updated_at         TIMESTAMPTZ DEFAULT NOW()
    )""",
    "CREATE INDEX IF NOT EXISTS fail_analysis_category_idx ON fail_analysis (category)",
    "CREATE INDEX IF NOT EXISTS fail_analysis_wo_idx ON fail_analysis (work_order, product_model)",
    "CREATE INDEX IF NOT EXISTS fail_analysis_time_idx ON fail_analysis (test_time)",
    "CREATE INDEX IF NOT EXISTS fail_analysis_station_idx ON fail_analysis (ssid_group, station_port)",
    # every LLM completion (purpose, success, duration): the call rate on the Settings page
    """CREATE TABLE IF NOT EXISTS llm_usage (
        id      BIGSERIAL PRIMARY KEY,
        at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        purpose TEXT,
        ok      BOOLEAN,
        ms      INT
    )""",
    "CREATE INDEX IF NOT EXISTS llm_usage_at_idx ON llm_usage (at)",
)

# Columns added later to this project's own tables (never to test_record).
OWN_COLUMNS = (
    ("wo_plan", "admin_qty", "INT"),            # the administrator's planned quantity (kept when upload batches are undone)
    ("wo_plan", "admin_by", "TEXT"),
    ("wo_plan", "admin_at", "TIMESTAMPTZ"),
    ("app_users", "role", "TEXT NOT NULL DEFAULT 'uploader'"),   # uploader | rd (5F)
)


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


def _column_exists(cur, table, column):
    cur.execute("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = %s AND column_name = %s
    """, (table, column))
    return cur.fetchone() is not None


def run():
    conn = db.connect()
    try:
        cur = conn.cursor()
        # Both uvicorn workers start together; serialise so CREATE … cannot race.
        cur.execute("SELECT pg_advisory_xact_lock(%s)", (config.MIGRATION_LOCK_KEY,))
        for ddl in TABLES:
            cur.execute(ddl)
        for table, column, kind in OWN_COLUMNS:
            if not _column_exists(cur, table, column):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        # A worker that died mid-generation leaves 'running' rows behind; re-queue them.
        cur.execute("UPDATE ai_reports SET status = 'queued', started_at = NULL WHERE status = 'running'")

        # The one test_record addition: which web upload inserted the row (desktop rows stay NULL).
        # Checked first so startup never takes an ALTER lock on the populated table needlessly.
        if not _column_exists(cur, "test_record", "upload_id"):
            cur.execute("ALTER TABLE test_record ADD COLUMN upload_id TEXT")
            log.info("added test_record.upload_id")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_test_record_upload_id ON test_record (upload_id)")

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

        from services import fail_taxonomy
        fail_taxonomy.seed(cur)      # new codes only: what RD changed stays

        if meta_get(cur, "product_registry_seeded") != "1":
            added = products.seed_from_data(cur)
            meta_set(cur, "product_registry_seeded", "1")
            log.info("product_registry seeded with %d products", added)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
