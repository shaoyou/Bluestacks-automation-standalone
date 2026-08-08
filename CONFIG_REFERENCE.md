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
  "variables": [
    { "name": "WAIT_SHORT", "value": "0.5", "note": "短等待秒数" }
  ],
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

- `variables` (array, 可选)
  - 当前脚本自己的变量列表。
  - 运行面板会显示 `name` / `value` / `note`，其中 `value` 和 `note` 可直接编辑并保存回脚本。
  - 动作字段里可用 `${变量名}` 或 `$变量名` 引用。

- `actions` (array, 必填)
  - 动作队列，按顺序执行。

## 2. 脚本变量

变量写在脚本根级：

```json
{
  "variables": [
    { "name": "WAIT_SHORT", "value": "0.5", "note": "短等待秒数" },
    { "name": "TARGET_TEXT", "value": "开始", "note": "按钮文字" }
  ],
  "actions": [
    { "type": "wait", "seconds": "${WAIT_SHORT}", "remark": "等待${WAIT_SHORT}秒" },
    { "type": "find_text_click", "text": "${TARGET_TEXT}", "remark": "点击${TARGET_TEXT}" }
  ]
}
```

- `name` (string, 必填): 变量名。建议使用字母、数字、下划线，且不要以数字开头。
- `value` (string, 必填): 变量值。
- `note` (string, 可选): 备注，仅用于运行面板展示和编辑，不参与执行。
- 当整个字段值就是变量时，例如 `"seconds": "${WAIT_SHORT}"`，运行时会尝试按 JSON 字面量解析，`"0.5"` 会变成数字 `0.5`，`"true"` 会变成布尔值。
- 当变量嵌在普通字符串里时，例如 `"remark": "等待${WAIT_SHORT}秒"`，运行时按字符串替换。

也兼容简写对象格式：

```json
{
  "variables": {
    "WAIT_SHORT": "0.5",
    "TARGET_TEXT": "开始"
  }
}
```

但对象格式没有 `note`，如果需要在运行面板维护备注，推荐使用数组格式。

## 3. type 命令列表

支持的 `type`：
- `click`
- `click_match`
- `save_screenshot`
- `find_image`
- `find_image_click`
- `if_image`
- `find_text_click`
- `swipe`
- `trace`
- `wait`
- `sequence`
- `loop`
- `patrol`

### 3.1 click

```json
{ "type": "click", "x": 1000, "y": 650 }
```

- `x` (number, 必填): X 坐标
- `y` (number, 必填): Y 坐标

### 3.2 click_match

```json
{ "type": "click_match" }
```

- 用于点击最近一次 `if_image` 命中的中心点
- 典型用法：写在 `if_image.then_actions` 里
- 支持可选偏移：

```json
{ "type": "click_match", "offset_x": 12, "offset_y": -8, "remark": "点击命中图标右上角" }
```

- 如果当前运行上下文里没有可用的 `if_image` 命中结果，会报错提醒脚本写法有问题

### 3.3 save_screenshot

```json
{
  "type": "save_screenshot",
  "pair_key": "draw_result",
  "stage": "before",
  "label": "min",
  "remark": "保存抽卡前截图"
}
```

- 默认保存到 `diagnostics/draw_result_pairs/`
- `pair_key` (string, 可选): 配对分组键；同一组的 `before/after` 会共用一套编号
- `stage` (string, 可选): `before` / `after` / `single`
- `label` (string, 可选): 追加到文件名末尾，便于区分 `min` / `max`
- `dir` (string, 可选): 自定义输出目录
- `storage_format` (string, 可选): `png`（默认）或 `webp`；`webp` 使用无损压缩，适合物品截图长期保存，不改变识别像素
- 成对截图命名示例：
  - `20260425-161652_draw_result_0001_before_min.png`
  - `20260425-161652_draw_result_0001_after_min.png`
- 每对截图完成后，会在同目录追加：
  - `index.csv`
  - `index.jsonl`

### 3.4 find_image

```json
{
  "type": "find_image",
  "template": "../image_templates/start_button.png",
  "threshold": 0.92,
  "timeout_sec": 2,
  "interval_sec": 0.6
}
```

- 只做图标识别，不执行点击
- 日志会输出是否识别到图标，以及命中框和相似度
- 可用于先验证模板是否稳定
- 参数与 `find_image_click` 基本一致

### 3.5 find_image_click

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

- `template` (string, 必填): 模板图标路径，支持绝对路径，也支持相对当前脚本文件的路径
- `templates` (array, 可选): 多个候选模板路径，按数组顺序依次判定，命中任意一个即成功；若同时命中多个，优先取数组中更靠前的模板
- 使用 `templates` 时，运行日志会按模板逐项输出 `matched` / `not matched`，方便观察识别进度
- `threshold` (number, 可选，默认 `0.92`): 匹配阈值
- `timeout_sec` (number, 可选，默认 `8`): 最长等待时间
- `interval_sec` (number, 可选，默认 `0.6`): 轮询截图间隔
- `max_attempts` (number, 可选，默认 `0`): 最多重试多少轮完整识别；`0` 表示只受 `timeout_sec` 限制，`1` 表示整组模板只扫一轮
- `index` (number, 可选，默认 `0`): 同一图标多次出现时，点击第几个命中项
- `max_candidates` (number, 可选，默认 `8`): 每个缩放级别最多保留多少个候选点；如果只关心“是否出现任意一个”，可设为 `1` 以减少计算量
- `offset_x` `offset_y` (number, 可选，默认 `0`): 对命中中心点做额外偏移
- `region` (object, 可选，默认全屏): 限定搜索区域，格式 `{ "x": 100, "y": 200, "width": 300, "height": 200 }`
- `scale_min` `scale_max` `scale_step` (number, 可选): 控制自动多尺度匹配范围，默认约 `0.75 ~ 1.25`
- `scales` (array[number], 可选): 直接指定要尝试的缩放列表，例如 `[0.75, 0.9, 1.0, 1.1]`
- `preview_only` (bool, 可选，默认 `false`): 命中后只保存高亮预览，不点击
- `save_debug` (bool, 可选，默认 `true`): 是否自动保存最佳候选调试图
- `debug_dir` (string, 可选): 自定义调试图输出目录
- 说明：推荐使用裁剪紧凑、背景干净的 PNG 图标；透明区域会自动忽略；若在 `timeout_sec` 内未命中，会记录日志并跳过当前动作，继续执行下一个动作
- 性能建议：尽量缩小 `region`；如果图标大小稳定，优先用 `scales: [1.0]` 或很窄的缩放范围；`templates` 中把最常出现的图标放前面；只做存在性判断时可配 `max_candidates: 1`
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

### 3.6 if_image

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

- 先做图标识别
- 识别到目标图标时执行 `then_actions`
- 未识别到时执行 `else_actions`
- `then_actions` 和 `else_actions` 都必须是数组，允许为空数组
- 图像匹配相关字段与 `find_image_click` 一致，也支持 `templates`
- 在 `then_actions` 中可以使用 `click_match`，直接点击当前这次 `if_image` 命中的目标点

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

### 3.7 find_text_click

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

- `text` (string, 必填): 目标文字
- `match` (string, 可选，默认 `contains`): `contains` 或 `exact`
- `lang` (string, 可选，默认 `eng`): tesseract 语言包
- `timeout_sec` (number, 可选，默认 `8`): 最长等待时间
- `interval_sec` (number, 可选，默认 `0.8`): 轮询截图间隔
- `index` (number, 可选，默认 `0`): 同一文字出现多次时，点击第几个命中项
- `offset_x` `offset_y` (number, 可选，默认 `0`): 对命中的中心点做额外偏移
- 说明：该动作会先截图，再做 OCR，点击坐标使用当前设备屏幕坐标，不依赖录制脚本的源分辨率

### 3.8 swipe

```json
{ "type": "swipe", "x1": 300, "y1": 400, "x2": 900, "y2": 400, "duration_ms": 600 }
```

- `x1` `y1` (number, 必填): 起点
- `x2` `y2` (number, 必填): 终点
- `duration_ms` (number, 可选，默认 `300`): 滑动时长（毫秒）

### 3.9 wait

```json
{ "type": "wait", "seconds": 1.0, "jitter_seconds": 0.2 }
```

- `seconds` (number, 可选，默认 `1.0`): 等待秒数
- `jitter_seconds` (number, 可选，默认 `0.0`): 随机扰动秒数（`±`）

### 3.10 trace

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

### 3.11 sequence

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

### 3.12 loop

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

### 3.13 patrol

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

## 4. 操作实例

### 4.1 实例A：进入页面后无限巡逻

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

### 4.2 实例B：固定执行 20 次采集动作

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

### 4.3 实例C：多段路径巡逻（矩形）

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

## 5. 命令行参数说明

脚本命令：

```bash
python3 adb_bot.py --plan patrol_plan.json [--device SERIAL] [--adb ADB_PATH] [--dry-run] [--max-runtime-sec N]
```

- `--plan` (必填): 配置文件路径
- `--device` (可选): 覆盖 JSON 的 `device`
- `--adb` (可选): 指定 adb 路径（如 BlueStacks 自带 adb）
- `--dry-run` (可选): 仅打印动作，不执行
- `--max-runtime-sec` (可选): 覆盖 JSON 的最大运行时间

## 6. 快速排错

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

## 7. 录制命令

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
