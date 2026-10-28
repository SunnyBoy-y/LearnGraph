package com.learngraph.mobile.ui.theme

import kotlin.math.ceil
import kotlin.math.floor
import kotlin.math.truncate

/** Android Blink's primary-font metrics and physical-pixel leading distribution. */
internal object WebCssLineBox {
    data class Plan(val heightPx: Int, val firstBaselinePx: Float, val childOffsetPx: Float)

    fun plan(lineHeightPx: Float, ascentPx: Float, descentPx: Float, nativeFirstBaselinePx: Float, lines: Int): Plan {
        require(lineHeightPx.isFinite() && lineHeightPx >= 0f)
        require(ascentPx.isFinite() && ascentPx >= 0f && descentPx.isFinite() && descentPx >= 0f)
        require(nativeFirstBaselinePx.isFinite() && lines > 0)
        // Blink FontMetrics::AscentDescentWithHacks rounds the Skia metrics,
        // then borrows one ascent pixel if Android subpixel rendering rounded
        // descent down. These are device pixels, before dividing by CSS DPR.
        // chromium/src/third_party/blink/renderer/platform/fonts/font_metrics.cc
        var ascent = floor(ascentPx.toDouble() + .5)
        var descent = floor(descentPx.toDouble() + .5)
        if (descent < descentPx && ascent >= 1) {
            ascent -= 1
            descent += 1
        }
        // LayoutUnit is 26.6 fixed point. Integer division truncates toward
        // zero; CalculateLeadingSpace then floors ascent's half-leading.
        // Its descent side receives the remainder, including negative leading.
        // chromium/src/third_party/blink/renderer/core/layout/inline/line_utils.cc
        val lineHeightUnits = truncate(lineHeightPx.toDouble() * 64)
        val halfLeadingUnits = truncate((lineHeightUnits - (ascent + descent) * 64) / 2)
        val baseline = (ascent + floor(halfLeadingUnits / 64)).toFloat()
        return Plan(ceil(lineHeightPx.toDouble() * lines).toInt(), baseline, baseline - nativeFirstBaselinePx)
    }
}
