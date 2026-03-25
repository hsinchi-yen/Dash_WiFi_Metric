#!/usr/bin/env bash
set -euo pipefail

# Import PostgreSQL data into new host container.
# Defaults match current project settings.

CONTAINER_NAME="${CONTAINER_NAME:-wifitest-db}"
DB_NAME="${DB_NAME:-wifitest}"
DB_USER="${DB_USER:-qc}"
DUMP_FILE="${DUMP_FILE:-./wifitest.dump}"
GLOBALS_FILE="${GLOBALS_FILE:-}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

if command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN="docker-compose"
else
  COMPOSE_BIN="docker compose"
fi

if [[ ! -f "${DUMP_FILE}" ]]; then
  echo "Dump file not found: ${DUMP_FILE}"
  exit 1
fi

echo "[1/5] Start PostgreSQL service only..."
${COMPOSE_BIN} -f "${COMPOSE_FILE}" up -d postgres

echo "[2/5] Wait for PostgreSQL readiness..."
for i in {1..60}; do
  if docker exec "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

docker exec "${CONTAINER_NAME}" pg_isready -U "${DB_USER}" -d "${DB_NAME}" >/dev/null

echo "[3/5] Copy dump into container..."
docker cp "${DUMP_FILE}" "${CONTAINER_NAME}:/tmp/wifitest.dump"

if [[ -n "${GLOBALS_FILE}" && -f "${GLOBALS_FILE}" ]]; then
  echo "[4/5] Restore global roles/privileges..."
  cat "${GLOBALS_FILE}" | docker exec -i "${CONTAINER_NAME}" psql -U "${DB_USER}"
else
  echo "[4/5] Skip global roles restore (GLOBALS_FILE not set or not found)."
fi

echo "[5/5] Restore database with clean/if-exists..."
docker exec "${CONTAINER_NAME}" pg_restore -U "${DB_USER}" -d "${DB_NAME}" --clean --if-exists /tmp/wifitest.dump

echo "Done."
echo "Now start full stack: ${COMPOSE_BIN} -f ${COMPOSE_FILE} up -d"
echo "Validate rows: docker exec -it ${CONTAINER_NAME} psql -U ${DB_USER} -d ${DB_NAME} -c \"SELECT COUNT(*) FROM test_record;\""
