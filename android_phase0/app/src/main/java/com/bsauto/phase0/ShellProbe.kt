package com.bsauto.phase0

import android.content.Context
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.os.ParcelFileDescriptor
import android.os.SystemClock
import moe.shizuku.server.IRemoteProcess
import moe.shizuku.server.IShizukuService
import rikka.shizuku.Shizuku
import java.io.InputStream
import java.io.OutputStream
import java.util.Collections
import java.util.concurrent.TimeUnit

data class CommandResult(
    val ok: Boolean,
    val exitCode: Int,
    val timedOut: Boolean,
    val output: String,
)

/**
 * Root / Shizuku / getevent 探测。
 *
 * 说明：非 root 应用无法读取 /dev/input；Shizuku 以 shell 身份运行命令，
 * 在多数机型上可执行 `getevent`，这是端上录制触摸的关键路径。
 */
object ShellProbe {
    const val SHIZUKU_PACKAGE = "moe.shizuku.privileged.api"
    private const val GETEVENT = "/system/bin/getevent"
    private const val SCREENCAP = "/system/bin/screencap"

    @Volatile
    private var rootCache: Boolean? = null

    fun detectRoot(): CommandResult {
        return try {
            val process = ProcessBuilder("su", "-c", "id")
                .redirectErrorStream(true)
                .start()
            readProcess(process, 5)
        } catch (t: Throwable) {
            CommandResult(false, -1, false, "su 不可用：${t.javaClass.simpleName}: ${t.message}")
        }
    }

    fun hasRoot(refresh: Boolean = false): Boolean {
        if (!refresh) {
            rootCache?.let { return it }
        }
        val result = detectRoot()
        val ok = result.output.contains("uid=0")
        rootCache = ok
        return ok
    }

    fun isShizukuInstalled(context: Context): Boolean {
        return try {
            context.packageManager.getPackageInfo(SHIZUKU_PACKAGE, 0)
            true
        } catch (_: PackageManager.NameNotFoundException) {
            false
        }
    }

    fun shizukuReady(): Boolean {
        return try {
            Shizuku.pingBinder() && Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
        } catch (_: Throwable) {
            false
        }
    }

    fun detectShizuku(context: Context): String {
        val installed = isShizukuInstalled(context)
        ReportStore.put("record", "shizuku_installed", installed)
        if (!installed) {
            ReportStore.put("record", "shizuku_running", false)
            return "未安装 Shizuku（可从 shizuku.rikka.app 安装后通过 USB ADB 激活）"
        }
        val running = try {
            Shizuku.pingBinder()
        } catch (_: Throwable) {
            false
        }
        ReportStore.put("record", "shizuku_running", running)
        if (!running) {
            return "Shizuku 已安装但未运行（需启动 Shizuku 并完成激活）"
        }
        val version = try {
            Shizuku.getVersion()
        } catch (_: Throwable) {
            -1
        }
        val granted = try {
            Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED
        } catch (_: Throwable) {
            false
        }
        ReportStore.put("record", "shizuku_version", version)
        ReportStore.put("record", "shizuku_permission", granted)
        return "Shizuku v$version 运行中，权限${if (granted) "已授予" else "未授予"}"
    }

    /** 返回 true 表示已发起权限请求，需要在 Activity 注册结果监听。 */
    fun requestShizukuPermission(requestCode: Int): Boolean {
        return try {
            if (!Shizuku.pingBinder()) return false
            if (Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED) return false
            Shizuku.requestPermission(requestCode)
            true
        } catch (t: Throwable) {
            LogStore.log("shizuku", "请求权限失败：${t.javaClass.simpleName}: ${t.message}")
            false
        }
    }

    fun runShellCommand(command: String, timeoutSec: Long = 10): CommandResult {
        return try {
            when {
                hasRoot() -> {
                    val process = ProcessBuilder("su", "-c", command)
                        .redirectErrorStream(true)
                        .start()
                    readProcess(process, timeoutSec)
                }
                shizukuReady() -> {
                    readProcess(startShizukuProcess(command), timeoutSec)
                }
                else -> CommandResult(
                    false,
                    -1,
                    false,
                    "无 root，且 Shizuku 未运行/未授权"
                )
            }
        } catch (t: Throwable) {
            CommandResult(false, -1, false, "执行失败：${t.javaClass.simpleName}: ${t.message}")
        }
    }

    /**
     * 读取 `getevent -lp`，确认是否存在可用的多点触摸设备。
     */
    fun probeGetEventDevices(): String {
        val result = runShellCommand("$GETEVENT -lp", 12)
        val lines = result.output.lineSequence().toList()
        val touchDevices = lines.filter {
            it.contains("ABS_MT_POSITION_X") || it.contains("ABS_MT_POSITION_Y")
        }.size
        val addDevices = lines.count { it.trimStart().startsWith("add device") }
        val fallbackDevices = lines.count { it.contains(" ABS_X ") && it.contains(" ABS_Y ") }
        val ok = result.ok && (touchDevices > 0 || fallbackDevices > 0)
        ReportStore.put("record", "getevent_devices_ok", ok)
        ReportStore.put("record", "getevent_input_devices", addDevices)
        ReportStore.put("record", "getevent_mt_devices", touchDevices)
        ReportStore.put("record", "getevent_fallback_devices", fallbackDevices)
        val summary = if (ok) {
            "PASS：检测到触摸设备（input=$addDevices，MT=$touchDevices，单点=$fallbackDevices）"
        } else {
            "FAIL：未检测到触摸设备（exit=${result.exitCode}, timeout=${result.timedOut}）"
        }
        if (!result.output.isBlank()) {
            ReportStore.put("record", "getevent_devices_output", result.output.take(4000))
        }
        val detail = if (!ok && result.output.isNotBlank()) "\n" + result.output.take(1200) else ""
        return summary + detail
    }

    /**
     * 通过 root 或 Shizuku 运行 `getevent -lt` 若干秒，统计是否能收到触摸事件。
     * 调用前请提示用户在这几秒内连续点击/滑动屏幕。
     */
    fun sampleGetEvent(seconds: Int, onResult: (String) -> Unit) {
        Thread({
            val mode = when {
                hasRoot() -> "root"
                shizukuReady() -> "shizuku"
                else -> null
            }
            if (mode == null) {
                val message = "无可用的 shell 权限（root / Shizuku 均不可用），无法读取 /dev/input"
                ReportStore.put("record", "getevent_sample_result", "FAIL：${message}")
                onResult(message)
                return@Thread
            }
            LogStore.log("record", "[$mode] 开始 $seconds 秒 getevent 采样，请操作屏幕")
            val process = try {
                if (mode == "root") {
                    ProcessBuilder("su", "-c", "$GETEVENT -lt")
                        .redirectErrorStream(true)
                        .start()
                } else {
                    startShizukuProcess("$GETEVENT -lt")
                }
            } catch (t: Throwable) {
                val message = "启动 getevent 失败：${t.javaClass.simpleName}: ${t.message}"
                ReportStore.put("record", "getevent_sample_result", "FAIL：${message}")
                onResult(message)
                return@Thread
            }
            val lines = Collections.synchronizedList(mutableListOf<String>())
            val reader = Thread({
                try {
                    process.inputStream.bufferedReader().use { buffered ->
                        buffered.forEachLine { line ->
                            if (lines.size < 10000) {
                                lines.add(line)
                            }
                        }
                    }
                } catch (_: Throwable) {
                    // 进程被主动销毁时读取线程会异常退出，属预期。
                }
            }, "getevent-reader")
            reader.start()
            Thread.sleep(seconds * 1000L)
            try {
                process.destroy()
            } catch (_: Throwable) {
            }
            reader.join(1200)

            val total = lines.size
            val touchCount = synchronized(lines) {
                lines.count {
                    it.contains("ABS_MT_POSITION_X") ||
                        it.contains("ABS_MT_POSITION_Y") ||
                        it.contains("ABS_X") ||
                        it.contains("ABS_Y") ||
                        it.contains("BTN_TOUCH")
                }
            }
            val firstLines = synchronized(lines) { lines.take(8).joinToString("\n") }
            val verdict = if (touchCount > 0) "PASS" else "FAIL"
            ReportStore.put("record", "getevent_sample_mode", mode)
            ReportStore.put("record", "getevent_sample_seconds", seconds)
            ReportStore.put("record", "getevent_sample_lines", total)
            ReportStore.put("record", "getevent_sample_touch_lines", touchCount)
            ReportStore.put("record", "getevent_sample_result", verdict)
            val summary = "[$mode] ${seconds}s 内收到 $total 行事件，触摸相关 $touchCount 行 → $verdict" +
                if (firstLines.isBlank()) "" else "\n样本：\n$firstLines"
            onResult(summary)
        }, "getevent-sample").start()
    }

    /**
     * 兜底截屏：绕过 MediaProjection，直接以 root/Shizuku 身份执行 `screencap -p`。
     * 对封禁 MediaProjection 的 OEM（如部分华为 ROM）这是唯一可用的端上截图路径，
     * 代价是每帧要启动一次进程，帧率通常只有 2~5fps。
     */
    fun probeShellScreencap(samples: Int, onResult: (String) -> Unit) {
        Thread({
            val mode = when {
                hasRoot() -> "root"
                shizukuReady() -> "shizuku"
                else -> null
            }
            if (mode == null) {
                val message = "FAIL：无 root / Shizuku，无法执行 shell screencap"
                ReportStore.put("screencap_shell", "result", "FAIL")
                ReportStore.put("screencap_shell", "reason", message)
                onResult(message)
                return@Thread
            }
            var success = 0
            var totalMs = 0L
            var width = 0
            var height = 0
            var lastError = ""
            val runs = samples.coerceIn(1, 20)
            for (index in 1..runs) {
                try {
                    val (bytes, elapsedMs) = runScreencapOnce(mode)
                    val bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
                    if (bitmap == null) {
                        lastError = "第 $index 帧 PNG 解码失败（${bytes.size} bytes）"
                    } else {
                        success += 1
                        totalMs += elapsedMs
                        width = bitmap.width
                        height = bitmap.height
                        bitmap.recycle()
                        LogStore.log(
                            "screencap",
                            "[$mode] 第 $index/$runs 帧 ${width}×${height}，${elapsedMs}ms，${bytes.size} bytes"
                        )
                    }
                } catch (t: Throwable) {
                    lastError = "第 $index 帧失败：${t.javaClass.simpleName}: ${t.message}"
                }
            }
            val avgMs = if (success > 0) totalMs / success else 0L
            val fps = if (totalMs > 0) success * 1000.0 / totalMs else 0.0
            val verdict = when {
                success == 0 -> "FAIL：0/$runs 帧成功 ${lastError}"
                success < runs -> "WARN：$success/$runs 帧成功，平均 ${avgMs}ms/帧（约 ${"%.1f".format(fps)}fps）"
                else -> "PASS：$success/$runs 帧，${width}×${height}，平均 ${avgMs}ms/帧（约 ${"%.1f".format(fps)}fps）"
            }
            ReportStore.put("screencap_shell", "mode", mode)
            ReportStore.put("screencap_shell", "samples", runs)
            ReportStore.put("screencap_shell", "success", success)
            ReportStore.put("screencap_shell", "width", width)
            ReportStore.put("screencap_shell", "height", height)
            ReportStore.put("screencap_shell", "avg_ms", avgMs)
            ReportStore.put("screencap_shell", "fps", DeviceProbe.round2(fps))
            ReportStore.put("screencap_shell", "result", verdict.substringBefore("："))
            ReportStore.put("screencap_shell", "verdict", verdict)
            LogStore.log("screencap", verdict)
            onResult(verdict)
        }, "shell-screencap").start()
    }

    private fun runScreencapOnce(mode: String): Pair<ByteArray, Long> {
        val start = SystemClock.elapsedRealtime()
        val process = if (mode == "root") {
            ProcessBuilder("su", "-c", "$SCREENCAP -p").start()
        } else {
            startShizukuProcess("$SCREENCAP -p")
        }
        var bytes = ByteArray(0)
        val reader = Thread({
            try {
                bytes = process.inputStream.use { it.readBytes() }
            } catch (_: Throwable) {
            }
        }, "screencap-reader")
        reader.start()
        val finished = try {
            process.waitFor(8, TimeUnit.SECONDS)
        } catch (_: Throwable) {
            false
        }
        if (!finished) {
            try {
                process.destroy()
            } catch (_: Throwable) {
            }
        }
        reader.join(2000)
        val elapsed = SystemClock.elapsedRealtime() - start
        if (bytes.isEmpty()) {
            error("screencap 输出为空（timeout=${!finished}）")
        }
        return bytes to elapsed
    }

    /**
     * 一次性探测录制链路：root、Shizuku、getevent 设备。
     */
    fun recordCapability(context: Context): String {
        val rootResult = detectRoot()
        val rootOk = rootResult.output.contains("uid=0")
        rootCache = rootOk
        ReportStore.put("record", "root_available", rootOk)
        ReportStore.put("record", "root_output", rootResult.output.take(1000))
        val shizukuSummary = detectShizuku(context)
        val devicesSummary = probeGetEventDevices()
        val path = when {
            rootOk -> "root"
            shizukuReady() -> "shizuku"
            else -> "none"
        }
        ReportStore.put("record", "recording_path", path)
        LogStore.log("record", "root=${if (rootOk) "可用" else "不可用"}")
        LogStore.log("record", shizukuSummary)
        LogStore.log("record", devicesSummary)
        return buildString {
            append("记录路径：")
            append(
                when (path) {
                    "root" -> "root（可读 getevent + 可 sendevent）"
                    "shizuku" -> "Shizuku/shell（可读 getevent；注入建议走无障碍）"
                    else -> "不可用（无法录制触摸）"
                }
            )
            append("\n")
            append(shizukuSummary)
            append("\n")
            append(devicesSummary)
        }
    }

    /**
     * Shizuku 13.x 不再公开 `newProcess`，这里通过其 AIDL 接口直接调用服务端开进程。
     * 运行身份为 shell（uid 2000），权限仍需用户授予 Shizuku 权限。
     */
    private fun startShizukuProcess(command: String): Process {
        val binder = Shizuku.getBinder() ?: error("Shizuku binder 不可用")
        val service = IShizukuService.Stub.asInterface(binder)
        val remote = service.newProcess(arrayOf("sh", "-c", command), null, null)
        return ShizukuProcess(remote)
    }

    private fun readProcess(process: Process, timeoutSec: Long): CommandResult {
        val output = StringBuilder()
        val err = StringBuilder()
        val outThread = Thread({
            try {
                process.inputStream.bufferedReader().use { reader ->
                    reader.forEachLine { line ->
                        synchronized(output) { output.appendLine(line) }
                    }
                }
            } catch (_: Throwable) {
            }
        }, "process-stdout")
        val errThread = Thread({
            try {
                process.errorStream.bufferedReader().use { reader ->
                    reader.forEachLine { line ->
                        synchronized(err) { err.appendLine(line) }
                    }
                }
            } catch (_: Throwable) {
            }
        }, "process-stderr")
        outThread.start()
        errThread.start()
        val finished = try {
            process.waitFor(timeoutSec, TimeUnit.SECONDS)
        } catch (_: Throwable) {
            false
        }
        if (!finished) {
            process.destroyForcibly()
        }
        outThread.join(800)
        errThread.join(800)
        val exit = try {
            if (finished) process.exitValue() else -1
        } catch (_: Throwable) {
            -1
        }
        val text = synchronized(output) { output.toString() } +
            synchronized(err) { err.toString() }
        return CommandResult(finished && exit == 0, exit, !finished, text.trim())
    }
}

/**
 * 将 Shizuku 的 IRemoteProcess 包装成标准 Process，便于复用 readProcess。
 */
private class ShizukuProcess(private val remote: IRemoteProcess) : Process() {
    private val stdin: OutputStream by lazy {
        ParcelFileDescriptor.AutoCloseOutputStream(remote.outputStream)
    }
    private val stdout: InputStream by lazy {
        ParcelFileDescriptor.AutoCloseInputStream(remote.inputStream)
    }
    private val stderr: InputStream by lazy {
        ParcelFileDescriptor.AutoCloseInputStream(remote.errorStream)
    }

    override fun getOutputStream(): OutputStream = stdin

    override fun getInputStream(): InputStream = stdout

    override fun getErrorStream(): InputStream = stderr

    override fun waitFor(): Int = try {
        remote.waitFor()
    } catch (_: Throwable) {
        -1
    }

    override fun waitFor(timeout: Long, unit: TimeUnit): Boolean = try {
        remote.waitForTimeout(timeout, unit.toString())
    } catch (_: Throwable) {
        false
    }

    override fun exitValue(): Int = try {
        remote.exitValue()
    } catch (_: Throwable) {
        -1
    }

    override fun destroy() {
        try {
            remote.destroy()
        } catch (_: Throwable) {
        }
    }
}
