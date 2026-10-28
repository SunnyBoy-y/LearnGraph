package com.learngraph.mobile.ui.theme

import android.graphics.Typeface
import android.os.Build
import android.text.TextPaint
import androidx.compose.material3.LocalTextStyle
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.FirstBaseline
import androidx.compose.ui.layout.LastBaseline
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFontFamilyResolver
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.TextLayoutResult
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontSynthesis
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.LineHeightStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.constrainHeight
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min
import kotlin.math.roundToInt

val WebCssLineHeightStyle = LineHeightStyle(LineHeightStyle.Alignment.Center, LineHeightStyle.Trim.None)

/** Apply also to explicit BasicTextField/TextStyle instances; CSS does not trim edge leading. */
fun TextStyle.withWebCssLineBox(): TextStyle = copy(
    fontFamily = fontFamily ?: WebGeistFontFamily,
    platformStyle = PlatformTextStyle(includeFontPadding = false), lineHeightStyle = WebCssLineHeightStyle,
)

private class WebTextResult {
    var layout: TextLayoutResult? = null
    var lastReport: String? = null
}

/**
 * UI text using the same Geist primary metrics and Android CJK fallback as Web.
 * The primary font defines the CSS baseline, even if a fallback glyph is taller.
 * Keep overflow visible, as CSS does; this wrapper never truncates the source.
 */
@Composable
fun WebText(
    text: String,
    modifier: Modifier = Modifier,
    color: Color = Color.Unspecified,
    fontSize: TextUnit = TextUnit.Unspecified,
    fontStyle: FontStyle? = null,
    fontWeight: FontWeight? = null,
    fontFamily: FontFamily? = null,
    letterSpacing: TextUnit = TextUnit.Unspecified,
    textAlign: TextAlign? = null,
    lineHeight: TextUnit = TextUnit.Unspecified,
    overflow: TextOverflow = TextOverflow.Clip,
    softWrap: Boolean = true,
    maxLines: Int = Int.MAX_VALUE,
    minLines: Int = 1,
    onTextLayout: (TextLayoutResult) -> Unit = {},
    style: TextStyle = LocalTextStyle.current,
    debugTag: String? = null,
) {
    val merged = style.merge(TextStyle(color = color, fontSize = fontSize, fontStyle = fontStyle,
        fontWeight = fontWeight, fontFamily = fontFamily, letterSpacing = letterSpacing,
        textAlign = textAlign ?: style.textAlign, lineHeight = lineHeight)).withWebCssLineBox()
    WebCssTextLayout(text, modifier, merged, minLines, maxLines, debugTag, editable = false) { resolved, recordLayout ->
        Text(text = text, color = color, style = resolved,
            overflow = overflow, softWrap = softWrap, maxLines = maxLines, minLines = minLines,
            onTextLayout = { recordLayout(it); onTextLayout(it) })
    }
}

/**
 * Place an editable paragraph in the same primary-font line box as [WebText].
 * Put padding/height constraints on this wrapper and forward its callback from
 * BasicTextField.onTextLayout. A placeholder inside BasicTextField.decorationBox
 * must use ordinary Material Text with the same style: the wrapper aligns both
 * together, so applying WebText to that placeholder would align it twice.
 */
@Composable
fun WebTextFieldLineBox(
    style: TextStyle,
    modifier: Modifier = Modifier,
    minLines: Int = 1,
    maxLines: Int = Int.MAX_VALUE,
    debugTag: String? = null,
    content: @Composable (onTextLayout: (TextLayoutResult) -> Unit) -> Unit,
) {
    WebCssTextLayout("", modifier, style.withWebCssLineBox(), minLines, maxLines, debugTag, editable = true) { _, record -> content(record) }
}

@Composable
private fun WebCssTextLayout(
    text: String,
    modifier: Modifier,
    merged: TextStyle,
    minLines: Int,
    maxLines: Int,
    debugTag: String?,
    editable: Boolean,
    content: @Composable (resolvedStyle: TextStyle, onTextLayout: (TextLayoutResult) -> Unit) -> Unit,
) {
    val density = LocalDensity.current
    val context = LocalContext.current
    require(minLines > 0 && maxLines >= minLines)
    val size = merged.fontSize.takeUnless { it == TextUnit.Unspecified } ?: 16.sp
    val leading = merged.lineHeight.takeUnless { it == TextUnit.Unspecified } ?: 1.5.em
    val sizePx = with(density) { size.toPx() }
    val leadingPx = if (leading.isEm) leading.value * sizePx else with(density) { leading.toPx() }
    val resolver = LocalFontFamilyResolver.current
    val typeface = resolver.resolve(merged.fontFamily ?: WebGeistFontFamily, merged.fontWeight ?: FontWeight.Normal,
        merged.fontStyle ?: FontStyle.Normal, merged.fontSynthesis ?: FontSynthesis.All).value as Typeface
    val paint = remember(typeface, sizePx, merged.letterSpacing, merged.fontFeatureSettings) {
        TextPaint(android.graphics.Paint.ANTI_ALIAS_FLAG or android.graphics.Paint.SUBPIXEL_TEXT_FLAG).apply {
            this.typeface = typeface; textSize = sizePx
            fontFeatureSettings = merged.fontFeatureSettings
            if (merged.letterSpacing != TextUnit.Unspecified) this.letterSpacing =
                if (merged.letterSpacing.isEm) merged.letterSpacing.value else with(density) { merged.letterSpacing.toPx() } / sizePx
            if (Build.VERSION.SDK_INT >= 24) textLocales = context.resources.configuration.locales
        }
    }
    val result = remember { WebTextResult() }
    Layout(modifier = modifier, content = {
        content(merged.copy(fontSize = size, lineHeight = leading)) { result.layout = it }
    }) { measurables, constraints ->
        // Preserve the editor's viewport budget so BasicTextField owns scrolling
        // and selection when a long draft exceeds maxHeight/maxLines.
        val child = measurables.single().measure(constraints.copy(minHeight = 0,
            maxHeight = if (editable) constraints.maxHeight else Constraints.Infinity))
        val textLayout = result.layout
        val nativeBaseline = textLayout?.firstBaseline ?: child[FirstBaseline].toFloat()
        val lines = max(minLines, min(maxLines, textLayout?.lineCount ?: 1))
        val fm = paint.fontMetrics
        val plan = WebCssLineBox.plan(leadingPx, -fm.ascent, fm.descent, nativeBaseline, lines)
        val height = constraints.constrainHeight(plan.heightPx)
        if (debugTag != null) {
            val report = "$debugTag|${text.hashCode()}|${child.width}|${child.height}|$plan|$sizePx|$leadingPx"
            if (report != result.lastReport) {
                result.lastReport = report
                reportWebTextMetrics(debugTag, text, paint, textLayout, plan, leadingPx, density.density, density.fontScale)
            }
        }
        layout(child.width, height, mapOf(
            FirstBaseline to plan.firstBaselinePx.roundToInt(),
            LastBaseline to (plan.firstBaselinePx + (lines - 1) * leadingPx).roundToInt(),
        )) {
            val integral = floor(plan.childOffsetPx).toInt()
            child.placeRelativeWithLayer(0, integral) { translationY = plan.childOffsetPx - integral }
        }
    }
}
