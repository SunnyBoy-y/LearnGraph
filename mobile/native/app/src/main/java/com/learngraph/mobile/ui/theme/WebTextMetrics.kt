package com.learngraph.mobile.ui.theme

import android.os.Build
import android.text.TextDirectionHeuristics
import android.text.TextPaint
import android.text.TextShaper
import android.util.Log
import androidx.annotation.RequiresApi
import androidx.compose.ui.text.TextLayoutResult
import com.learngraph.mobile.BuildConfig
import org.json.JSONArray
import org.json.JSONObject

/** Debug log only: no added screen, gesture, service, or user text in logs. */
internal fun reportWebTextMetrics(tag: String, text: String, paint: TextPaint, layout: TextLayoutResult?,
    plan: WebCssLineBox.Plan, lineHeightPx: Float, density: Float, fontScale: Float) {
    if (!BuildConfig.DEBUG) return
    val fm = paint.fontMetrics
    val record = JSONObject().apply {
        put("tag", tag); put("characters", text.length); put("density", density); put("fontScale", fontScale)
        put("fontSizePx", paint.textSize); put("lineHeightPx", lineHeightPx)
        put("primaryAscentPx", -fm.ascent); put("primaryDescentPx", fm.descent)
        put("cssFirstBaselinePx", plan.firstBaselinePx); put("nativeFirstBaselinePx", layout?.firstBaseline)
        put("childOffsetPx", plan.childOffsetPx); put("cssHeightPx", plan.heightPx)
        put("nativeHeightPx", layout?.size?.height)
        put("nativeBaselinesPx", JSONArray(layout?.let { result -> (0 until result.lineCount).map(result::getLineBaseline) }.orEmpty()))
        if (Build.VERSION.SDK_INT >= 31) put("glyphFonts", actualGlyphFonts(text.take(256), paint))
    }
    Log.d("LGWebTextMetrics", record.toString())
}

@RequiresApi(31)
private fun actualGlyphFonts(text: String, paint: TextPaint): JSONArray {
    val fonts = linkedMapOf<String, JSONObject>()
    runCatching {
        TextShaper.shapeText(text, 0, text.length, TextDirectionHeuristics.FIRSTSTRONG_LTR, paint) { _, _, glyphs, _ ->
            for (index in 0 until glyphs.glyphCount()) {
                val font = glyphs.getFont(index)
                val key = "${font.file?.path}|${font.ttcIndex}|${font.style.weight}|${font.axes?.joinToString()}"
                fonts.getOrPut(key) { JSONObject().apply {
                    put("file", font.file?.path); put("ttcIndex", font.ttcIndex); put("weight", font.style.weight)
                    put("axes", JSONArray(font.axes?.map { "${it.tag}=${it.styleValue}" }.orEmpty()))
                } }
            }
        }
    }
    return JSONArray(fonts.values.toList())
}
