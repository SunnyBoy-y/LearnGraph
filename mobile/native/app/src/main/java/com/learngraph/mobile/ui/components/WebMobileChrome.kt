package com.learngraph.mobile.ui.components

import androidx.compose.animation.core.LinearEasing
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathParser
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalTextToolbar
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.launch

@Stable
class WebDrawerState internal constructor(
    reduceMotion: Boolean,
    internal val side: WebDrawerGesturePolicy.Side = WebDrawerGesturePolicy.Side.Left,
) {
    var reduceMotion by mutableStateOf(reduceMotion)
        internal set
    private val traceSide = if (side == WebDrawerGesturePolicy.Side.Left) "LeftDrawer" else "RightDrawer"
    val progress = WebAnimatedFloat(0f, "$traceSide-transform")
    val opacity = WebAnimatedFloat(0f, "$traceSide-opacity")
    val scrimOpacity = WebAnimatedFloat(0f, "$traceSide-backdrop")
    private val closeButtonOpacity = WebAnimatedFloat(0f, "$traceSide-close")
    var isOpen by mutableStateOf(false)
        private set
    var dragging by mutableStateOf(false)
        private set
    private var dragProgress by mutableFloatStateOf(0f)
    private var rendererGestureActive = false
    private var rendererBase = 0f
    internal var logicalWidth = if (side == WebDrawerGesturePolicy.Side.Left) 286f else 420f
    val position: Float get() = if (dragging) dragProgress else progress.value
    val panelAlpha: Float get() = if (dragging) dragProgress else opacity.value
    val scrimAlpha: Float get() = if (dragging) dragProgress else scrimOpacity.value
    val closeButtonAlpha: Float get() = closeButtonOpacity.value
    val isVisible: Boolean get() = isOpen || dragging || position > 0f || panelAlpha > 0f || scrimAlpha > 0f

    suspend fun open() = settle(true)
    suspend fun close() = settle(false)
    internal fun beginDrag(base: Float) { dragProgress = base; dragging = true }
    internal fun dragTo(value: Float) { dragProgress = value.coerceIn(0f, 1f) }
    internal suspend fun stopAnimations() { progress.stop(); opacity.stop(); scrimOpacity.stop(); closeButtonOpacity.stop() }
    internal suspend fun finishDrag() = settle(WebDrawerGesturePolicy.settleOpen(dragProgress))

    /** Main-thread adapter for a shared renderer that already applied the Web DOM exclusions. */
    fun beginRendererDrag() { rendererGestureActive = true; rendererBase = if (isOpen) 1f else 0f; beginDrag(rendererBase) }
    fun progressRendererDrag(dx: Float) {
        if (rendererGestureActive && dx.isFinite()) dragTo(WebDrawerGesturePolicy.progress(WebDrawerGesturePolicy.Resolution(side, rendererBase), dx, logicalWidth))
    }
    /** Web touchcancel uses the same >50% settlement as touchend. */
    suspend fun finishRendererDrag(cancelled: Boolean = false) {
        if (rendererGestureActive) { rendererGestureActive = false; finishDrag() }
    }

    private suspend fun settle(open: Boolean) = kotlinx.coroutines.coroutineScope {
        // Start the settled tween at the exact final finger position.
        if (dragging) {
            progress.snapTo(dragProgress); opacity.snapTo(dragProgress); scrimOpacity.snapTo(dragProgress)
        }
        dragging = false
        rendererGestureActive = false
        isOpen = open
        val target = if (open) 1f else 0f
        launch { if (reduceMotion) progress.snapTo(target) else progress.animateTo(target, if (side == WebDrawerGesturePolicy.Side.Left) WebMotionContract.LeftDrawerMillis else WebMotionContract.RightDrawerMillis, WebDrawerEase) }
        val fade = if (reduceMotion) WebMotionContract.ReducedDrawerMillis else WebMotionContract.DrawerFadeMillis
        val fadeEase = if (reduceMotion) LinearEasing else WebTransitionEase
        launch { opacity.animateTo(target, fade, fadeEase) }
        // Final mobile CSS overrides scrim opacity to 180ms; reduced uses 140ms linear.
        launch { scrimOpacity.animateTo(target, fade, fadeEase) }
        launch { closeButtonOpacity.animateTo(target, fade, fadeEase, delayMillis = if (open && !reduceMotion) WebMotionContract.GraphCloseDelayMillis else 0) }
    }
}

val LocalWebDrawerState = staticCompositionLocalOf<WebDrawerState?> { null }
val LocalWebGraphDrawerState = staticCompositionLocalOf<WebDrawerState?> { null }

@Composable
fun rememberWebDrawerState(): WebDrawerState = rememberDrawerState(WebDrawerGesturePolicy.Side.Left)

@Composable
fun rememberWebGraphDrawerState(): WebDrawerState = rememberDrawerState(WebDrawerGesturePolicy.Side.Right)

@Composable
private fun rememberDrawerState(side: WebDrawerGesturePolicy.Side): WebDrawerState {
    val reduced = rememberWebReducedMotion()
    val state = remember(side) { WebDrawerState(reduced, side) }
    SideEffect { state.reduceMotion = reduced }
    // Preference changes retarget the existing state; they do not reset an open panel.
    LaunchedEffect(reduced) { if (state.isVisible && !state.dragging) { if (state.isOpen) state.open() else state.close() } }
    return state
}

/**
 * Port of the Web mobile drawers: full-canvas directional swipe, 8dp/1.2 direction
 * lock, linear finger travel, strict >50% settle and no velocity shortcut.
 * Child-owned horizontal scroll, editable controls, tables and active selections opt out.
 */
@Composable
fun WebNavigationDrawer(
    state: WebDrawerState,
    graphState: WebDrawerState = rememberWebGraphDrawerState(),
    drawerContent: @Composable ColumnScope.() -> Unit,
    graphContent: @Composable () -> Unit = {},
    content: @Composable () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    val exclusions = remember { WebDrawerSwipeRegistry() }
    val originalToolbar = LocalTextToolbar.current
    val toolbar = remember(originalToolbar, exclusions) { DrawerTrackingTextToolbar(originalToolbar, exclusions) }
    var rootPosition by remember { mutableStateOf(Offset.Zero) }
    var graphMounted by remember { mutableStateOf(false) }
    LaunchedEffect(graphState.isVisible) { if (graphState.isVisible) graphMounted = true }

    CompositionLocalProvider(LocalWebDrawerSwipeRegistry provides exclusions, LocalWebDrawerState provides state, LocalWebGraphDrawerState provides graphState, LocalTextToolbar provides toolbar) {
        BoxWithConstraints(Modifier.fillMaxSize().onGloballyPositioned { rootPosition = it.positionInRoot() }) {
            val drawerWidth = minOf(286.dp, maxWidth * .86f)
            val graphWidth = minOf(420.dp, maxWidth * .94f)
            val widthPx = with(density) { drawerWidth.toPx() }
            val graphWidthPx = with(density) { graphWidth.toPx() }
            SideEffect { state.logicalWidth = drawerWidth.value; graphState.logicalWidth = graphWidth.value }
            Box(Modifier.fillMaxSize().pointerInput(state, graphState, widthPx, graphWidthPx, density.density) {
                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Final)
                    // Match touchstart: a gesture beginning with two fingers never enters
                    // the controller; a later finger does not replace the tracked identifier.
                    if (currentEvent.changes.count { it.pressed } != 1) return@awaitEachGesture
                    val start = down.position
                    // Web z-indices put navigation (73/74) above graph (69/70).
                    // Its scrim belongs to the same surface as its panel.
                    val surface = when {
                        state.isOpen || state.dragging -> WebDrawerGesturePolicy.Surface.Navigation
                        graphState.isOpen || graphState.dragging -> WebDrawerGesturePolicy.Surface.Graph
                        else -> WebDrawerGesturePolicy.Surface.Canvas
                    }
                    var axis = WebDrawerGesturePolicy.Axis.Pending
                    var resolution: WebDrawerGesturePolicy.Resolution? = null
                    var activeState: WebDrawerState? = null
                    while (true) {
                        val event = awaitPointerEvent(PointerEventPass.Final)
                        val change = event.changes.firstOrNull { it.id == down.id }
                        if (change == null) {
                            if (axis == WebDrawerGesturePolicy.Axis.Horizontal) activeState?.let { target -> scope.launch { target.finishDrag() } }
                            break
                        }
                        if (!change.pressed) {
                            if (axis == WebDrawerGesturePolicy.Axis.Horizontal) activeState?.let { target -> scope.launch { target.finishDrag() } }
                            break
                        }
                        if (axis == WebDrawerGesturePolicy.Axis.Dead) continue
                        val dx = change.position.x - start.x
                        val dy = change.position.y - start.y
                        if (axis == WebDrawerGesturePolicy.Axis.Pending) {
                            // Observe after children: a native scroll/long-press selection wins.
                            if (change.isConsumed) { axis = WebDrawerGesturePolicy.Axis.Dead; continue }
                            axis = WebDrawerGesturePolicy.axis(dx / density.density, dy / density.density)
                            if (axis != WebDrawerGesturePolicy.Axis.Horizontal) continue
                            if (exclusions.blocks(start + rootPosition)) { axis = WebDrawerGesturePolicy.Axis.Dead; continue }
                            resolution = WebDrawerGesturePolicy.resolve(
                                surface, dx, leftAvailable = true, rightAvailable = true,
                            )
                            if (resolution == null) { axis = WebDrawerGesturePolicy.Axis.Dead; continue }
                            activeState = if (resolution!!.side == WebDrawerGesturePolicy.Side.Left) state else graphState
                            activeState!!.beginDrag(resolution!!.base)
                            activeState!!.let { target -> scope.launch { target.stopAnimations() } }
                        }
                        if (axis == WebDrawerGesturePolicy.Axis.Horizontal) {
                            activeState!!.dragTo(WebDrawerGesturePolicy.progress(resolution!!, dx, if (resolution!!.side == WebDrawerGesturePolicy.Side.Left) widthPx else graphWidthPx))
                            change.consume()
                        }
                    }
                }
            }) {
                content()
                if (graphState.isVisible) {
                    Box(Modifier.fillMaxSize().background(Color(0xFF0C0C0C).copy(alpha = .38f * graphState.scrimAlpha)).webClickFeedback(enabled = graphState.isOpen || graphState.dragging, hasPopup = true) { scope.launch { graphState.close() } })
                }
                if (graphMounted || graphState.isVisible) {
                    // Retain the Web ContextRail after first opening, including local graph state.
                    Surface(
                        color = MaterialTheme.colorScheme.surface,
                        shadowElevation = 18.dp,
                        modifier = Modifier.align(Alignment.CenterEnd).width(graphWidth).fillMaxHeight()
                            .graphicsLayer { translationX = graphWidthPx * (1f - graphState.position); alpha = graphState.panelAlpha },
                    ) { Box(Modifier.fillMaxSize().systemBarsPadding()) { graphContent() } }
                }
                if (graphState.isVisible) {
                    Box(Modifier.align(Alignment.TopEnd).systemBarsPadding().padding(top = 12.dp, end = 12.dp)
                        .graphicsLayer { alpha = graphState.closeButtonAlpha }
                        .size(28.dp).clip(RoundedCornerShape(8.dp)).background(MaterialTheme.colorScheme.primaryContainer)
                        .webClickFeedback(enabled = graphState.isOpen || graphState.dragging, hasPopup = true) { scope.launch { graphState.close() } }, contentAlignment = Alignment.Center) {
                        WebGlyph(WebIcon.Close, "收起图谱面板", Modifier.size(16.dp), color = MaterialTheme.colorScheme.onSurface)
                    }
                }
                if (state.isVisible) {
                    Box(Modifier.fillMaxSize().background(Color(0xFF0C0C0C).copy(alpha = .38f * state.scrimAlpha)).webClickFeedback(enabled = state.isOpen || state.dragging, hasPopup = true) { scope.launch { state.close() } })
                }
                Surface(
                    color = MaterialTheme.colorScheme.surfaceContainerLow,
                    shadowElevation = 18.dp,
                    modifier = Modifier.width(drawerWidth).fillMaxHeight()
                        .graphicsLayer { translationX = -widthPx * (1f - state.position); alpha = state.panelAlpha },
                ) {
                    Column(Modifier.fillMaxHeight().systemBarsPadding().padding(horizontal = 12.dp, vertical = 16.dp), content = drawerContent)
                }
            }
        }
    }
}

/** Exact source SVG nodes; tint, cap/join and viewBox match lucide-react's defaults. */
@Composable
fun WebGlyph(
    icon: WebIcon,
    description: String? = null,
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.onSurfaceVariant,
    strokeWidth: Float = 2f,
) {
    val vector = remember(icon, strokeWidth) {
        ImageVector.Builder(icon.name, 24.dp, 24.dp, 24f, 24f).apply {
            WEB_ICON_PATHS.getValue(icon).forEach { node ->
                addPath(
                    pathData = PathParser().parsePathString(node.data).toNodes(),
                    fill = if (node.filled) SolidColor(Color.Black) else null,
                    stroke = if (node.filled) null else SolidColor(Color.Black),
                    strokeLineWidth = strokeWidth,
                    strokeLineCap = StrokeCap.Round,
                    strokeLineJoin = StrokeJoin.Round,
                )
            }
        }.build()
    }
    Icon(vector, description, modifier.size(20.dp), tint = color)
}
