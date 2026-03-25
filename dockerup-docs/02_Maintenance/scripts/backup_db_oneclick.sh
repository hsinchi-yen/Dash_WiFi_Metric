#!/usr/bin/env bash
set -euo pipefail

# One-click DB backup helper for WiFi Dashboard.
# Default mode: logical backup (pg_dump + globals) and tar.gz packaging.
# Optional mode: cold backup of pgdata (requires temporary postgres stop).
#
# Usage:
#   ./backup_db_oneclick.sh
#   MODE=logical ./backup_db_oneclick.sh
#   MODE=cold ./backup_db_oneclick.sh
#
# Optional overrides:
#   BASE=/mnt/md127/WIFI_YFP_DashBoard
#   CONTAINER=wifitest-db
#   DB_NAME=wifitest
#   DB_USER=qc

MODE="${MODE:-logical}"
BASE="${BASE:-/mnt/md127/WIFI_YFP_DashBoard}"
CONTAINER="${CONTAINER:-wifitest-db}"
DB_NAME="${DB_NAME:-wifitest}"
DB_USER="${DB_USER:-qc}"
COMPOSE_FILE="${BASE}/dockerup-essential/docker-compose.yml"
BACKUP_DIR="${BASE}/backups"
TS="$(date +%Y%m%d_%H%M%S)"

log() {
  echo "[$(date +%F' '%T)] $*"
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[error] command not found: $1" >&2
    exit 1
  }
}

prepare() {
  need_cmd docker
  need_cmd tar
  mkdir -p "${BACKUP_DIR}"
}

backup_logical() {
  local dump_name="wifitest_${TS}.dump"
  local globals_name="globals_${TS}.sql"
  local archive_name="db_backup_${TS}.tar.gz"

  log "starting logical backup from container ${CONTAINER}"

  docker exec "${CONTAINER}" sh -lc "pg_dump -U ${DB_USER} -d ${DB_NAME} -Fc -f /tmp/${dump_name}"
  docker cp "${CONTAINER}:/tmp/${dump_name}" "${BACKUP_DIR}/${dump_name}"

  docker exec "${CONTAINER}" sh -lc "pg_dumpall -U ${DB_USER} --globals-only -f /tmp/${globals_name}"
  docker cp "${CONTAINER}:/tmp/${globals_name}" "${BACKUP_DIR}/${globals_name}"

  tar -C "${BACKUP_DIR}" -czf "${BACKUP_DIR}/${archive_name}" "${dump_name}" "${globals_name}"

  log "logical backup complete"
  log "archive: ${BACKUP_DIR}/${archive_name}"
  ls -lh "${BACKUP_DIR}/${archive_name}"
}

backup_cold() {
  local pgdata_archive="pgdata_${TS}.tar.gz"

  if [[ ! -f "${COMPOSE_FILE}" ]]; then
    echo "[error] compose file not found: ${COMPOSE_FILE}" >&2
    exit 1
  fi

  log "stopping postgres service for cold backup"
  docker compose -f "${COMPOSE_FILE}" stop postgres

  trap 'log "attempting to restart postgres after interruption"; docker compose -f "${COMPOSE_FILE}" start postgres || true' EXIT

  log "creating pgdata archive"
  tar -C "${BASE}/docker-data" -czf "${BACKUP_DIR}/${pgdata_archive}" pgdata

  log "starting postgres service"
  docker compose -f "${COMPOSE_FILE}" start postgres
  trap - EXIT

  log "cold backup complete"
  log "archive: ${BACKUP_DIR}/${pgdata_archive}"
  ls -lh "${BACKUP_DIR}/${pgdata_archive}"
}

main() {
  prepare

  case "${MODE}" in
    logical)
      backup_logical
      ;;
    cold)
      backup_cold
      ;;
    *)
      echo "[error] unsupported MODE=${MODE}. Use logical or cold." >&2
      exit 1
      ;;
  esac
}

main "$@"
