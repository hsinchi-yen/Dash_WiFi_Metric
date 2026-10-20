#!/usr/bin/env bash
set -euo pipefail

APP_NAME="${1:-wifi_stress_log_analyzer}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$SCRIPT_DIR"

echo "[1/4] Checking python3..."
command -v python3 >/dev/null 2>&1 || { echo "python3 not found"; exit 1; }

echo "[2/4] Installing/Upgrading build dependencies..."
python3 -m pip install --upgrade pip pyinstaller psycopg2-binary

echo "[3/4] Building Linux executable..."
rm -rf dist build "${APP_NAME}.spec"
python3 -m PyInstaller \
  --onefile \
  --noconfirm \
  --clean \
  --name "$APP_NAME" \
  --add-data "log_parser.py:." \
  wifi_stress_log_analyzer.py

echo "[4/4] Done"
echo "BIN: ${SCRIPT_DIR}/dist/${APP_NAME}"
