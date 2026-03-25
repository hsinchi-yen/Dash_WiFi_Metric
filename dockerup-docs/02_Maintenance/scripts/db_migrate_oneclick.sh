#!/usr/bin/env bash
set -euo pipefail

# One-click DB migration helper for this project.
# Modes:
#   export  - run backup on old host
#   import  - restore on new host
#   verify  - validate record counts and API health
#
# Examples:
#   ./scripts/db_migrate_oneclick.sh export
#   DUMP_FILE=./wifitest_20260324_120000.dump GLOBALS_FILE=./globals_20260324_120000.sql ./scripts/db_migrate_oneclick.sh import
#   API_URL=http://10.88.88.250:8000 ./scripts/db_migrate_oneclick.sh verify

MODE="${1:-help}"

CONTAINER_NAME="${CONTAINER_NAME:-wifitest-db}"
DB_NAME="${DB_NAME:-wifitest}"
DB_USER="${DB_USER:-qc}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"
OUTPUT_DIR="${OUTPUT_DIR:-./backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
DUMP_FILE="${DUMP_FILE:-${OUTPUT_DIR}/wifitest_${TIMESTAMP}.dump}"
GLOBALS_FILE="${GLOBALS_FILE:-${OUTPUT_DIR}/globals_${TIMESTAMP}.sql}"
API_URL="${API_URL:-http://localhost:8000}"
STORAGE_PATH="${STORAGE_PATH:-/mnt/wifi-storage}"
APP_DIR_NAME="${APP_DIR_NAME:-wifi-dashboard}"

APP_ROOT="${STORAGE_PATH}/${APP_DIR_NAME}"
DATA_ROOT="${STORAGE_PATH}/docker-data"

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN="docker-compose"
else
  COMPOSE_BIN="docker compose"
fi

start_postgres_only() {
  echo "[info] starting postgres service only..."
  ${COMPOSE_BIN} -f "${COMPOSE_FILE}" up -d postgres
}

wait_postgres_ready() {
  echo "[info] waiting for postgres ready..."
  for i in {1..60}; do
    if docker exec "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "[error] postgres not ready in time"
  exit 1
}

do_export() {
  mkdir -p "${OUTPUT_DIR}"
  echo "[1/3] export dump from ${CONTAINER_NAME}"
  docker exec "${CONTAINER_NAME}" pg_dump -U "${DB_USER}" -d "${DB_NAME}" -Fc -f /tmp/wifitest.dump

  echo "[2/3] copy dump to host: ${DUMP_FILE}"
  docker cp "${CONTAINER_NAME}:/tmp/wifitest.dump" "${DUMP_FILE}"

  echo "[3/3] export globals: ${GLOBALS_FILE}"
  docker exec "${CONTAINER_NAME}" pg_dumpall -U "${DB_USER}" --globals-only > "${GLOBALS_FILE}"

  echo "[done] export complete"
  echo "DUMP_FILE=${DUMP_FILE}"
  echo "GLOBALS_FILE=${GLOBALS_FILE}"
}

do_import() {
  if [[ ! -f "${DUMP_FILE}" ]]; then
    echo "[error] dump file not found: ${DUMP_FILE}"
    exit 1
  fi

  start_postgres_only
  wait_postgres_ready

  echo "[1/4] copy dump into container"
  docker cp "${DUMP_FILE}" "${CONTAINER_NAME}:/tmp/wifitest.dump"

  if [[ -n "${GLOBALS_FILE}" && -f "${GLOBALS_FILE}" ]]; then
    echo "[2/4] restore globals"
    cat "${GLOBALS_FILE}" | docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}"
  else
    echo "[2/4] skip globals (GLOBALS_FILE not set or missing)"
  fi

  echo "[3/4] restore database"
  docker exec "${CONTAINER_NAME}" pg_restore -U "${DB_USER}" -d "${DB_NAME}" --clean --if-exists /tmp/wifitest.dump

  echo "[4/4] start full stack"
  ${COMPOSE_BIN} -f "${COMPOSE_FILE}" up -d

  echo "[done] import complete"
}

do_verify() {
  echo "[1/3] check row count"
  docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT COUNT(*) AS total_records FROM test_record;"

  echo "[2/3] sample work orders"
  docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT work_order, COUNT(*) FROM test_record GROUP BY work_order ORDER BY 2 DESC LIMIT 10;"

  echo "[3/3] api health"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "${API_URL}/health" || (echo "[error] api health check failed"; exit 1)
    echo
    echo "[done] verify complete"
  else
    echo "[warn] curl not found, skip API health check"
  fi
}

do_prepare_layout() {
  echo "[1/5] create mount root: ${STORAGE_PATH}"
  mkdir -p "${STORAGE_PATH}"

  echo "[2/5] create app root: ${APP_ROOT}"
  mkdir -p "${APP_ROOT}"

  echo "[3/5] create docker data dirs under ${DATA_ROOT}"
  mkdir -p "${DATA_ROOT}/pgdata" "${DATA_ROOT}/grafana-data" "${DATA_ROOT}/app-logs"

  echo "[4/5] create app subdirs"
  mkdir -p "${APP_ROOT}/api" "${APP_ROOT}/grafana" "${APP_ROOT}/logs" "${APP_ROOT}/WiFiTestLogs" "${APP_ROOT}/scripts"

  echo "[5/5] set basic permissions"
  chmod 755 "${STORAGE_PATH}" "${APP_ROOT}" || true
  chmod -R 755 "${APP_ROOT}" || true

  echo "[done] layout prepared"
  echo "APP_ROOT=${APP_ROOT}"
  echo "DATA_ROOT=${DATA_ROOT}"
}

do_copy_manifest() {
  cat <<EOF
Copy these required files/folders from current project root into:
  ${APP_ROOT}

Required:
  - docker-compose.yml
  - docker-compose-ubuntu.yml
  - schema.sql
  - log_parser.py
  - wifi_dashboard.html
  - .env.example
  - api/
  - grafana/
  - logs/
  - WiFiTestLogs/
  - scripts/

Optional docs:
  - MigrationGuide.txt
  - DEPLOYMENT_GUIDE.md
  - UBUNTU_QUICK_START.md
  - UBUNTU_DISK_MANAGEMENT.md
  - DBEAVER_SQL_OPERATIONS.md

Suggested folder naming:
  - mountpoint: ${STORAGE_PATH}
  - app root:   ${APP_ROOT}
  - db data:    ${DATA_ROOT}/pgdata
  - grafana:    ${DATA_ROOT}/grafana-data
  - app logs:   ${DATA_ROOT}/app-logs

Recommended copy command from project root:
  rsync -av \
    --exclude '.git' \
    --exclude '.venv' \
    --exclude '__pycache__' \
    --exclude 'backups' \
    ./ "${APP_ROOT}/"
EOF
}

case "${MODE}" in
  export)
    do_export
    ;;
  import)
    do_import
    ;;
  verify)
    do_verify
    ;;
  prepare-layout)
    do_prepare_layout
    ;;
  copy-manifest)
    do_copy_manifest
    ;;
  *)
    cat <<EOF
Usage:
  $0 export
  $0 import
  $0 verify
  $0 prepare-layout
  $0 copy-manifest

Environment variables (optional):
  CONTAINER_NAME=${CONTAINER_NAME}
  DB_NAME=${DB_NAME}
  DB_USER=${DB_USER}
  COMPOSE_FILE=${COMPOSE_FILE}
  OUTPUT_DIR=${OUTPUT_DIR}
  DUMP_FILE=${DUMP_FILE}
  GLOBALS_FILE=${GLOBALS_FILE}
  API_URL=${API_URL}
  STORAGE_PATH=${STORAGE_PATH}
  APP_DIR_NAME=${APP_DIR_NAME}
EOF
    ;;
esac
