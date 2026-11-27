package com.bsauto.phase0

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Color
import android.graphics.Path
import android.graphics.PixelFormat
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.Gravity
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import kotlin.math.min

/**
 * 无障碍手势注入探针。
 *
 * - 向自身测试页注入点击/长按/滑动/双指，验证 dispatchGesture 是否可用
 * - 提供 TYPE_ACCESSIBILITY_OVERLAY 悬浮面板，可在目标游戏前台时倒计时注入
 */
class GestureProbeService : AccessibilityService() {

    companion object {
        @Volatile
        var instance: GestureProbeService? = null

        fun isConnected(): Boolean = instance != null

        fun injectTap(
            x: Float,
            y: Float,
            durationMs: Long = 60L,
            tag: String = "tap",
            callback: ((Boolean, String) -> Unit)? = null,
        ): Boolean {
            val service = instance
            if (service == null) {
                callback?.invoke(false, "无障碍服务未连接")
                return false
            }
            return service.dispatchTap(x, y, durationMs, tag, callback)
        }

        fun injectSwipe(
            x1: Float,
            y1: Float,
            x2: Float,
            y2: Float,
            durationMs: Long = 400L,
            tag: String = "swipe",
            callback: ((Boolean, String) -> Unit)? = null,
        ): Boolean {
            val service = instance
            if (service == null) {
                callback?.invoke(false, "无障碍服务未连接")
                return false
            }
            return service.dispatchSwipe(x1, y1, x2, y2, durationMs, tag, callback)
        }

        fun injectPinch(
            cx: Float,
            cy: Float,
            startRadius: Float,
            endRadius: Float,
            durationMs: Long = 500L,
            tag: String = "pinch",
            callback: ((Boolean, String) -> Unit)? = null,
        ): Boolean {
            val service = instance
            if (service == null) {
                callback?.invoke(false, "无障碍服务未连接")
                return false
            }
            return service.dispatchPinch(cx, cy, startRadius, endRadius, durationMs, tag, callback)
        }

        fun showOverlayPanel() {
            instance?.showOverlay()
        }

        fun hideOverlayPanel() {
            instance?.removeOverlay()
        }

        fun overlayVisible(): Boolean = instance?.overlayView != null
    }

    private val handler = Handler(Looper.getMainLooper())
    private var overlayView: View? = null
    private var overlayStatus: TextView? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        instance = this
        LogStore.log("gesture", "无障碍服务已连接")
        ReportStore.put("gesture", "service_connected", true)
        ReportStore.put("gesture", "package", packageName)
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Phase 0 不依赖窗口内容/事件，仅使用手势注入能力。
    }

    override fun onInterrupt() {
        LogStore.log("gesture", "onInterrupt")
    }

    override fun onUnbind(intent: android.content.Intent?): Boolean {
        instance = null
        removeOverlay()
        ReportStore.put("gesture", "service_connected", false)
        LogStore.log("gesture", "无障碍服务已断开")
        return super.onUnbind(intent)
    }

    override fun onDestroy() {
        instance = null
        removeOverlay()
        ReportStore.put("gesture", "service_connected", false)
        super.onDestroy()
    }

    private fun screenSize(): Pair<Int, Int> {
        val windowManager = getSystemService(WindowManager::class.java)
        if (Build.VERSION.SDK_INT >= 30 && windowManager != null) {
            val bounds = windowManager.currentWindowMetrics.bounds
            return bounds.width() to bounds.height()
        }
        val metrics = resources.displayMetrics
        return metrics.widthPixels to metrics.heightPixels
    }

    private fun dispatchTap(
        x: Float,
        y: Float,
        durationMs: Long,
        tag: String,
        callback: ((Boolean, String) -> Unit)?,
    ): Boolean {
        val path = Path().apply { moveTo(x, y) }
        return dispatchPath(path, durationMs, tag, callback)
    }

    private fun dispatchSwipe(
        x1: Float,
        y1: Float,
        x2: Float,
        y2: Float,
        durationMs: Long,
        tag: String,
        callback: ((Boolean, String) -> Unit)?,
    ): Boolean {
        val path = Path().apply {
            moveTo(x1, y1)
            lineTo(x2, y2)
        }
        return dispatchPath(path, durationMs, tag, callback)
    }

    private fun dispatchPinch(
        cx: Float,
        cy: Float,
        startRadius: Float,
        endRadius: Float,
        durationMs: Long,
        tag: String,
        callback: ((Boolean, String) -> Unit)?,
    ): Boolean {
        val left = Path().apply {
            moveTo(cx - startRadius, cy)
            lineTo(cx - endRadius, cy)
        }
        val right = Path().apply {
            moveTo(cx + startRadius, cy)
            lineTo(cx + endRadius, cy)
        }
        val duration = durationMs.coerceIn(1L, 59000L)
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(left, 0L, duration))
            .addStroke(GestureDescription.StrokeDescription(right, 0L, duration))
            .build()
        return dispatchGestureCompat(gesture, tag, callback)
    }

    private fun dispatchPath(
        path: Path,
        durationMs: Long,
        tag: String,
        callback: ((Boolean, String) -> Unit)?,
    ): Boolean {
        val duration = durationMs.coerceIn(1L, 59000L)
        val gesture = GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0L, duration))
            .build()
        return dispatchGestureCompat(gesture, tag, callback)
    }

    private fun dispatchGestureCompat(
        gesture: GestureDescription,
        tag: String,
        callback: ((Boolean, String) -> Unit)?,
    ): Boolean {
        val start = SystemClock.elapsedRealtime()
        val accepted = try {
            dispatchGesture(gesture, object : GestureResultCallback() {
                override fun onCompleted(gestureDescription: GestureDescription?) {
                    val cost = SystemClock.elapsedRealtime() - start
                    LogStore.log("gesture", "$tag 完成（dispatch 回调 ${cost}ms）")
                    ReportStore.put("gesture", "$tag.completed", true)
                    ReportStore.put("gesture", "$tag.callback_ms", cost)
                    callback?.invoke(true, "完成（${cost}ms）")
                }

                override fun onCancelled(gestureDescription: GestureDescription?) {
                    LogStore.log("gesture", "$tag 被系统取消")
                    ReportStore.put("gesture", "$tag.completed", false)
                    callback?.invoke(false, "被系统取消")
                }
            }, handler)
        } catch (t: Throwable) {
            LogStore.log("gesture", "$tag 调用异常：${t.javaClass.simpleName}: ${t.message}")
            callback?.invoke(false, "异常：${t.message}")
            false
        }
        if (!accepted) {
            LogStore.log("gesture", "$tag dispatchGesture 返回 false（被拒绝）")
            ReportStore.put("gesture", "$tag.accepted", false)
            callback?.invoke(false, "dispatchGesture 被拒绝")
        } else {
            ReportStore.put("gesture", "$tag.accepted", true)
        }
        return accepted
    }

    fun showOverlay() {
        if (overlayView != null) {
            return
        }
        val windowManager = getSystemService(WindowManager::class.java) ?: return
        val container = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.argb(220, 18, 18, 18))
            setPadding(28, 18, 28, 18)
        }
        val title = TextView(this).apply {
            text = "Phase0 手势注入"
            setTextColor(Color.WHITE)
            textSize = 12f
            setPadding(0, 0, 0, 8)
        }
        val status = TextView(this).apply {
            text = "目标应用在前台时，点下面的按钮"
            setTextColor(Color.parseColor("#FFD54F"))
            textSize = 11f
            setPadding(0, 0, 0, 10)
        }
        overlayStatus = status
        container.addView(title)
        container.addView(status)
        container.addView(overlayButton("倒计时 3s → 点击屏幕中心") {
            countdown("点击中心") { cx, cy ->
                dispatchTap(cx, cy, 60L, "overlay_tap_center") { ok, message ->
                    status.text = "点击中心：$message"
                    toast("点击中心：$message ${if (ok) "" else "（请确认目标窗口未拒绝注入）"}")
                }
            }
        })
        container.addView(overlayButton("倒计时 3s → 中心上滑") {
            countdown("中心上滑") { cx, cy ->
                dispatchSwipe(cx, cy + 320f, cx, cy - 320f, 420L, "overlay_swipe") { ok, message ->
                    status.text = "中心上滑：$message"
                    toast("中心上滑：$message")
                }
            }
        })
        container.addView(overlayButton("倒计时 3s → 长按中心 800ms") {
            countdown("长按中心") { cx, cy ->
                dispatchTap(cx, cy, 800L, "overlay_long_press") { ok, message ->
                    status.text = "长按中心：$message"
                    toast("长按中心：$message")
                }
            }
        })
        container.addView(overlayButton("倒计时 3s → 双指缩放") {
            countdown("双指缩放") { cx, cy ->
                val (width, height) = screenSize()
                val radius = min(width, height) / 4f
                dispatchPinch(cx, cy, radius, radius * 0.35f, 500L, "overlay_pinch") { ok, message ->
                    status.text = "双指缩放：$message"
                    toast("双指缩放：$message")
                }
            }
        })
        container.addView(overlayButton("关闭面板") {
            removeOverlay()
        })

        val params = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or
                WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT
        ).apply {
            gravity = Gravity.TOP or Gravity.START
            x = 20
            y = 200
        }
        try {
            windowManager.addView(container, params)
            overlayView = container
            LogStore.log("gesture", "悬浮注入面板已显示")
            ReportStore.put("gesture", "overlay_shown", true)
        } catch (t: Throwable) {
            LogStore.log("gesture", "显示悬浮面板失败：${t.javaClass.simpleName}: ${t.message}")
        }
    }

    private fun removeOverlay() {
        val view = overlayView ?: return
        overlayView = null
        overlayStatus = null
        try {
            getSystemService(WindowManager::class.java)?.removeView(view)
            LogStore.log("gesture", "悬浮注入面板已移除")
        } catch (t: Throwable) {
            LogStore.log("gesture", "移除悬浮面板失败：${t.message}")
        }
    }

    private fun overlayButton(text: String, onClick: () -> Unit): Button {
        return Button(this).apply {
            this.text = text
            textSize = 12f
            setPadding(20, 6, 20, 6)
            setOnClickListener { onClick() }
        }
    }

    private fun countdown(label: String, action: (Float, Float) -> Unit) {
        var remaining = 3
        fun tick() {
            if (remaining > 0) {
                overlayStatus?.text = "$label：${remaining}s 后注入，请切到目标画面"
                remaining -= 1
                handler.postDelayed({ tick() }, 1000L)
            } else {
                val (width, height) = screenSize()
                overlayStatus?.text = "$label：正在注入…"
                action(width / 2f, height / 2f)
            }
        }
        tick()
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}
