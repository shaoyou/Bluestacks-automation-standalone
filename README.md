# BlueStacks 自动巡逻脚本（ADB）

本方案通过 `adb shell input ...` 直接控制 BlueStacks，支持：
- 点击（`click`）
- 保存截图（`save_screenshot`）
- 只识别图标（`find_image`）
- 识别图标并点击（`find_image_click`）
- 条件识别分支（`if_image`）
- 识别文字并点击（`find_text_click`）
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
- `image_templates/`: 图标模板目录（用于 `find_image_click`）
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
- `dist/BSManagerApp.dmg`

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
- `variables`: 脚本变量数组，可在动作字段里用 `${变量名}` 或 `$变量名` 引用
- `actions`: 动作数组

### 6.1 脚本变量 variables

每个脚本可以在根级配置自己的变量：

```json
{
  "variables": [
    { "name": "WAIT_SHORT", "value": "0.5", "note": "短等待秒数" },
    { "name": "TARGET_TEXT", "value": "开始", "note": "需要识别的按钮文字" }
  ],
  "actions": [
    { "type": "wait", "seconds": "${WAIT_SHORT}", "remark": "等待${WAIT_SHORT}秒" },
    { "type": "find_text_click", "text": "${TARGET_TEXT}", "remark": "点击${TARGET_TEXT}" }
  ]
}
```

- `name`: 变量名，只建议使用字母、数字、下划线，且不要以数字开头。
- `value`: 变量值。运行面板可直接编辑。
- `note`: 备注。运行面板可直接编辑，不参与执行。
- 当整个字段值就是变量（例如 `"seconds": "${WAIT_SHORT}"`）时，运行时会尝试按 JSON 字面量解析，所以 `"0.5"` 会变成数字 `0.5`，`"true"` 会变成布尔值。
- 当变量嵌在字符串中（例如 `"remark": "等待${WAIT_SHORT}秒"`）时，会按字符串替换。

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

4. `find_image`

```json
{
  "type": "find_image",
  "template": "../image_templates/start_button.png",
  "threshold": 0.92,
  "timeout_sec": 2,
  "interval_sec": 0.6
}
```

- 只识别图标，不点击
- 适合先验证模板和区域是否设置正确

5. `save_screenshot`

```json
{
  "type": "save_screenshot",
  "pair_key": "draw_result",
  "stage": "before",
  "label": "min",
  "remark": "保存抽卡前截图"
}
```

- 默认输出到 `diagnostics/draw_result_pairs/`
- 如果用 `pair_key + before/after`，文件名会自动成对，并在同目录生成 `index.csv` 和 `index.jsonl`
- 适合做抽卡前后对比、结果统计、批量回看

6. `find_text_click`

```json
{
  "type": "find_text_click",
  "text": "START",
  "match": "contains",
  "lang": "eng",
  "timeout_sec": 8,
  "interval_sec": 0.8
}
```

- 运行时会先截图，再用 `tesseract` OCR 查找文字，找到后点击文字区域中心
- 当前默认使用 `tesseract` 的 `eng` 语言包；如本机安装了其他语言包，可自行传 `lang`
- `match` 支持：
  - `contains`
  - `exact`

7. `find_image_click`

```json
{
  "type": "find_image_click",
  "template": "../image_templates/start_button.png",
  "threshold": 0.92,
  "timeout_sec": 8,
  "interval_sec": 0.6,
  "preview_only": true
}
```

- 运行时会先截图，再与模板图标做匹配，找到后点击图标中心
- 若在 `timeout_sec` 内未命中，会输出一条跳过日志并继续执行下一个动作，不会终止整条脚本
- 推荐把图标裁成尽量紧凑的 PNG，小图标背景越干净越准
- `template` 支持绝对路径，也支持相对当前脚本文件的路径
- 如果要匹配多个候选图标，可改用 `templates: ["a.png", "b.png", "c.png"]`；按数组顺序依次判定，命中任意一个即成功
- 使用 `templates` 时，日志会逐个模板输出成功或失败，方便看识别进度
- 可选字段：
  - `threshold`: 匹配阈值，默认 `0.92`
  - `timeout_sec`: 最长等待时间，默认 `8`
  - `interval_sec`: 轮询截图间隔，默认 `0.6`
  - `max_attempts`: 最多重试多少轮完整识别；设为 `1` 时整组模板只扫一轮
  - `index`: 同图标多处出现时，点击第几个命中项
  - `max_candidates`: 每个缩放级别最多保留多少个候选点；如果只关心“是否出现任意一个”，可设为 `1`
  - `offset_x` / `offset_y`: 对命中中心额外偏移
  - `region`: 限定搜索区域，格式如 `{ "x": 100, "y": 200, "width": 300, "height": 200 }`；默认全屏
- 默认会自动尝试一组缩放比例来适配屏幕上图标大小变化；如需手动控制，可额外传：
  - `scale_min`
  - `scale_max`
  - `scale_step`
  - 或 `scales: [0.75, 0.9, 1.0, 1.1]`
- `preview_only: true` 时只会保存高亮预览图，不会执行点击
- 每次运行 `find_image_click` 都会自动把最佳候选保存到 `diagnostics/image_match_debug/`
- 提速建议：尽量缩小 `region`；如果图标大小稳定，优先用 `scales: [1.0]`；把最常出现的模板放在 `templates` 前面；只做存在性判断时可配 `max_candidates: 1`
- SwiftUI 编辑器支持两种模板来源：
  - 直接上传本地图标
  - 从 `image_templates/` 中直接选择已有图标
  - 从当前设备截图中框选图标并保存到 `image_templates/`
- SwiftUI 编辑器支持从当前设备截图中框选搜索区域，并可一键恢复为全屏区域
- 多目标示例：

```json
{
  "type": "find_image_click",
  "templates": [
    "../image_templates/reward_a.png",
    "../image_templates/reward_b.png",
    "../image_templates/reward_c.png"
  ],
  "threshold": 0.9,
  "timeout_sec": 5,
  "remark": "任意奖励图标出现就点击"
}
```

8. `if_image`

```json
{
  "type": "if_image",
  "template": "../image_templates/start_button.png",
  "threshold": 0.88,
  "timeout_sec": 2,
  "interval_sec": 0.6,
  "then_actions": [
    { "type": "click_match", "remark": "点击当前命中的图标" }
  ],
  "else_actions": [
    { "type": "wait", "seconds": 0.5 }
  ]
}
```

- 识别到图标时走 `then_actions`
- 未识别到时走 `else_actions`
- 也支持 `templates`，用于“多个目标图标出现任意一个就进 then_actions”
- 在 `then_actions` 里可以写 `{ "type": "click_match" }`，直接点击当前这次 `if_image` 命中的图标中心

```json
{
  "type": "if_image",
  "templates": [
    "../image_templates/a.png",
    "../image_templates/b.png"
  ],
  "then_actions": [
    { "type": "click_match", "remark": "点击当前命中的图标" }
  ],
  "else_actions": [
    { "type": "wait", "seconds": 0.5, "remark": "没有命中任何图标" }
  ]
}
```

9. `loop`

```json
{ "type": "loop", "count": -1, "actions": [ ... ] }
```

- `count = -1` 表示无限循环

10. `patrol`

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
