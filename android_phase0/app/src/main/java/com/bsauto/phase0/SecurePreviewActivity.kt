package com.bsauto.phase0

import android.app.Activity
import android.graphics.Color
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.WindowManager
import android.widget.LinearLayout
import android.widget.TextView

/**
 * FLAG_SECURE 阳性对照页：
 * 本页设置 FLAG_SECURE 并显示明亮内容；若截屏链路正常，MediaProjection 只能拿到黑屏。
 * 服务在 6 秒采样后自动给出 PASS/FAIL。
 */
class SecurePreviewActivity : Activity() {
    private val handler = Handler(Looper.getMainLooper())
    private lateinit var layout: LinearLayout
    private val colors = intArrayOf(
        Color.rgb(255, 193, 7),
        Color.WHITE,
        Color.rgb(0, 188, 212),
        Color.rgb(255, 109, 0)
    )
    private var colorIndex = 0
    private val animate = object : Runnable {
        override fun run() {
            colorIndex = (colorIndex + 1) % colors.size
            layout.setBackgroundColor(colors[colorIndex])
            handler.postDelayed(this, 120L)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(
            WindowManager.LayoutParams.FLAG_SECURE or
                WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
        )
        layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER
            setBackgroundColor(Color.rgb(255, 193, 7))
            setPadding(48, 48, 48, 48)
        }
        layout.addView(TextView(this).apply {
            text = "FLAG_SECURE 对照页"
            textSize = 26f
            setTextColor(Color.rgb(20, 20, 20))
            gravity = Gravity.CENTER
        })
        layout.addView(TextView(this).apply {
            text = "本页内容明亮且带 FLAG_SECURE。\n若截屏探针正常，捕获结果应为全黑。"
            textSize = 16f
            setTextColor(Color.rgb(40, 40, 40))
            gravity = Gravity.CENTER
            setPadding(0, 24, 0, 0)
        })
        layout.addView(TextView(this).apply {
            text = "采样 6 秒，本页将在约 9 秒后自动返回。"
            textSize = 14f
            setTextColor(Color.rgb(80, 60, 0))
            gravity = Gravity.CENTER
            setPadding(0, 32, 0, 0)
        })
        setContentView(layout)

        handler.postDelayed({
            if (!isFinishing) {
                finish()
            }
        }, 9500L)
    }

    override fun onResume() {
        super.onResume()
        CaptureService.setMode(CaptureService.MODE_SECURE_CONTROL)
        handler.removeCallbacks(animate)
        handler.post(animate)
    }

    override fun onPause() {
        handler.removeCallbacks(animate)
        super.onPause()
    }

    override fun onDestroy() {
        handler.removeCallbacksAndMessages(null)
        super.onDestroy()
    }
}
