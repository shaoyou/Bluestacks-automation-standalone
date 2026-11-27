package com.bsauto.phase0

import android.app.Activity
import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.os.Bundle
import android.os.SystemClock
import android.view.View
import android.view.WindowManager

/**
 * 全屏时间戳图案页：服务端从截屏中解码图案，计算“绘制→捕获→处理”的年龄。
 * 16 个等宽块：前 4 个标记位 1/0/1/1，后 12 位为 elapsedRealtime() % 4096。
 */
class LatencyActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(TimestampPatternView(this))
        // 注意：不要在这里访问 window.insetsController（DecorView 尚未挂载，会 NPE）。
        // 用兼容性最好的 systemUiVisibility 进入沉浸式；图案位于屏幕顶部 4%~12%，
        // 即使 OEM 保留导航栏也不影响解码。
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility =
            View.SYSTEM_UI_FLAG_FULLSCREEN or
                View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
                View.SYSTEM_UI_FLAG_LAYOUT_STABLE
    }

    override fun onResume() {
        super.onResume()
        CaptureService.setMode(CaptureService.MODE_LATENCY)
    }
}

class TimestampPatternView(context: Context) : View(context) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG)
    private val marker = intArrayOf(1, 0, 1, 1)

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawColor(Color.BLACK)
        val width = width.toFloat()
        val height = height.toFloat()
        if (width <= 0f || height <= 0f) {
            postInvalidateOnAnimation()
            return
        }

        val blockWidth = width / 16f
        val top = height * 0.04f
        val bottom = height * 0.12f
        val value = (SystemClock.elapsedRealtime() % 4096L).toInt()

        paint.style = Paint.Style.FILL
        for (i in 0 until 16) {
            val bit = if (i < 4) marker[i] else (value shr (i - 4)) and 1
            paint.color = if (bit == 1) Color.WHITE else Color.BLACK
            canvas.drawRect(
                i * blockWidth,
                top,
                (i + 1) * blockWidth,
                bottom,
                paint
            )
        }

        // 给解码端一个明确的上下边界：在黑块上下各画一条极细灰线。
        paint.style = Paint.Style.STROKE
        paint.strokeWidth = 1f
        paint.color = Color.rgb(40, 40, 40)
        canvas.drawRect(0f, top, width, bottom, paint)

        paint.style = Paint.Style.FILL
        paint.textAlign = Paint.Align.CENTER
        paint.color = Color.WHITE
        paint.textSize = height * 0.022f
        canvas.drawText("延迟探针运行中", width / 2f, height * 0.24f, paint)
        paint.textSize = height * 0.014f
        canvas.drawText(
            "当前计数 $value / 4096 ms",
            width / 2f,
            height * 0.285f,
            paint
        )
        paint.color = Color.rgb(170, 170, 170)
        paint.textSize = height * 0.012f
        canvas.drawText(
            "请保持本页在前台；按返回键退出",
            width / 2f,
            height * 0.93f,
            paint
        )

        postInvalidateOnAnimation()
    }
}
