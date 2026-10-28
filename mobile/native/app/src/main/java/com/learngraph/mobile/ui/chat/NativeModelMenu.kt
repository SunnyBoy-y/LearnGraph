package com.learngraph.mobile.ui.chat

import androidx.compose.animation.animateColorAsState
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.interaction.collectIsFocusedAsState
import androidx.compose.foundation.interaction.collectIsPressedAsState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.Canvas
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import com.learngraph.mobile.ui.theme.WebText as Text
import com.learngraph.mobile.ui.theme.withWebCssLineBox
import com.learngraph.mobile.ui.theme.WebTextFieldLineBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.paneTitle
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.learngraph.mobile.data.ComposerOptions
import com.learngraph.mobile.data.NativeModelChoice
import com.learngraph.mobile.data.nativeModelMatchesQuery
import com.learngraph.mobile.ui.components.WebGlyph
import com.learngraph.mobile.ui.components.WebIcon
import com.learngraph.mobile.ui.components.WebThinkingSlider
import com.learngraph.mobile.ui.components.WebPopup
import com.learngraph.mobile.ui.components.webCssBoxShadow
import com.learngraph.mobile.ui.components.webCssOuterRing
import com.learngraph.mobile.ui.components.webMotionTween
import com.learngraph.mobile.ui.components.webDrawerSwipeExcluded

/** Text-mode picker from chat-pages.tsx; call inside the model trigger's Box. */
@Composable
fun NativeModelMenu(
    expanded: Boolean,
    options: ComposerOptions,
    onOptionsChange: (ComposerOptions) -> Unit,
    choices: List<NativeModelChoice>,
    loading: Boolean,
    onDismiss: () -> Unit,
    availableHeight: Dp,
    availableWidth: Dp,
    modifier: Modifier = Modifier,
) {
    var search by remember { mutableStateOf("") }
    LaunchedEffect(expanded) { if (!expanded) search = "" }
    val width = minOf(320.dp, (availableWidth - 24.dp).coerceAtLeast(0.dp))
    val height = minOf(540.dp, availableHeight * .68f).coerceAtLeast(0.dp)
    val filtered = remember(choices, search) { choices.filter { nativeModelMatchesQuery(it, search) } }
    fun requestDismiss() { search = ""; onDismiss() }
    WebPopup(expanded = expanded, onDismissRequest = ::requestDismiss, alignEnd = true) {
        val focus = LocalFocusManager.current
        val keyboard = LocalSoftwareKeyboardController.current
        fun dismiss() { focus.clearFocus(force = true); keyboard?.hide(); requestDismiss() }
        val dark = MaterialTheme.colorScheme.background.luminance() < .5f
        val foreground = MaterialTheme.colorScheme.onBackground
        val secondary = MaterialTheme.colorScheme.onSurfaceVariant
        val border = if (dark) Color(0xFF454B52) else MaterialTheme.colorScheme.outline
        val hover = if (dark) Color(0xFF292D32) else MaterialTheme.colorScheme.surfaceVariant
        val stop = if (options.responseMode == "fast") 0 else listOf("off", "low", "medium", "high", "xhigh").indexOf(options.thinkingMode).coerceAtLeast(0)
        var searchFocused by remember { mutableStateOf(false) }
        val searchBorder by animateColorAsState(if (searchFocused) foreground.copy(alpha = .55f) else border, webMotionTween(150), label = "model-search-border")
        Box(modifier.width(width).webCssBoxShadow().webCssOuterRing(14.dp, foreground.copy(alpha = .1f))
            .webDrawerSwipeExcluded().semantics { paneTitle = "响应与思考力度" }) {
        Surface(shape = RoundedCornerShape(14.dp), color = if (dark) Color(0xFF1C1F23) else Color.White,
            contentColor = foreground, tonalElevation = 0.dp, shadowElevation = 0.dp, modifier = Modifier.fillMaxWidth()) {
            // Web scrolls the whole menu, including its headings and slider.
            LazyColumn(Modifier.fillMaxWidth().heightIn(max = height).selectableGroup(), contentPadding = PaddingValues(6.dp)) {
                item("response-label") { ModelMenuLabel("响应与思考力度", secondary) }
                item("thinking") {
                    WebThinkingSlider(stop, enabled = true, onChange = { index ->
                        onOptionsChange(options.copy(responseMode = if (index == 0) "fast" else "agentic", thinkingMode = listOf("off", "low", "medium", "high", "xhigh")[index]))
                    }, modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp))
                }
                item("separator") {
                    val line = MaterialTheme.colorScheme.outline
                    Canvas(Modifier.fillMaxWidth().height(9.dp)) {
                        drawRect(line, topLeft = androidx.compose.ui.geometry.Offset(-4.dp.toPx(), 4.dp.toPx()),
                            size = androidx.compose.ui.geometry.Size(size.width + 8.dp.toPx(), 1.dp.toPx()))
                    }
                }
                item("model-label") { ModelMenuLabel("模型", secondary) }
                item("search") {
                    Box(Modifier.fillMaxWidth().padding(start = 4.dp, end = 4.dp, bottom = 4.dp).height(32.dp)
                        .border((2f / 3f).dp, searchBorder, RoundedCornerShape(12.dp))
                        .background(if (dark) border.copy(alpha = .3f) else Color.Transparent, RoundedCornerShape(12.dp))) {
                        WebGlyph(WebIcon.Search, modifier = Modifier.align(Alignment.CenterStart).padding(start = 8.dp).size(14.dp), color = secondary)
                        val searchStyle = TextStyle(fontFamily = FontFamily.Monospace, fontSize = 12.sp, lineHeight = 16.sp, color = foreground).withWebCssLineBox()
                        WebTextFieldLineBox(style = searchStyle, maxLines = 1, modifier = Modifier.align(Alignment.CenterStart).fillMaxWidth().padding(start = 32.dp, end = 10.dp)
                            .onFocusChanged { searchFocused = it.isFocused }) { recordLayout ->
                            BasicTextField(search, { search = it }, singleLine = true, textStyle = searchStyle,
                                onTextLayout = recordLayout, cursorBrush = SolidColor(foreground),
                                modifier = Modifier.fillMaxWidth().semantics { contentDescription = "搜索模型" },
                                decorationBox = { field -> Box { if (search.isEmpty()) androidx.compose.material3.Text("搜索模型名称或 Provider…", style = searchStyle, color = secondary, maxLines = 1, overflow = TextOverflow.Ellipsis); field() } })
                        }
                    }
                }
                if (filtered.isEmpty()) item("empty") {
                    Text(if (loading) "正在载入模型…" else "暂无可用模型", fontSize = 14.sp, lineHeight = 21.sp, color = secondary.copy(alpha = .5f), modifier = Modifier.padding(horizontal = 6.dp, vertical = 4.dp))
                }
                items(filtered, key = { "${it.providerId}:${it.modelId}" }) { choice ->
                    val selected = options.providerId == choice.providerId && options.modelId == choice.modelId
                    val interaction = remember { MutableInteractionSource() }
                    val pressed by interaction.collectIsPressedAsState()
                    val focused by interaction.collectIsFocusedAsState()
                    Box(Modifier.fillMaxWidth().background(if (pressed || focused) hover else Color.Transparent, RoundedCornerShape(9.6.dp))
                        .selectable(selected = selected, interactionSource = interaction, indication = null, role = Role.RadioButton, onClick = {
                            // A model change never silently rewrites mode/thinking/search.
                            onOptionsChange(options.copy(providerId = choice.providerId, modelId = choice.modelId)); dismiss()
                        })) {
                        Row(Modifier.fillMaxWidth().padding(start = 6.dp, end = 32.dp, top = 4.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                            Column(Modifier.weight(1f)) {
                                Text(choice.modelId, fontFamily = FontFamily.Monospace, fontSize = 14.sp, lineHeight = 20.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                Text("${choice.providerName} · ${choice.protocolLabel}", fontSize = 10.sp, lineHeight = 14.2857.sp, color = secondary)
                            }
                            if (choice.supportsImageInput) Text(" · 图片", fontSize = 14.sp, lineHeight = 20.sp)
                        }
                        Box(Modifier.align(Alignment.CenterEnd).padding(end = 8.dp).size(16.dp), contentAlignment = Alignment.Center) {
                            if (selected) WebGlyph(WebIcon.Check, modifier = Modifier.size(16.dp), color = foreground)
                        }
                    }
                }
            }
        }
        }
    }
}

@Composable
private fun ModelMenuLabel(text: String, color: Color) {
    Text(text, fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium, color = color, modifier = Modifier.padding(horizontal = 6.dp, vertical = 4.dp))
}
