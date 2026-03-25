#!/usr/bin/env bash
set -euo pipefail

# One-click DB restore helper for WiFi Dashboard backups.
# Supports restoring from:
# 1) logical archive: db_backup_<TS>.tar.gz (contains *.dump + globals*.sql)
# 2) cold archive:    pgdata_<TS>.tar.gz (filesystem-level pgdata backup)
#
# Usage examples:
#   sudo ./restore_db_oneclick.sh ARCHIVE=/mnt/md127/WIFI_YFP_DashBoard/backups/db_backup_20260325_120000.tar.gz
#   sudo MODE=logical ARCHIVE=/mnt/md127/WIFI_YFP_DashBoard/backups/db_backup_20260325_120000.tar.gz ./restore_db_oneclick.sh
#   sudo MODE=cold ARCHIVE=/mnt/md127/WIFI_YFP_DashBoard/backups/pgdata_20260325_120000.tar.gz ./restore_db_oneclick.sh

MODE="${MODE:-logical}"
BASE="${BASE:-/mnt/md127/WIFI_YFP_DashBoard}"
CONTAINER="${CONTAINER:-wifitest-db}"
DB_NAME="${DB_NAME:-wifitest}"
DB_USER="${DB_USER:-qc}"
COMPOSE_FILE="${BASE}/dockerup-essential/docker-compose.yml"
BACKUP_DIR="${BASE}/backups"
WORK_DIR="${BACKUP_DIR}/restore_tmp"
ARCHIVE="${ARCHIVE:-}"

log() {
  echo "[$(date +%F' '%T)] $*"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[error] command not found: $1" >&2
    exit 1
  }
}

wait_postgres_ready() {
  log "waiting for postgres readiness"
  for _ in {1..60}; do
    if docker exec "${CONTAINER}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "[error] postgres not ready in time" >&2
  exit 1
}

prepare() {
  need_cmd docker
  need_cmd tar

  if [[ -z "${ARCHIVE}" ]]; then
    echo "[error] ARCHIVE is required" >&2
    exit 1
  fi
  if [[ ! -f "${ARCHIVE}" ]]; then
    echo "[error] archive not found: ${ARCHIVE}" >&2
    exit 1
  fi
  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "[error] compose file not found: ${COMPOSE_FILE}" >&2
    exit 1
  fi

  rm -rf "${WORK_DIR}"
  mkdir -p "${WORK_DIR}"
}

restore_logical() {
  log "extracting logical archive"
  tar -C "${WORK_DIR}" -xzf "${ARCHIVE}"

  local dump_file
  dump_file="$(find "${WORK_DIR}" -maxdepth 1 -type f -name '*.dump' | head -n 1 || true)"
  local globals_file
  globals_file="$(find "${WORK_DIR}" -maxdepth 1 -type f -name 'globals*.sql' | head -n 1 || true)"

  if [[ -z "${dump_file}" ]]; then
    echo "[error] no .dump file found in archive" >&2
    exit 1
  fi

  log "starting postgres service"
  docker compose -f "${COMPOSE_FILE}" up -d postgres
  wait_postgres_ready

  if [[ -n "${globals_file}" ]]; then
    log "restoring globals"
    cat "${globals_file}" | docker exec -i "${CONTAINER}" psql -U "${DB_USER}" || true
  fi

  local container_dump="/tmp/restore_$(basename "${dump_file}")"
  log "copying dump into container"
  docker cp "${dump_file}" "${CONTAINER}:${container_dump}"

  log "restoring database (clean + if-exists)"
  docker exec "${CONTAINER}" pg_restore -U "${DB_USER}" -d "${DB_NAME}" --clean --if-exists "${container_dump}"

  log "starting full stack"
  docker compose -f "${COMPOSE_FILE}" up -d

  log "verify row count"
  docker exec -i "${CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT COUNT(*) AS total_records FROM test_record;"

  log "logical restore complete"
}

restore_cold() {
  log "stopping stack for cold restore"
  docker compose -f "${COMPOSE_FILE}" down

  local pgdata_dir="${BASE}/docker-data/pgdata"
  mkdir -p "${pgdata_dir}"

  log "clearing existing pgdata"
  rm -rf "${pgdata_dir:?}"/*

  log "extracting cold backup into docker-data"
  tar -C "${BASE}/docker-data" -xzf "${ARCHIVE}"

  log "fixing ownership for postgres"
  chown -R 999:999 "${pgdata_dir}"

  log "starting stack"
  docker compose -f "${COMPOSE_FILE}" up -d

  log "verify postgres readiness"
  wait_postgres_ready

  log "verify row count"
  docker exec -i "${CONTAINER}" psql -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT COUNT(*) AS total_records FROM test_record;"

  log "cold restore complete"
}

main() {
  prepare

  case "${MODE}" in
    logical)
      restore_logical
      ;;
    cold)
      restore_cold
      ;;
    *)
      echo "[error] unsupported MODE=${MODE}. Use logical or cold." >&2
      exit 1
      ;;
  esac
}

main "$@"
