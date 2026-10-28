package com.learngraph.mobile.ui.components

import org.junit.Assert.*
import org.junit.Test

class WebDrawerGesturePolicyTest {
    private val policy = WebDrawerGesturePolicy

    @Test fun directionLockRetainsTheWebSlopAndAmbiguousDiagonal() {
        assertEquals(WebDrawerGesturePolicy.Axis.Pending, policy.axis(7.99f, 0f))
        assertEquals(WebDrawerGesturePolicy.Axis.Horizontal, policy.axis(8f, 0f))
        assertEquals(WebDrawerGesturePolicy.Axis.Pending, policy.axis(8f, 7f))
        assertEquals(WebDrawerGesturePolicy.Axis.Horizontal, policy.axis(8.5f, 7f))
        assertEquals(WebDrawerGesturePolicy.Axis.Dead, policy.axis(8f, 8f))
        assertEquals(WebDrawerGesturePolicy.Axis.Dead, policy.axis(1f, -8f))
    }
    @Test fun canvasOpensByDirectionWhileEitherPanelAndItsScrimOnlyCloseTheirOwnSide() {
        val left = policy.resolve(WebDrawerGesturePolicy.Surface.Canvas, 30f, true, true)!!
        assertEquals(WebDrawerGesturePolicy.Side.Left, left.side)
        assertEquals(0f, left.base)
        val right = policy.resolve(WebDrawerGesturePolicy.Surface.Canvas, -30f, true, true)!!
        assertEquals(WebDrawerGesturePolicy.Side.Right, right.side)
        assertEquals(0f, right.base)
        val close = policy.resolve(WebDrawerGesturePolicy.Surface.Navigation, -30f, true, true)!!
        assertEquals(WebDrawerGesturePolicy.Side.Left, close.side)
        assertEquals(1f, close.base)
        assertNull(policy.resolve(WebDrawerGesturePolicy.Surface.Canvas, -30f, true, false))
    }
    @Test fun settlementUsesTotalTravelAndStrictHalfProgressWithNoFlingShortcut() {
        val opening = WebDrawerGesturePolicy.Resolution(WebDrawerGesturePolicy.Side.Left, 0f)
        assertFalse(policy.settleOpen(policy.progress(opening, 143f, 286f)))
        assertTrue(policy.settleOpen(policy.progress(opening, 143.1f, 286f)))
        assertFalse(policy.settleOpen(policy.progress(opening, 20f, 286f)))
        assertEquals(0f, policy.progress(opening, -20f, 286f))
        assertEquals(1f, policy.progress(opening, 500f, 286f))
        val closing = WebDrawerGesturePolicy.Resolution(WebDrawerGesturePolicy.Side.Left, 1f)
        assertFalse(policy.settleOpen(policy.progress(closing, -143f, 286f)))
        val right = WebDrawerGesturePolicy.Resolution(WebDrawerGesturePolicy.Side.Right, 0f)
        assertTrue(policy.settleOpen(policy.progress(right, -215f, 420f)))
    }
    @Test fun horizontalOverflowBelowEightLogicalPixelsDoesNotStealCanvasGestures() {
        assertFalse(policy.blocksHorizontalOverflow(0f))
        assertFalse(policy.blocksHorizontalOverflow(8f))
        assertTrue(policy.blocksHorizontalOverflow(8.01f))
    }
    @Test fun rightGraphDrawerClosesWithRightwardTravelAndCannotBecomeANavigationSwipe() {
        val right = policy.resolve(WebDrawerGesturePolicy.Surface.Graph, 200f, true, true)!!
        assertEquals(WebDrawerGesturePolicy.Side.Right, right.side)
        assertEquals(1f, right.base)
        assertTrue(policy.settleOpen(policy.progress(right, 209.9f, 420f)))
        assertFalse(policy.settleOpen(policy.progress(right, 210f, 420f)))
        assertEquals(1f, policy.progress(right, -200f, 420f))
        assertEquals(0f, policy.progress(right, 900f, 420f))
        assertNull(policy.resolve(WebDrawerGesturePolicy.Surface.Graph, 200f, true, false))
    }
    @Test fun graphTravelUsesTheMeasuredNarrowPhoneWidthRatherThanThe420PixelMaximum() {
        val right = policy.resolve(WebDrawerGesturePolicy.Surface.Canvas, -8f, true, true)!!
        val measuredWidth = 390f * .94f
        assertFalse(policy.settleOpen(policy.progress(right, -measuredWidth / 2f, measuredWidth)))
        assertTrue(policy.settleOpen(policy.progress(right, -measuredWidth / 2f - .1f, measuredWidth)))
        assertEquals(.25f, policy.progress(right, -measuredWidth / 4f, measuredWidth), .0001f)
    }
}
