# 华为 CLS-AL00（Android 12）Phase 0 现场记录

- 设备：HUAWEI CLS-AL00，Android 12（API 31），1216×2688 @ 520dpi，arm64-v8a
- 日期：2026-11-27
- 测试 App：`com.bsauto.phase0`（debug，adb 侧载）
- 结论：**这台机器上 MediaProjection 未能建立稳定采集**；需要先排除干扰项后复测；如仍被系统阻断，走 Shizuku + `screencap` 兜底。

---

## 1. MediaProjection 行为

### 1.1 默认状态

```text
adb shell appops get com.bsauto.phase0 PROJECT_MEDIA
# No operations.
# Default mode: ignore
```

- 点“启动截屏”后，系统 `MediaProjectionPermissionActivity` 正常拉起；
- 约 0.25 秒后该 Activity **自行结束**，App 收到 `RESULT_CANCELED`；
- 关键日志：

```text
HwActivityStarterEx: isNeedInterceptStart true, interceptionState:BACKGROUND, callerPkg:com.bsauto.phase0
HwASInterceptor: both page open. caller:com.bsauto.phase0, target:com.android.systemui
HwASInterceptor: Adb enabled, allow to start activity
HWMW_HwMagicStackManager: finish application, finishActivity:...MediaProjectionPermissionActivity reason=app-request
```

### 1.2 将 PROJECT_MEDIA 设为 allow 后

```text
adb shell appops set com.bsauto.phase0 PROJECT_MEDIA allow
```

- 授权弹窗被跳过，App 直接拿到 token；
- `VirtualDisplay` 创建成功，前台服务启动；
- 约 0.5 秒后系统撤销投影：

```text
MediaProjectionManagerService: unregisterCallback, uid: 10280, packageName: com.bsauto.phase0
MediaProjection 已停止（系统/用户撤销）
dumpsys media_projection -> null
```

- 说明：不是 App 代码问题（同一 APK 在 Android 13 模拟器上稳定采集 200+ 秒），属于该 ROM/机型的策略限制或额外权限要求。

### 1.3 已复原的测试改动

- `PROJECT_MEDIA`、`SYSTEM_ALERT_WINDOW` 已恢复 `default`；
- `POST_NOTIFICATION` 已恢复 `ignore`；
- 测试 App 已 force-stop，未卸载（便于后续复测）。
- 未在真机安装 Shizuku，未改动其他应用。

---

## 2. 关键干扰：按键精灵在运行

测试期间发现：

```text
u0_a258  14549  ... com.zdanjian.zdanjian
u0_a258  29439  ... com.zdanjian.zdanjian:ScriptService
u0_a258  29895  ... com.zdanjian.zdanjian:ocr
```

现象：

- 页面按钮在没有 adb 点击的情况下被连续触发（“申请 Shizuku 权限”“getevent 采样”“停止截屏”等）；
- “停止截屏”Toast 以约 2.6 秒的间隔持续排队，最多时系统提示 `Package has already queued 5 toasts`；
- 截屏授权页刚出现就被关闭，和“另一套自动化脚本在点屏幕”的表现一致。

**复测前必须先停止按键精灵脚本/服务，或直接卸载其脚本服务。**

---

## 3. Shell 能力（adb 身份已验证）

```text
adb shell id
# uid=2000(shell) ... groups=... 1004(input) ...

adb shell getevent -lp
# add device 1: /dev/input/event1  name: "input_mt_wrapper"
#   ABS_MT_POSITION_X / ABS_MT_POSITION_Y ...
# add device 2: /dev/input/event4  name: "hw_fingerprint_mouse"
# add device 3: /dev/input/event3  name: "fingerprint"

adb shell which su
# (无输出，su 不存在)
```

结论：

- shell 身份可以读 `getevent`，说明 **Shizuku 激活后录制路径大概率可用**；
- 无 root，无法走 root `sendevent`；
- `adb shell screencap -p` 可正常出图（本次所有手机截图都是这样取的），因此 **Shizuku + `screencap` 可作为 MediaProjection 的兜底截图通道**，代价是低帧率。

---

## 4. 复测清单（建议顺序）

1. 停止/卸载按键精灵脚本服务，确认没有其他自动化工具在注入点击。
2. 安装并激活 Shizuku（USB ADB 或无线调试），在本 App 内授权。
3. 跑“探测 Root / Shizuku”“getevent 8 秒采样”“Shell screencap 5 帧”，确认录制与兜底截图可用。
4. 再点“启动截屏”：
   - 若仍被系统阻断，记录 `dumpsys media_projection` 与 `logcat | grep MediaProjection`，并按 OEM 策略处理；
   - 若可用，继续跑延迟、FLAG_SECURE、目标游戏 30 秒检测。
5. 把**目标游戏**切前台跑阶段门 2/3/4：FLAG_SECURE、无障碍注入、游戏内触摸响应。

> 如果目标游戏本身是 `FLAG_SECURE`，上述所有截图路径（MediaProjection / shell screencap / root screencap）都不会有画面，端上方案应判定为不可行。
