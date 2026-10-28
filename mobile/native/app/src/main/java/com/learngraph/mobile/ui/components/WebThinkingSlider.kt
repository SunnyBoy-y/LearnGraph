package com.learngraph.mobile.ui.components

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import com.learngraph.mobile.ui.theme.WebText as Text
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.ProgressBarRangeInfo
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.disabled
import androidx.compose.ui.semantics.progressBarRangeInfo
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.setProgress
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.learngraph.mobile.util.Haptics
import kotlin.math.roundToInt

/** Exact ThinkingSlider geometry from index.css:7605; taps and drags commit discrete stops. */
@Composable
fun WebThinkingSlider(value: Int, enabled: Boolean, onChange: (Int) -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    var lastIndex by remember { mutableIntStateOf(value) }
    val change by rememberUpdatedState(onChange)
    LaunchedEffect(value) { lastIndex = value }
    val fill by rememberWebAnimatedFloat(value / 4f, WebMotionContract.SliderMillis)
    val primary = MaterialTheme.colorScheme.primary
    val foreground = MaterialTheme.colorScheme.onSurface
    fun commit(next: Int): Boolean {
        if (!enabled || next == lastIndex) return false
        lastIndex = next; Haptics.haptic(context, 1); change(next); return true
    }
    Column(modifier.webDrawerSwipeExcluded(), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Canvas(Modifier.fillMaxWidth().height(24.dp).semantics {
            contentDescription = "思考力度"
            progressBarRangeInfo = ProgressBarRangeInfo(value.toFloat(), 0f..4f, 3)
            if (!enabled) disabled()
            setProgress { commit(it.roundToInt().coerceIn(0, 4)) }
        }.pointerInput(enabled) {
            if (enabled) awaitEachGesture {
                val down = awaitFirstDown(requireUnconsumed = false)
                fun fromX(x: Float) = (x / size.width.coerceAtLeast(1) * 4f).roundToInt().coerceIn(0, 4)
                commit(fromX(down.position.x)); down.consume()
                while (true) {
                    val event = awaitPointerEvent()
                    val pointer = event.changes.firstOrNull { it.id == down.id } ?: break
                    if (!pointer.pressed) break
                    commit(fromX(pointer.position.x)); pointer.consume()
                }
            }
        }) {
            drawRoundRect(foreground.copy(alpha = .08f), cornerRadius = CornerRadius(12.dp.toPx()))
            if (fill > 0f) drawRoundRect(primary, size = Size(size.width * fill, size.height), cornerRadius = CornerRadius(12.dp.toPx()))
            repeat(5) { index -> drawCircle(foreground.copy(alpha = .25f), 2.5.dp.toPx(), Offset(10.dp.toPx() + (size.width - 20.dp.toPx()) * index / 4f, size.height / 2)) }
            val center = Offset(size.width * value / 4f, size.height / 2)
            drawCircle(Color.Black.copy(alpha = .12f), 10.dp.toPx(), center + Offset(0f, 1.dp.toPx()))
            drawCircle(Color.White, 8.dp.toPx(), center)
            drawCircle(primary, 8.dp.toPx(), center, style = Stroke(2.dp.toPx()))
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            listOf("极速", "低", "中", "高", "极高").forEachIndexed { index, label ->
                Text(label, fontSize = 11.sp, lineHeight = 16.5.sp, color = if (index == value) foreground else MaterialTheme.colorScheme.onSurfaceVariant,
                    fontWeight = if (index == value) FontWeight.Bold else FontWeight.Normal)
            }
        }
    }
}
