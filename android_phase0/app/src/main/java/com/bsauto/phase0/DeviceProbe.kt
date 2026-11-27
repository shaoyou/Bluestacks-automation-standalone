package com.bsauto.phase0

import android.content.Context
import android.os.Build
import android.provider.Settings
import android.view.WindowManager
import kotlin.math.roundToLong

object DeviceProbe {
    fun round2(value: Double): Double =
        if (value.isFinite()) (value * 100.0).roundToLong() / 100.0 else 0.0

    fun screenSize(context: Context): Pair<Int, Int> {
        val wm = context.getSystemService(WindowManager::class.java)
        if (Build.VERSION.SDK_INT >= 30 && wm != null) {
            val bounds = wm.currentWindowMetrics.bounds
            return bounds.width() to bounds.height()
        }
        val dm = context.resources.displayMetrics
        return dm.widthPixels to dm.heightPixels
    }

    fun isAccessibilityServiceEnabled(context: Context): Boolean {
        val enabled = Settings.Secure.getString(
            context.contentResolver,
            Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
        ) ?: return false
        val full = "${context.packageName}/com.bsauto.phase0.GestureProbeService"
        val short = "${context.packageName}/.GestureProbeService"
        return enabled.split(':').any {
            it.equals(full, ignoreCase = true) || it.equals(short, ignoreCase = true)
        }
    }

    fun summary(context: Context): String {
        val (w, h) = screenSize(context)
        val dm = context.resources.displayMetrics
        @Suppress("DEPRECATION")
        val refresh = context.getSystemService(WindowManager::class.java)?.defaultDisplay?.refreshRate ?: 0f
        return "${Build.MANUFACTURER} ${Build.MODEL} · Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT}) · " +
            "${w}×${h} @ ${round2(refresh.toDouble())}Hz · ${dm.densityDpi}dpi · ${Build.SUPPORTED_ABIS.firstOrNull() ?: "-"}"
    }

    fun record(context: Context) {
        val (w, h) = screenSize(context)
        val dm = context.resources.displayMetrics
        @Suppress("DEPRECATION")
        val refresh = context.getSystemService(WindowManager::class.java)?.defaultDisplay?.refreshRate ?: 0f
        ReportStore.put("device", "manufacturer", Build.MANUFACTURER)
        ReportStore.put("device", "model", Build.MODEL)
        ReportStore.put("device", "android_release", Build.VERSION.RELEASE)
        ReportStore.put("device", "sdk_int", Build.VERSION.SDK_INT)
        ReportStore.put("device", "abi", Build.SUPPORTED_ABIS.firstOrNull() ?: "")
        ReportStore.put("device", "screen_width", w)
        ReportStore.put("device", "screen_height", h)
        ReportStore.put("device", "density_dpi", dm.densityDpi)
        ReportStore.put("device", "refresh_hz", round2(refresh.toDouble()))
        ReportStore.put("device", "accessibility_enabled", isAccessibilityServiceEnabled(context))
        ReportStore.put("device", "package", context.packageName)
    }
}
