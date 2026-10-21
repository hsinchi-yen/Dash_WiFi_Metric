-- Remove everything the new web dashboard API added, returning the database to the schema the
-- old API, the desktop uploader and Grafana were built for. test_record rows are kept, including
-- rows uploaded through the web page: they are ordinary rows in the desktop column format.
--
-- Use only together with going back to the old API image; the new API recreates these objects
-- on its next start. tests/db_compat.sh proves that the result is identical to the old schema.
--
--   docker compose stop api
--   docker exec -i wifitest-db psql -U qc -d wifitest -v ON_ERROR_STOP=1 < rollback_web_additions.sql
--
-- Optional, before running it: keep which rows came from which web upload.
--   docker exec wifitest-db psql -U qc -d wifitest -c "\copy (SELECT id, upload_id FROM test_record WHERE upload_id IS NOT NULL) TO STDOUT CSV HEADER" > web_upload_rows.csv
--   docker exec wifitest-db psql -U qc -d wifitest -c "\copy upload_log TO STDOUT CSV HEADER" > upload_log.csv

BEGIN;

DROP MATERIALIZED VIEW IF EXISTS mv_unit;

DROP TABLE IF EXISTS
    wo_status_note,
    upload_log_item,
    upload_log,
    wo_plan,
    product_registry,
    account_audit,
    auth_log,
    app_sessions,
    app_users,
    ai_reports,
    app_meta;

-- The one change to an old table: the nullable column that marks web-uploaded rows.
DROP INDEX IF EXISTS idx_test_record_upload_id;
ALTER TABLE test_record DROP COLUMN IF EXISTS upload_id;

COMMIT;
