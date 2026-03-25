#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOUNT_SCRIPT_SRC="${SCRIPT_DIR}/wifi-md127-mount.sh"
MOUNT_SERVICE_SRC="${SCRIPT_DIR}/wifi-md127-mount.service"
MOUNT_SCRIPT_DST="/usr/local/sbin/wifi-md127-mount.sh"
MOUNT_SERVICE_DST="/etc/systemd/system/wifi-md127-mount.service"
MOUNT_ENV="/etc/default/wifi-md127-mount"

detect_md_device() {
  for candidate in /dev/md0 /dev/md127 /dev/md/*; do
    if [[ -b "${candidate}" ]]; then
      echo "${candidate}"
      return 0
    fi
  done
  return 1
}

DETECTED_DEVICE="$(detect_md_device || true)"
DETECTED_UUID=""
if [[ -n "${DETECTED_DEVICE}" ]] && command -v blkid >/dev/null 2>&1; then
  DETECTED_UUID="$(blkid -s UUID -o value "${DETECTED_DEVICE}" 2>/dev/null || true)"
fi

if [[ ! -f "${MOUNT_SCRIPT_SRC}" || ! -f "${MOUNT_SERVICE_SRC}" ]]; then
  echo "[error] mount script/service template not found in ${SCRIPT_DIR}"
  exit 1
fi

echo "[1/7] install mount script"
sudo cp "${MOUNT_SCRIPT_SRC}" "${MOUNT_SCRIPT_DST}"
sudo chmod 755 "${MOUNT_SCRIPT_DST}"

echo "[2/7] install service file"
sudo cp "${MOUNT_SERVICE_SRC}" "${MOUNT_SERVICE_DST}"

echo "[3/7] write default config (${MOUNT_ENV}) if missing"
if [[ ! -f "${MOUNT_ENV}" ]]; then
  cat <<EOF | sudo tee "${MOUNT_ENV}" >/dev/null
# Optional UUID. If set, service mounts by UUID first.
UUID_VALUE=${DETECTED_UUID}

# Fallback block device when UUID_VALUE is empty.
DEVICE=${DETECTED_DEVICE}

# Filesystem type.
FS_TYPE=ext4

# Mount point used by the project.
MOUNT_POINT=/mnt/md127

# Grant login user access after mount.
MOUNT_OWNER=sit
MOUNT_GROUP=sit
MOUNT_MODE=775

# Project folder under the mount point.
PROJECT_SUBDIR=WIFI_YFP_DashBoard
EOF
fi

echo "[4/7] daemon-reload"
sudo systemctl daemon-reload

echo "[5/7] enable wifi-md127-mount.service"
sudo systemctl enable wifi-md127-mount.service

echo "[6/7] start wifi-md127-mount.service now"
sudo systemctl start wifi-md127-mount.service

echo "[7/7] service status"
sudo systemctl status wifi-md127-mount.service --no-pager

echo "[done] detected device: ${DETECTED_DEVICE:-<none>}"
echo "[done] detected uuid:   ${DETECTED_UUID:-<none>}"
echo "[done] if sit should run docker without sudo, add docker group access:"
echo "  sudo usermod -aG docker sit"
echo "  newgrp docker"
echo "[done] if needed, edit ${MOUNT_ENV} then restart service:"
echo "  sudo systemctl restart wifi-md127-mount.service"
