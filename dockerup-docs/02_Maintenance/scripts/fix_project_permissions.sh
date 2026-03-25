#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${PROJECT_ROOT:-/mnt/md0/WIFI_YFP_DashBoard}"
LOGIN_USER="${LOGIN_USER:-sit}"
LOGIN_GROUP="${LOGIN_GROUP:-sit}"
POSTGRES_UID="${POSTGRES_UID:-999}"
POSTGRES_GID="${POSTGRES_GID:-999}"
GRAFANA_UID="${GRAFANA_UID:-472}"
GRAFANA_GID="${GRAFANA_GID:-472}"

if [[ ! -d "${PROJECT_ROOT}" ]]; then
  echo "[error] project root not found: ${PROJECT_ROOT}"
  exit 1
fi

echo "[1/6] ensure top-level project paths are owned by ${LOGIN_USER}:${LOGIN_GROUP}"
chown "${LOGIN_USER}:${LOGIN_GROUP}" /mnt/md0 || true
chown -R "${LOGIN_USER}:${LOGIN_GROUP}" "${PROJECT_ROOT}"
chmod 775 /mnt/md0 || true
chmod 775 "${PROJECT_ROOT}"

for path in \
  "${PROJECT_ROOT}/dockerup-essential" \
  "${PROJECT_ROOT}/dockerup-docs" \
  "${PROJECT_ROOT}/noneedfile" \
  "${PROJECT_ROOT}/logs" \
  "${PROJECT_ROOT}/WiFiTestLogs"
do
  if [[ -e "${path}" ]]; then
    chown -R "${LOGIN_USER}:${LOGIN_GROUP}" "${path}"
    chmod -R u+rwX,g+rwX,o+rX "${path}"
  fi
done

echo "[2/6] set postgres data ownership"
if [[ -d "${PROJECT_ROOT}/docker-data/pgdata" ]]; then
  chown -R "${POSTGRES_UID}:${POSTGRES_GID}" "${PROJECT_ROOT}/docker-data/pgdata"
  chmod 700 "${PROJECT_ROOT}/docker-data/pgdata"
fi

echo "[3/6] set grafana data ownership"
if [[ -d "${PROJECT_ROOT}/docker-data/grafana-data" ]]; then
  chown -R "${GRAFANA_UID}:${GRAFANA_GID}" "${PROJECT_ROOT}/docker-data/grafana-data"
  chmod 755 "${PROJECT_ROOT}/docker-data/grafana-data"
fi

echo "[4/6] set app logs ownership for login user"
if [[ -d "${PROJECT_ROOT}/docker-data/app-logs" ]]; then
  chown -R "${LOGIN_USER}:${LOGIN_GROUP}" "${PROJECT_ROOT}/docker-data/app-logs"
  chmod -R u+rwX,g+rwX,o+rX "${PROJECT_ROOT}/docker-data/app-logs"
fi

echo "[5/6] ensure login user can access docker CLI"
if getent group docker >/dev/null 2>&1; then
  usermod -aG docker "${LOGIN_USER}" || true
fi

echo "[6/6] summary"
ls -ld /mnt/md0 "${PROJECT_ROOT}" || true
ls -ld "${PROJECT_ROOT}/docker-data/pgdata" "${PROJECT_ROOT}/docker-data/grafana-data" "${PROJECT_ROOT}/docker-data/app-logs" 2>/dev/null || true
id "${LOGIN_USER}" || true

echo "[done] permission repair complete"
echo "[note] if docker group was just added, re-login or run: newgrp docker"
