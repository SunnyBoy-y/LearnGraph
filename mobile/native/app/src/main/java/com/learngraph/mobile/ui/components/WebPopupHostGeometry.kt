package com.learngraph.mobile.ui.components

/** Window coordinates stay separate from the unpadded menu body. The host is
 * viewport-wide so a 320dp menu never loses width to its 63dp Gaussian tail.
 */
internal data class WebPopupHostGeometry(
    val left: Int, val top: Int, val width: Int, val height: Int,
    val bodyX: Int, val bodyY: Int, val bodyWidth: Int, val bodyHeight: Int,
) {
    fun originX(bodyFraction: Float): Float = (bodyX + bodyWidth * bodyFraction) / width.coerceAtLeast(1)
    fun originY(bodyFraction: Float): Float = (bodyY + bodyHeight * bodyFraction) / height.coerceAtLeast(1)

    companion object {
        fun enclose(
            bodyLeft: Int, bodyTop: Int, bodyWidth: Int, bodyHeight: Int,
            viewportLeft: Int, viewportTop: Int, viewportRight: Int, viewportBottom: Int,
            overflowBefore: Int, overflowAfter: Int,
        ): WebPopupHostGeometry {
            val left = minOf(viewportLeft, bodyLeft)
            val right = maxOf(viewportRight, bodyLeft + bodyWidth)
            val top = minOf(bodyTop, maxOf(viewportTop, bodyTop - overflowBefore))
            val bottom = maxOf(bodyTop + bodyHeight, minOf(viewportBottom, bodyTop + bodyHeight + overflowAfter))
            return WebPopupHostGeometry(left, top, right - left, bottom - top,
                bodyLeft - left, bodyTop - top, bodyWidth, bodyHeight)
        }
    }
}
