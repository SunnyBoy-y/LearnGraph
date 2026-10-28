package com.learngraph.mobile.ui.home

import android.net.Uri
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.material3.Text as MaterialText
import com.learngraph.mobile.ui.theme.WebText as Text
import com.learngraph.mobile.ui.theme.withWebCssLineBox
import com.learngraph.mobile.ui.theme.WebTextFieldLineBox
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.input.key.*
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import com.learngraph.mobile.data.NativeWorkspaceSearchHit
import com.learngraph.mobile.ui.components.WebDialog
import com.learngraph.mobile.ui.components.WebSpinner
import com.learngraph.mobile.ui.components.webClickFeedback
import com.learngraph.mobile.ui.components.WebGlyph
import com.learngraph.mobile.ui.components.WebIcon
import com.learngraph.mobile.ui.theme.WebGeistFontFamily
import java.util.concurrent.atomic.AtomicInteger
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

/** Same server search as WorkspaceSearchDialog: documents, sessions, message contents and IDs. */
@Composable
internal fun NativeWorkspaceSearchDialog(vm: HomeViewModel, onSelectSession: (String) -> Unit, onNavigate: (String) -> Unit, onDismiss: () -> Unit) {
    var queryValue by remember { mutableStateOf(TextFieldValue("")) }
    val query = queryValue.text
    val trimmedQuery = query.trim()
    var debouncedQuery by remember { mutableStateOf("") }
    var kind by remember { mutableStateOf("all") }
    var hits by remember { mutableStateOf(emptyList<NativeWorkspaceSearchHit>()) }
    var hasMore by remember { mutableStateOf(false) }
    var nextOffset by remember { mutableIntStateOf(0) }
    var loading by remember { mutableStateOf(true) }
    var loadingMore by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var loadedQuery by remember { mutableStateOf<String?>(null) }
    var loadedKind by remember { mutableStateOf<String?>(null) }
    var retry by remember { mutableIntStateOf(0) }
    var activeIndex by remember { mutableIntStateOf(-1) }
    val generation = remember { AtomicInteger(0) }
    val coroutine = rememberCoroutineScope()
    val listState = rememberLazyListState()
    val focus = remember { FocusRequester() }
    val waiting = trimmedQuery != debouncedQuery || loadedQuery != debouncedQuery || loadedKind != kind
    val visibleHits = if (waiting) emptyList() else hits
    LaunchedEffect(Unit) { focus.requestFocus() }
    // Match the Web query key: the initial empty query and filter/retry changes
    // fetch immediately. Only text changes wait for the 250ms debounce.
    LaunchedEffect(trimmedQuery) {
        delay(250)
        debouncedQuery = trimmedQuery
    }
    LaunchedEffect(debouncedQuery, kind, retry) {
        val version = generation.incrementAndGet()
        val requestedQuery = debouncedQuery; val requestedKind = kind
        loading = true; loadingMore = false; error = null; activeIndex = -1
        try {
            val page = vm.searchWorkspace(requestedQuery, requestedKind, 0)
            if (generation.get() == version) {
                loadedQuery = requestedQuery; loadedKind = requestedKind
                if (page != null) { hits = page.items; hasMore = page.hasMore; nextOffset = page.nextOffset }
                else { hits = emptyList(); hasMore = false; error = vm.error.value ?: "搜索失败" }
            }
        } finally { if (generation.get() == version) loading = false }
    }
    fun moveActive(direction: Int) {
        if (visibleHits.isEmpty()) return
        activeIndex = if (activeIndex < 0) { if (direction > 0) 0 else visibleHits.lastIndex } else (activeIndex + direction + visibleHits.size) % visibleHits.size
        coroutine.launch { listState.scrollToItem(activeIndex + 1) }
    }
    val dialogPadding = 16.dp
    WebDialog(onDismissRequest = onDismiss, contentPadding = dialogPadding, animateContent = false, exitDurationMillis = 0) {
        fun dismiss() = close { onDismiss() }
        fun select(hit: NativeWorkspaceSearchHit) {
            close {
                onDismiss()
                when (hit.kind) {
                    "file" -> onNavigate("documents/${Uri.encode(hit.id)}")
                    "message" -> onNavigate("chat/${Uri.encode(hit.sessionId ?: hit.id)}?message_id=${Uri.encode(hit.id)}")
                    "session" -> onSelectSession(hit.sessionId ?: hit.id)
                }
            }
        }
        BoxWithConstraints(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
                // These constraints already exclude system bars and the IME. Capture
                // them before the nested Column/LazyColumn layout receivers.
                val panelMaxHeight = availableHeight
                // The Web viewport excludes system bars and shrinks with the IME.
                // availableHeight also excludes our dialog padding; restore only
                // that padding before applying CSS min(460px, 58dvh), min-height:200px.
                val viewportHeight = panelMaxHeight + dialogPadding * 2
                val resultsHeight = minOf(460.dp, viewportHeight * .58f).coerceAtLeast(200.dp)
                Surface(shape = RoundedCornerShape(22.dp), color = if (MaterialTheme.colorScheme.background.luminance() < .5f) Color(0xFF1C1F23) else Color.White,
                    border = BorderStroke(1.dp, Color.Black.copy(alpha = .1f)), shadowElevation = 24.dp,
                    modifier = panelModifier.widthIn(max = 760.dp).fillMaxWidth().pointerInput(Unit) {
                    // Become the foreground hit target even for empty panel space.
                    // No events are consumed: child scrolling, selection and text
                    // editing retain their own gesture handling.
                    awaitPointerEventScope { while (true) awaitPointerEvent() }
                }) {
                    // DialogContent's text-sm gives all smaller descendants the
                    // same unitless 20/14 leading, including filters and footer.
                    ProvideTextStyle(MaterialTheme.typography.bodyMedium.copy(lineHeight = (20f / 14f).em)) {
                    Column(Modifier.heightIn(max = panelMaxHeight).onPreviewKeyEvent { event ->
                        if (event.type != KeyEventType.KeyDown || queryValue.composition != null) false else when (event.key) {
                            Key.DirectionDown -> { moveActive(1); true }
                            Key.DirectionUp -> { moveActive(-1); true }
                            Key.Enter -> { visibleHits.getOrNull(activeIndex.coerceAtLeast(0))?.let(::select); true }
                            Key.Escape -> { dismiss(); true }
                            else -> false
                        }
                    }) {
                        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 18.dp, bottom = 14.dp), verticalAlignment = Alignment.CenterVertically) {
                            WebGlyph(WebIcon.Search, modifier = Modifier.size(21.dp))
                            Spacer(Modifier.width(10.dp))
                            val searchStyle = TextStyle(fontFamily = WebGeistFontFamily, fontSize = 16.sp, lineHeight = 28.sp, color = MaterialTheme.colorScheme.onSurface).withWebCssLineBox()
                            WebTextFieldLineBox(style = searchStyle, maxLines = 1, modifier = Modifier.weight(1f)) { recordLayout ->
                                BasicTextField(queryValue, { queryValue = if (it.text.length <= 240) it else TextFieldValue(it.text.take(240), TextRange(240)) }, singleLine = true,
                                    textStyle = searchStyle, onTextLayout = recordLayout,
                                    cursorBrush = SolidColor(MaterialTheme.colorScheme.onSurface), keyboardOptions = KeyboardOptions(imeAction = ImeAction.Go), keyboardActions = KeyboardActions(onGo = { visibleHits.getOrNull(activeIndex.coerceAtLeast(0))?.let(::select) }),
                                    modifier = Modifier.fillMaxWidth().focusRequester(focus), decorationBox = { field -> Box { if (query.isEmpty()) MaterialText("搜索文件、会话、消息或 ID…", style = searchStyle, maxLines = 1, color = MaterialTheme.colorScheme.onSurfaceVariant); field() } })
                            }
                            if (query.isNotEmpty()) {
                                Spacer(Modifier.width(10.dp))
                                Box(Modifier.size(30.dp).clip(RoundedCornerShape(8.dp)).webClickFeedback(hasPopup = true) { queryValue = TextFieldValue("") }, contentAlignment = Alignment.Center) { WebGlyph(WebIcon.Close, "清空搜索", Modifier.size(18.dp)) }
                            }
                            Spacer(Modifier.width(10.dp))
                            Box(Modifier.size(30.dp).clip(RoundedCornerShape(8.dp)).webClickFeedback(hasPopup = true) { dismiss() }, contentAlignment = Alignment.Center) { WebGlyph(WebIcon.Close, "关闭搜索", Modifier.size(18.dp)) }
                        }
                        Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, bottom = 16.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            listOf("all" to "全部", "session" to "会话", "message" to "消息", "file" to "文件").forEach { (value, label) ->
                                Text(label, fontSize = 13.sp, fontWeight = if (kind == value) FontWeight.SemiBold else FontWeight.Normal, color = if (kind == value) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                                    modifier = Modifier.clip(RoundedCornerShape(8.dp)).background(if (kind == value) MaterialTheme.colorScheme.surfaceVariant else Color.Transparent).webClickFeedback(hasPopup = true) { kind = value }.padding(horizontal = 13.dp, vertical = 5.dp))
                            }
                        }
                        HorizontalDivider(color = MaterialTheme.colorScheme.outline)
                        // A preferred height keeps sparse/loading states the same
                        // size. Weight only caps it if a very short IME viewport
                        // cannot fit the CSS height plus its header and footer.
                        LazyColumn(state = listState, modifier = Modifier.fillMaxWidth().height(resultsHeight).weight(1f, fill = false), contentPadding = PaddingValues(horizontal = 12.dp, vertical = 18.dp)) {
                            item("heading") { Text(if (query.isNotBlank()) "搜索结果" else when (kind) { "file" -> "搜索文件"; "message" -> "搜索消息"; else -> "最近会话" }, fontSize = 13.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(start = 12.dp, end = 12.dp, bottom = 12.dp)) }
                            when {
                                loading || waiting -> item("loading") { Column(Modifier.fillMaxWidth().heightIn(min = 260.dp).padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                                    WebSpinner(size = 22.dp); Spacer(Modifier.height(12.dp)); Text("正在搜索…", fontSize = 14.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                } }
                                error != null -> item("error") { Column(Modifier.fillMaxWidth().heightIn(min = 260.dp).padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) { Text(error.orEmpty(), fontSize = 14.sp, color = MaterialTheme.colorScheme.error); Spacer(Modifier.height(12.dp)); SearchResultButton("重试", onClick = { retry++ }) } }
                                visibleHits.isEmpty() -> item("empty") { Column(Modifier.fillMaxWidth().heightIn(min = 260.dp).padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
                                    Text(if (query.isNotBlank()) "没有找到相关结果" else if (kind in listOf("file", "message")) "输入关键词开始搜索" else "暂无最近会话", fontSize = 14.sp)
                                    Spacer(Modifier.height(12.dp)); Text(if (query.isNotBlank()) "试试其他关键词，或粘贴完整的会话 / 消息 ID" else if (kind in listOf("file", "message")) "支持名称、正文关键词和 ID" else "开始一段对话后，可以在这里快速找到它", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                } }
                                else -> itemsIndexed(visibleHits, key = { _, hit -> "${hit.kind}:${hit.id}" }) { index, hit ->
                                    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).background(if (activeIndex == index) MaterialTheme.colorScheme.surfaceVariant else Color.Transparent).webClickFeedback(hasPopup = true) { select(hit) }.padding(horizontal = 10.dp, vertical = 13.dp), verticalAlignment = Alignment.CenterVertically) {
                                        WebGlyph(if (hit.kind == "file") WebIcon.FileText else WebIcon.MessageCircle, modifier = Modifier.size(20.dp), color = MaterialTheme.colorScheme.onSurface)
                                        Spacer(Modifier.width(12.dp))
                                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                                            MaterialText(searchHighlight(hit.title, query.trim(), MaterialTheme.colorScheme.primary.copy(alpha = .15f)), fontSize = 15.sp, lineHeight = 22.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                                            if (hit.snippet.isNotBlank()) MaterialText(searchHighlight(hit.snippet, query.trim(), MaterialTheme.colorScheme.primary.copy(alpha = .15f)), fontSize = 12.sp, lineHeight = 19.sp, maxLines = 2, overflow = TextOverflow.Ellipsis, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        }
                                        if (query.isNotBlank()) {
                                            Spacer(Modifier.width(12.dp)); Text(when (hit.kind) { "file" -> "文件"; "message" -> "消息"; else -> "会话" }, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                                        }
                                        Spacer(Modifier.width(12.dp))
                                        Box(Modifier.size(15.dp)) { if (activeIndex == index) WebGlyph(WebIcon.ArrowUpRight, modifier = Modifier.size(15.dp)) }
                                    }
                                }
                            }
                            if (hasMore && !loading && !waiting && error == null) item("more") { Box(Modifier.fillMaxWidth().padding(top = 16.dp, bottom = 4.dp), contentAlignment = Alignment.Center) { SearchResultButton(if (loadingMore) "加载中…" else "加载更多", onClick = {
                                if (!loadingMore) {
                                    loadingMore = true
                                    val version = generation.get(); val requestedQuery = debouncedQuery; val requestedKind = kind; val offset = nextOffset
                                    coroutine.launch { try {
                                        val page = vm.searchWorkspace(requestedQuery, requestedKind, offset)
                                        if (generation.get() == version) {
                                            if (page != null) { hits = (hits + page.items).distinctBy { "${it.kind}:${it.id}" }; hasMore = page.hasMore; nextOffset = page.nextOffset }
                                            else error = vm.error.value ?: "加载更多失败"
                                        }
                                    } finally { if (generation.get() == version) loadingMore = false } }
                                }
                            }, enabled = !loadingMore) } }
                        }
                        HorizontalDivider(color = MaterialTheme.colorScheme.outline)
                        Text("搜索当前工作区", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp))
                    }
                    }
                }
        }
    }
}

@Composable
private fun SearchResultButton(text: String, enabled: Boolean = true, onClick: () -> Unit) {
    Text(text, fontSize = 13.sp, modifier = Modifier.clip(RoundedCornerShape(8.dp))
        .background(MaterialTheme.colorScheme.surfaceVariant).webClickFeedback(enabled = enabled, hasPopup = true, onClick = onClick)
        .padding(horizontal = 16.dp, vertical = 8.dp))
}

private fun searchHighlight(text: String, query: String, background: Color): AnnotatedString {
    if (query.isEmpty()) return AnnotatedString(text)
    return buildAnnotatedString {
        var cursor = 0
        while (cursor < text.length) {
            val index = text.indexOf(query, cursor, ignoreCase = true)
            if (index < 0) { append(text.substring(cursor)); break }
            append(text.substring(cursor, index)); withStyle(SpanStyle(background = background)) { append(text.substring(index, index + query.length)) }
            cursor = index + query.length
        }
    }
}
