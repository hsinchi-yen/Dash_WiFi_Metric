#!/usr/bin/env bash
set -euo pipefail

MOUNT_POINT="${MOUNT_POINT:-/mnt/md0}"
DEVICE="${DEVICE:-}"
FS_TYPE="${FS_TYPE:-ext4}"
UUID_VALUE="${UUID_VALUE:-}"
MOUNT_OWNER="${MOUNT_OWNER:-sit}"
MOUNT_GROUP="${MOUNT_GROUP:-sit}"
MOUNT_MODE="${MOUNT_MODE:-775}"
PROJECT_SUBDIR="${PROJECT_SUBDIR:-WIFI_YFP_DashBoard}"

apply_permissions() {
  local target_path="$1"
  if [[ -e "${target_path}" ]]; then
    chown "${MOUNT_OWNER}:${MOUNT_GROUP}" "${target_path}"
    chmod "${MOUNT_MODE}" "${target_path}"
  fi
}

mkdir -p "${MOUNT_POINT}"

if findmnt -rn -T "${MOUNT_POINT}" >/dev/null 2>&1; then
  echo "[ok] ${MOUNT_POINT} already mounted"
  exit 0
fi

if [[ -n "${UUID_VALUE}" ]]; then
  TARGET="UUID=${UUID_VALUE}"
elif [[ -b "${DEVICE}" ]]; then
  TARGET="${DEVICE}"
else
  for candidate in /dev/md0 /dev/md127 /dev/md/*; do
    if [[ -b "${candidate}" ]]; then
      TARGET="${candidate}"
      break
    fi
  done
  if [[ -z "${TARGET:-}" ]]; then
    echo "[error] no usable UUID_VALUE or md block device found"
    exit 1
  fi
fi

echo "[info] mounting ${TARGET} -> ${MOUNT_POINT}"
mount -t "${FS_TYPE}" "${TARGET}" "${MOUNT_POINT}"

if ! findmnt -rn -T "${MOUNT_POINT}" >/dev/null 2>&1; then
  echo "[error] mount verification failed for ${MOUNT_POINT}"
  exit 1
fi

apply_permissions "${MOUNT_POINT}"
apply_permissions "${MOUNT_POINT}/${PROJECT_SUBDIR}"

echo "[done] ${MOUNT_POINT} mounted"
