package com.learngraph.mobile.util

import android.util.Log
import com.learngraph.mobile.BuildConfig
import org.json.JSONObject

/** Local debug measurements contain fixture labels and geometry, never account state. */
object UiParityDiagnostics {
    fun web(raw: String) {
        if (!BuildConfig.DEBUG || raw.length > 256_000) return
        val value = runCatching { JSONObject(raw) }.getOrNull() ?: return
        if (value.optString("kind") !in setOf("typography", "motion")) return
        val id = value.optString("id")
        if (!id.matches(Regex("[a-z0-9_-]{1,80}"))) return
        val parts = value.toString().chunked(1800)
        parts.forEachIndexed { index, part ->
            Log.i("LGUiParity", JSONObject().put("id", id).put("part", index).put("total", parts.size).put("data", part).toString())
        }
    }
}
