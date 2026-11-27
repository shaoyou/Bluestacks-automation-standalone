package com.bsauto.phase0

import android.app.Activity
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.Image
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.view.WindowManager
import org.json.JSONArray
import java.io.File
import java.io.FileOutputStream
import java.util.Collections
import java.util.Locale
import java.util.concurrent.Executors
import kotlin.math.max

/**
 * MediaProjection 截屏探针。
 *
 * 采集内容：
 *  - 帧率、帧间隔、处理耗时
 *  - 画面平均亮度 / 黑屏比例 / 静止帧数量
 *  - 通过全屏时间戳图案测量“绘制 → 捕获 → 处理”的端到端年龄
 *  - FLAG_SECURE 阳性对照（自身设置 FLAG_SECURE 的页面应全黑）
 *  - 目标应用 30 秒截图可用性检测（黑屏比 + 唯一帧数）
 */
class CaptureService : Service() {

    companion object {
        const val ACTION_START = "com.bsauto.phase0.capture.START"
        const val ACTION_STOP = "com.bsauto.phase0.capture.STOP"
        const val ACTION_SET_MODE = "com.bsauto.phase0.capture.SET_MODE"
        const val ACTION_TARGET_TEST = "com.bsauto.phase0.capture.TARGET_TEST"

        const val EXTRA_RESULT_CODE = "result_code"
        const val EXTRA_RESULT_DATA = "result_data"
        const val EXTRA_MODE = "mode"
        const val EXTRA_SECONDS = "seconds"

        const val MODE_GENERAL = "general"
        const val MODE_LATENCY = "latency"
        const val MODE_SECURE_CONTROL = "secure_control"
        const val MODE_TARGET = "target"

        private const val CHANNEL_ID = "phase0_capture"
        private const val NOTIF_ID = 1001
        private const val RESULT_NOTIF_ID = 1002
        private const val TARGET_GRACE_MS = 2000L
        private const val TARGET_SAVE_COUNT = 4

        @Volatile
        var isRunning: Boolean = false
            private set

        @Volatile
        private var instance: CaptureService? = null

        fun setMode(mode: String) {
            instance?.let { service ->
                service.frameHandler.post { service.applyMode(mode) }
            }
        }

        fun startTargetTest(seconds: Int) {
            instance?.let { service ->
                service.frameHandler.post { service.applyTargetTest(seconds) }
            }
        }

        fun isTargetTestRunning(): Boolean {
            val service = instance ?: return false
            val endAt = service.targetEndAt
            return endAt > 0 && endAt > SystemClock.elapsedRealtime()
        }

        fun statusText(): String {
            val service = instance ?: return "未运行"
            return service.buildStatus()
        }

        fun fillReport() {
            val service = instance
            if (service == null) {
                ReportStore.put("capture", "running", false)
                return
            }
            service.fillReportNow()
        }
    }

    private lateinit var handlerThread: HandlerThread
    private lateinit var frameHandler: Handler
    private val mainHandler = Handler(Looper.getMainLooper())
    private val saveExecutor = Executors.newSingleThreadExecutor()

    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null

    private val stats = Stats()

    @Volatile
    private var currentMode: String = MODE_GENERAL

    // 以下字段只在 frameHandler 线程读写
    private var lastNotificationAt = 0L
    private var secureStartAt = 0L
    private var secureEndAt = 0L
    private var secureBlackSum = 0.0
    private var secureLumaSum = 0.0
    private var secureFrames = 0L
    private var secureEvaluated = true
    private val secureEndRunnable = Runnable { finishSecureControl() }

    @Volatile
    private var targetEndAt = 0L
    private var targetStartAt = 0L
    private var targetBlackSum = 0.0
    private var targetLumaSum = 0.0
    private var targetFrames = 0L
    private val targetHashes = HashSet<Long>()
    private val targetSamples = Collections.synchronizedList(mutableListOf<String>())
    private var targetSaved = 0
    private var targetNextSaveAt = 0L
    private var targetEvaluated = false
    private val targetEndRunnable = Runnable { finishTargetTest() }

    private data class FrameSample(
        val meanLuma: Double,
        val blackRatio: Double,
        val hash: Long,
    )

    private class Stats {
        var startedAt = 0L
        var lastFrameAt = 0L
        var frameCount = 0L
        var uniqueFrames = 0
        var lastHash = 0L
        val frameIntervals = ArrayDeque<Long>()
        val analysisTimes = ArrayDeque<Long>()
        val latencyCapture = ArrayDeque<Long>()
        val latencyProcessing = ArrayDeque<Long>()
        val blackRatios = ArrayDeque<Double>()
        val lumaValues = ArrayDeque<Double>()
    }

    override fun onCreate() {
        super.onCreate()
        handlerThread = HandlerThread("phase0-capture")
        handlerThread.start()
        frameHandler = Handler(handlerThread.looper)
        createChannel()
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_START -> {
                startForegroundCompat("正在初始化截屏…")
                val resultCode = intent.getIntExtra(EXTRA_RESULT_CODE, Activity.RESULT_CANCELED)
                val data: Intent? = if (Build.VERSION.SDK_INT >= 33) {
                    intent.getParcelableExtra(EXTRA_RESULT_DATA, Intent::class.java)
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(EXTRA_RESULT_DATA)
                }
                if (resultCode == Activity.RESULT_OK && data != null) {
                    startProjection(resultCode, data)
                } else {
                    LogStore.log("capture", "缺少 MediaProjection 授权数据，停止服务")
                    ReportStore.put("capture", "start_failed", "no_projection_consent")
                    stopForegroundCompat()
                    stopSelf()
                }
            }

            ACTION_STOP -> {
                LogStore.log("capture", "收到停止命令")
                releaseAll()
                stopForegroundCompat()
                stopSelf()
            }

            ACTION_SET_MODE -> {
                val mode = intent.getStringExtra(EXTRA_MODE) ?: MODE_GENERAL
                frameHandler.post { applyMode(mode) }
            }

            ACTION_TARGET_TEST -> {
                val seconds = intent.getIntExtra(EXTRA_SECONDS, 30)
                frameHandler.post { applyTargetTest(seconds) }
            }
        }
        return START_NOT_STICKY
    }

    private fun startProjection(resultCode: Int, data: Intent) {
        if (projection != null) {
            LogStore.log("capture", "已有截屏会话，忽略重复启动")
            return
        }
        val manager = getSystemService(MediaProjectionManager::class.java)
        val newProjection = try {
            manager.getMediaProjection(resultCode, data)
        } catch (t: Throwable) {
            LogStore.log("capture", "getMediaProjection 失败：${t.javaClass.simpleName}: ${t.message}")
            null
        }
        if (newProjection == null) {
            ReportStore.put("capture", "start_failed", "getMediaProjection_null")
            stopForegroundCompat()
            stopSelf()
            return
        }

        newProjection.registerCallback(object : MediaProjection.Callback() {
            override fun onStop() {
                LogStore.log("capture", "MediaProjection 已停止（系统/用户撤销）")
                ReportStore.put("capture", "projection_stopped_by_system", true)
                frameHandler.post {
                    releaseAll()
                    stopForegroundCompat()
                    stopSelf()
                }
            }
        }, mainHandler)

        val (width, height) = screenSize()
        val density = resources.displayMetrics.densityDpi
        LogStore.log("capture", "屏幕 ${width}×${height} @ ${density}dpi，创建 VirtualDisplay")

        try {
            val reader = ImageReader.newInstance(width, height, android.graphics.PixelFormat.RGBA_8888, 2)
            val display = newProjection.createVirtualDisplay(
                "phase0-capture",
                width,
                height,
                density,
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                reader.surface,
                null,
                frameHandler
            )
            reader.setOnImageAvailableListener({ r -> onImageAvailable(r) }, frameHandler)
            imageReader = reader
            virtualDisplay = display
            projection = newProjection
            isRunning = true
            instance = this
            ReportStore.put("capture", "running", true)
            ReportStore.put("capture", "width", width)
            ReportStore.put("capture", "height", height)
            ReportStore.put("capture", "density_dpi", density)
            LogStore.log("capture", "截屏服务已启动")
            updateNotification()
        } catch (t: Throwable) {
            LogStore.log("capture", "创建 VirtualDisplay 失败：${t.javaClass.simpleName}: ${t.message}")
            ReportStore.put("capture", "start_failed", "create_virtual_display: ${t.message}")
            try {
                newProjection.stop()
            } catch (_: Throwable) {
            }
            stopForegroundCompat()
            stopSelf()
        }
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

    private fun onImageAvailable(reader: ImageReader) {
        val image = try {
            reader.acquireLatestImage()
        } catch (t: Throwable) {
            LogStore.log("capture", "acquireLatestImage 失败：${t.message}")
            null
        } ?: return

        val t0 = SystemClock.elapsedRealtime()
        try {
            val sample = sampleImage(image)
            val t1 = SystemClock.elapsedRealtime()
            synchronized(stats) {
                if (stats.startedAt == 0L) {
                    stats.startedAt = t0
                }
                if (stats.lastFrameAt > 0L) {
                    stats.frameIntervals.addLast(t0 - stats.lastFrameAt)
                    while (stats.frameIntervals.size > 600) {
                        stats.frameIntervals.removeFirst()
                    }
                }
                stats.lastFrameAt = t0
                stats.frameCount += 1
                stats.analysisTimes.addLast(t1 - t0)
                while (stats.analysisTimes.size > 600) {
                    stats.analysisTimes.removeFirst()
                }
                stats.blackRatios.addLast(sample.blackRatio)
                while (stats.blackRatios.size > 600) {
                    stats.blackRatios.removeFirst()
                }
                stats.lumaValues.addLast(sample.meanLuma)
                while (stats.lumaValues.size > 600) {
                    stats.lumaValues.removeFirst()
                }
                if (sample.hash != stats.lastHash || stats.frameCount == 1L) {
                    stats.lastHash = sample.hash
                    stats.uniqueFrames += 1
                }
            }

            when (currentMode) {
                MODE_LATENCY -> decodeLatency(image, t0, t1)
                MODE_SECURE_CONTROL -> accumulateSecure(sample, t0)
                MODE_TARGET -> accumulateTarget(sample, image, t0)
            }

            if (t0 - lastNotificationAt > 2000L) {
                lastNotificationAt = t0
                updateNotification()
            }
        } catch (t: Throwable) {
            LogStore.log("capture", "帧处理异常：${t.javaClass.simpleName}: ${t.message}")
        } finally {
            image.close()
        }
    }

    private fun sampleImage(image: Image): FrameSample {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        val width = image.width
        val height = image.height
        val stepX = max(8, width / 160)
        val stepY = max(8, height / 160)

        var lumaSum = 0L
        var blackCount = 0L
        var samples = 0L
        var hash = -3750763034362895579L // FNV-1a 64 offset basis
        var y = 0
        while (y < height) {
            val rowStart = y * rowStride
            var x = 0
            while (x < width) {
                val index = rowStart + x * pixelStride
                if (index + 2 < buffer.capacity()) {
                    val r = buffer.get(index).toInt() and 0xFF
                    val g = buffer.get(index + 1).toInt() and 0xFF
                    val b = buffer.get(index + 2).toInt() and 0xFF
                    val luma = (r * 299 + g * 587 + b * 114) / 1000
                    lumaSum += luma
                    if (luma < 16) {
                        blackCount += 1
                    }
                    samples += 1
                    hash = hash xor luma.toLong()
                    hash *= 1099511628211L
                }
                x += stepX
            }
            y += stepY
        }
        if (samples == 0L) {
            return FrameSample(0.0, 1.0, 0L)
        }
        return FrameSample(
            meanLuma = lumaSum.toDouble() / samples,
            blackRatio = blackCount.toDouble() / samples,
            hash = hash
        )
    }

    /**
     * 解码全屏时间戳图案：
     * 16 个等宽方块，前 4 个为标记 1/0/1/1，后 12 个为 elapsedRealtime() % 4096 的位。
     */
    private fun decodeLatency(image: Image, t0: Long, t1: Long) {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val rowStride = plane.rowStride
        val pixelStride = plane.pixelStride
        val width = image.width
        val height = image.height
        val blockWidth = width / 16f
        if (blockWidth < 3f) {
            return
        }
        val sampleY = (height * 0.08f).toInt().coerceIn(0, height - 1)
        val bits = IntArray(16)
        for (i in 0 until 16) {
            val x = ((i + 0.5f) * blockWidth).toInt().coerceIn(0, width - 1)
            bits[i] = lumaAt(buffer, rowStride, pixelStride, x, sampleY)
        }
        val marker = intArrayOf(1, 0, 1, 1)
        for (i in 0 until 4) {
            val bit = if (bits[i] >= 128) 1 else 0
            if (bit != marker[i]) {
                return
            }
        }
        var value = 0
        for (i in 0 until 12) {
            if (bits[4 + i] >= 128) {
                value = value or (1 shl i)
            }
        }
        val now = SystemClock.elapsedRealtime()
        val delta = ((now % 4096L) - value.toLong() + 4096L) % 4096L
        if (delta > 1500L) {
            return
        }
        val processing = t1 - t0
        var sampleIndex = 0
        synchronized(stats) {
            stats.latencyCapture.addLast(delta)
            while (stats.latencyCapture.size > 1200) {
                stats.latencyCapture.removeFirst()
            }
            stats.latencyProcessing.addLast(processing)
            while (stats.latencyProcessing.size > 1200) {
                stats.latencyProcessing.removeFirst()
            }
            sampleIndex = stats.latencyCapture.size
        }
        if (sampleIndex == 1 || sampleIndex % 120 == 0) {
            LogStore.log(
                "latency",
                "样本 $sampleIndex：截屏年龄 ${delta}ms，本帧处理 ${processing}ms"
            )
        }
    }

    private fun lumaAt(
        buffer: java.nio.ByteBuffer,
        rowStride: Int,
        pixelStride: Int,
        x: Int,
        y: Int,
    ): Int {
        val index = y * rowStride + x * pixelStride
        if (index + 2 >= buffer.capacity()) {
            return 0
        }
        val r = buffer.get(index).toInt() and 0xFF
        val g = buffer.get(index + 1).toInt() and 0xFF
        val b = buffer.get(index + 2).toInt() and 0xFF
        return (r * 299 + g * 587 + b * 114) / 1000
    }

    private fun accumulateSecure(sample: FrameSample, t0: Long) {
        if (secureEvaluated) {
            return
        }
        if (secureStartAt == 0L) {
            secureStartAt = t0
            LogStore.log("secure", "FLAG_SECURE 对照采集开始（6 秒）")
        }
        if (t0 < secureStartAt + 1000L) {
            return
        }
        secureBlackSum += sample.blackRatio
        secureLumaSum += sample.meanLuma
        secureFrames += 1
        if (t0 >= secureEndAt) {
            finishSecureControl()
        }
    }

    /**
     * 结束 FLAG_SECURE 对照。定时触发，不依赖安全窗口是否还能产出帧。
     */
    private fun finishSecureControl() {
        if (secureEvaluated) {
            return
        }
        secureEvaluated = true
        frameHandler.removeCallbacks(secureEndRunnable)

        val frames = secureFrames
        val avgBlack = if (frames > 0) secureBlackSum / frames else 0.0
        val avgLuma = if (frames > 0) secureLumaSum / frames else 0.0
        val verdict = when {
            frames == 0L -> "PASS：安全窗口期间未产生可见帧（0 帧，符合 FLAG_SECURE 屏蔽特征）"
            frames < 10L -> "UNKNOWN：有效帧不足（$frames 帧），请重试"
            avgBlack >= 0.98 -> "PASS：FLAG_SECURE 内容被正确屏蔽为黑屏"
            else -> "FAIL：FLAG_SECURE 页面仍可被截取（OEM 行为或截图链路异常）"
        }
        ReportStore.put("secure_control", "frames", frames)
        ReportStore.put("secure_control", "avg_black_ratio", DeviceProbe.round2(avgBlack))
        ReportStore.put("secure_control", "avg_luma", DeviceProbe.round2(avgLuma))
        ReportStore.put("secure_control", "result", verdict.substringBefore("："))
        ReportStore.put("secure_control", "verdict", verdict)
        LogStore.log(
            "secure",
            "对照结果：$verdict（帧=$frames，黑屏比=${fmt3(avgBlack)}，亮度=${fmt1(avgLuma)}）"
        )
        currentMode = MODE_GENERAL
        updateNotification()
    }

    private fun applyMode(mode: String) {
        currentMode = mode
        if (mode != MODE_TARGET) {
            frameHandler.removeCallbacks(targetEndRunnable)
            targetEndAt = 0L
        }
        if (mode == MODE_LATENCY) {
            // 只在新一轮延迟采集开始时清空样本；回到主界面后仍要保留结果供导出。
            synchronized(stats) {
                stats.latencyCapture.clear()
                stats.latencyProcessing.clear()
            }
        }
        when (mode) {
            MODE_LATENCY -> LogStore.log("latency", "延迟采集开始（请保持时间戳页面在前台）")
            MODE_SECURE_CONTROL -> {
                secureStartAt = 0L
                secureEndAt = SystemClock.elapsedRealtime() + 6000L
                secureBlackSum = 0.0
                secureLumaSum = 0.0
                secureFrames = 0L
                secureEvaluated = false
                frameHandler.removeCallbacks(secureEndRunnable)
                frameHandler.postDelayed(secureEndRunnable, 6250L)
                LogStore.log("secure", "进入 FLAG_SECURE 对照模式")
            }
            MODE_GENERAL -> {
                if (!secureEvaluated) {
                    secureEvaluated = true
                    frameHandler.removeCallbacks(secureEndRunnable)
                    ReportStore.put("secure_control", "result", "CANCELLED")
                    ReportStore.put("secure_control", "verdict", "CANCELLED：对照页提前退出")
                }
                LogStore.log("capture", "进入普通采集模式")
            }
        }
        updateNotification()
    }

    private fun applyTargetTest(seconds: Int) {
        val safeSeconds = seconds.coerceIn(5, 300)
        currentMode = MODE_TARGET
        targetEvaluated = false
        targetStartAt = SystemClock.elapsedRealtime()
        targetEndAt = targetStartAt + safeSeconds * 1000L
        targetBlackSum = 0.0
        targetLumaSum = 0.0
        targetFrames = 0L
        targetHashes.clear()
        targetSamples.clear()
        targetSaved = 0
        targetNextSaveAt = targetStartAt + 6000L
        // 即使画面静止、MediaProjection 不产帧，也要按墙钟时间结束，避免卡在检测模式。
        frameHandler.removeCallbacks(targetEndRunnable)
        frameHandler.postDelayed(targetEndRunnable, safeSeconds * 1000L + 250L)
        ReportStore.put("target", "duration_sec", safeSeconds)
        ReportStore.put("target", "grace_ms", TARGET_GRACE_MS)
        ReportStore.put("target", "note", "前 2 秒为切到目标应用的宽限期，不计入统计")
        LogStore.log("target", "目标应用检测开始：${safeSeconds}s，请切到目标应用并保持亮屏")
        updateNotification()
    }

    private fun accumulateTarget(sample: FrameSample, image: Image, t0: Long) {
        if (t0 < targetStartAt + TARGET_GRACE_MS) {
            return
        }
        targetBlackSum += sample.blackRatio
        targetLumaSum += sample.meanLuma
        targetFrames += 1
        if (targetHashes.size < 8192) {
            targetHashes.add(sample.hash)
        }

        if (targetSaved < TARGET_SAVE_COUNT && t0 >= targetNextSaveAt) {
            targetSaved += 1
            targetNextSaveAt = t0 + 6000L
            val index = targetSaved
            val bitmap = try {
                imageToBitmap(image)
            } catch (t: Throwable) {
                LogStore.log("target", "帧转 Bitmap 失败：${t.message}")
                null
            }
            if (bitmap != null) {
                saveExecutor.execute {
                    try {
                        val dir = File(getExternalFilesDir(null), "phase0_target")
                        dir.mkdirs()
                        val file = File(dir, "target_${index}_${System.currentTimeMillis()}.png")
                        FileOutputStream(file).use { output ->
                            bitmap.compress(Bitmap.CompressFormat.PNG, 100, output)
                        }
                        targetSamples.add(file.absolutePath)
                        LogStore.log("target", "样本截图已保存：${file.absolutePath}")
                    } catch (t: Throwable) {
                        LogStore.log("target", "保存样本截图失败：${t.message}")
                    } finally {
                        bitmap.recycle()
                    }
                }
            }
        }

        if (t0 >= targetEndAt) {
            finishTargetTest()
        }
    }

    /**
     * 结束目标应用检测。由定时任务或帧回调触发，二者只生效一次。
     */
    private fun finishTargetTest() {
        if (targetEvaluated || targetEndAt == 0L) {
            return
        }
        targetEvaluated = true
        frameHandler.removeCallbacks(targetEndRunnable)
        targetEndAt = 0L

        val frames = targetFrames
        val durationMs = if (targetStartAt > 0L) {
            (SystemClock.elapsedRealtime() - targetStartAt).coerceAtLeast(1L)
        } else {
            1L
        }
        val avgBlack = if (frames > 0) targetBlackSum / frames else 1.0
        val avgLuma = if (frames > 0) targetLumaSum / frames else 0.0
        val unique = targetHashes.size
        @Suppress("DEPRECATION")
        val interactive = (getSystemService(Context.POWER_SERVICE) as PowerManager).isInteractive
        val verdict = when {
            !interactive -> "FAIL：屏幕已熄灭，请保持亮屏后重测"
            frames < 5 -> "FAIL：几乎未收到有效帧（frames=$frames）"
            avgBlack >= 0.98 && unique <= 3 -> "FAIL：疑似 FLAG_SECURE（持续纯黑且画面静止）"
            avgBlack >= 0.98 -> "WARN：画面接近纯黑，可能是深色场景，建议换明亮界面重测"
            avgBlack >= 0.90 -> "WARN：90% 以上采样点为黑色，请人工确认画面内容"
            else -> "PASS：可正常截屏"
        }
        ReportStore.put("target", "frames", frames)
        ReportStore.put("target", "duration_ms", durationMs)
        ReportStore.put(
            "target",
            "effective_fps",
            DeviceProbe.round2(frames * 1000.0 / durationMs.toDouble())
        )
        ReportStore.put("target", "avg_black_ratio", DeviceProbe.round2(avgBlack))
        ReportStore.put("target", "avg_luma", DeviceProbe.round2(avgLuma))
        ReportStore.put("target", "unique_sampled_frames", unique)
        ReportStore.put("target", "screen_interactive", interactive)
        ReportStore.put("target", "result", verdict.substringBefore("："))
        ReportStore.put("target", "verdict", verdict)
        ReportStore.put("target", "samples", JSONArray(targetSamples))
        LogStore.log(
            "target",
            "检测结果：$verdict（帧=$frames，时长=${durationMs}ms，黑屏比=${fmt3(avgBlack)}，" +
                "亮度=${fmt1(avgLuma)}，唯一帧=$unique）"
        )
        notifyResult("目标应用截屏检测完成", verdict)
        currentMode = MODE_GENERAL
        updateNotification()
    }

    private fun imageToBitmap(image: Image): Bitmap {
        val plane = image.planes[0]
        val buffer = plane.buffer
        val pixelStride = plane.pixelStride
        val rowStride = plane.rowStride
        val rowPadding = rowStride - pixelStride * image.width
        val paddedWidth = image.width + rowPadding / pixelStride
        val padded = Bitmap.createBitmap(paddedWidth, image.height, Bitmap.Config.ARGB_8888)
        buffer.rewind()
        padded.copyPixelsFromBuffer(buffer)
        if (paddedWidth == image.width) {
            return padded
        }
        val cropped = Bitmap.createBitmap(padded, 0, 0, image.width, image.height)
        padded.recycle()
        return cropped
    }

    private fun buildStatus(): String {
        synchronized(stats) {
            val elapsed = if (stats.startedAt > 0L) {
                (SystemClock.elapsedRealtime() - stats.startedAt) / 1000.0
            } else {
                0.0
            }
            val fps = if (stats.frameIntervals.isNotEmpty()) {
                1000.0 / stats.frameIntervals.average()
            } else {
                0.0
            }
            val black = stats.blackRatios.lastOrNull() ?: 0.0
            val luma = stats.lumaValues.lastOrNull() ?: 0.0
            val analysis = if (stats.analysisTimes.isNotEmpty()) {
                stats.analysisTimes.average()
            } else {
                0.0
            }
            val latencies = stats.latencyCapture.toList()
            val p50 = percentile(latencies, 50.0)
            val p95 = percentile(latencies, 95.0)
            val maxLatency = latencies.maxOrNull() ?: 0L
            return "运行 ${fmt1(elapsed)}s · ${fmt1(fps)}fps · 帧 ${stats.frameCount} · " +
                "黑屏 ${fmt1(black * 100)}% · 亮度 ${fmt1(luma)} · " +
                "延迟 p50=${p50}ms p95=${p95}ms max=${maxLatency}ms · 处理 ${fmt1(analysis)}ms"
        }
    }

    private fun fillReportNow() {
        synchronized(stats) {
            val fps = if (stats.frameIntervals.isNotEmpty()) {
                1000.0 / stats.frameIntervals.average()
            } else {
                0.0
            }
            val latencies = stats.latencyCapture.toList()
            ReportStore.put("capture", "running", true)
            ReportStore.put("capture", "mode", currentMode)
            ReportStore.put("capture", "status", buildStatus())
            ReportStore.put("capture", "frames", stats.frameCount)
            ReportStore.put("capture", "fps", DeviceProbe.round2(fps))
            ReportStore.put("capture", "unique_sampled_frames", stats.uniqueFrames)
            ReportStore.put(
                "capture",
                "avg_black_ratio",
                DeviceProbe.round2(stats.blackRatios.toList().takeLast(300).average().takeIf { !it.isNaN() } ?: 0.0)
            )
            ReportStore.put(
                "capture",
                "avg_luma",
                DeviceProbe.round2(stats.lumaValues.toList().takeLast(300).average().takeIf { !it.isNaN() } ?: 0.0)
            )
            ReportStore.put(
                "capture",
                "avg_analysis_ms",
                DeviceProbe.round2(stats.analysisTimes.toList().takeLast(300).average().takeIf { !it.isNaN() } ?: 0.0)
            )
            ReportStore.put("capture", "latency_samples", latencies.size)
            ReportStore.put("capture", "latency_p50_ms", percentile(latencies, 50.0))
            ReportStore.put("capture", "latency_p95_ms", percentile(latencies, 95.0))
            ReportStore.put("capture", "latency_max_ms", latencies.maxOrNull() ?: 0L)
            ReportStore.put(
                "capture",
                "processing_p50_ms",
                percentile(stats.latencyProcessing.toList(), 50.0)
            )
        }
        if (targetEndAt > SystemClock.elapsedRealtime()) {
            ReportStore.put("target", "test_in_progress", true)
        }
    }

    private fun percentile(values: List<Long>, percentile: Double): Long {
        if (values.isEmpty()) {
            return 0L
        }
        val sorted = values.sorted()
        val index = ((percentile / 100.0) * (sorted.size - 1)).toInt().coerceIn(0, sorted.size - 1)
        return sorted[index]
    }

    private fun createChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        if (manager.getNotificationChannel(CHANNEL_ID) == null) {
            val channel = NotificationChannel(
                CHANNEL_ID,
                "Phase0 截屏探针",
                NotificationManager.IMPORTANCE_LOW
            )
            channel.description = "MediaProjection 截屏探针前台服务"
            manager.createNotificationChannel(channel)
        }
    }

    private fun buildNotification(title: String, text: String, ongoing: Boolean): Notification {
        val contentIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val builder = Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_stat_probe)
            .setContentTitle(title)
            .setContentText(text)
            .setContentIntent(contentIntent)
            .setOngoing(ongoing)
        if (!ongoing) {
            builder.setAutoCancel(true)
        }
        return builder.build()
    }

    private fun startForegroundCompat(text: String) {
        val notification = buildNotification("Phase0 截屏探针", text, true)
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(
                NOTIF_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION
            )
        } else {
            startForeground(NOTIF_ID, notification)
        }
    }

    private fun stopForegroundCompat() {
        if (Build.VERSION.SDK_INT >= 24) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
    }

    private fun updateNotification() {
        if (!isRunning) {
            return
        }
        val text = when (currentMode) {
            MODE_TARGET -> {
                val remain = ((targetEndAt - SystemClock.elapsedRealtime()) / 1000L).coerceAtLeast(0L)
                "目标应用检测中，剩余 ${remain}s（请保持目标应用在前台）"
            }
            MODE_LATENCY -> "延迟采集中（请保持时间戳页面在前台）"
            MODE_SECURE_CONTROL -> "FLAG_SECURE 对照采集中"
            else -> "截屏探针运行中"
        }
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(NOTIF_ID, buildNotification("Phase0 截屏探针", text, true))
    }

    private fun notifyResult(title: String, text: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.notify(RESULT_NOTIF_ID, buildNotification(title, text.take(120), false))
    }

    private fun releaseAll() {
        isRunning = false
        instance = null
        try {
            imageReader?.setOnImageAvailableListener(null, null)
        } catch (_: Throwable) {
        }
        try {
            virtualDisplay?.release()
        } catch (_: Throwable) {
        }
        try {
            imageReader?.close()
        } catch (_: Throwable) {
        }
        try {
            projection?.stop()
        } catch (_: Throwable) {
        }
        imageReader = null
        virtualDisplay = null
        projection = null
        ReportStore.put("capture", "running", false)
    }

    override fun onDestroy() {
        releaseAll()
        try {
            saveExecutor.shutdownNow()
        } catch (_: Throwable) {
        }
        try {
            handlerThread.quitSafely()
        } catch (_: Throwable) {
        }
        super.onDestroy()
    }

    private fun fmt1(value: Double): String = String.format(Locale.US, "%.1f", value)

    private fun fmt3(value: Double): String = String.format(Locale.US, "%.3f", value)
}
