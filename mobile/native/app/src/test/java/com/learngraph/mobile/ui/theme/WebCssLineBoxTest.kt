package com.learngraph.mobile.ui.theme

import org.junit.Assert.*
import org.junit.Test

class WebCssLineBoxTest {
    @Test fun equalHalfLeadingUsesPrimaryMetricsInsteadOfFallbackGlyphHeight() {
        val latin = WebCssLineBox.plan(24f, 16.08f, 4.72f, 16.08f, 1)
        val fallback = WebCssLineBox.plan(24f, 16.08f, 4.72f, 18.56f, 1)
        assertEquals(24, latin.heightPx)
        assertEquals(17f, latin.firstBaselinePx, .0001f)
        assertEquals(latin.firstBaselinePx, fallback.firstBaselinePx, 0f)
        assertEquals(.92f, latin.childOffsetPx, .0001f)
        assertEquals(-1.56f, fallback.childOffsetPx, .0001f)
    }

    @Test fun negativeLeadingDoesNotExpandTheCssBoxOrClampItsBaseline() {
        val box = WebCssLineBox.plan(31.2f, 26.13f, 7.67f, 26.13f, 1)
        assertEquals(32, box.heightPx)
        assertEquals(24f, box.firstBaselinePx, .0001f)
        assertEquals(-2.13f, box.childOffsetPx, .0001f)
    }

    @Test fun fractionalLineHeightRoundsTheWholeBoxOnceButBaselineUsesPhysicalPixels() {
        assertEquals(135, WebCssLineBox.plan(67.5f, 45f, 10f, 45f, 2).heightPx)
        val logical = WebCssLineBox.plan(22.5f, 15.075f, 4.425f, 15.075f, 2)
        val pixels = WebCssLineBox.plan(67.5f, 45.225f, 13.275f, 45.225f, 2)
        assertEquals(15f, logical.firstBaselinePx, 0f)
        assertEquals(48f, pixels.firstBaselinePx, 0f)
        // Blink snaps physical metrics; scaling a CSS-pixel result is incorrect.
        assertNotEquals(logical.firstBaselinePx * 3, pixels.firstBaselinePx, 0f)
    }

    @Test fun descentBorrowsAnAscentPixelOnlyWhenRoundingDroppedPartOfTheDescent() {
        assertEquals(23f, WebCssLineBox.plan(30f, 20f, 4f, 20f, 1).firstBaselinePx, 0f)
        assertEquals(22f, WebCssLineBox.plan(30f, 20f, 4.01f, 20f, 1).firstBaselinePx, 0f)
        assertEquals(22f, WebCssLineBox.plan(30f, 20f, 4.8f, 20f, 1).firstBaselinePx, 0f)
    }

    @Test fun fixedPointDivisionPrecedesFlooringForNearZeroNegativeLeading() {
        assertEquals(20f, WebCssLineBox.plan(29.99f, 20f, 10f, 20f, 1).firstBaselinePx, 0f)
        assertEquals(19f, WebCssLineBox.plan(29.96f, 20f, 10f, 20f, 1).firstBaselinePx, 0f)
    }

    @Test fun invalidMetricsCannotProduceAnOverflowingOrNaNLayout() {
        assertThrows(IllegalArgumentException::class.java) { WebCssLineBox.plan(Float.NaN, 1f, 1f, 1f, 1) }
        assertThrows(IllegalArgumentException::class.java) { WebCssLineBox.plan(20f, -1f, 1f, 1f, 1) }
        assertThrows(IllegalArgumentException::class.java) { WebCssLineBox.plan(20f, 1f, 1f, 1f, 0) }
    }
}
