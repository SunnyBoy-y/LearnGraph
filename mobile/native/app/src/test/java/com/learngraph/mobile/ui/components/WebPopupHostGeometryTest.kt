package com.learngraph.mobile.ui.components

import org.junit.Assert.*
import org.junit.Test

class WebPopupHostGeometryTest {
    @Test fun fullWidthHostKeepsTheSame320dpBodyAndRemovesTheMeasuredShadowCutoff() {
        val g = WebPopupHostGeometry.enclose(36, 265, 960, 608, 0, 123, 1080, 2316, 166, 262)
        assertEquals(1080, g.width)
        assertEquals(960, g.bodyWidth)
        assertEquals(608, g.bodyHeight)
        assertEquals(36, g.left + g.bodyX)
        assertEquals(265, g.top + g.bodyY)
        assertEquals(123, g.top)
        assertEquals(1135, g.top + g.height)
        assertTrue(g.top + g.height > 921) // Actual pre-fix rectangular cutoff.
    }

    @Test fun hostOriginIsTheBodyPointForStartEndAndInterpolatedEntry() {
        val g = WebPopupHostGeometry.enclose(36, 265, 960, 608, 0, 123, 1080, 2316, 166, 262)
        for (fraction in listOf(0f, .5f, .566453f, 1f)) {
            assertEquals(g.bodyX + g.bodyWidth * fraction, g.width * g.originX(fraction), .0001f)
            assertEquals(g.bodyY + g.bodyHeight * fraction, g.height * g.originY(fraction), .0001f)
        }
        assertEquals(996f / 1080f, g.originX(1f), .00001f)
    }

    @Test fun keyboardViewportLimitsOnlyTransparentOverflowNotTheBody() {
        val g = WebPopupHostGeometry.enclose(36, 265, 960, 608, 0, 123, 1080, 1000, 166, 262)
        assertEquals(1000, g.top + g.height)
        assertEquals(265, g.top + g.bodyY)
        assertEquals(608, g.bodyHeight)
        assertEquals(123, g.top)
    }

    @Test fun shiftedWindowAndAboveCollisionDoNotAddTheStatusInsetToBodyAgain() {
        val g = WebPopupHostGeometry.enclose(60, 100, 384, 180, 24, 40, 744, 900, 55, 87)
        assertEquals(24, g.left)
        assertEquals(45, g.top)
        assertEquals(60, g.left + g.bodyX)
        assertEquals(100, g.top + g.bodyY)
        assertEquals(280f, g.top + g.height * g.originY(1f), .0001f)
    }
}
