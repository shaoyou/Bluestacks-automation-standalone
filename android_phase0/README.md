# Phase 0 · Android 端上自动化可行性探针

这是 Phase 0 的最小验证工程：一个独立 Android App，用来回答“端上能否稳定完成 **截屏 → 图像识别 → 触摸注入 → 触摸录制**”。

> 结论先行（2026-11-27，模拟器 Samsung SM-G998B / Android 13）：
> **技术上可行**。MediaProjection 截屏、FLAG_SECURE 屏蔽、无障碍手势注入、Shizuku + getevent 录制四条链路均实测通过。
> 仍有两个必须用**目标游戏**验证的门：目标窗口是否 `FLAG_SECURE`、目标游戏是否接受/检测无障碍注入。

---

## 1. 实测结果

| 阶段门 | 验证内容 | 结果 | 关键数据 |
|---|---|---|---|
| 1 截屏 | MediaProjection + ImageReader | **PASS** | 1080×1920，延迟 p50 **53ms** / p95 **67ms** / max 408ms，单帧处理 **13ms** |
| 2 FLAG_SECURE | 自身页面设置 `FLAG_SECURE`，捕获应全黑 | **PASS** | 47 帧平均黑屏比 **1.000**，平均亮度 0.12 |
| 3 目标应用 | 第三方/桌面 30 秒截图 | **PASS** | 桌面 24 帧、黑屏比 0、唯一帧 19（静态桌面帧率低，见 §6） |
| 4 手势注入 | 无障碍 `dispatchGesture` | **PASS** | 点击/长按 800ms/滑动 450ms/双指缩放均收到 MotionEvent |
| 5 触摸录制 | Shizuku（shell）执行 `getevent` | **PASS** | 8 秒 72 行事件，其中触摸 36 行；检测到 3 个 input 设备、2 个 MT 设备 |
| 5b 截图兜底 | Shizuku 执行 `screencap -p` | **PASS** | 5/5 帧 1080×1920；MediaProjection 同时运行时 788ms/帧，仅跑兜底时约 143ms/帧 |

完整报告与截图：

- [`reports/phase0_emulator_sm-g998b.json`](reports/phase0_emulator_sm-g998b.json)
- [`reports/screenshot_emulator_final.png`](reports/screenshot_emulator_final.png)
- [`reports/notes_huawei_cls-al00.md`](reports/notes_huawei_cls-al00.md)

---

## 2. 工程结构

```
android_phase0/
├── app/src/main/java/com/bsauto/phase0/
│   ├── MainActivity.kt            # 总控台、阶段门状态、报告导出、自动化指令入口
│   ├── CaptureService.kt          # MediaProjection 前台服务：FPS/延迟/黑屏/目标检测
│   ├── LatencyActivity.kt         # 全屏时间戳图案页（服务端解码测端到端年龄）
│   ├── SecurePreviewActivity.kt   # FLAG_SECURE 阳性对照页（动画明亮内容）
│   ├── GestureProbeService.kt     # 无障碍服务：手势注入 + TYPE_ACCESSIBILITY_OVERLAY 悬浮面板
│   ├── GestureTestActivity.kt     # 手势自检画布（记录 DOWN/MOVE/UP/多指）
│   ├── ShellProbe.kt              # Root / Shizuku / getevent / shell screencap 探测
│   ├── ReportStore.kt / LogStore.kt / DeviceProbe.kt
├── reports/                       # 实测报告与截图
└── scripts/collect_report.sh      # 从设备拉取最新报告
```

依赖刻意保持最小：无 Compose、无 AndroidX UI，只用平台 View + 平台 API；唯一第三方依赖是 Shizuku API/Provider。

---

## 3. 构建与安装

要求：JDK 17、Android SDK Platform 35、Android Build-Tools 35。

```bash
cd android_phase0
./gradlew :app:assembleDebug
adb -s <serial> install -r app/build/outputs/apk/debug/app-debug.apk
```

Gradle Wrapper 为 8.14，AGP 8.7.2，Kotlin 2.0.21。`local.properties` 里的 `sdk.dir` 指向本机 SDK，该文件不入库。

---

## 4. 使用说明

### 4.1 截屏探针

1. 点“启动截屏”，在系统弹窗点“Start now / 开始录制”。
2. 顶部状态出现 `运行 … fps` 即成功。
3. 点“延迟测试页”，保持该页前台 10 秒，按返回。主界面状态会显示 `延迟 p50/p95/max`。
4. 点“FLAG_SECURE 对照”，页面会自动展示 6 秒并返回；日志/阶段门 2 给出 PASS/FAIL。
5. 点“目标应用检测”，App 自动切后台 30 秒。请提前把目标游戏切到前台并保持亮屏。结束后回来看结果。

### 4.2 手势注入

1. 系统设置 → 无障碍 → 启用“Phase0 手势探针”。
2. “打开手势测试页”，依次点注入按钮；画布计数增加且出现轨迹即成功。
3. 需要往目标游戏注入时，点“显示悬浮注入面板”，切到游戏，点面板上的“倒计时 3s → 点击屏幕中心”等按钮。

### 4.3 触摸录制（Shizuku）

1. 安装 Shizuku（[shizuku.rikka.app](https://shizuku.rikka.app/)）。
2. 按 Shizuku 首页指引启动：电脑 ADB 方式可点 “View command” 复制命令执行；Android 11+ 也可用无线调试。
3. 回到本 App → “申请 Shizuku 权限” → 允许。
4. 点“探测 Root / Shizuku”：应显示 `可用：shizuku`、`getevent PASS`。
5. 点“getevent 8 秒采样”，在这 8 秒内连续点击/滑动屏幕；结果为 `PASS` 即录制链路可用。

### 4.4 截图兜底

点“Shell screencap 5 帧”：以 Shizuku/root 身份直接执行 `screencap -p`，用于绕过被封禁的 MediaProjection。实测约 1~7fps，适合 0.5s 级轮询的 UI 自动化，不适合高帧率场景。

### 4.5 报告导出

- App 内点“生成并分享报告”，或
- 执行 `scripts/collect_report.sh <serial>`，或
- `adb pull /sdcard/Android/data/com.bsauto.phase0/files/phase0_report.json`

---

## 5. 自动化指令（便于回归）

`MainActivity` 是 `singleTop`，可以在任意时刻用 adb 下发：

```bash
S=<serial>
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action request_capture
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action stop_capture
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action open_latency
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action open_secure
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action target_test
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action gesture_tap
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action gesture_long
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action gesture_swipe
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action gesture_pinch
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action record_probe
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action sample_getevent
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action shell_screencap
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action overlay
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action overlay_hide
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action request_shizuku
adb -s $S shell am start -n com.bsauto.phase0/.MainActivity -e probe_action export_report
```

模拟器/测试机专用快捷方式（**正式产品不要使用**）：

```bash
# 绕过 MediaProjection 授权弹窗，仅用于自动化回归
adb shell appops set com.bsauto.phase0 PROJECT_MEDIA allow
# 直接用 settings 启用无障碍，省去手工点击
adb shell settings put secure enabled_accessibility_services com.bsauto.phase0/com.bsauto.phase0.GestureProbeService
adb shell settings put secure accessibility_enabled 1
# 复原
adb shell appops set com.bsauto.phase0 PROJECT_MEDIA default
adb shell settings put secure enabled_accessibility_services null
```

> 注意：`PROJECT_MEDIA allow` 会让应用无需用户确认即可截屏，测试完必须复原或卸载。

---

## 6. 判定标准与解读

### 截屏（MediaProjection）

- 正常：连续收到帧，黑屏比低，`avg_analysis_ms` 稳定（本次 13ms）。
- `FLAG_SECURE`：持续纯黑且唯一帧 ≤3。注意深色游戏场景可能误报，请换明亮界面重测。
- 帧率：Android 的虚拟显示通常按“画面变化”出帧；**静态桌面 0.6~1.8fps 是正常现象**，游戏动画场景才能体现真实上限。

### 延迟

- 图案编码 `elapsedRealtime() % 4096` 的 12 位时间戳，服务端解码后计算“绘制 → 合成 → 捕获 → 处理”的年龄。
- 本次 p50 53ms、p95 67ms、max 408ms；处理耗时 p50 13ms。
- 该数值包含合成器与捕获管线，**不是触摸到画面的触控延迟**；Phase 1 需要另测注入到生效的延迟。

### 手势注入

本次注入事件的取证：

- `deviceId = -1`
- `source = 0x1002`（`SOURCE_TOUCHSCREEN`）
- `flags = 0x800`

这些字段可能被游戏反作弊用来识别“非物理触摸”。如果目标游戏检测注入，需要评估 root/uinput 方案或接受风险。

### 录制

- 无 root、无 Shizuku：应用自身读不到 `/dev/input`，端上“真实触摸录制”不成立。
- Shizuku（shell）：`getevent` 可读，本次 8 秒抓到 36 行触摸事件；坐标需要按设备 `ABS_MT_POSITION_X/Y` 的 max 值归一化（本机 32767）。
- 回放建议优先 `dispatchGesture`；需要精确按压时长/轨迹时再考虑 shell `input` 或 root `sendevent`。

---

## 7. 关键限制（Phase 1 前必须知道）

1. **FLAG_SECURE 无解**：MediaProjection、无障碍截图、shell `screencap`、root `screencap` 都会得到黑屏/无帧。目标游戏若开启，端上截图路线直接不可行。
2. **OEM 可能封 MediaProjection**：本机华为 CLS-AL00（Android 12）实测系统授权页 0.25 秒自动关闭；把 `PROJECT_MEDIA` app-op 设为 allow 后，授权通过但投影在 0.5 秒内被系统撤销。详见 `reports/notes_huawei_cls-al00.md`。
3. **其他自动化 App 会污染测试**：该华为机上的按键精灵 `com.zdanjian.zdanjian:ScriptService` 在运行时会持续注入点击，导致授权框被关、按钮被误触。测前必须停止其他自动化工具。
4. **Android 14+**：MediaProjection 每次会话都要用户确认，且前台服务必须声明 `mediaProjection` 类型；无法跨重启复用授权。
5. **后台保活**：需要前台服务 + 通知 + 屏幕常亮；部分 ROM 会杀无障碍服务。
6. **上架政策**：无障碍 + 自动化类应用在 Google Play 审核风险高，行业普遍走官网 APK 侧载。
7. **Shizuku 依赖用户手动激活**：重启后通常需要重新激活；产品化需做好引导与降级。

---

## 8. 已知实现细节 / 踩坑

- Shizuku 13.x 的 `Shizuku.newProcess` 已变为 private，本工程通过 `IShizukuService.Stub.asInterface(Shizuku.getBinder()).newProcess(...)` + `IRemoteProcess` 包装成标准 `Process`。
- Android 11+ 包可见性：必须在 manifest 声明 `<queries><package android:name="moe.shizuku.privileged.api"/></queries>`，否则 `getPackageInfo` 查不到 Shizuku。
- `LatencyActivity` 不要在 `setContentView` 之前访问 `window.insetsController`，否则 DecorView 尚未挂载会 NPE。
- 目标检测与 FLAG_SECURE 对照都用**墙钟定时器**结束，不能只依赖帧回调；安全窗口可能完全不产帧。
- MediaProjection 授权弹窗点“START NOW”时，抬手事件可能穿透到下层按钮（模拟器上会误触“目标应用检测”）。自动化回归建议使用 `PROJECT_MEDIA allow` 或调整弹窗背后控件位置。

---

## 9. Phase 1 建议

1. 用**目标游戏**重跑阶段门 2/3/4：确认是否 `FLAG_SECURE`、是否接受无障碍注入。
2. 若 MediaProjection 被 OEM 封禁但 shell `screencap` 可用，Phase 1 实现“双通道截图”：MediaProjection 优先，失败自动降级到 Shizuku `screencap`（目标 2~5fps）。
3. 把现有 `plans/*.json` DSL 移植为端上执行器：先支持 `click/swipe/long_press/trace/wait/loop/sequence/if_image/find_image_click/save_screenshot/variables`。
4. 图像匹配用 OpenCV Android `matchTemplate(TM_CCOEFF_NORMED + mask)` 或多尺度实现，保留现有阈值/`region`/`scales` 语义。
5. 录制：Shizuku `getevent` 解析直接复用 `record_touch.py` 的手势分类逻辑；回放用 `dispatchGesture`，必要时补 shell `input motionevent`。
6. 最后再接 OCR（ML Kit）、计划组、定时、悬浮 HUD。
