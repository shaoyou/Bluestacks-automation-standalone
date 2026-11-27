package com.bsauto.phase0

import android.app.Activity
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Path
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.util.SparseArray
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import kotlin.math.min

/**
 * 手势注入自检页：
 * 点击按钮后由无障碍服务向本页中央注入手势，画布记录收到的 MotionEvent。
 * 只要画布出现对应的 DOWN/UP/MOVE，就说明 dispatchGesture 链路可用。
 */
class GestureTestActivity : Activity() {
    private lateinit var touchView: TouchProbeView
    private lateinit var statusView: TextView
    private lateinit var hintView: TextView
    private val handler = Handler(Looper.getMainLooper())

    private val refresh = object : Runnable {
        override fun run() {
            val connected = if (GestureProbeService.isConnected()) "已连接" else "未连接"
            statusView.text = "无障碍：$connected · DOWN=${touchView.downCount} UP=${touchView.upCount} " +
                "MOVE=${touchView.moveCount} 多指按下=${touchView.pointerDownCount} " +
                "最近 deviceId=${touchView.lastDeviceId} source=${touchView.lastSource}"
            handler.postDelayed(this, 400L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.rgb(245, 245, 245))
            setPadding(24, 24, 24, 24)
        }

        root.addView(TextView(this).apply {
            text = "手势注入自检"
            textSize = 20f
            setTextColor(Color.rgb(20, 20, 20))
        })
        hintView = TextView(this).apply {
            text = "先确认主界面显示“无障碍：已连接”。按钮注入的坐标是本画布中心；" +
                "若画布计数增加，说明 dispatchGesture 生效。"
            textSize = 13f
            setTextColor(Color.rgb(80, 80, 80))
            setPadding(0, 8, 0, 8)
        }
        root.addView(hintView)
        statusView = TextView(this).apply {
            text = "等待注入…"
            textSize = 12f
            setTextColor(Color.rgb(20, 70, 120))
            setPadding(0, 0, 0, 8)
        }
        root.addView(statusView)

        val row1 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        row1.addView(makeButton("注入点击") { injectDelay("点击") { cx, cy -> GestureProbeService.injectTap(cx, cy, 60L, "test_tap", reporter("点击")) } })
        row1.addView(makeButton("注入长按") { injectDelay("长按") { cx, cy -> GestureProbeService.injectTap(cx, cy, 800L, "test_long_press", reporter("长按")) } })
        row1.addView(makeButton("注入上滑") { injectDelay("上滑") { cx, cy -> GestureProbeService.injectSwipe(cx, cy + touchView.height * 0.22f, cx, cy - touchView.height * 0.22f, 450L, "test_swipe", reporter("上滑")) } })
        root.addView(row1)

        val row2 = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        row2.addView(makeButton("注入双指") {
            injectDelay("双指") { cx, cy ->
                val radius = min(touchView.width, touchView.height) / 4f
                GestureProbeService.injectPinch(cx, cy, radius, radius * 0.35f, 500L, "test_pinch", reporter("双指"))
            }
        })
        row2.addView(makeButton("清空画布") {
            touchView.clear()
            LogStore.log("gesture", "测试画布已清空")
        })
        row2.addView(makeButton("查看坐标") {
            val location = IntArray(2)
            touchView.getLocationOnScreen(location)
            val cx = location[0] + touchView.width / 2f
            val cy = location[1] + touchView.height / 2f
            val message = "画布中心 ($cx, $cy)，尺寸 ${touchView.width}×${touchView.height}"
            hintView.text = message
            LogStore.log("gesture", message)
        })
        root.addView(row2)

        touchView = TouchProbeView(this)
        root.addView(touchView, LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            0,
            1f
        ).apply { topMargin = 12 })

        setContentView(root)
        LogStore.log("gesture", "手势自检页已打开")

        val autoAction = intent.getStringExtra("auto_action")
        if (autoAction != null) {
            LogStore.log("gesture", "收到自动注入指令：$autoAction")
            handler.postDelayed({ autoInject(autoAction) }, 1200L)
        }
    }

    private fun autoInject(action: String) {
        when (action) {
            "tap" -> injectDelay("点击") { cx, cy ->
                GestureProbeService.injectTap(cx, cy, 60L, "test_tap", reporter("点击"))
            }
            "long" -> injectDelay("长按") { cx, cy ->
                GestureProbeService.injectTap(cx, cy, 800L, "test_long_press", reporter("长按"))
            }
            "swipe" -> injectDelay("上滑") { cx, cy ->
                GestureProbeService.injectSwipe(
                    cx,
                    cy + touchView.height * 0.22f,
                    cx,
                    cy - touchView.height * 0.22f,
                    450L,
                    "test_swipe",
                    reporter("上滑")
                )
            }
            "pinch" -> injectDelay("双指") { cx, cy ->
                val radius = min(touchView.width, touchView.height) / 4f
                GestureProbeService.injectPinch(
                    cx,
                    cy,
                    radius,
                    radius * 0.35f,
                    500L,
                    "test_pinch",
                    reporter("双指")
                )
            }
            else -> LogStore.log("gesture", "未知自动注入指令：$action")
        }
    }

    override fun onResume() {
        super.onResume()
        handler.removeCallbacks(refresh)
        handler.post(refresh)
    }

    override fun onPause() {
        super.onPause()
        handler.removeCallbacks(refresh)
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }

    private fun makeButton(text: String, onClick: () -> Unit): Button {
        return Button(this).apply {
            this.text = text
            textSize = 12f
            setPadding(8, 0, 8, 0)
            setOnClickListener { onClick() }
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                .apply { marginEnd = 6 }
        }
    }

    private fun injectDelay(label: String, action: (Float, Float) -> Unit) {
        if (!GestureProbeService.isConnected()) {
            Toast.makeText(this, "无障碍服务未连接，请先启用", Toast.LENGTH_SHORT).show()
            LogStore.log("gesture", "$label：无障碍服务未连接")
            return
        }
        handler.postDelayed({
            val location = IntArray(2)
            touchView.getLocationOnScreen(location)
            val cx = location[0] + touchView.width / 2f
            val cy = location[1] + touchView.height / 2f
            LogStore.log("gesture", "$label：准备注入画布中心 ($cx, $cy)")
            ReportStore.put("gesture", "test_expected_center", "$cx,$cy")
            ReportStore.put("gesture", "test_expected_action", label)
            action(cx, cy)
        }, 400L)
    }

    private fun reporter(label: String): (Boolean, String) -> Unit {
        return { ok, message ->
            runOnUiThread {
                hintView.text = "$label：$message"
                Toast.makeText(this, "$label：$message", Toast.LENGTH_SHORT).show()
            }
        }
    }
}

class TouchProbeView(context: Context) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val paths = mutableListOf<Path>()
    private var currentPath: Path? = null
    private var downAt = 0L
    private var downX = 0f
    private var downY = 0f
    private var moveLogCounter = 0

    var downCount = 0
        private set
    var upCount = 0
        private set
    var moveCount = 0
        private set
    var pointerDownCount = 0
        private set
    var lastDeviceId = -999
        private set
    var lastSource = -1
        private set
    var lastFlags = -1
        private set

    init {
        isClickable = true
        setBackgroundColor(Color.rgb(16, 24, 32))
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                downCount += 1
                downAt = event.eventTime
                downX = event.x
                downY = event.y
                lastDeviceId = event.deviceId
                lastSource = event.source
                lastFlags = event.flags
                currentPath = Path().apply { moveTo(event.x, event.y) }
                LogStore.log(
                    "gesture",
                    "画布 DOWN x=${fmt(event.x)} y=${fmt(event.y)} " +
                        "pointerCount=${event.pointerCount} deviceId=${event.deviceId} " +
                        "source=0x${Integer.toHexString(event.source)} flags=0x${Integer.toHexString(event.flags)} " +
                        "toolType=${event.getToolType(0)} pressure=${fmt(event.pressure)}"
                )
                ReportStore.put("gesture", "test_last_down_x", DeviceProbe.round2(event.x.toDouble()))
                ReportStore.put("gesture", "test_last_down_y", DeviceProbe.round2(event.y.toDouble()))
                ReportStore.put("gesture", "test_last_device_id", event.deviceId)
                ReportStore.put("gesture", "test_last_source", event.source)
                ReportStore.put("gesture", "test_last_flags", event.flags)
                ReportStore.put("gesture", "test_down_count", downCount)
            }

            MotionEvent.ACTION_POINTER_DOWN -> {
                pointerDownCount += 1
                LogStore.log(
                    "gesture",
                    "画布 POINTER_DOWN index=${event.actionIndex} x=${fmt(event.getX(event.actionIndex))} " +
                        "y=${fmt(event.getY(event.actionIndex))} pointerCount=${event.pointerCount}"
                )
                ReportStore.put("gesture", "test_pointer_down_count", pointerDownCount)
                ReportStore.put("gesture", "test_max_pointer_count", event.pointerCount)
            }

            MotionEvent.ACTION_MOVE -> {
                moveCount += 1
                currentPath?.lineTo(event.x, event.y)
                moveLogCounter += 1
                if (moveLogCounter >= 20) {
                    moveLogCounter = 0
                    LogStore.log(
                        "gesture",
                        "画布 MOVE x=${fmt(event.x)} y=${fmt(event.y)} pointerCount=${event.pointerCount}"
                    )
                }
            }

            MotionEvent.ACTION_UP -> {
                upCount += 1
                currentPath?.lineTo(event.x, event.y)
                currentPath?.let { paths.add(it) }
                currentPath = null
                val duration = event.eventTime - downAt
                val distance = Math.hypot((event.x - downX).toDouble(), (event.y - downY).toDouble())
                LogStore.log(
                    "gesture",
                    "画布 UP x=${fmt(event.x)} y=${fmt(event.y)} 位移=${fmt(distance.toFloat())} " +
                        "按压=${duration}ms pointerCount=${event.pointerCount}"
                )
                ReportStore.put("gesture", "test_up_count", upCount)
                ReportStore.put("gesture", "test_last_press_ms", duration)
                ReportStore.put("gesture", "test_last_move_distance", DeviceProbe.round2(distance))
            }

            MotionEvent.ACTION_CANCEL -> {
                LogStore.log("gesture", "画布 CANCEL")
                currentPath?.let { paths.add(it) }
                currentPath = null
            }
        }
        invalidate()
        return true
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        val width = width.toFloat()
        val height = height.toFloat()

        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 2f
        paint.color = Color.rgb(70, 90, 110)
        canvas.drawRect(1f, 1f, width - 1f, height - 1f, paint)

        paint.strokeWidth = 4f
        paint.color = Color.rgb(0, 210, 255)
        for (path in paths) {
            canvas.drawPath(path, paint)
        }
        paint.color = Color.rgb(255, 170, 0)
        currentPath?.let { canvas.drawPath(it, paint) }

        paint.style = Paint.Style.FILL
        paint.color = Color.rgb(255, 80, 80)
        canvas.drawCircle(downX, downY, 8f, paint)

        paint.color = Color.rgb(200, 220, 235)
        paint.textSize = 14f * resources.displayMetrics.scaledDensity
        canvas.drawText(
            "DOWN=$downCount  UP=$upCount  MOVE=$moveCount  POINTER=${pointerDownCount}",
            18f,
            32f,
            paint
        )
        paint.color = Color.rgb(110, 130, 150)
        paint.textSize = 12f * resources.displayMetrics.scaledDensity
        canvas.drawText(
            "在此区域手动触摸或接收注入手势",
            18f,
            height - 18f,
            paint
        )
    }

    fun clear() {
        paths.clear()
        currentPath = null
        downCount = 0
        upCount = 0
        moveCount = 0
        pointerDownCount = 0
        downX = -100f
        downY = -100f
        invalidate()
    }

    private fun fmt(value: Float): String = String.format(java.util.Locale.US, "%.1f", value)
}
