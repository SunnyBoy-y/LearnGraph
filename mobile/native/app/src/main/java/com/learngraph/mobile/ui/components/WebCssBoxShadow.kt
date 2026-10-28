package com.learngraph.mobile.ui.components

import android.graphics.Bitmap
import android.graphics.BlurMaskFilter
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Rect
import android.graphics.RectF
import android.os.SystemClock
import android.util.Log
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.graphics.toArgb
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.dp
import com.learngraph.mobile.BuildConfig
import kotlin.math.abs
import kotlin.math.ceil
import kotlin.math.max
import kotlin.math.min

/** CSS blur radius is twice Gaussian sigma. Android's legacy mask radius uses
 * sigma=.57735*radius+.5, so passing CSS42 directly to Paint is not equivalent.
 * See SkBlurMask::ConvertRadiusToSigma in the Android/Skia source.
 */
internal data class WebCssShadowPlan(val cornerPx: Float, val sigmaPx: Float, val dyPx: Int, val bodyWidth: Int, val bodyHeight: Int) {
    val pad = ceil(3 * sigmaPx + 1).toInt()
    val width = bodyWidth + 2 * pad
    val height = bodyHeight + 2 * pad + abs(dyPx)
    val centerX = pad + bodyWidth / 2
    val centerY = pad + max(0, dyPx) + bodyHeight / 2
    val maskRadius: Float get() = ((sigmaPx - .5f) / .57735f).coerceAtLeast(.01f)

    companion object {
        fun create(width: Int, height: Int, density: Float, corner: Float, blur: Float, offsetY: Float): WebCssShadowPlan {
            val sigma = blur * density / 2
            // Three-sigma flat centers can stretch; corners and blur profiles cannot.
            // Small menus use their actual width/height to preserve overlapping blur.
            val maximumBody = ceil(2 * (corner * density + 3 * sigma) + 1).toInt()
            return WebCssShadowPlan(corner * density, sigma, (offsetY * density).toInt(), min(width, maximumBody), min(height, maximumBody))
        }
    }
}

private class ShadowTile(val bitmap: Bitmap, val plan: WebCssShadowPlan) {
    val xs = intArrayOf(0, plan.centerX, plan.centerX + 1, plan.width)
    val ys = intArrayOf(0, plan.centerY, plan.centerY + 1, plan.height)
    val sources = Array(9) { i -> Rect(xs[i % 3], ys[i / 3], xs[i % 3 + 1], ys[i / 3 + 1]) }
}

private object CssShadowTiles {
    // Alpha8 rather than ARGB, capped six templates; opening a menu reuses them.
    private val tiles = object : LinkedHashMap<WebCssShadowPlan, ShadowTile>(8, .75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<WebCssShadowPlan, ShadowTile>?) = size > 6
    }
    @Synchronized fun get(plan: WebCssShadowPlan): ShadowTile = tiles[plan] ?: run {
        val start = SystemClock.elapsedRealtimeNanos()
        val bitmap = Bitmap.createBitmap(plan.width, plan.height, Bitmap.Config.ALPHA_8)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = android.graphics.Color.WHITE
            maskFilter = BlurMaskFilter(plan.maskRadius, BlurMaskFilter.Blur.NORMAL)
        }
        Canvas(bitmap).drawRoundRect(RectF(plan.pad.toFloat(), (plan.pad + max(0, plan.dyPx)).toFloat(),
            (plan.pad + plan.bodyWidth).toFloat(), (plan.pad + max(0, plan.dyPx) + plan.bodyHeight).toFloat()), plan.cornerPx, plan.cornerPx, paint)
        ShadowTile(bitmap, plan).also {
            tiles[plan] = it
            if (BuildConfig.DEBUG) Log.d("LGUiParity", "native-css-shadow coldMs=${(SystemClock.elapsedRealtimeNanos() - start) / 1_000_000f} bytes=${bitmap.allocationByteCount} size=${plan.width}x${plan.height} cache=${tiles.size}")
        }
    }
}

/** index.css:279's exact shadow, rendered once into a reusable Alpha8 nine-slice.
 * No dependency, per-frame bitmap, Material elevation, or new layout padding.
 */
fun Modifier.webCssBoxShadow(cornerRadius: Dp = 14.dp, blur: Dp = 42.dp, offsetY: Dp = 16.dp, color: Color = Color.Black.copy(alpha = .14f)): Modifier = composed {
    var pixels by remember { mutableStateOf(IntSize.Zero) }
    val density = LocalDensity.current.density
    val tile = remember(pixels, density, cornerRadius, blur, offsetY) {
        if (pixels.width <= 0 || pixels.height <= 0) null else CssShadowTiles.get(WebCssShadowPlan.create(pixels.width, pixels.height, density, cornerRadius.value, blur.value, offsetY.value))
    }
    this.onSizeChanged { if (pixels != it) pixels = it }.drawWithCache {
        val paint = Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG).apply { this.color = color.toArgb() }
        val destinations = tile?.let {
            val plan = it.plan
            val width = size.width + 2 * plan.pad
            val height = size.height + 2 * plan.pad + abs(plan.dyPx)
            val x = floatArrayOf(0f, plan.centerX.toFloat(), width - (plan.width - plan.centerX - 1), width)
            val y = floatArrayOf(0f, plan.centerY.toFloat(), height - (plan.height - plan.centerY - 1), height)
            Array(9) { i -> RectF(x[i % 3] - plan.pad, y[i / 3] - plan.pad + min(0, plan.dyPx), x[i % 3 + 1] - plan.pad, y[i / 3 + 1] - plan.pad + min(0, plan.dyPx)) }
        }
        onDrawBehind {
            if (tile != null && destinations != null) drawIntoCanvas { canvas ->
                for (i in 0..8) canvas.nativeCanvas.drawBitmap(tile.bitmap, tile.sources[i], destinations[i], paint)
            }
        }
    }
}

/** Tailwind ring-1 is outside the box and does not steal two pixels of content. */
fun Modifier.webCssOuterRing(cornerRadius: Dp, color: Color, width: Dp = 1.dp): Modifier = drawBehind {
    val stroke = width.toPx()
    drawRoundRect(color, Offset(-stroke / 2, -stroke / 2), Size(size.width + stroke, size.height + stroke),
        CornerRadius(cornerRadius.toPx() + stroke / 2), style = Stroke(stroke))
}
