#!/bin/bash
# One-shot installer for the UOS print job backup service (CLI + systemd only, no GUI).
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "请使用 sudo 运行此脚本: sudo ./install.sh" >&2
  exit 1
fi

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"
python3 -m printbackup.cli install
