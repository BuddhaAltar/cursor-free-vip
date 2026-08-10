"""Background daemon: polls CUPS for completed print jobs and copies each
job's spooled document into the configured backup directory.

Requires CUPS to be running with PreserveJobFiles=yes (the CLI's ``install``
command turns this on), otherwise CUPS deletes job data right after printing
and there is nothing left to copy.
"""
import logging
import os
import shutil
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

from .config import load_config

SPOOL_DIR = Path("/var/spool/cups")
STATE_FILE = Path("/var/lib/print-backup/state.json")

# Maps CUPS document-format MIME types to a reasonable file extension.
EXT_MAP = {
    "application/pdf": "pdf",
    "application/postscript": "ps",
    "application/vnd.cups-pdf": "pdf",
    "application/vnd.cups-postscript": "ps",
    "application/vnd.cups-raw": "raw",
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/tiff": "tiff",
    "image/urf": "urf",
    "image/pwg-raster": "pwg",
    "text/plain": "txt",
    "application/octet-stream": "bin",
}


def sanitize(name, maxlen=80):
    name = str(name)
    cleaned = "".join(c if c.isalnum() or c in "-_.() " else "_" for c in name).strip()
    cleaned = cleaned.replace(" ", "_")
    return cleaned[:maxlen] or "job"


def load_state():
    import json

    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"last_job_id": 0}


def save_state(state):
    import json

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state), encoding="utf-8")


def spool_files_for_job(job_id):
    """CUPS names a job's spooled documents dNNNNN-DDD (job id, doc number)."""
    files = []
    doc_num = 1
    while True:
        candidate = SPOOL_DIR / f"d{job_id:05d}-{doc_num:03d}"
        if not candidate.exists():
            break
        files.append(candidate)
        doc_num += 1
    return files


def backup_job(conn, job_id, cfg, logger):
    files = spool_files_for_job(job_id)
    if not files:
        # Job produced no retained spool data (already purged, or nothing to print).
        return False

    try:
        attrs = conn.getJobAttributes(job_id)
    except Exception as exc:  # pycups raises cups.IPPError and friends
        logger.warning("job %s: could not read attributes (%s), using defaults", job_id, exc)
        attrs = {}

    printer_uri = attrs.get("job-printer-uri", attrs.get("printer-uri", ""))
    printer_name = sanitize(str(printer_uri).rstrip("/").split("/")[-1] or "printer")
    job_name = sanitize(attrs.get("job-name", f"job{job_id}"))
    user = sanitize(attrs.get("job-originating-user-name", "unknown"))
    doc_format = attrs.get("document-format", "application/octet-stream")
    ext = EXT_MAP.get(doc_format, "prn")

    created = attrs.get("time-at-creation")
    dt = datetime.fromtimestamp(created) if created else datetime.now()

    dest_dir = Path(cfg["backup_dir"]) / dt.strftime("%Y") / dt.strftime("%m")
    dest_dir.mkdir(parents=True, exist_ok=True)

    for idx, src in enumerate(files, start=1):
        suffix = "" if len(files) == 1 else f"_{idx}"
        fname = f"{dt.strftime('%Y%m%d_%H%M%S')}_{printer_name}_{user}_{job_name}_job{job_id}{suffix}.{ext}"
        dest = dest_dir / fname
        shutil.copy2(src, dest)
        os.chmod(dest, 0o644)
        logger.info("backed up job %s (printer=%s user=%s) -> %s", job_id, printer_name, user, dest)

    return True


def cleanup_old(cfg, logger):
    days = cfg.get("retention_days", 0)
    if not days or days <= 0:
        return
    cutoff = time.time() - days * 86400
    root = Path(cfg["backup_dir"])
    if not root.exists():
        return
    for f in root.rglob("*"):
        if f.is_file() and f.stat().st_mtime < cutoff:
            try:
                f.unlink()
                logger.info("removed expired backup %s", f)
            except OSError as exc:
                logger.warning("could not remove %s: %s", f, exc)


def run():
    cfg = load_config()
    logging.basicConfig(
        level=getattr(logging, str(cfg.get("log_level", "INFO")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger("print-backup")

    if os.geteuid() != 0:
        logger.warning("not running as root; CUPS spool files may not be readable")

    try:
        import cups
    except ImportError:
        logger.error("python3-cups is not installed; run: sudo apt install python3-cups")
        sys.exit(1)

    Path(cfg["backup_dir"]).mkdir(parents=True, exist_ok=True)
    state = load_state()

    running = {"flag": True}

    def handle_signal(signum, frame):
        running["flag"] = False

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    logger.info("print-backup daemon started, backup_dir=%s, poll_interval=%ss",
                cfg["backup_dir"], cfg["poll_interval"])

    last_cleanup_day = None
    while running["flag"]:
        try:
            conn = cups.Connection()
            jobs = conn.getJobs(which_jobs="completed", my_jobs=False)
            new_ids = sorted(jid for jid in jobs if jid > state["last_job_id"])
            for jid in new_ids:
                try:
                    backup_job(conn, jid, cfg, logger)
                except Exception:
                    logger.exception("failed to back up job %s", jid)
                state["last_job_id"] = jid
                save_state(state)
        except Exception:
            logger.exception("polling CUPS failed")

        today = datetime.now().date()
        if last_cleanup_day != today:
            cleanup_old(cfg, logger)
            last_cleanup_day = today

        for _ in range(int(cfg.get("poll_interval", 5) * 10)):
            if not running["flag"]:
                break
            time.sleep(0.1)

    logger.info("print-backup daemon stopped")


if __name__ == "__main__":
    run()
