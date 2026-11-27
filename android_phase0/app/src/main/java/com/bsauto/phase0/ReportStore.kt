package com.bsauto.phase0

import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Phase 0 报告收集器：各探针按 section/key 写入，最后导出为 JSON。
 */
object ReportStore {
    private val sections = LinkedHashMap<String, MutableMap<String, Any?>>()
    private val notes = mutableListOf<String>()
    private val startedAt = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())

    @Synchronized
    fun put(section: String, key: String, value: Any?) {
        sections.getOrPut(section) { LinkedHashMap() }[key] = value
    }

    @Synchronized
    fun get(section: String, key: String): Any? = sections[section]?.get(key)

    @Synchronized
    fun getString(section: String, key: String): String? = sections[section]?.get(key)?.toString()

    @Synchronized
    fun note(text: String) {
        notes.add(text)
    }

    @Synchronized
    fun toJson(): String {
        val root = JSONObject()
        root.put("probe", "android_phase0")
        root.put("started_at", startedAt)
        root.put(
            "generated_at",
            SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(Date())
        )
        val sectionsJson = JSONObject()
        for ((name, values) in sections) {
            val obj = JSONObject()
            for ((key, value) in values) {
                obj.put(key, if (value == null) JSONObject.NULL else value)
            }
            sectionsJson.put(name, obj)
        }
        root.put("sections", sectionsJson)
        root.put("notes", JSONArray(notes))
        root.put("log_tail", LogStore.tail())
        return root.toString(2)
    }

    @Synchronized
    fun reset() {
        sections.clear()
        notes.clear()
    }
}
