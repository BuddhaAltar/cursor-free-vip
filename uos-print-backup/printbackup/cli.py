"""Command-line management tool for the print-backup service.

Everything here is CLI-only: there is no GUI. The actual backup work is done
by the background systemd service (printbackup.daemon); this module only
installs/configures/inspects that service.
"""
import argparse
import datetime
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import CONFIG_PATH, load_config, save_config

SERVICE_NAME = "print-backup.service"
INSTALL_DIR = Path("/opt/print-backup")
UNIT_PATH = Path("/etc/systemd/system") / SERVICE_NAME
STATE_DIR = Path("/var/lib/print-backup")
WRAPPER_PATH = Path("/usr/local/bin/printbackup")

UNIT_TEMPLATE = """[Unit]
Description=UOS Print Job Backup Service
After=cups.service
Wants=cups.service

[Service]
Type=simple
ExecStart=/usr/bin/python3 -m printbackup.daemon
Environment=PYTHONPATH=/opt/print-backup
Restart=on-failure
RestartSec=5
User=root

[Install]
WantedBy=multi-user.target
"""

WRAPPER_TEMPLATE = """#!/usr/bin/env python3
import sys
sys.path.insert(0, "/opt/print-backup")
from printbackup.cli import main
sys.exit(main())
"""


def require_root():
    if os.geteuid() != 0:
        print("该操作需要 root 权限，请使用 sudo 运行，例如: sudo printbackup install", file=sys.stderr)
        sys.exit(1)


def run(cmd, check=True):
    print("+", " ".join(cmd))
    return subprocess.run(cmd, check=check)


def cmd_install(args):
    require_root()

    src_root = Path(__file__).resolve().parent.parent
    if src_root.resolve() != INSTALL_DIR.resolve():
        print(f"==> 复制程序到 {INSTALL_DIR} ...")
        if INSTALL_DIR.exists():
            shutil.rmtree(INSTALL_DIR)
        shutil.copytree(src_root / "printbackup", INSTALL_DIR / "printbackup")
    else:
        print("==> 程序已经在目标目录，跳过复制。")

    print("==> 安装依赖 (cups-client, python3-cups) ...")
    try:
        run(["apt-get", "update"])
        run(["apt-get", "install", "-y", "cups-client", "python3-cups"])
    except subprocess.CalledProcessError:
        print("警告: 自动安装依赖失败，请手动执行: sudo apt install cups-client python3-cups", file=sys.stderr)

    print("==> 配置 CUPS 保留打印任务文件 (PreserveJobFiles) ...")
    try:
        run(["cupsctl", "PreserveJobFiles=yes", "PreserveJobHistory=yes"])
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("警告: cupsctl 配置失败，请确认 CUPS 打印服务已安装并运行。", file=sys.stderr)

    cfg = load_config()
    save_config(cfg)
    Path(cfg["backup_dir"]).mkdir(parents=True, exist_ok=True)
    os.chmod(cfg["backup_dir"], 0o755)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"==> 写入命令行工具 {WRAPPER_PATH} ...")
    WRAPPER_PATH.write_text(WRAPPER_TEMPLATE, encoding="utf-8")
    WRAPPER_PATH.chmod(0o755)

    print("==> 安装 systemd 服务 ...")
    UNIT_PATH.write_text(UNIT_TEMPLATE, encoding="utf-8")
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", SERVICE_NAME])

    print("\n安装完成！打印文件将自动备份到:", cfg["backup_dir"])
    print("常用命令: printbackup status | printbackup list | printbackup logs -f")


def cmd_uninstall(args):
    require_root()
    subprocess.run(["systemctl", "disable", "--now", SERVICE_NAME], check=False)
    if UNIT_PATH.exists():
        UNIT_PATH.unlink()
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
    if WRAPPER_PATH.exists():
        WRAPPER_PATH.unlink()

    if args.purge:
        cfg = load_config()
        backup_dir = Path(cfg["backup_dir"])
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        if CONFIG_PATH.exists():
            CONFIG_PATH.unlink()
        if STATE_DIR.exists():
            shutil.rmtree(STATE_DIR)
        print("已卸载服务，并删除所有备份文件与配置。")
    else:
        print("已卸载服务（备份文件与配置已保留）。如需一并删除，请加 --purge 参数。")


def cmd_status(args):
    subprocess.run(["systemctl", "status", SERVICE_NAME, "--no-pager"], check=False)
    cfg = load_config()
    backup_dir = Path(cfg["backup_dir"])
    count = sum(1 for f in backup_dir.rglob("*") if f.is_file()) if backup_dir.exists() else 0
    print(f"\n配置文件: {CONFIG_PATH}")
    print(f"备份目录: {cfg['backup_dir']} (共 {count} 个文件)")
    print(f"保留天数: {cfg['retention_days']} 天 (0 表示永久保留)")


def cmd_logs(args):
    cmd = ["journalctl", "-u", SERVICE_NAME, "-n", str(args.lines)]
    if args.follow:
        cmd.append("-f")
    subprocess.run(cmd, check=False)


def cmd_list(args):
    cfg = load_config()
    backup_dir = Path(cfg["backup_dir"])
    if not backup_dir.exists():
        print("还没有任何备份。")
        return

    files = [f for f in backup_dir.rglob("*") if f.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    if args.days:
        import time

        cutoff = time.time() - args.days * 86400
        files = [f for f in files if f.stat().st_mtime >= cutoff]

    for f in files[: args.limit]:
        mtime = datetime.datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        print(f"{mtime}  {f.stat().st_size:>10} bytes  {f}")

    print(f"\n共 {len(files)} 个文件。")


def cmd_config(args):
    cfg = load_config()
    if args.action == "get":
        if args.key:
            print(cfg.get(args.key))
        else:
            for k, v in cfg.items():
                print(f"{k} = {v}")
        return

    if args.key is None or args.value is None:
        print("用法: printbackup config set <key> <value>", file=sys.stderr)
        sys.exit(1)

    require_root()
    value = args.value
    if args.key in ("retention_days", "poll_interval"):
        value = int(value)
    cfg[args.key] = value
    save_config(cfg)
    if args.key == "backup_dir":
        Path(value).mkdir(parents=True, exist_ok=True)
    print(f"已设置 {args.key} = {value}")
    print(f"重启服务以生效: sudo systemctl restart {SERVICE_NAME}")


def cmd_doctor(args):
    ok = True

    try:
        import cups  # noqa: F401

        print("[OK]   python3-cups 已安装")
    except ImportError:
        print("[FAIL] python3-cups 未安装，请运行: sudo apt install python3-cups")
        ok = False

    r = subprocess.run(["systemctl", "is-active", "cups"], capture_output=True, text=True)
    if r.stdout.strip() == "active":
        print("[OK]   CUPS 打印服务运行中")
    else:
        print("[FAIL] CUPS 打印服务未运行")
        ok = False

    try:
        r = subprocess.run(["cupsctl"], capture_output=True, text=True, check=False)
        out = r.stdout.lower()
        if "preservejobfiles=yes" in out:
            print("[OK]   CUPS 已开启 PreserveJobFiles")
        else:
            print("[WARN] 未确认 PreserveJobFiles 已开启，请运行: sudo cupsctl PreserveJobFiles=yes PreserveJobHistory=yes")
    except FileNotFoundError:
        print("[FAIL] 未找到 cupsctl 命令，请确认已安装 cups-client")
        ok = False

    cfg = load_config()
    backup_dir = Path(cfg["backup_dir"])
    if backup_dir.exists() and os.access(backup_dir, os.W_OK):
        print(f"[OK]   备份目录可写: {backup_dir}")
    else:
        print(f"[WARN] 备份目录不存在或不可写: {backup_dir}")

    r = subprocess.run(["systemctl", "is-active", SERVICE_NAME], capture_output=True, text=True)
    if r.stdout.strip() == "active":
        print(f"[OK]   {SERVICE_NAME} 正在运行")
    else:
        print(f"[WARN] {SERVICE_NAME} 未运行，请运行: sudo printbackup install")

    return 0 if ok else 1


def build_parser():
    parser = argparse.ArgumentParser(prog="printbackup", description="UOS 打印任务自动备份服务管理工具（纯命令行，无图形界面）")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("install", help="安装依赖、配置 CUPS 并启动后台备份服务").set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="卸载后台备份服务")
    p.add_argument("--purge", action="store_true", help="同时删除已备份文件和配置")
    p.set_defaults(func=cmd_uninstall)

    sub.add_parser("status", help="查看服务运行状态与备份概况").set_defaults(func=cmd_status)

    p = sub.add_parser("logs", help="查看服务日志")
    p.add_argument("-f", "--follow", action="store_true", help="持续跟踪日志")
    p.add_argument("-n", "--lines", type=int, default=50, help="显示最近 N 行")
    p.set_defaults(func=cmd_logs)

    p = sub.add_parser("list", help="列出已备份的打印文件")
    p.add_argument("--days", type=int, default=0, help="只显示最近 N 天的备份")
    p.add_argument("--limit", type=int, default=50, help="最多显示的条数")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("config", help="查看或修改配置 (backup_dir / retention_days / poll_interval)")
    p.add_argument("action", choices=["get", "set"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.set_defaults(func=cmd_config)

    sub.add_parser("doctor", help="检查运行环境与依赖是否齐全").set_defaults(func=cmd_doctor)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
