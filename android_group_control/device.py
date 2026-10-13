"""ADB 设备封装：截图、点击、滑动、按键、输入文字。"""
import base64
import os
import re
import shlex
import shutil
import subprocess
import threading
from io import BytesIO

from PIL import Image

ADB = os.environ.get("ADB_PATH") or shutil.which("adb") or "adb"
# Windows 下隐藏 adb 弹出的黑色控制台窗口
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def list_devices():
    """返回 [(serial, model), ...]，只包含状态为 device 的在线设备。"""
    out = subprocess.run(
        [ADB, "devices", "-l"], capture_output=True, text=True,
        timeout=15, creationflags=_NO_WINDOW,
    ).stdout
    devices = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            model = next((p.split(":", 1)[1] for p in parts[2:] if p.startswith("model:")), "")
            devices.append((parts[0], model))
    return devices


class Device:
    def __init__(self, serial, model=""):
        self.serial = serial
        self.model = model or serial
        self.width, self.height = self._query_size()
        self._shell = None
        self._lock = threading.Lock()

    def __repr__(self):
        return f"<Device {self.serial} {self.width}x{self.height}>"

    # ---------- 底层 ----------
    def _adb(self, *args, timeout=15):
        return subprocess.run(
            [ADB, "-s", self.serial, *args], capture_output=True,
            timeout=timeout, creationflags=_NO_WINDOW,
        )

    def _query_size(self):
        out = self._adb("shell", "wm", "size").stdout.decode(errors="ignore")
        sizes = re.findall(r"(\d+)x(\d+)", out)  # 有 Override size 时取最后一个
        if not sizes:
            raise RuntimeError(f"无法获取 {self.serial} 的分辨率: {out!r}")
        w, h = map(int, sizes[-1])
        return min(w, h), max(w, h)  # 统一按竖屏记录

    def run(self, cmd):
        """通过常驻 adb shell 执行命令，避免每次重新建立连接，延迟更低。"""
        with self._lock:
            for _ in range(2):
                if self._shell is None or self._shell.poll() is not None:
                    self._shell = subprocess.Popen(
                        [ADB, "-s", self.serial, "shell"],
                        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
                    )
                try:
                    self._shell.stdin.write((cmd + "\n").encode())
                    self._shell.stdin.flush()
                    return
                except OSError:
                    self._shell = None

    def close(self):
        with self._lock:
            if self._shell and self._shell.poll() is None:
                try:
                    self._shell.stdin.close()
                    self._shell.terminate()
                except OSError:
                    pass
            self._shell = None

    # ---------- 截图 ----------
    def screenshot(self):
        data = self._adb("exec-out", "screencap", "-p", timeout=10).stdout
        if not data:
            raise RuntimeError(f"{self.serial} 截图失败")
        return Image.open(BytesIO(data)).convert("RGB")

    # ---------- 坐标换算 ----------
    def to_pixels(self, nx, ny, landscape):
        """把 0~1 的相对坐标换算成本机像素坐标（按主控的横竖屏方向）。"""
        w, h = (self.height, self.width) if landscape else (self.width, self.height)
        return int(nx * w), int(ny * h)

    # ---------- 操作 ----------
    def tap(self, nx, ny, landscape=False):
        x, y = self.to_pixels(nx, ny, landscape)
        self.run(f"input tap {x} {y}")

    def swipe(self, nx1, ny1, nx2, ny2, duration_ms=300, landscape=False):
        x1, y1 = self.to_pixels(nx1, ny1, landscape)
        x2, y2 = self.to_pixels(nx2, ny2, landscape)
        self.run(f"input swipe {x1} {y1} {x2} {y2} {int(duration_ms)}")

    def long_press(self, nx, ny, duration_ms=800, landscape=False):
        self.swipe(nx, ny, nx, ny, duration_ms, landscape)

    def key(self, keycode):
        self.run(f"input keyevent {keycode}")

    def text(self, s):
        if s.isascii():
            # input text 用 %s 表示空格
            self.run("input text " + shlex.quote(s.replace(" ", "%s")))
        else:
            # 中文等非 ASCII 需要手机安装 ADBKeyBoard 并设为当前输入法
            b64 = base64.b64encode(s.encode("utf-8")).decode()
            self.run(f"am broadcast -a ADB_INPUT_B64 --es msg {b64}")
