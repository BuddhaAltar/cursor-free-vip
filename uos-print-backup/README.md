# UOS 打印任务自动备份服务

一个运行在 UOS（统信 UOS，基于 CUPS 打印系统的 Debian 系发行版）上的后台服务：
每次你打印文件，它都会自动把被打印的那份文档复制一份保存到指定目录下，
方便日后误删恢复，或者查找几个月前打印过的文件。

**没有任何图形界面** —— 只有：

- 一个后台 `systemd` 服务 `print-backup.service`（开机自启，静默运行）
- 一个命令行管理工具 `printbackup`

## 工作原理

UOS 和大多数 Linux 桌面一样，打印统一经过 **CUPS** 打印系统处理。默认情况下，
CUPS 打印完成后会立刻删除任务的临时数据。本服务做两件事：

1. 安装时执行 `cupsctl PreserveJobFiles=yes PreserveJobHistory=yes`，让 CUPS
   保留每个打印任务的原始数据文件（不再打印完就删）。
2. 后台服务每隔几秒查询一次 CUPS 已完成的打印任务，把新完成任务对应的
   数据文件复制到备份目录，并按 `年/月` 分类、用打印机名/用户名/文档名/
   任务号自动命名，之后再清理旧的临时数据不影响系统。

对绝大多数应用（浏览器、WPS、LibreOffice、Office 等）来说，发送给打印机的
内容就是 PDF 或 PostScript，因此备份下来的文件可以直接双击打开查看，
也可以重新打印。部分驱动直通（raw）打印保存下来的是打印机语言格式，
仍可作为存档留底。

## 安装

```bash
cd uos-print-backup
sudo ./install.sh
```

安装脚本会自动：

- 安装依赖 `cups-client`、`python3-cups`（通过 `apt`）
- 打开 CUPS 的 `PreserveJobFiles`
- 把程序装到 `/opt/print-backup`，并在 `/usr/local/bin/printbackup` 生成命令行入口
- 安装并启动 `print-backup.service`（`systemctl enable --now`，开机自启）

默认备份目录：`/var/backups/print-jobs/`（按年/月分文件夹），普通用户也可以
直接浏览，无需 root。

## 命令行用法

```bash
printbackup status          # 查看服务运行状态、备份文件数量
printbackup list            # 列出已备份的文件（--days 7 只看最近7天）
printbackup logs -f         # 实时查看服务日志
printbackup config get      # 查看当前配置
sudo printbackup config set backup_dir /home/xxx/PrintBackups   # 修改备份目录
sudo printbackup config set retention_days 365                  # 修改保留天数，0 表示永久保留
printbackup doctor          # 检查依赖、CUPS 状态、目录权限等是否正常
sudo printbackup uninstall [--purge]   # 卸载服务，--purge 一并删除备份和配置
```

修改配置后需要重启服务生效：

```bash
sudo systemctl restart print-backup.service
```

## 目录结构

```
uos-print-backup/
├── install.sh              # 一键安装脚本
├── uninstall.sh             # 卸载脚本
├── systemd/print-backup.service   # systemd 单元文件（供参考，安装时会自动生成同样内容）
└── printbackup/
    ├── cli.py               # printbackup 命令行工具
    ├── daemon.py            # 后台备份守护进程（被 systemd 启动）
    └── config.py            # 配置读写 (/etc/print-backup/config.json)
```

## 注意事项

- 需要系统已安装并正在运行 CUPS 打印服务（UOS 默认自带打印功能即基于 CUPS）。
- 备份守护进程以 root 身份运行，因为 CUPS 的打印任务数据目录
  （`/var/spool/cups/`）默认只有 root 可读。
- 安装后建议打印一个测试页，然后运行 `printbackup list` 确认能看到刚才的
  备份文件，`printbackup doctor` 可以快速排查环境问题。
- 打开 `PreserveJobFiles` 只是让 CUPS 保留任务数据 **直到本服务把它复制走并
  再清理**，不会无限占用 `/var/spool/cups` 磁盘空间；备份目录本身的清理由
  `retention_days` 配置控制。
