"""安卓群控：在电脑上操作主控手机画面，所有勾选的手机同步执行相同操作。

运行: python main.py
"""
import csv
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from device import Device, list_devices

DISPLAY_HEIGHT = 760   # 主控画面显示高度（像素）
TAP_MOVE_LIMIT = 10    # 鼠标移动小于这个距离视为点击
LONG_PRESS_SEC = 0.5   # 按住超过这个时间视为长按

KEYS = [
    ("返回", 4), ("主页", 3), ("多任务", 187),
    ("音量+", 24), ("音量-", 25), ("电源", 26),
]


class GroupControlApp:
    def __init__(self, root):
        self.root = root
        root.title("安卓群控")

        self.devices = {}          # serial -> Device
        self.sync_vars = {}        # serial -> BooleanVar（是否同步）
        self.master_var = tk.StringVar()
        self.pool = ThreadPoolExecutor(max_workers=32)

        self.frame_image = None    # 截图线程写入的最新画面
        self.frame_lock = threading.Lock()
        self.tk_image = None
        self.disp_size = (1, 1)
        self.landscape = False
        self.press = None          # (x, y, time)
        self.running = True
        self.csv_rows = []         # 导入的 CSV 行（dict）

        self._build_ui()
        self.refresh_devices()
        threading.Thread(target=self._capture_loop, daemon=True).start()
        self.root.after(50, self._update_canvas)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------- 界面 ----------
    def _build_ui(self):
        left = ttk.Frame(self.root, padding=8)
        left.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(left, text="设备列表（◉ 主控  ☑ 同步）").pack(anchor="w")
        self.dev_frame = ttk.Frame(left)
        self.dev_frame.pack(fill=tk.X, pady=4)

        btns = ttk.Frame(left)
        btns.pack(fill=tk.X, pady=4)
        ttk.Button(btns, text="刷新设备", command=self.refresh_devices).pack(side=tk.LEFT)
        ttk.Button(btns, text="全选", command=lambda: self._set_all(True)).pack(side=tk.LEFT)
        ttk.Button(btns, text="全不选", command=lambda: self._set_all(False)).pack(side=tk.LEFT)

        ttk.Separator(left).pack(fill=tk.X, pady=8)
        ttk.Label(left, text="按键（同步到所有设备）").pack(anchor="w")
        keys = ttk.Frame(left)
        keys.pack(fill=tk.X)
        for i, (name, code) in enumerate(KEYS):
            ttk.Button(keys, text=name, width=8,
                       command=lambda c=code: self.dispatch("key", c)
                       ).grid(row=i // 3, column=i % 3, padx=2, pady=2)

        ttk.Separator(left).pack(fill=tk.X, pady=8)
        ttk.Label(left, text="输入文字（先点中输入框）").pack(anchor="w")
        self.text_entry = ttk.Entry(left, width=28)
        self.text_entry.pack(fill=tk.X, pady=2)
        self.text_entry.bind("<Return>", lambda e: self.send_text())
        ttk.Button(left, text="发送文字", command=self.send_text).pack(anchor="w")

        ttk.Separator(left).pack(fill=tk.X, pady=8)
        ttk.Label(left, text="按设备输入不同内容（CSV 文件）").pack(anchor="w")
        ttk.Button(left, text="导入 CSV", command=self.import_csv).pack(anchor="w", pady=2)
        self.field_var = tk.StringVar()
        self.field_box = ttk.Combobox(left, textvariable=self.field_var,
                                      state="readonly", width=26)
        self.field_box.pack(fill=tk.X, pady=2)
        ttk.Button(left, text="输入每台设备对应的值",
                   command=self.send_per_device).pack(anchor="w")
        self.csv_info = tk.StringVar(value="未导入")
        ttk.Label(left, textvariable=self.csv_info, wraplength=220,
                  foreground="#555").pack(anchor="w")

        ttk.Separator(left).pack(fill=tk.X, pady=8)
        self.status = tk.StringVar(value="就绪")
        ttk.Label(left, textvariable=self.status, wraplength=220,
                  foreground="#555").pack(anchor="w")

        self.canvas = tk.Canvas(self.root, width=DISPLAY_HEIGHT * 9 // 16,
                                height=DISPLAY_HEIGHT, bg="black",
                                highlightthickness=0, cursor="hand2")
        self.canvas.pack(side=tk.LEFT, padx=8, pady=8)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<MouseWheel>", self.on_wheel)          # Windows / macOS
        self.canvas.bind("<Button-4>", lambda e: self.on_wheel(e, 120))   # Linux
        self.canvas.bind("<Button-5>", lambda e: self.on_wheel(e, -120))
        self.canvas.bind("<Button-3>", lambda e: self.dispatch("key", 4))  # 右键=返回

    def refresh_devices(self):
        self.status.set("正在扫描设备…")
        self.root.update_idletasks()
        try:
            found = list_devices()
        except Exception as e:  # adb 不存在等
            messagebox.showerror("错误", f"无法执行 adb：{e}")
            return

        found_serials = {s for s, _ in found}
        for serial in list(self.devices):
            if serial not in found_serials:
                self.devices.pop(serial).close()
                self.sync_vars.pop(serial, None)
        for serial, model in found:
            if serial not in self.devices:
                try:
                    self.devices[serial] = Device(serial, model)
                    self.sync_vars[serial] = tk.BooleanVar(value=True)
                except Exception as e:
                    print(f"跳过 {serial}: {e}")

        for w in self.dev_frame.winfo_children():
            w.destroy()
        for serial, dev in self.devices.items():
            row = ttk.Frame(self.dev_frame)
            row.pack(fill=tk.X)
            ttk.Radiobutton(row, variable=self.master_var, value=serial).pack(side=tk.LEFT)
            ttk.Checkbutton(row, variable=self.sync_vars[serial]).pack(side=tk.LEFT)
            ttk.Label(row, text=f"{dev.model}  {dev.width}x{dev.height}").pack(side=tk.LEFT)

        if self.master_var.get() not in self.devices:
            self.master_var.set(next(iter(self.devices), ""))
        self.status.set(f"在线设备 {len(self.devices)} 台")

    def _set_all(self, value):
        for var in self.sync_vars.values():
            var.set(value)

    # ---------- 主控画面 ----------
    def _capture_loop(self):
        while self.running:
            dev = self.devices.get(self.master_var.get())
            if dev is None:
                time.sleep(0.5)
                continue
            try:
                img = dev.screenshot()
                with self.frame_lock:
                    self.frame_image = img
            except Exception:
                time.sleep(0.5)

    def _update_canvas(self):
        with self.frame_lock:
            img, self.frame_image = self.frame_image, None
        if img is not None:
            self.landscape = img.width > img.height
            if self.landscape:
                h = DISPLAY_HEIGHT * 9 // 16
                w = round(img.width * h / img.height)
            else:
                h = DISPLAY_HEIGHT
                w = round(img.width * h / img.height)
            self.disp_size = (w, h)
            self.tk_image = ImageTk.PhotoImage(img.resize((w, h)))
            self.canvas.config(width=w, height=h)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor="nw", image=self.tk_image)
        if self.running:
            self.root.after(30, self._update_canvas)

    # ---------- 鼠标操作 → 同步 ----------
    def _norm(self, x, y):
        w, h = self.disp_size
        return min(max(x / w, 0), 1), min(max(y / h, 0), 1)

    def on_press(self, e):
        self.press = (e.x, e.y, time.time())

    def on_release(self, e):
        if not self.press:
            return
        x0, y0, t0 = self.press
        self.press = None
        held = time.time() - t0
        nx0, ny0 = self._norm(x0, y0)
        if abs(e.x - x0) < TAP_MOVE_LIMIT and abs(e.y - y0) < TAP_MOVE_LIMIT:
            if held >= LONG_PRESS_SEC:
                self.dispatch("long_press", nx0, ny0, int(held * 1000))
            else:
                self.dispatch("tap", nx0, ny0)
        else:
            nx1, ny1 = self._norm(e.x, e.y)
            duration = min(max(int(held * 1000), 100), 2000)
            self.dispatch("swipe", nx0, ny0, nx1, ny1, duration)

    def on_wheel(self, e, delta=None):
        delta = delta if delta is not None else e.delta
        nx, ny = self._norm(e.x, e.y)
        dy = -0.25 if delta > 0 else 0.25   # 滚轮向上 = 内容往下拉
        self.dispatch("swipe", nx, ny - dy / 2, nx, ny + dy / 2, 200)

    def send_text(self):
        s = self.text_entry.get()
        if s:
            self.dispatch("text", s)
            self.text_entry.delete(0, tk.END)

    # ---------- 按设备输入不同内容 ----------
    def import_csv(self):
        path = filedialog.askopenfilename(
            title="选择 CSV 文件", filetypes=[("CSV", "*.csv"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8-sig") as f:  # 兼容 Excel 导出的 BOM
                rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k}
                        for r in csv.DictReader(f)]
        except UnicodeDecodeError:
            with open(path, newline="", encoding="gbk") as f:        # 中文版 Excel 默认编码
                rows = [{k.strip(): (v or "").strip() for k, v in r.items() if k}
                        for r in csv.DictReader(f)]
        if not rows:
            messagebox.showwarning("提示", "文件里没有数据")
            return
        self.csv_rows = rows
        fields = [k for k in rows[0] if k not in ("serial", "设备")]
        self.field_box["values"] = fields
        if fields:
            self.field_var.set(fields[0])
        self._show_mapping()

    def _row_mapping(self):
        """返回 {serial: row}。有 serial/设备 列就按序列号对应，否则按设备列表顺序对应。"""
        if not self.csv_rows:
            return {}
        key = next((k for k in ("serial", "设备") if k in self.csv_rows[0]), None)
        if key:
            return {r[key]: r for r in self.csv_rows if r.get(key) in self.devices}
        return dict(zip(self.devices, self.csv_rows))

    def _show_mapping(self):
        mapping = self._row_mapping()
        field = self.field_var.get()
        lines = [f"{self.devices[s].model}: {r.get(field, '')}" for s, r in mapping.items()]
        missing = len(self.devices) - len(mapping)
        if missing:
            lines.append(f"（{missing} 台设备没有对应的行，会跳过）")
        self.csv_info.set(f"已导入 {len(self.csv_rows)} 行\n" + "\n".join(lines))

    def send_per_device(self):
        field = self.field_var.get()
        if not self.csv_rows or not field:
            messagebox.showinfo("提示", "请先导入 CSV 并选择要输入的列")
            return
        mapping = self._row_mapping()
        sent = 0
        for dev in self.targets():
            value = mapping.get(dev.serial, {}).get(field)
            if value:
                self.pool.submit(dev.text, value)
                sent += 1
        self._show_mapping()
        self.status.set(f"已按设备输入「{field}」→ {sent} 台设备")

    def targets(self):
        master = self.master_var.get()
        return [d for s, d in self.devices.items()
                if s == master or self.sync_vars[s].get()]

    def dispatch(self, action, *args):
        """把同一个操作并发下发到主控 + 所有勾选的设备。"""
        targets = self.targets()
        if not targets:
            return
        kwargs = {"landscape": self.landscape} if action in ("tap", "swipe", "long_press") else {}
        for dev in targets:
            self.pool.submit(getattr(dev, action), *args, **kwargs)
        self.status.set(f"{action} → {len(targets)} 台设备")

    def on_close(self):
        self.running = False
        for dev in self.devices.values():
            dev.close()
        self.pool.shutdown(wait=False)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    GroupControlApp(root)
    root.mainloop()
