# patrol_plan.json 关键字与命令手册

本文档是 `patrol_plan.json` 的完整参考，包含：
- 所有关键字说明
- `type` 动作命令列表
- 可直接复制的操作实例

## 1. 顶层关键字

```json
{
  "device": "127.0.0.1:5555",
  "jitter_px": 3,
  "max_runtime_sec": 0,
  "actions": []
}
```

- `device` (string)
  - 默认 ADB 设备 serial。
  - 可被命令行 `--device` 覆盖。
  - 例：`127.0.0.1:5555`、`emulator-5554`

- `jitter_px` (number, 可选，默认 `0`)
  - 对点击与滑动坐标加入随机偏移（`±jitter_px`）。
  - 用于降低机械固定轨迹。

- `max_runtime_sec` (number, 可选，默认 `0`)
  - 最大运行秒数。
  - `0` 表示不限制。
  - 也可被命令行 `--max-runtime-sec` 覆盖。

- `actions` (array, 必填)
  - 动作队列，按顺序执行。

## 2. type 命令列表

支持的 `type`：
- `click`
- `swipe`
- `trace`
- `wait`
- `sequence`
- `loop`
- `patrol`

### 2.1 click

```json
{ "type": "click", "x": 1000, "y": 650 }
```

- `x` (number, 必填): X 坐标
- `y` (number, 必填): Y 坐标

### 2.2 swipe

```json
{ "type": "swipe", "x1": 300, "y1": 400, "x2": 900, "y2": 400, "duration_ms": 600 }
```

- `x1` `y1` (number, 必填): 起点
- `x2` `y2` (number, 必填): 终点
- `duration_ms` (number, 可选，默认 `300`): 滑动时长（毫秒）

### 2.3 wait

```json
{ "type": "wait", "seconds": 1.0, "jitter_seconds": 0.2 }
```

- `seconds` (number, 可选，默认 `1.0`): 等待秒数
- `jitter_seconds` (number, 可选，默认 `0.0`): 随机扰动秒数（`±`）

### 2.3 trace

```json
{
  "type": "trace",
  "points": [
    { "x": 500, "y": 500, "t_ms": 0 },
    { "x": 520, "y": 530, "t_ms": 30 },
    { "x": 540, "y": 580, "t_ms": 70 }
  ],
  "min_segment_ms": 16,
  "max_segment_ms": 80
}
```

- `points` (array, 必填): 轨迹点列表（至少 2 个）
- `t_ms` (number, 可选): 相对起点时间，回放时用于估算每小段时长
- `min_segment_ms` (number, 可选，默认 `16`): 每段最小时长
- `max_segment_ms` (number, 可选，默认 `80`): 每段最大时长
- 用途：精细回放手写轨迹（如画 `123`）

### 2.4 sequence

```json
{
  "type": "sequence",
  "actions": [
    { "type": "click", "x": 800, "y": 600 },
    { "type": "wait", "seconds": 0.8 }
  ]
}
```

- `actions` (array, 必填): 子动作列表
- 用途：把一组动作打包成逻辑段落

### 2.5 loop

```json
{
  "type": "loop",
  "count": 10,
  "actions": [
    { "type": "click", "x": 500, "y": 500 },
    { "type": "wait", "seconds": 1.2 }
  ]
}
```

- `count` (number, 可选，默认 `1`)
  - `>0`: 循环指定次数
  - `0`: 跳过不执行
  - `-1`: 无限循环
- `actions` (array, 必填): 循环体

### 2.6 patrol

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

- `from` (object, 必填): 起始点 `{x,y}`
- `to` (object, 必填): 终点 `{x,y}`
- `duration_ms` (number, 可选，默认 `500`): 单程滑动时长
- `leg_wait_sec` (number, 可选，默认 `0.4`): 单程后等待
- `rounds` (number, 可选，默认 `1`)
  - `1`: A->B->A 一次往返
  - `>1`: 多次往返
  - `0`: 不执行
  - `-1`: 无限往返

## 3. 操作实例

### 3.1 实例A：进入页面后无限巡逻

```json
{
  "device": "127.0.0.1:5555",
  "jitter_px": 3,
  "max_runtime_sec": 0,
  "actions": [
    { "type": "click", "x": 1000, "y": 650 },
    { "type": "wait", "seconds": 1.2 },
    {
      "type": "loop",
      "count": -1,
      "actions": [
        {
          "type": "patrol",
          "from": { "x": 300, "y": 400 },
          "to": { "x": 900, "y": 400 },
          "duration_ms": 600,
          "leg_wait_sec": 0.5,
          "rounds": 1
        },
        { "type": "wait", "seconds": 0.8, "jitter_seconds": 0.2 }
      ]
    }
  ]
}
```

### 3.2 实例B：固定执行 20 次采集动作

```json
{
  "device": "127.0.0.1:5555",
  "actions": [
    {
      "type": "loop",
      "count": 20,
      "actions": [
        { "type": "click", "x": 1200, "y": 680 },
        { "type": "wait", "seconds": 1.5 },
        { "type": "click", "x": 1080, "y": 620 },
        { "type": "wait", "seconds": 0.8 }
      ]
    }
  ]
}
```

### 3.3 实例C：多段路径巡逻（矩形）

```json
{
  "device": "127.0.0.1:5555",
  "actions": [
    {
      "type": "loop",
      "count": -1,
      "actions": [
        { "type": "swipe", "x1": 300, "y1": 300, "x2": 900, "y2": 300, "duration_ms": 500 },
        { "type": "wait", "seconds": 0.3 },
        { "type": "swipe", "x1": 900, "y1": 300, "x2": 900, "y2": 700, "duration_ms": 500 },
        { "type": "wait", "seconds": 0.3 },
        { "type": "swipe", "x1": 900, "y1": 700, "x2": 300, "y2": 700, "duration_ms": 500 },
        { "type": "wait", "seconds": 0.3 },
        { "type": "swipe", "x1": 300, "y1": 700, "x2": 300, "y2": 300, "duration_ms": 500 },
        { "type": "wait", "seconds": 0.5 }
      ]
    }
  ]
}
```

## 4. 命令行参数说明

脚本命令：

```bash
python3 adb_bot.py --plan patrol_plan.json [--device SERIAL] [--adb ADB_PATH] [--dry-run] [--max-runtime-sec N]
```

- `--plan` (必填): 配置文件路径
- `--device` (可选): 覆盖 JSON 的 `device`
- `--adb` (可选): 指定 adb 路径（如 BlueStacks 自带 adb）
- `--dry-run` (可选): 仅打印动作，不执行
- `--max-runtime-sec` (可选): 覆盖 JSON 的最大运行时间

## 5. 快速排错

- 先验证通道：
```bash
adb devices
adb -s 127.0.0.1:5555 shell getprop ro.build.version.release
adb -s 127.0.0.1:5555 shell input tap 500 500
```

- 先干跑：
```bash
python3 adb_bot.py --plan patrol_plan.json --dry-run
```

- 限时实跑：
```bash
python3 adb_bot.py --plan patrol_plan.json --max-runtime-sec 30
```

## 6. 录制命令

录制 BlueStacks 触摸并生成脚本：

```bash
python3 record_touch.py --output plans/recorded.json --device 127.0.0.1:5555 --loop-count -1
```

- 录制期间在 BlueStacks 中执行你的触摸操作。
- 结束录制按 `Ctrl+C`。
- 生成脚本后可直接执行：

```bash
python3 adb_bot.py --plan plans/recorded.json
```
