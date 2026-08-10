#!/bin/bash
# Uninstalls the UOS print job backup service. Pass --purge to also delete
# already-backed-up files and configuration.
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "请使用 sudo 运行此脚本: sudo ./uninstall.sh [--purge]" >&2
  exit 1
fi

if command -v printbackup >/dev/null 2>&1; then
  printbackup uninstall "$@"
else
  systemctl disable --now print-backup.service 2>/dev/null || true
  rm -f /etc/systemd/system/print-backup.service
  systemctl daemon-reload
  rm -rf /opt/print-backup
  rm -f /usr/local/bin/printbackup
  echo "已卸载。"
fi
