import json
import os
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("PRINT_BACKUP_CONFIG", "/etc/print-backup/config.json"))

DEFAULTS = {
    # Where backed-up print jobs end up, organized as <backup_dir>/<year>/<month>/
    "backup_dir": "/var/backups/print-jobs",
    # Days to keep backups before automatic cleanup; 0 = keep forever.
    "retention_days": 180,
    # How often (seconds) the daemon polls CUPS for newly completed jobs.
    "poll_interval": 5,
    "log_level": "INFO",
}


def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            pass
    return cfg


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
