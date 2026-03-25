#!/usr/bin/env bash
set -euo pipefail

# Export PostgreSQL data from old host container.
# Defaults match current project settings.

CONTAINER_NAME="${CONTAINER_NAME:-wifitest-db}"
DB_NAME="${DB_NAME:-wifitest}"
DB_USER="${DB_USER:-qc}"
OUTPUT_DIR="${OUTPUT_DIR:-./backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="${DUMP_FILE:-${OUTPUT_DIR}/wifitest_${TIMESTAMP}.dump}"
GLOBALS_FILE="${GLOBALS_FILE:-${OUTPUT_DIR}/globals_${TIMESTAMP}.sql}"

mkdir -p "${OUTPUT_DIR}"

echo "[1/3] Export database dump from container ${CONTAINER_NAME}..."
docker exec "${CONTAINER_NAME}" pg_dump -U "${DB_USER}" -d "${DB_NAME}" -Fc -f /tmp/wifitest.dump

echo "[2/3] Copy dump to host file ${DUMP_FILE}..."
docker cp "${CONTAINER_NAME}:/tmp/wifitest.dump" "${DUMP_FILE}"

echo "[3/3] Export global roles/privileges to ${GLOBALS_FILE}..."
docker exec "${CONTAINER_NAME}" pg_dumpall -U "${DB_USER}" --globals-only > "${GLOBALS_FILE}"

echo "Done."
echo "Dump file: ${DUMP_FILE}"
echo "Globals file: ${GLOBALS_FILE}"
echo "Next: copy both files to new host and run scripts/db_import_to_new_host.sh"
