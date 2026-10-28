@file:OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class, androidx.compose.ui.InternalComposeUiApi::class)

package com.learngraph.mobile.ui.components

import android.database.ContentObserver
import android.os.Handler
import android.os.Looper
import android.os.Build
import android.os.SystemClock
import android.provider.Settings
import android.util.Log
import androidx.compose.animation.core.Animatable
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.Easing
import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.LinearEasing
import androidx.compose.animation.core.snap
import androidx.compose.animation.core.tween
import androidx.compose.foundation.clickable
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.MotionDurationScale
import androidx.compose.ui.composed
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.BlurEffect
import androidx.compose.ui.graphics.TileMode
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.WindowRecomposerFactory
import androidx.compose.ui.platform.WindowRecomposerPolicy
import androidx.compose.ui.platform.createLifecycleAwareWindowRecomposer
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.learngraph.mobile.BuildConfig
import kotlinx.coroutines.withContext
import kotlin.math.sqrt

val WebCssEase = CubicBezierEasing(.25f, .1f, .25f, 1f)
val WebTransitionEase = CubicBezierEasing(.4f, 0f, .2f, 1f)
val WebDrawerEase = CubicBezierEasing(.32f, .72f, 0f, 1f)

/** CSS clocks do not inherit Android's 0x / 2x Animator duration multiplier. */
object WebCssMotionClock : MotionDurationScale { override val scaleFactor: Float = 1f }

/** Call before the first Compose setContent; dialogs/popups inherit this context. */
@OptIn(androidx.compose.ui.ExperimentalComposeUiApi::class)
fun installWebCssMotionClock() {
    WindowRecomposerPolicy.setFactory(WindowRecomposerFactory { view ->
        view.createLifecycleAwareWindowRecomposer(coroutineContext = WebCssMotionClock)
    })
}

private val LocalWebReducedMotion = compositionLocalOf<Boolean?> { null }

/** Dialog windows are outside the Activity's RenderNode. Blur only layers below
 * the current modal, leaving the top modal, IME and system bar icons untouched.
 */
@Stable
internal class WebBackdropRegistry {
    private val dialogs = mutableStateListOf<Any>()
    val depth: Int get() = dialogs.size
    fun add(token: Any) { if (token !in dialogs) dialogs.add(token) }
    fun remove(token: Any) { dialogs.remove(token) }
    fun depthAbove(token: Any): Int = dialogs.indexOf(token).let { if (it < 0) 0 else dialogs.lastIndex - it }
}
internal val LocalWebBackdrop = staticCompositionLocalOf<WebBackdropRegistry?> { null }

internal fun Modifier.webBackdropBlur(depth: Int, density: Float): Modifier = if (Build.VERSION.SDK_INT >= 31 && depth > 0) {
    // CSS blur(1px) uses a 1 CSS-pixel Gaussian sigma. Stacked backdrops add
    // variance; Android uses physical pixels. Pre-31 keeps the dim fallback.
    graphicsLayer { renderEffect = BlurEffect(density * sqrt(depth.toFloat()), density * sqrt(depth.toFloat()), TileMode.Clamp) }
} else this

@Composable
fun WebMotionConfig(content: @Composable () -> Unit) {
    val reduced = observeWebReducedMotion()
    val backdrops = remember { WebBackdropRegistry() }
    val density = LocalDensity.current.density
    CompositionLocalProvider(LocalWebReducedMotion provides reduced, LocalWebBackdrop provides backdrops) {
        Box(Modifier.fillMaxSize().webBackdropBlur(backdrops.depth, density)) { content() }
    }
}

@Composable
fun rememberWebReducedMotion(): Boolean = LocalWebReducedMotion.current ?: observeWebReducedMotion()

@Composable
private fun observeWebReducedMotion(): Boolean {
    val resolver = LocalContext.current.contentResolver
    fun read() = runCatching { Settings.Global.getFloat(resolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f }.getOrDefault(false)
    var reduced by remember(resolver) { mutableStateOf(read()) }
    DisposableEffect(resolver) {
        val observer = object : ContentObserver(Handler(Looper.getMainLooper())) {
            override fun onChange(selfChange: Boolean) { reduced = read() }
        }
        resolver.registerContentObserver(Settings.Global.getUriFor(Settings.Global.ANIMATOR_DURATION_SCALE), false, observer)
        onDispose { resolver.unregisterContentObserver(observer) }
    }
    return reduced
}

@Composable
fun <T> webMotionTween(durationMillis: Int, easing: Easing = WebTransitionEase): FiniteAnimationSpec<T> =
    if (rememberWebReducedMotion()) snap() else tween(durationMillis, easing = easing)

/** One CSS transition, preserving current pixels when its target reverses. */
@Stable
class WebAnimatedFloat(initial: Float, private val debugTag: String? = null) {
    val animation = Animatable(initial)
    val value: Float get() = animation.value
    private val tracker = WebCssTransitionTracker()
    suspend fun snapTo(value: Float) { tracker.reset(); withContext(WebCssMotionClock) { animation.snapTo(value) } }
    suspend fun stop() { tracker.reset(); animation.stop() }
    suspend fun animateTo(target: Float, durationMillis: Int, easing: Easing = WebCssEase, delayMillis: Int = 0) {
        if (animation.isRunning && animation.targetValue == target) return
        val duration = tracker.begin(value, target, durationMillis, animation.isRunning)
        val started = SystemClock.uptimeMillis()
        val samples = if (BuildConfig.DEBUG && debugTag != null) ArrayList<String>() else null
        var lastSample = -16L
        try {
            withContext(WebCssMotionClock) {
                animation.animateTo(target, tween(duration, delayMillis = delayMillis, easing = easing)) {
                    val t = SystemClock.uptimeMillis() - started
                    if (samples != null && t - lastSample >= 15 && samples.size < 90) {
                        samples.add("$t:${"%.4f".format(java.util.Locale.ROOT, value)}"); lastSample = t
                    }
                }
            }
        } finally {
            if (samples != null) Log.d("LGUiParity", "native-motion tag=$debugTag target=$target duration=$duration delay=$delayMillis samples=${samples.joinToString(",")}")
        }
    }
}

@Composable
fun rememberWebAnimatedFloat(target: Float, durationMillis: Int, easing: Easing = WebCssEase, debugTag: String? = null): State<Float> {
    val state = remember { WebAnimatedFloat(target, debugTag) }
    val reduced = rememberWebReducedMotion()
    LaunchedEffect(target, durationMillis, easing, reduced) { state.animateTo(target, WebMotionContract.duration(durationMillis, reduced), easing) }
    return remember(state) { derivedStateOf { state.value } }
}

/** button.tsx: active:not-aria-[haspopup]:translate-y-px, transition-all 150ms.
 * hasPopup buttons do not translate. There is no Material ink or invented scale.
 */
fun Modifier.webClickFeedback(
    enabled: Boolean = true,
    hasPopup: Boolean = false,
    translateOnPress: Boolean = true,
    onClickLabel: String? = null,
    role: Role? = null,
    onClick: () -> Unit,
): Modifier = composed {
    val interaction = remember { MutableInteractionSource() }
    val pressed by interaction.collectIsPressedAsState()
    val offset by rememberWebAnimatedFloat(if (pressed && !hasPopup && translateOnPress) 1f else 0f, WebMotionContract.ButtonMillis, WebTransitionEase)
    val pixels = LocalDensity.current.density
    graphicsLayer { translationY = offset * pixels }
        .clickable(interactionSource = interaction, indication = null, enabled = enabled, onClickLabel = onClickLabel, role = role, onClick = onClick)
}

/** Web's animate-spin (1s linear), including its one-iteration reduced state. */
@Composable
fun WebSpinner(modifier: Modifier = Modifier, size: Dp = 12.dp, periodMillis: Int = 1000) {
    val reduced = rememberWebReducedMotion()
    val rotation = remember { Animatable(0f) }
    LaunchedEffect(reduced, periodMillis) {
        withContext(WebCssMotionClock) {
            rotation.snapTo(0f)
            if (!reduced) while (true) { rotation.animateTo(360f, tween(periodMillis.coerceAtLeast(1), easing = LinearEasing)); rotation.snapTo(0f) }
        }
    }
    Box(modifier.size(size).graphicsLayer { rotationZ = rotation.value }) { WebGlyph(WebIcon.LoaderCircle, modifier = Modifier.size(size)) }
}
