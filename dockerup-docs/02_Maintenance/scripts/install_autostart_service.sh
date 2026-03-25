#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="wifi-dashboard.service"
SERVICE_SRC="$(cd "$(dirname "$0")" && pwd)/${SERVICE_NAME}"
SERVICE_DST="/etc/systemd/system/${SERVICE_NAME}"

if [[ ! -f "${SERVICE_SRC}" ]]; then
  echo "[error] service template not found: ${SERVICE_SRC}"
  exit 1
fi

echo "[1/5] install service file"
sudo cp "${SERVICE_SRC}" "${SERVICE_DST}"

echo "[2/5] reload systemd"
sudo systemctl daemon-reload

echo "[3/5] enable docker service"
sudo systemctl enable docker

echo "[4/5] enable ${SERVICE_NAME}"
sudo systemctl enable "${SERVICE_NAME}"

echo "[5/5] start ${SERVICE_NAME}"
sudo systemctl start "${SERVICE_NAME}"

echo "[done] service status"
sudo systemctl status "${SERVICE_NAME}" --no-pager
