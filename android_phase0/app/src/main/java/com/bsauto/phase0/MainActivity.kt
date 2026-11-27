package com.bsauto.phase0

import android.Manifest
import android.app.Activity
import android.app.AlertDialog
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.Settings
import android.text.method.ScrollingMovementMethod
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import rikka.shizuku.Shizuku
import java.io.File

class MainActivity : Activity() {

    companion object {
        private const val REQ_CAPTURE = 1001
        private const val REQ_NOTIFICATIONS = 1002
        private const val SHIZUKU_REQUEST_CODE = 2001
    }

    private lateinit var deviceView: TextView
    private lateinit var gateView: TextView
    private lateinit var captureStatusView: TextView
    private lateinit var gestureStatusView: TextView
    private lateinit var recordStatusView: TextView
    private lateinit var logView: TextView
    private lateinit var logScroll: ScrollView

    private val uiHandler = Handler(Looper.getMainLooper())
    private var lastLogVersion = -1L
    private var lastRecordSummary = "尚未探测"
    private var shizukuListenerRegistered = false

    private val shizukuPermissionListener = object : Shizuku.OnRequestPermissionResultListener {
        override fun onRequestPermissionResult(requestCode: Int, grantResult: Int) {
            if (requestCode != SHIZUKU_REQUEST_CODE) {
                return
            }
            val granted = grantResult == PackageManager.PERMISSION_GRANTED
            LogStore.log("shizuku", "权限请求结果：${if (granted) "已授予" else "被拒绝"}")
            ReportStore.put("record", "shizuku_permission", granted)
            runOnUiThread {
                Toast.makeText(
                    this@MainActivity,
                    "Shizuku 权限${if (granted) "已授予" else "被拒绝"}",
                    Toast.LENGTH_SHORT
                ).show()
                if (granted) {
                    probeRecordCapability()
                }
            }
        }
    }

    private val refreshRunnable = object : Runnable {
        override fun run() {
            if (LogStore.version != lastLogVersion) {
                lastLogVersion = LogStore.version
                logView.text = LogStore.snapshot()
                logScroll.post { logScroll.fullScroll(View.FOCUS_DOWN) }
            }
            captureStatusView.text = "截屏探针：${CaptureService.statusText()}"
            gestureStatusView.text = "无障碍：${accessibilityStatus()}"
            updateGates()
            uiHandler.postDelayed(this, 500L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        DeviceProbe.record(this)
        buildUi()
        registerShizukuListener()
        requestNotificationPermission()
        LogStore.log("app", "Phase0 探针启动")
        LogStore.log("app", DeviceProbe.summary(this))
        ReportStore.note("Phase 0 目标：验证端上截屏、FLAG_SECURE、手势注入、触摸录制四条链路。")
        logView.text = LogStore.snapshot()
        handleProbeIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handleProbeIntent(intent)
    }

    /**
     * 自动化入口，便于 Phase 0 用 adb 脚本复现，无需人工滚动点击：
     *   adb shell am start -n com.bsauto.phase0/.MainActivity -e probe_action record_probe
     */
    private fun handleProbeIntent(intent: Intent?) {
        val action = intent?.getStringExtra("probe_action") ?: return
        LogStore.log("app", "收到自动化指令：$action")
        uiHandler.postDelayed({
            when (action) {
                "request_capture" -> requestCapture()
                "stop_capture" -> stopCapture()
                "open_latency" -> openLatencyPage()
                "open_secure" -> openSecureControl()
                "target_test" -> startTargetAppTest()
                "gesture_test" -> startActivity(Intent(this, GestureTestActivity::class.java))
                "gesture_tap", "gesture_long", "gesture_swipe", "gesture_pinch" -> {
                    startActivity(
                        Intent(this, GestureTestActivity::class.java)
                            .putExtra("auto_action", action.removePrefix("gesture_"))
                    )
                }
                "record_probe" -> probeRecordCapability()
                "sample_getevent" -> sampleGetEvent()
                "shell_screencap" -> probeShellScreencap()
                "request_shizuku" -> requestShizukuPermission()
                "overlay" -> {
                    if (GestureProbeService.isConnected()) {
                        GestureProbeService.showOverlayPanel()
                    } else {
                        toast("无障碍服务未连接")
                    }
                }
                "overlay_hide" -> GestureProbeService.hideOverlayPanel()
                "export_report" -> exportReport(share = false)
                "accessibility_settings" -> startActivity(
                    Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)
                )
                else -> LogStore.log("app", "未知自动化指令：$action")
            }
        }, 300L)
    }

    override fun onResume() {
        super.onResume()
        DeviceProbe.record(this)
        deviceView.text = DeviceProbe.summary(this)
        if (CaptureService.isRunning && !CaptureService.isTargetTestRunning()) {
            CaptureService.setMode(CaptureService.MODE_GENERAL)
        }
        uiHandler.removeCallbacks(refreshRunnable)
        uiHandler.post(refreshRunnable)
    }

    override fun onPause() {
        super.onPause()
        uiHandler.removeCallbacks(refreshRunnable)
    }

    override fun onDestroy() {
        uiHandler.removeCallbacksAndMessages(null)
        if (shizukuListenerRegistered) {
            try {
                Shizuku.removeRequestPermissionResultListener(shizukuPermissionListener)
            } catch (_: Throwable) {
            }
        }
        super.onDestroy()
    }

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_CAPTURE) {
            return
        }
        if (resultCode == RESULT_OK && data != null) {
            val intent = Intent(this, CaptureService::class.java).apply {
                action = CaptureService.ACTION_START
                putExtra(CaptureService.EXTRA_RESULT_CODE, resultCode)
                putExtra(CaptureService.EXTRA_RESULT_DATA, data)
            }
            startForegroundService(intent)
            LogStore.log("capture", "已获得 MediaProjection 授权，启动截屏服务")
            ReportStore.put("capture", "user_consent", true)
            toast("截屏服务启动中")
        } else {
            LogStore.log("capture", "用户取消截屏授权")
            ReportStore.put("capture", "user_consent", false)
            toast("未授权截屏")
        }
    }

    private fun buildUi() {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(246, 247, 249))
        }

        val controls = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(18), dp(18), dp(18), dp(8))
        }
        val scroll = ScrollView(this).apply {
            addView(controls)
        }
        root.addView(scroll, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            1f
        ))

        controls.addView(TextView(this).apply {
            text = "Phase 0 · Android 端上自动化探针"
            textSize = 21f
            setTextColor(Color.rgb(18, 18, 18))
            setTypeface(typeface, Typeface.BOLD)
        })
        deviceView = TextView(this).apply {
            text = DeviceProbe.summary(this@MainActivity)
            textSize = 12f
            setTextColor(Color.rgb(80, 80, 80))
            setPadding(0, dp(6), 0, dp(10))
        }
        controls.addView(deviceView)

        gateView = TextView(this).apply {
            text = "阶段门状态待采集"
            textSize = 12f
            setTextColor(Color.rgb(20, 40, 70))
            setBackgroundColor(Color.rgb(226, 238, 250))
            setPadding(dp(12), dp(10), dp(12), dp(10))
            setTypeface(Typeface.MONOSPACE)
        }
        controls.addView(gateView)

        controls.addView(body(
            "建议顺序：1) 启动截屏 → 2) 打开延迟页 → 3) 做 FLAG_SECURE 对照 → " +
                "4) 目标应用检测（自动切后台 30 秒）→ 5) 手势测试页 → 6) 录制能力探测。"
        ))

        // 1. 截屏
        controls.addView(sectionTitle("1. 截屏探针（MediaProjection）"))
        captureStatusView = TextView(this).apply {
            text = "截屏探针：未运行"
            textSize = 12f
            setTextColor(Color.rgb(20, 70, 120))
            setPadding(0, 0, 0, dp(6))
        }
        controls.addView(captureStatusView)
        controls.addView(buttonRow(
            makeButton("启动截屏") { requestCapture() },
            makeButton("停止截屏") { stopCapture() }
        ))
        controls.addView(buttonRow(
            makeButton("延迟测试页") { openLatencyPage() },
            makeButton("FLAG_SECURE 对照") { openSecureControl() }
        ))
        controls.addView(makeButton("目标应用检测（30 秒，自动切后台）") { startTargetAppTest() })
        controls.addView(body(
            "目标检测前请把游戏切到前台并保持亮屏。结果中“持续纯黑且唯一帧≤3”通常意味着游戏设置了 " +
                "FLAG_SECURE，端上截图路线不可用；深色场景可能误报，请换明亮界面重测。"
        ))

        // 2. 手势
        controls.addView(sectionTitle("2. 手势注入（无障碍服务）"))
        gestureStatusView = TextView(this).apply {
            text = "无障碍：检测中"
            textSize = 12f
            setTextColor(Color.rgb(20, 70, 120))
            setPadding(0, 0, 0, dp(6))
        }
        controls.addView(gestureStatusView)
        controls.addView(buttonRow(
            makeButton("打开手势测试页") {
                startActivity(Intent(this@MainActivity, GestureTestActivity::class.java))
            },
            makeButton("打开无障碍设置") {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        ))
        controls.addView(makeButton("显示 / 隐藏悬浮注入面板") { toggleOverlay() })
        controls.addView(body(
            "先在系统设置里启用“Phase0 手势探针”。悬浮面板可在目标游戏前台时倒计时注入点击/滑动/长按/双指；" +
                "若面板按钮有回调但游戏无反应，说明该游戏窗口拒绝无障碍注入。"
        ))

        // 3. 录制
        controls.addView(sectionTitle("3. 触摸录制（Root / Shizuku / getevent）"))
        recordStatusView = TextView(this).apply {
            text = "录制权限：$lastRecordSummary"
            textSize = 12f
            setTextColor(Color.rgb(20, 70, 120))
            setPadding(0, 0, 0, dp(6))
        }
        controls.addView(recordStatusView)
        controls.addView(buttonRow(
            makeButton("探测 Root / Shizuku") { probeRecordCapability() },
            makeButton("申请 Shizuku 权限") { requestShizukuPermission() }
        ))
        controls.addView(makeButton("Shell screencap 5 帧（绕过 MediaProjection 兜底）") {
            probeShellScreencap()
        })
        controls.addView(makeButton("getevent 8 秒采样（请连续操作屏幕）") { sampleGetEvent() })
        controls.addView(body(
            "非 root 应用无法直接读取 /dev/input。Shizuku 以 shell 身份运行命令，多数机型可执行 getevent，" +
                "这是端上录制的关键路径；无 Shizuku 且无 root 时，只能走悬浮层录制或手动标点。" +
                "若 OEM 封禁 MediaProjection，可用 Shizuku 执行 screencap 兜底截图（约 2~5fps）。"
        ))

        // 4. 报告
        controls.addView(sectionTitle("4. 报告"))
        controls.addView(buttonRow(
            makeButton("生成并分享报告") { exportReport(share = true) },
            makeButton("复制报告 JSON") { exportReport(share = false) }
        ))
        controls.addView(body(
            "报告同时写入应用外部目录 phase0_report.json，可通过 " +
                "adb pull /sdcard/Android/data/com.bsauto.phase0/files/phase0_report.json 取回。"
        ))

        // 日志区
        root.addView(TextView(this).apply {
            text = "运行日志"
            textSize = 13f
            setTextColor(Color.rgb(30, 30, 30))
            setTypeface(typeface, Typeface.BOLD)
            setPadding(dp(18), dp(4), dp(18), dp(4))
        })
        val logHeader = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.END
            setPadding(dp(18), 0, dp(18), dp(4))
        }
        logHeader.addView(makeButton("清空日志") {
            LogStore.clear()
            logView.text = ""
            lastLogVersion = -1L
        })
        root.addView(logHeader)

        logView = TextView(this).apply {
            textSize = 10f
            setTextColor(Color.rgb(30, 30, 30))
            setBackgroundColor(Color.WHITE)
            setPadding(dp(8), dp(6), dp(8), dp(6))
            setTypeface(Typeface.MONOSPACE)
            setTextIsSelectable(true)
            movementMethod = ScrollingMovementMethod()
        }
        logScroll = ScrollView(this).apply {
            addView(logView)
            setBackgroundColor(Color.WHITE)
        }
        root.addView(logScroll, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            dp(190)
        ).apply { setMargins(dp(18), 0, dp(18), dp(12)) })

        setContentView(root)
    }

    private fun sectionTitle(text: String): TextView {
        return TextView(this).apply {
            this.text = text
            textSize = 15f
            setTextColor(Color.rgb(20, 20, 20))
            setTypeface(typeface, Typeface.BOLD)
            setPadding(0, dp(18), 0, dp(6))
        }
    }

    private fun body(text: String): TextView {
        return TextView(this).apply {
            this.text = text
            textSize = 12f
            setTextColor(Color.rgb(90, 90, 90))
            setPadding(0, dp(4), 0, dp(4))
        }
    }

    private fun makeButton(text: String, onClick: () -> Unit): Button {
        return Button(this).apply {
            this.text = text
            textSize = 12f
            isAllCaps = false
            setPadding(dp(8), 0, dp(8), 0)
            setOnClickListener { onClick() }
        }
    }

    private fun buttonRow(vararg buttons: Button): LinearLayout {
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(0, dp(2), 0, dp(2))
        }
        buttons.forEachIndexed { index, button ->
            button.layoutParams = LinearLayout.LayoutParams(
                0,
                LinearLayout.LayoutParams.WRAP_CONTENT,
                1f
            ).apply {
                if (index < buttons.lastIndex) {
                    marginEnd = dp(6)
                }
            }
            layout.addView(button)
        }
        return layout
    }

    private fun accessibilityStatus(): String {
        return when {
            GestureProbeService.isConnected() -> "已连接"
            DeviceProbe.isAccessibilityServiceEnabled(this) -> "已在系统设置启用，等待连接"
            else -> "未启用（去系统设置 → 无障碍）"
        }
    }

    private fun updateGates() {
        val capture = if (CaptureService.isRunning) {
            val frames = ReportStore.getString("capture", "frames") ?: "-"
            "运行中（已采集 $frames 帧）"
        } else {
            "待测"
        }
        val secure = ReportStore.getString("secure_control", "verdict") ?: "待测"
        val target = ReportStore.getString("target", "verdict") ?: "待测"
        val tap = when (ReportStore.getString("gesture", "test_tap.completed")) {
            "true" -> "PASS（画布收到注入）"
            "false" -> "FAIL（被拒绝/取消）"
            else -> "待测"
        }
        val recordPath = ReportStore.getString("record", "recording_path")
        val recordSample = ReportStore.getString("record", "getevent_sample_result")
        val shellScreencap = ReportStore.getString("screencap_shell", "verdict")
        val record = when {
            recordPath == null -> "待测"
            recordPath == "none" -> "FAIL（无 root/Shizuku）"
            else -> buildString {
                append("可用：$recordPath")
                if (recordSample != null) {
                    append("（采样 $recordSample）")
                }
                if (shellScreencap != null) {
                    append("\n截图兜底：$shellScreencap")
                }
            }
        }
        gateView.text = buildString {
            append("阶段门 1 · 截屏：")
            append(capture)
            append("\n阶段门 2 · FLAG_SECURE：")
            append(secure)
            append("\n阶段门 3 · 目标应用：")
            append(target)
            append("\n阶段门 4 · 手势注入：")
            append(tap)
            append("\n阶段门 5 · 触摸录制：")
            append(record)
        }
    }

    private fun requestCapture() {
        if (CaptureService.isRunning) {
            toast("截屏探针已在运行")
            return
        }
        val manager = getSystemService(MediaProjectionManager::class.java)
        try {
            startActivityForResult(manager.createScreenCaptureIntent(), REQ_CAPTURE)
            LogStore.log("capture", "弹出 MediaProjection 授权对话框")
        } catch (t: Throwable) {
            LogStore.log("capture", "发起截屏授权失败：${t.message}")
            toast("发起截屏授权失败")
        }
    }

    private fun stopCapture() {
        if (!CaptureService.isRunning) {
            toast("截屏探针未运行")
            return
        }
        LogStore.log("capture", "stopCapture() 调用栈：\n" + android.util.Log.getStackTraceString(Throwable()))
        startService(
            Intent(this, CaptureService::class.java).setAction(CaptureService.ACTION_STOP)
        )
        toast("正在停止截屏探针")
    }

    private fun requireCapture(): Boolean {
        if (!CaptureService.isRunning) {
            toast("请先启动截屏探针并完成授权")
            return false
        }
        return true
    }

    private fun openLatencyPage() {
        if (!requireCapture()) {
            return
        }
        startActivity(Intent(this, LatencyActivity::class.java))
    }

    private fun openSecureControl() {
        if (!requireCapture()) {
            return
        }
        startActivity(Intent(this, SecurePreviewActivity::class.java))
    }

    private fun startTargetAppTest() {
        if (!requireCapture()) {
            return
        }
        CaptureService.startTargetTest(30)
        toast("已开始 30 秒检测：请立刻切到目标游戏并保持亮屏，结束后回来看结果")
        LogStore.log("target", "应用切到后台，开始目标应用检测")
        moveTaskToBack(true)
    }

    private fun toggleOverlay() {
        if (!GestureProbeService.isConnected()) {
            AlertDialog.Builder(this)
                .setTitle("无障碍服务未连接")
                .setMessage("请在系统设置 → 无障碍中启用“Phase0 手势探针”，然后返回本页。")
                .setPositiveButton("去设置") { _, _ ->
                    startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
                }
                .setNegativeButton("取消", null)
                .show()
            return
        }
        if (GestureProbeService.overlayVisible()) {
            GestureProbeService.hideOverlayPanel()
            toast("已请求隐藏悬浮面板")
        } else {
            GestureProbeService.showOverlayPanel()
            toast("已请求显示悬浮面板")
        }
    }

    private fun probeRecordCapability() {
        recordStatusView.text = "录制权限：探测中…"
        LogStore.log("record", "开始探测 root / Shizuku / getevent")
        Thread({
            val summary = ShellProbe.recordCapability(applicationContext)
            lastRecordSummary = summary
            runOnUiThread {
                recordStatusView.text = "录制权限：$summary"
                toast("录制能力探测完成")
            }
        }, "record-probe").start()
    }

    private fun requestShizukuPermission() {
        if (!ShellProbe.isShizukuInstalled(this)) {
            toast("未安装 Shizuku：请先安装并激活（需 PC ADB 或无线调试）")
            LogStore.log("shizuku", "未安装 Shizuku")
            return
        }
        if (ShellProbe.shizukuReady()) {
            toast("Shizuku 权限已授予")
            return
        }
        val started = ShellProbe.requestShizukuPermission(SHIZUKU_REQUEST_CODE)
        if (!started) {
            toast("Shizuku 未运行，请先启动并激活 Shizuku")
        }
    }

    private fun sampleGetEvent() {
        toast("开始 8 秒采样：请连续点击/滑动屏幕")
        LogStore.log("record", "开始 getevent 8 秒采样")
        Thread({
            ShellProbe.sampleGetEvent(8) { summary ->
                LogStore.log("record", summary)
                lastRecordSummary = summary
                runOnUiThread {
                    recordStatusView.text = "录制权限：$summary"
                    toast("getevent 采样结束，详情见日志/报告")
                }
            }
        }, "getevent-trigger").start()
    }

    private fun probeShellScreencap() {
        toast("开始 shell screencap 采样（5 帧）")
        recordStatusView.text = "录制权限：shell screencap 采集中…"
        ShellProbe.probeShellScreencap(5) { summary ->
            lastRecordSummary = summary
            runOnUiThread {
                recordStatusView.text = "录制权限：$summary"
                toast("shell screencap 采样完成")
            }
        }
    }

    private fun exportReport(share: Boolean) {
        CaptureService.fillReport()
        DeviceProbe.record(this)
        val json = ReportStore.toJson()
        val file = File(getExternalFilesDir(null) ?: filesDir, "phase0_report.json")
        try {
            file.parentFile?.mkdirs()
            file.writeText(json)
            LogStore.log("report", "报告已写入 ${file.absolutePath}")
        } catch (t: Throwable) {
            LogStore.log("report", "写报告失败：${t.message}")
        }
        if (share) {
            val intent = Intent(Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(Intent.EXTRA_SUBJECT, "Android Phase 0 自动化探针报告")
                putExtra(Intent.EXTRA_TEXT, json)
            }
            startActivity(Intent.createChooser(intent, "分享 Phase 0 报告"))
        } else {
            val clipboard = getSystemService(ClipboardManager::class.java)
            clipboard.setPrimaryClip(ClipData.newPlainText("Phase0 report", json))
            toast("报告 JSON 已复制到剪贴板")
        }
    }

    private fun requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFICATIONS)
        }
    }

    private fun registerShizukuListener() {
        if (!ShellProbe.isShizukuInstalled(this)) {
            return
        }
        try {
            Shizuku.addRequestPermissionResultListener(shizukuPermissionListener)
            shizukuListenerRegistered = true
        } catch (t: Throwable) {
            LogStore.log("shizuku", "注册权限监听失败：${t.message}")
        }
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private fun dp(value: Int): Int =
        (value * resources.displayMetrics.density + 0.5f).toInt()
}
