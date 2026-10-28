package com.learngraph.mobile.ui.components

import kotlin.math.abs
import kotlin.math.roundToInt

/** Source contracts: index.css's final mobile overrides, Radix and tw-animate-css. */
object WebMotionContract {
    const val MenuMillis = 100
    const val DialogMillis = 150
    const val ButtonMillis = 150
    const val SliderMillis = 120
    const val LeftDrawerMillis = 320
    const val RightDrawerMillis = 340
    const val DrawerFadeMillis = 180
    const val ReducedDrawerMillis = 140
    const val GraphCloseDelayMillis = 80
    const val MenuSlideDp = 8f
    const val ClosedScale = .95f

    fun duration(millis: Int, reduced: Boolean): Int = if (reduced) 0 else millis

    /** Radix no-arrow origin plus the Web transition-all origin interpolation.
     * Closed content starts at the CSS center; the mounted exit keeps its final
     * alignment. Applies independently to x/y, including top-side collisions.
     */
    fun popupOriginFraction(alignedFraction: Float, progress: Float, closing: Boolean): Float =
        if (closing) alignedFraction else .5f + (alignedFraction - .5f) * progress.coerceIn(0f, 1f)
}

/** CSS Transitions §3.1 reversing-adjusted start value / shortening factor.
 * A new non-reversing target always gets the full CSS duration. Dragging resets
 * the tracker because Web disables transitions while following the finger.
 */
internal class WebCssTransitionTracker {
    private var from = 0f
    private var target = 0f
    private var reversingStart = 0f
    private var shortening = 1f
    private var active = false

    fun reset() { active = false }

    fun begin(current: Float, next: Float, duration: Int, running: Boolean): Int {
        val reverses = active && running && next == reversingStart && target != from
        val factor = if (reverses) {
            val output = (current - from) / (target - from)
            abs(output * shortening + (1f - shortening)).coerceIn(0f, 1f)
        } else 1f
        reversingStart = if (reverses) target else current
        from = current
        target = next
        shortening = factor
        active = duration > 0 && current != next
        return (duration * factor).roundToInt()
    }
}
