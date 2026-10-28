package com.learngraph.mobile.ui.components

import kotlin.math.abs

/** Port of frontend/src/lib/mobile-shell.ts. Distances are logical CSS px / Android dp. */
internal object WebDrawerGesturePolicy {
    const val LOCK_DISTANCE = 8f
    const val LOCK_SLOPE = 1.2f
    const val HORIZONTAL_OVERFLOW_SLOP = 8f
    const val COMMIT_RATIO = .5f
    enum class Axis { Pending, Horizontal, Dead }
    enum class Surface { Canvas, Navigation, Graph }
    enum class Side { Left, Right }
    data class Resolution(val side: Side, val base: Float)
    fun axis(dx: Float, dy: Float): Axis = when {
        abs(dy) >= LOCK_DISTANCE && abs(dy) >= abs(dx) -> Axis.Dead
        abs(dx) < LOCK_DISTANCE -> Axis.Pending
        abs(dx) < abs(dy) * LOCK_SLOPE -> Axis.Pending
        else -> Axis.Horizontal
    }
    fun resolve(surface: Surface, dx: Float, leftAvailable: Boolean, rightAvailable: Boolean): Resolution? = when (surface) {
        Surface.Navigation -> if (leftAvailable) Resolution(Side.Left, 1f) else null
        Surface.Graph -> if (rightAvailable) Resolution(Side.Right, 1f) else null
        Surface.Canvas -> if (dx > 0) { if (leftAvailable) Resolution(Side.Left, 0f) else null }
            else if (rightAvailable) Resolution(Side.Right, 0f) else null
    }
    fun progress(resolution: Resolution, dx: Float, drawerWidth: Float): Float {
        require(drawerWidth > 0f && drawerWidth.isFinite())
        val travel = if (resolution.side == Side.Left) dx else -dx
        return (resolution.base + travel / drawerWidth).coerceIn(0f, 1f)
    }
    /** The Web controller intentionally has no velocity/fling override. Exactly half closes. */
    fun settleOpen(progress: Float): Boolean = progress > COMMIT_RATIO
    fun blocksHorizontalOverflow(overflow: Float): Boolean = overflow > HORIZONTAL_OVERFLOW_SLOP
}
