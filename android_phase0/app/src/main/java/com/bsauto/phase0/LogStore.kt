package com.bsauto.phase0

import android.util.Log
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/**
 * 轻量级日志仓库：所有探针写入同一时间线，主界面与测试页共享。
 */
object LogStore {
    private const val MAX_LINES = 3000
    private val formatter = DateTimeFormatter.ofPattern("HH:mm:ss.SSS")
    private val lines = ArrayDeque<String>()

    @Volatile
    var version: Long = 0L
        private set

    fun log(tag: String, message: String) {
        val line = "${LocalTime.now().format(formatter)} [$tag] $message"
        synchronized(lines) {
            lines.addLast(line)
            while (lines.size > MAX_LINES) {
                lines.removeFirst()
            }
            version += 1
        }
        Log.i("Phase0", line)
    }

    fun snapshot(): String = synchronized(lines) { lines.joinToString("\n") }

    fun tail(maxChars: Int = 12000): String = snapshot().takeLast(maxChars)

    fun clear() {
        synchronized(lines) {
            lines.clear()
            version += 1
        }
    }
}
