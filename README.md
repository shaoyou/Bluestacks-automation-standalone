# BlueStacks 自动巡逻脚本（ADB）

本方案通过 `adb shell input ...` 直接控制 BlueStacks，支持：
- 点击（`click`）
- 滑动（`swipe`）
- 等待（`wait`）
- 循环（`loop`）
- 巡逻（`patrol`：A 点到 B 点再返回）

## 1. 环境要求

- macOS / Linux / Windows（有 Python3）
- BlueStacks 已安装并可运行
- Android Platform Tools（包含 `adb`）已安装并可在终端执行 `adb`

## 2. 连接 BlueStacks

1. 启动 BlueStacks，打开你要自动化的游戏/应用。
2. 在终端执行：

```bash
adb connect 127.0.0.1:5555
adb devices
```

正常会看到类似：

```text
127.0.0.1:5555    device
```

如果不是 `5555`，请在 BlueStacks 的 ADB 设置里确认端口，并修改配置文件中的 `device`。

## 3. 文件说明

- `adb_bot.py`: 主脚本
- `patrol_plan.json`: 可直接运行的巡逻配置样例
- `plans/`: UI 管理的脚本目录（建议所有脚本放这里）
- `ui_manager.py`: 本地脚本管理 UI（新建/编辑/保存/切换/双实例运行）
- `swiftui_manager/`: SwiftUI 管理 UI（推荐）
- `record_touch.py`: 录制 BlueStacks 触摸并导出脚本
- `CONFIG_REFERENCE.md`: 关键字/type 列表/操作实例手册

## 3.1 启动管理 UI

```bash
python3 /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/ui_manager.py
```

UI 功能：
- 新建脚本（`New`）
- 编辑脚本（中间编辑器）
- 保存脚本（`Save`，会校验 JSON）
- 切换脚本（左侧列表）
- 同时运行 2 个脚本（`Runner A` 与 `Runner B` 独立启动/停止）

说明：
- UI 从 `plans/*.json` 读取脚本。
- 你可以在 `Runner A/B` 各自选择不同脚本、不同设备号并行执行。
- 两个 Runner 的日志互不影响，分别显示。

## 3.2 启动 SwiftUI 管理 UI（推荐）

```bash
/Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/swiftui_manager/run.sh
```

或手动：

```bash
cd /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/swiftui_manager
swift run BSManagerApp
```

说明：
- SwiftUI 版同样支持：新建、编辑、保存、切换脚本。
- SwiftUI 版支持录制触摸：`Recorder` 面板里点击 `Start Recording`，操作 BlueStacks 后点 `Stop Recording`。
- SwiftUI 左侧新增 `Calibration` 面板，可做 Ping/分辨率查询/点击滑动测试和 Invert 预设切换。
- Recorder 新增 `Auto Detect Mapping`：在 BlueStacks 画一笔“左下滑动”，自动回填 Invert/Swap 组合。
- Recorder 新增 `Lock Mapping`：锁定后不允许误改映射开关，并将映射配置写入脚本元数据，避免后续录制漂移。
- 支持 `Runner A` 和 `Runner B` 同时运行两个脚本。
- 如 Tk 版窗口异常，可直接使用 SwiftUI 版。

## 3.4 打包为 macOS App

一键打包：

```bash
/Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/package_app.sh
```

输出目录：
- `dist/BSManagerApp.app`（可双击运行）
- `dist/BSManagerApp`（可执行文件）

## 3.3 录制触摸并生成可循环脚本

命令行方式：

```bash
python3 /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/record_touch.py \
  --output /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/plans/recorded.json \
  --device 127.0.0.1:5555 \
  --loop-count -1
```

说明：
- 运行后开始监听 BlueStacks 触摸事件。
- 在 BlueStacks 内进行点击/滑动操作。
- 按 `Ctrl+C` 结束录制并保存。
- `--loop-count -1` 表示生成无限循环；`1` 表示只执行一次；`N` 表示循环 N 次。
- 默认开启温和清洗（删除明显误触 click 噪声），可通过 `--no-clean-noise` 关闭。
- 坐标轴翻转：`--invert-y`（常用于修正上下方向反转），`--invert-x`（左右反转）。
- 若出现 `No actions captured`，可强制指定触摸设备：

```bash
python3 /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/record_touch.py \
  --output /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/plans/recorded.json \
  --device 127.0.0.1:5555 \
  --event-dev /dev/input/event2 \
  --loop-count -1
```

## 4. 快速运行

先 dry-run（仅打印，不执行点击）：

```bash
python3 /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/adb_bot.py --plan /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/patrol_plan.json --dry-run
```

正式执行：

```bash
python3 /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/adb_bot.py --plan /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/patrol_plan.json
```

按 `Ctrl+C` 停止。

## 5. 坐标标定（非常关键）

先获取分辨率：

```bash
adb -s 127.0.0.1:5555 shell wm size
```

查看/抓取当前 UI 布局坐标（可选）：

```bash
adb -s 127.0.0.1:5555 shell uiautomator dump /sdcard/view.xml
adb -s 127.0.0.1:5555 pull /sdcard/view.xml
```

也可用试点法：

```bash
adb -s 127.0.0.1:5555 shell input tap 500 500
```

逐步修正 `patrol_plan.json` 中坐标直到准确。

## 6. 配置格式

`patrol_plan.json` 顶层字段：

- `device`: ADB 设备号（如 `127.0.0.1:5555`）
- `jitter_px`: 点击/滑动的随机偏移像素，降低固定轨迹风险
- `max_runtime_sec`: 最大运行秒数，`0` 为不限制
- `actions`: 动作数组

动作定义：

1. `click`

```json
{ "type": "click", "x": 1000, "y": 650 }
```

2. `swipe`

```json
{ "type": "swipe", "x1": 300, "y1": 400, "x2": 900, "y2": 400, "duration_ms": 600 }
```

3. `wait`

```json
{ "type": "wait", "seconds": 1.0, "jitter_seconds": 0.2 }
```

4. `loop`

```json
{ "type": "loop", "count": -1, "actions": [ ... ] }
```

- `count = -1` 表示无限循环

5. `patrol`

```json
{
  "type": "patrol",
  "from": { "x": 300, "y": 400 },
  "to": { "x": 900, "y": 400 },
  "duration_ms": 600,
  "leg_wait_sec": 0.5,
  "rounds": 1
}
```

表示 A->B->A 一次往返。`rounds=-1` 表示无限往返。

## 7. 调试流程（建议）

1. 先 `--dry-run`，检查动作顺序是否正确。
2. 去掉 `--dry-run`，先只保留一个 `click` 和一个 `swipe` 验证坐标。
3. 再打开 `loop` 与 `patrol`。
4. 运行初期加 `--max-runtime-sec 30` 防止长时间失控。

示例：

```bash
python3 /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/adb_bot.py --plan /Users/admins/Documents/QuickJSOn/bluestacks-automation-standalone/patrol_plan.json --max-runtime-sec 30
```

## 8. 常见问题

- `adb not found`
  - 安装 Android Platform Tools，并把 `adb` 加入 PATH。

- `Unable to connect to device`
  - 检查 BlueStacks 是否开启 ADB。
  - 重新执行 `adb connect 127.0.0.1:5555`。
  - 执行 `adb devices` 确认状态是 `device` 不是 `offline`。

- `error: closed`
  - 说明当前 serial 通道不可用（常见于 `127.0.0.1:5555`）。
  - 运行 `adb devices`，改用可用 serial（例如 `emulator-5554`）。
  - 本脚本会自动尝试切换到健康设备，但建议在 `patrol_plan.json` 里直接写可用 serial。

- 点击位置不准
  - BlueStacks 窗口缩放/分辨率变化后，坐标会失效。
  - 重新标定坐标；必要时关闭自动缩放，固定分辨率。
