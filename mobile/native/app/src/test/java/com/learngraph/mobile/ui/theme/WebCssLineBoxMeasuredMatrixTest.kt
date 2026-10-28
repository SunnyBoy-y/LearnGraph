package com.learngraph.mobile.ui.theme

import org.junit.Assert.assertEquals
import org.junit.Test
import org.junit.runner.RunWith
import org.junit.runners.Parameterized

/** Original same-device Web DOM samples: logs/native-v0184-web-metrics.json, DPR 3. */
@RunWith(Parameterized::class)
class WebCssLineBoxMeasuredMatrixTest(
    private val id: String,
    private val fontSizeCss: Float,
    private val weight: Int,
    private val lineHeightCss: Float,
    private val measuredBaselineCss: Float,
) {
    @Test fun matchesMeasuredDomBaselineForTheUnmodifiedWebGeistSourceMetrics() {
        val dpr = 3f
        val sizePx = fontSizeCss * dpr
        // Font-source guard preserves 1005/-295 metrics in every shipped weight.
        val box = WebCssLineBox.plan(lineHeightCss * dpr, sizePx * 1.005f, sizePx * .295f, 0f, 1)
        assertEquals("$id font=$fontSizeCss weight=$weight", measuredBaselineCss, box.firstBaselinePx / dpr, .0001f)
    }

    companion object {
        @JvmStatic
        @Parameterized.Parameters(name = "{0}: {1}px/{3}px weight={2}")
        fun samples(): Collection<Array<Any>> = listOf(
            arrayOf("empty_eyebrow", 12f, 500, 18f, 13f),
            arrayOf("empty_title", 26f, 650, 31.2f, 24.3333f),
            arrayOf("suggestion", 14f, 400, 19.6f, 14.3333f),
            arrayOf("toolbar", 13f, 500, 13f, 11f),
            arrayOf("model_trigger", 13f, 650, 17.3333f, 13f),
            arrayOf("composer", 15f, 400, 22.5f, 16f),
            arrayOf("sidebar_heading", 17f, 700, 24.2857f, 17.6667f),
            arrayOf("sidebar_nav", 14f, 400, 20f, 14.6667f),
            arrayOf("avatar", 12f, 600, 16f, 12f),
        )
    }
}
