package com.learngraph.mobile.ui.components

import androidx.compose.foundation.ScrollState
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.composed
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.layout.boundsInRoot
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.TextToolbar
import androidx.compose.ui.platform.TextToolbarStatus

/** Native equivalent of data-drawer-swipe="ignore" and Web's table/input exclusions. */
internal class WebDrawerSwipeRegistry {
    private val regions = mutableMapOf<Any, Rect>()
    var selectionActive = false
    fun update(key: Any, rect: Rect) { regions[key] = rect }
    fun remove(key: Any) { regions.remove(key) }
    fun blocks(point: Offset): Boolean = selectionActive || regions.values.any { it.contains(point) }
}
internal val LocalWebDrawerSwipeRegistry = staticCompositionLocalOf<WebDrawerSwipeRegistry?> { null }

/** Apply to editable controls, sliders, tables, selection affordances and other native gesture owners. */
fun Modifier.webDrawerSwipeExcluded(enabled: Boolean = true): Modifier = composed {
    val registry = LocalWebDrawerSwipeRegistry.current
    val token = remember { Any() }
    DisposableEffect(registry, token, enabled) {
        if (!enabled) registry?.remove(token)
        onDispose { registry?.remove(token) }
    }
    this.onGloballyPositioned { if (enabled) registry?.update(token, it.boundsInRoot()) else registry?.remove(token) }
}

/** Tables always exclude; ordinary horizontal scrollers exclude only for >8 logical px overflow. */
fun Modifier.webDrawerHorizontalScrollable(state: ScrollState): Modifier = composed {
    val density = LocalDensity.current.density
    this.webDrawerSwipeExcluded(WebDrawerGesturePolicy.blocksHorizontalOverflow(state.maxValue / density))
}

/** Retains Android's actual selection menu and tells the drawer when a selection is being adjusted. */
internal class DrawerTrackingTextToolbar(
    private val delegate: TextToolbar,
    private val registry: WebDrawerSwipeRegistry,
) : TextToolbar {
    override val status: TextToolbarStatus get() = delegate.status
    override fun showMenu(
        rect: Rect,
        onCopyRequested: (() -> Unit)?,
        onPasteRequested: (() -> Unit)?,
        onCutRequested: (() -> Unit)?,
        onSelectAllRequested: (() -> Unit)?,
    ) {
        registry.selectionActive = true
        delegate.showMenu(rect, onCopyRequested, onPasteRequested, onCutRequested, onSelectAllRequested)
    }
    override fun hide() { registry.selectionActive = false; delegate.hide() }
}
