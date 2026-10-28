package com.learngraph.mobile.ui.components

import android.view.WindowManager
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.interaction.collectIsHoveredAsState
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.graphics.TransformOrigin
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.layout.positionInParent
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.*
import androidx.compose.ui.window.*
import androidx.core.view.WindowCompat
import kotlinx.coroutines.launch
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlin.math.roundToInt

private class WebInputCleanup {
    var action: () -> Unit = {}
    private var cleaned = false
    fun rearm() { cleaned = false }
    fun clean() { if (!cleaned) { cleaned = true; action() } }
}
private val LocalWebInputCleanup = staticCompositionLocalOf<WebInputCleanup?> { null }
private val LocalWebDialog = staticCompositionLocalOf<WebDialogScope?> { null }

@Composable
private fun BindWebInputCleanup(cleanup: WebInputCleanup) {
    val focus = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    DisposableEffect(cleanup, focus, keyboard) {
        cleanup.action = { focus.clearFocus(force = true); keyboard?.hide() }
        onDispose { cleanup.action = {}; focus.clearFocus(force = true) }
    }
}

/** CSS keyframes run 100/150ms in both directions, rather than CSS transition
 * reversal shortening. A canceled animation starts its inverse at current pixels.
 */
@Stable
private class WebPresence(private val initialOffset: Float, tag: String, initiallyMounted: Boolean) {
    val opacity = WebAnimatedFloat(0f, "$tag-opacity")
    val slide = WebAnimatedFloat(initialOffset, "$tag-slide")
    var mounted by mutableStateOf(initiallyMounted)
    var visible by mutableStateOf(false)
    var closing by mutableStateOf(false)
    var entryRequest by mutableIntStateOf(0)
        private set
    var awaitingEnter = false
        private set
    var serial = 0
    suspend fun prepareEnter() {
        // The parent window can tick while a new Popup/Dialog window is still
        // attaching and measuring. Do not spend its CSS duration in that gap.
        ++serial
        opacity.stop(); slide.stop()
        if (!mounted) { opacity.snapTo(0f); slide.snapTo(initialOffset) }
        mounted = true; closing = false; awaitingEnter = true; entryRequest++
    }
    suspend fun setVisible(show: Boolean, duration: Int, onHidden: () -> Unit) = kotlinx.coroutines.coroutineScope {
        if (show && !mounted) { opacity.snapTo(0f); slide.snapTo(initialOffset) }
        mounted = true; visible = show; closing = !show; awaitingEnter = false
        val version = ++serial
        // CSS animations restart their specified duration, unlike drawer transitions.
        opacity.stop(); slide.stop()
        launch { slide.animateTo(0f, duration) }
        opacity.animateTo(if (show) 1f else 0f, duration)
        if (!show && version == serial) { mounted = false; closing = false; onHidden() }
    }
}

/** This effect lives inside the actual overlay composition and therefore uses
 * its window's frame clock. No arbitrary delay or longer CSS duration is added.
 */
@Composable
private fun StartWebOverlayEnter(
    presence: WebPresence, expanded: Boolean, positioned: Boolean, reduced: Boolean, duration: Int,
) {
    val view = LocalView.current
    var attached by remember(view) { mutableStateOf(view.isAttachedToWindow) }
    val requested by rememberUpdatedState(expanded)
    DisposableEffect(view) {
        val listener = object : android.view.View.OnAttachStateChangeListener {
            override fun onViewAttachedToWindow(v: android.view.View) { attached = true }
            override fun onViewDetachedFromWindow(v: android.view.View) { attached = false }
        }
        view.addOnAttachStateChangeListener(listener)
        attached = view.isAttachedToWindow
        onDispose { view.removeOnAttachStateChangeListener(listener) }
    }
    LaunchedEffect(expanded, positioned, attached, presence.entryRequest, reduced) {
        if (expanded && positioned && attached && presence.awaitingEnter) {
            withFrameNanos { }
            currentCoroutineContext().ensureActive()
            if (requested && view.isAttachedToWindow && presence.awaitingEnter) {
                presence.setVisible(true, WebMotionContract.duration(duration, reduced)) {}
            }
        }
    }
}

/** Shared Radix phone popup. Leave mounted while expanded=false for exit playback.
 * Outside/Back dismissal runs its exit before calling onDismissRequest itself.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WebPopup(
    expanded: Boolean,
    onDismissRequest: () -> Unit,
    modifier: Modifier = Modifier,
    offset: DpOffset = DpOffset.Zero,
    alignEnd: Boolean = false,
    onClosed: () -> Unit = {},
    content: @Composable () -> Unit,
) {
    val scope = rememberCoroutineScope()
    val reduced = rememberWebReducedMotion()
    val density = LocalDensity.current
    val anchorView = LocalView.current
    var origin by remember { mutableStateOf(TransformOrigin(.5f, 0f)) }
    var entrySign by remember { mutableFloatStateOf(-1f) }
    var bodySize by remember { mutableStateOf(IntSize.Zero) }
    var host by remember { mutableStateOf<WebPopupHostGeometry?>(null) }
    val presence = remember { WebPresence(1f, "Menu", expanded) }
    val cleanup = remember { WebInputCleanup() }
    val dismiss by rememberUpdatedState(onDismissRequest)
    val closed by rememberUpdatedState(onClosed)
    fun requestDismiss() {
        if (presence.closing) return
        cleanup.clean()
        scope.launch { presence.setVisible(false, WebMotionContract.duration(WebMotionContract.MenuMillis, reduced)) { dismiss(); closed() } }
    }
    LaunchedEffect(expanded, reduced) {
        if (expanded) {
            cleanup.rearm()
            if (presence.visible && !presence.closing) presence.setVisible(true, WebMotionContract.duration(WebMotionContract.MenuMillis, reduced)) {}
            else presence.prepareEnter()
        }
        else if (presence.mounted) { cleanup.clean(); presence.setVisible(false, WebMotionContract.duration(WebMotionContract.MenuMillis, reduced)) { closed() } }
    }
    if (!presence.mounted) return
    // CSS42 blur has sigma21. Preserve the whole 3-sigma tail plus16px offset
    // and the8px entry translation. This is real window space, not body padding.
    val overflowBefore = with(density) { (63.dp - 16.dp + 8.dp).roundToPx() } + 1
    val overflowAfter = with(density) { (63.dp + 16.dp + 8.dp).roundToPx() } + 1
    val viewport = remember(anchorView) {
        val frame = android.graphics.Rect()
        val screen = IntArray(2)
        val window = IntArray(2)
        val read: () -> IntRect = {
            anchorView.getWindowVisibleDisplayFrame(frame)
            anchorView.getLocationOnScreen(screen)
            anchorView.getLocationInWindow(window)
            // VisibleDisplayFrame is in screen coordinates; Popup anchorBounds
            // is in window coordinates. Do not add status-bar height twice.
            IntRect(frame.left - screen[0] + window[0], frame.top - screen[1] + window[1],
                frame.right - screen[0] + window[0], frame.bottom - screen[1] + window[1])
        }
        read
    }
    val position = remember(density, offset, alignEnd, viewport) {
        WebPopupPositionProvider(with(density) { 12.dp.roundToPx() }, with(density) { 4.dp.roundToPx() },
            IntOffset(with(density) { offset.x.roundToPx() }, with(density) { offset.y.roundToPx() }), alignEnd,
            { bodySize }, viewport, overflowBefore, overflowAfter) { nextOrigin, bottom, nextHost ->
            origin = nextOrigin; entrySign = if (bottom) -1f else 1f; host = nextHost
        }
    }
    Popup(popupPositionProvider = position, onDismissRequest = ::requestDismiss,
        properties = PopupProperties(focusable = true, dismissOnBackPress = true, dismissOnClickOutside = true, clippingEnabled = false)) {
        BindWebInputCleanup(cleanup)
        var laidOutBodySize by remember { mutableStateOf(IntSize.Zero) }
        var laidOutBodyOffset by remember { mutableStateOf(IntOffset.Zero) }
        var laidOutHostSize by remember { mutableStateOf(IntSize.Zero) }
        val positioned = host?.let { it.bodyWidth == bodySize.width && it.bodyHeight == bodySize.height &&
            laidOutBodySize == bodySize && laidOutBodyOffset == IntOffset(it.bodyX, it.bodyY) &&
            laidOutHostSize == IntSize(it.width, it.height) } == true
        StartWebOverlayEnter(presence, expanded, positioned, reduced, WebMotionContract.MenuMillis)
        CompositionLocalProvider(LocalWebInputCleanup provides cleanup, LocalRippleConfiguration provides null) {
            // Alpha clips only at the expanded viewport host. Scale lives inside
            // that layer, so it cannot pull a clipped viewport edge inward.
            Box(Modifier.graphicsLayer { alpha = presence.opacity.value }.onSizeChanged { laidOutHostSize = it }) {
                Box(Modifier.matchParentSize().pointerInput(reduced) { detectTapGestures { requestDismiss() } })
                Layout(content = {
                    Box(modifier.pointerInput(Unit) {
                        // Blank body space is still inside. Observe without
                        // consuming text selection, input, buttons or scrolling.
                        awaitPointerEventScope { while (true) awaitPointerEvent() }
                    }.onGloballyPositioned {
                        laidOutBodySize = it.size
                        val p = it.positionInParent()
                        laidOutBodyOffset = IntOffset(p.x.roundToInt(), p.y.roundToInt())
                    }) { content() }
                }, modifier = Modifier.graphicsLayer {
                    scaleX = WebMotionContract.ClosedScale + .05f * presence.opacity.value; scaleY = scaleX
                    translationY = presence.slide.value * WebMotionContract.MenuSlideDp * density.density * entrySign
                    val x = WebMotionContract.popupOriginFraction(origin.pivotFractionX, presence.opacity.value, presence.closing)
                    val y = WebMotionContract.popupOriginFraction(origin.pivotFractionY, presence.opacity.value, presence.closing)
                    transformOrigin = host?.let { TransformOrigin(it.originX(x), it.originY(y)) } ?: TransformOrigin.Center
                }) { measurables, constraints ->
                    // Measure the original body before adding window overflow.
                    // In particular, a360dp viewport still permits its320dp body.
                    val body = measurables.single().measure(constraints.copy(minWidth = 0, minHeight = 0))
                    bodySize = IntSize(body.width, body.height)
                    val current = host
                    val bounds = viewport()
                    val width = (current?.width ?: bounds.width).coerceIn(constraints.minWidth, constraints.maxWidth)
                    val bodyX = current?.bodyX ?: 0
                    val bodyY = current?.bodyY ?: overflowBefore
                    val height = (if (current != null && current.bodyWidth == body.width && current.bodyHeight == body.height) current.height
                        else bodyY + body.height + overflowAfter).coerceIn(constraints.minHeight, constraints.maxHeight)
                    layout(width, height) { body.place(bodyX, bodyY) }
                }
            }
        }
    }
}

/** Radix collision-aware bottom/start (or bottom/end) placement, 4px side gap. */
private class WebPopupPositionProvider(
    private val margin: Int, private val sideOffset: Int, private val offset: IntOffset,
    private val alignEnd: Boolean, private val bodySize: () -> IntSize, private val viewport: () -> IntRect,
    private val overflowBefore: Int, private val overflowAfter: Int,
    private val onPlacement: (TransformOrigin, Boolean, WebPopupHostGeometry) -> Unit,
) : PopupPositionProvider {
    override fun calculatePosition(anchorBounds: IntRect, windowSize: IntSize, layoutDirection: LayoutDirection, popupContentSize: IntSize): IntOffset {
        val body = bodySize()
        if (body.width <= 0 || body.height <= 0) return IntOffset.Zero
        val right = (layoutDirection == LayoutDirection.Ltr) == alignEnd
        val desired = (if (right) anchorBounds.right - body.width else anchorBounds.left) + offset.x
        val x = desired.coerceIn(margin, maxOf(margin, windowSize.width - body.width - margin))
        val below = anchorBounds.bottom + sideOffset + offset.y
        val above = anchorBounds.top - sideOffset - body.height - offset.y
        val y = when { below + body.height <= windowSize.height - margin -> below; above >= margin -> above
            else -> below.coerceIn(margin, maxOf(margin, windowSize.height - body.height - margin)) }
        val bottom = y >= anchorBounds.bottom
        val bounds = viewport().takeIf { it.width > 0 && it.height > 0 } ?: IntRect(0, 0, windowSize.width, windowSize.height)
        val host = WebPopupHostGeometry.enclose(x, y, body.width, body.height,
            bounds.left, bounds.top, bounds.right, bounds.bottom, overflowBefore, overflowAfter)
        onPlacement(TransformOrigin(if (right) 1f else 0f, if (bottom) 0f else 1f), bottom, host)
        return IntOffset(host.left, host.top)
    }
}

@Composable
fun WebDropdownMenu(
    expanded: Boolean, onDismissRequest: () -> Unit, modifier: Modifier = Modifier,
    offset: DpOffset = DpOffset.Zero, onClosed: () -> Unit = {},
    content: @Composable ColumnScope.() -> Unit,
) {
    WebPopup(expanded, onDismissRequest, offset = offset, onClosed = onClosed) {
        val visibleHeight = rememberWebVisibleHeight()
        Box(modifier.width(IntrinsicSize.Max).widthIn(min = 128.dp, max = 320.dp)
            .webCssBoxShadow().webCssOuterRing(14.dp, MaterialTheme.colorScheme.onSurface.copy(alpha = .1f))) {
            Surface(Modifier.fillMaxWidth(), shape = RoundedCornerShape(14.dp), color = webPopoverColor(), tonalElevation = 0.dp, shadowElevation = 0.dp) {
                Column(Modifier.heightIn(max = minOf(540.dp, (visibleHeight - 24.dp).coerceAtLeast(0.dp))).verticalScroll(rememberScrollState()).padding(6.dp), content = content)
            }
        }
    }
}

@Composable
fun WebDropdownMenuItem(
    text: @Composable () -> Unit, onClick: () -> Unit, modifier: Modifier = Modifier,
    leadingIcon: (@Composable () -> Unit)? = null, trailingIcon: (@Composable () -> Unit)? = null,
    enabled: Boolean = true, contentPadding: PaddingValues = PaddingValues(horizontal = 6.dp, vertical = 4.dp),
) {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val focused by interaction.collectIsFocusedAsState()
    val hovered by interaction.collectIsHoveredAsState()
    val cleanup = LocalWebInputCleanup.current
    Row(modifier.fillMaxWidth().background(if (pressed || focused || hovered) MaterialTheme.colorScheme.surfaceVariant else Color.Transparent, RoundedCornerShape(6.dp))
        .clickable(interactionSource = interaction, indication = null, enabled = enabled, role = Role.Button, onClick = { cleanup?.clean(); onClick() })
        .padding(contentPadding), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        CompositionLocalProvider(LocalContentColor provides MaterialTheme.colorScheme.onSurface.copy(alpha = if (enabled) 1f else .5f)) {
            ProvideTextStyle(MaterialTheme.typography.bodyMedium.copy(fontSize = 14.sp, lineHeight = 21.sp)) {
                leadingIcon?.invoke(); Box(Modifier.weight(1f)) { text() }; trailingIcon?.invoke()
            }
        }
    }
}

@Stable
class WebDialogScope internal constructor(
    val panelModifier: Modifier,
    val availableHeight: Dp,
    val availableWidth: Dp,
    private val closeAction: (() -> Unit) -> Unit,
) {
    /** Retain the dialog through its exit, then apply the parent's state/action. */
    fun close(action: () -> Unit = {}) = closeAction(action)
}

@Composable
fun rememberWebDialogCloseAction(action: () -> Unit): () -> Unit {
    val dialog = LocalWebDialog.current
    val current by rememberUpdatedState(action)
    return remember(dialog) { { if (dialog != null) dialog.close { current() } else current() } }
}

/** Radix Dialog: separate .12 scrim, 150ms fade/scale95, no window animation.
 * Insets are consumed once in this host; availableHeight is already IME-safe.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WebDialog(
    onDismissRequest: () -> Unit,
    modifier: Modifier = Modifier,
    properties: DialogProperties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    expanded: Boolean = true,
    onClosed: () -> Unit = {},
    contentPadding: Dp = 16.dp,
    animateContent: Boolean = true,
    exitDurationMillis: Int = WebMotionContract.DialogMillis,
    content: @Composable WebDialogScope.() -> Unit,
) {
    val scope = rememberCoroutineScope()
    val reduced = rememberWebReducedMotion()
    val presence = remember { WebPresence(0f, "Dialog", expanded) }
    val cleanup = remember { WebInputCleanup() }
    val dismiss by rememberUpdatedState(onDismissRequest)
    val closed by rememberUpdatedState(onClosed)
    fun close(action: () -> Unit) {
        if (presence.closing) return
        cleanup.clean()
        scope.launch { presence.setVisible(false, WebMotionContract.duration(exitDurationMillis.coerceAtLeast(0), reduced)) { action(); closed() } }
    }
    LaunchedEffect(expanded, reduced) {
        if (expanded) {
            cleanup.rearm()
            if (presence.visible && !presence.closing) presence.setVisible(true, WebMotionContract.duration(WebMotionContract.DialogMillis, reduced)) {}
            else presence.prepareEnter()
        }
        else if (presence.mounted) { cleanup.clean(); presence.setVisible(false, WebMotionContract.duration(exitDurationMillis.coerceAtLeast(0), reduced)) { closed() } }
    }
    if (!presence.mounted) return
    val backdrop = LocalWebBackdrop.current
    val dialogToken = remember { Any() }
    val backdropDensity = LocalDensity.current.density
    DisposableEffect(backdrop, dialogToken) {
        backdrop?.add(dialogToken)
        onDispose { backdrop?.remove(dialogToken) }
    }
    Dialog(onDismissRequest = { close { dismiss() } }, properties = DialogProperties(
        dismissOnBackPress = properties.dismissOnBackPress, dismissOnClickOutside = properties.dismissOnClickOutside,
        securePolicy = properties.securePolicy, usePlatformDefaultWidth = false, decorFitsSystemWindows = false,
    )) {
        BindWebInputCleanup(cleanup)
        var positioned by remember { mutableStateOf(false) }
        StartWebOverlayEnter(presence, expanded, positioned, reduced, WebMotionContract.DialogMillis)
        val view = LocalView.current
        val statusBarBackground = MaterialTheme.colorScheme.background
        val lightStatusIcons = statusBarBackground.luminance() >= .5f
        SideEffect {
            (view.parent as? DialogWindowProvider)?.window?.let { window ->
                val controller = WindowCompat.getInsetsController(window, view)
                if (controller.isAppearanceLightStatusBars != lightStatusIcons) controller.isAppearanceLightStatusBars = lightStatusIcons
                if (window.attributes.windowAnimations != 0) window.setWindowAnimations(0)
                if (window.attributes.dimAmount != 0f) window.setDimAmount(0f)
                val input = (window.attributes.softInputMode and WindowManager.LayoutParams.SOFT_INPUT_MASK_ADJUST.inv()) or WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE
                val legacy = WindowManager.LayoutParams.FLAG_FULLSCREEN or WindowManager.LayoutParams.FLAG_TRANSLUCENT_STATUS or WindowManager.LayoutParams.FLAG_TRANSLUCENT_NAVIGATION
                if (window.attributes.softInputMode != input || window.attributes.flags and legacy != 0 || window.attributes.flags and WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN == 0) {
                    WindowCompat.setDecorFitsSystemWindows(window, false)
                    if (window.attributes.softInputMode != input) window.setSoftInputMode(input)
                    if (window.attributes.flags and legacy != 0) window.clearFlags(legacy)
                    if (window.attributes.flags and WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN == 0) window.addFlags(WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN)
                }
            }
        }
        CompositionLocalProvider(LocalRippleConfiguration provides null) {
            Box(Modifier.fillMaxSize().webBackdropBlur(backdrop?.depthAbove(dialogToken) ?: 0, backdropDensity)) {
                Box(Modifier.matchParentSize().statusBarsPadding().background(Color.Black.copy(alpha = .12f * presence.opacity.value))
                    .clickable(interactionSource = remember { MutableInteractionSource() }, indication = null, enabled = properties.dismissOnClickOutside, onClick = { close { dismiss() } }))
                Box(Modifier.fillMaxWidth().windowInsetsTopHeight(WindowInsets.statusBars).background(statusBarBackground))
                BoxWithConstraints(Modifier.fillMaxSize().systemBarsPadding().imePadding().padding(contentPadding), contentAlignment = Alignment.Center) {
                    val panel = Modifier.graphicsLayer { alpha = presence.opacity.value; scaleX = .95f + .05f * presence.opacity.value; scaleY = scaleX }
                        .onGloballyPositioned { positioned = it.size.width > 0 && it.size.height > 0 }
                    val dialog = WebDialogScope(panel, maxHeight, maxWidth, ::close)
                    CompositionLocalProvider(LocalWebDialog provides dialog) {
                        val hitTarget = if (animateContent) Modifier.pointerInput(Unit) {
                            // Match the DOM panel: blank content is inside the dialog.
                            // Observe without consuming child input, selection or scrolling.
                            awaitPointerEventScope { while (true) awaitPointerEvent() }
                        } else Modifier
                        Box((if (animateContent) panel else Modifier).then(modifier).then(hitTarget)) { dialog.content() }
                    }
                }
            }
        }
    }
}

@Composable
private fun rememberWebVisibleHeight(): Dp {
    val view = LocalView.current
    val density = LocalDensity.current
    val fallback = LocalConfiguration.current.screenHeightDp.dp
    var pixels by remember(view) { mutableIntStateOf(0) }
    DisposableEffect(view) {
        val bounds = android.graphics.Rect()
        fun read() { view.getWindowVisibleDisplayFrame(bounds); pixels = bounds.height() }
        val observer = android.view.ViewTreeObserver.OnGlobalLayoutListener { read() }
        view.viewTreeObserver.addOnGlobalLayoutListener(observer); read()
        onDispose { if (view.viewTreeObserver.isAlive) view.viewTreeObserver.removeOnGlobalLayoutListener(observer) }
    }
    return if (pixels > 0) with(density) { pixels.toDp() } else fallback
}

@Composable
private fun webPopoverColor(): Color = if (MaterialTheme.colorScheme.background.luminance() < .5f) Color(0xFF1C1F23) else Color.White

@Composable
fun WebAlertDialog(
    onDismissRequest: () -> Unit, confirmButton: @Composable () -> Unit, modifier: Modifier = Modifier,
    dismissButton: (@Composable () -> Unit)? = null, icon: (@Composable () -> Unit)? = null,
    title: (@Composable () -> Unit)? = null, text: (@Composable () -> Unit)? = null,
    shape: Shape = RoundedCornerShape(16.dp), containerColor: Color = Color.Unspecified,
    iconContentColor: Color = Color.Unspecified, titleContentColor: Color = Color.Unspecified, textContentColor: Color = Color.Unspecified,
    tonalElevation: Dp = 0.dp, properties: DialogProperties = DialogProperties(),
    expanded: Boolean = true, onClosed: () -> Unit = {},
) {
    WebDialog(onDismissRequest, properties = properties, expanded = expanded, onClosed = onClosed) {
        Surface(modifier.widthIn(max = 448.dp).fillMaxWidth(), shape = shape,
            color = if (containerColor == Color.Unspecified) webPopoverColor() else containerColor,
            tonalElevation = tonalElevation, shadowElevation = 24.dp,
            border = BorderStroke(1.dp, Color.Black.copy(alpha = .1f))) {
            Column(Modifier.heightIn(max = availableHeight).verticalScroll(rememberScrollState()).padding(20.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
                if (icon != null) CompositionLocalProvider(LocalContentColor provides if (iconContentColor == Color.Unspecified) MaterialTheme.colorScheme.onSurface else iconContentColor) { icon() }
                if (title != null) CompositionLocalProvider(LocalContentColor provides if (titleContentColor == Color.Unspecified) MaterialTheme.colorScheme.onSurface else titleContentColor) {
                    ProvideTextStyle(MaterialTheme.typography.titleMedium.copy(fontSize = 16.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium)) { title() }
                }
                if (text != null) CompositionLocalProvider(LocalContentColor provides if (textContentColor == Color.Unspecified) MaterialTheme.colorScheme.onSurfaceVariant else textContentColor) {
                    ProvideTextStyle(MaterialTheme.typography.bodyMedium.copy(fontSize = 14.sp, lineHeight = 21.sp)) { text() }
                }
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End), verticalAlignment = Alignment.CenterVertically) { dismissButton?.invoke(); confirmButton() }
            }
        }
    }
}
