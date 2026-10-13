# 安卓群控（Android Group Control）

在电脑上显示**一台主控手机**的画面，你在画面上点击、滑动、长按、按键、输入文字，
**所有勾选的手机会同时执行同样的操作**。不同分辨率的手机按相对坐标自动换算。

## 原理

```
 电脑窗口（显示主控画面）
   │ 鼠标点击 (x, y) → 换算成相对坐标 (0~1)
   ▼
 并发下发到每台手机：adb -s <序列号> shell input tap/swipe/keyevent …
   │ 每台手机按自己的分辨率换算回像素坐标
   ▼
 手机 A、B、C …… 同步执行
```

- 主控画面：循环 `adb exec-out screencap -p` 截图（约 2~5 帧/秒）。
- 操作下发：每台手机维持一个常驻 `adb shell`，命令直接写进去，减少延迟；多台手机并行执行。

## 准备

1. 电脑安装 Python 3.8+ 和 [Android platform-tools（adb）](https://developer.android.com/tools/releases/platform-tools)，把 adb 加入 PATH（或设置环境变量 `ADB_PATH` 指向 adb）。
2. 每台手机打开 **开发者选项 → USB 调试**。小米/OPPO/vivo 等还需要打开 **“USB 调试（安全设置）”**，否则无法模拟点击。
3. 用 USB（多台建议用带供电的 USB Hub）连接手机，在手机上点“允许 USB 调试”。`adb devices` 能看到所有设备即可。
   - 也可以用 WiFi：`adb tcpip 5555` 后 `adb connect 手机IP:5555`。
4. 安装依赖：`pip install -r requirements.txt`

## 运行

```bash
python main.py
```

| 操作 | 效果 |
|------|------|
| 左键单击 | 点击 |
| 左键按住不动 ≥0.5 秒 | 长按 |
| 左键拖动 | 滑动（拖动速度即滑动速度） |
| 滚轮 | 上下滚动 |
| 右键 | 返回键 |
| 左侧按钮 | 返回 / 主页 / 多任务 / 音量 / 电源 |
| 输入框 + 发送 | 向当前输入框输入文字 |

左侧设备列表：圆点选择**主控机**（显示它的画面），勾选框决定该设备**是否同步**。

### 输入中文

`adb input text` 不支持中文。需要在每台手机上安装 [ADBKeyBoard](https://github.com/senzhk/ADBKeyBoard) 并把它设为当前输入法，
程序遇到非英文字符会自动通过它输入。

## 注意事项

- 同步是**按坐标**复制的，各手机界面要保持一致（同一个 App、同一个页面、同样的横竖屏），否则会点错位置。建议所有手机屏幕比例相同。
- 截图方式帧率有限，适合“点一下、看一下”的操作。需要实时流畅画面，可以把截图部分换成 [scrcpy](https://github.com/Genymobile/scrcpy) 的视频流。
- 单台电脑 USB 带 20~30 台手机通常没问题，更多时建议走 WiFi 或分多台电脑。
