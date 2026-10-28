package com.learngraph.mobile.ui.chat

import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.tween
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.interaction.collectIsDraggedAsState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.PlatformTextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.data.ComposerOptions
import com.learngraph.mobile.data.Message
import com.learngraph.mobile.data.NativeModelChoice
import com.learngraph.mobile.data.NativeModelCatalog
import com.learngraph.mobile.data.resolveNativeTextModelOptions
import com.learngraph.mobile.ui.components.WebGlyph
import com.learngraph.mobile.ui.components.WebIcon
import com.learngraph.mobile.ui.components.webDrawerSwipeExcluded
import com.learngraph.mobile.ui.components.webDrawerHorizontalScrollable
import com.learngraph.mobile.ui.web.NativeRendererPolicy
import com.learngraph.mobile.ui.web.NativeComposerHandoff
import com.learngraph.mobile.util.Haptics
import com.learngraph.mobile.ui.theme.WebGeistFontFamily
import com.learngraph.mobile.ui.theme.WebText as Text
import com.learngraph.mobile.ui.theme.withWebCssLineBox
import com.learngraph.mobile.ui.theme.WebTextFieldLineBox
import com.learngraph.mobile.ui.components.WebDropdownMenu as DropdownMenu
import com.learngraph.mobile.ui.components.WebDropdownMenuItem as DropdownMenuItem
import com.learngraph.mobile.ui.components.WebAlertDialog as AlertDialog
import com.learngraph.mobile.ui.components.rememberWebDialogCloseAction
import com.learngraph.mobile.ui.components.webMotionTween
import com.learngraph.mobile.ui.components.webClickFeedback
import com.learngraph.mobile.ui.components.WebSpinner
import com.learngraph.mobile.web.EmbeddedBrowserActivity
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlin.math.roundToInt

/** Phone geometry follows the actual responsive Web UI, rather than a separate ChatGPT skin. */
@Composable
fun ChatScreen(
    vm: ChatViewModel?,
    onBack: () -> Unit,
    onOpenWeb: () -> Unit,
    title: String = "LearnGraph",
    onNewChat: () -> Unit = {},
    onSendNew: (String, ComposerOptions) -> Unit = { _, _ -> },
    creatingSession: Boolean = false,
    createError: String? = null,
    initialMessage: String? = null,
    initialOptions: ComposerOptions? = null,
    onInitialMessageSent: () -> Unit = {},
    modelChoices: List<NativeModelChoice> = emptyList(),
    loadingModels: Boolean = false,
    onLoadModels: () -> Unit = {},
    onNavigateWeb: (String) -> Unit = { onOpenWeb() },
    onOpenGraph: () -> Unit = onOpenWeb,
    onOpenSession: (String, String?, ComposerOptions) -> Unit = { _, _, _ -> },
    modelCatalog: NativeModelCatalog? = null,
    modelSnapshot: JsonObject? = null,
    modelSnapshotReady: Boolean = true,
    modelCatalogError: String? = null,
) {
    val messages by (vm?.messages?.collectAsState() ?: remember { mutableStateOf(emptyList<Message>()) })
    val loading by (vm?.loading?.collectAsState() ?: remember { mutableStateOf(false) })
    val sending by (vm?.sending?.collectAsState() ?: remember { mutableStateOf(false) })
    val error by (vm?.error?.collectAsState() ?: remember { mutableStateOf<String?>(null) })
    val failedDraft by (vm?.failedDraft?.collectAsState() ?: remember { mutableStateOf<String?>(null) })
    val requestedDraft by (vm?.requestedComposerDraft?.collectAsState() ?: remember { mutableStateOf<String?>(null) })
    val offline by (vm?.isOffline?.collectAsState() ?: remember { mutableStateOf(false) })
    val refreshing by (vm?.refreshing?.collectAsState() ?: remember { mutableStateOf(false) })
    val hasOlder by (vm?.hasMoreBefore?.collectAsState() ?: remember { mutableStateOf(false) })
    val loadingOlder by (vm?.loadingOlder?.collectAsState() ?: remember { mutableStateOf(false) })
    val presentations by (vm?.presentations?.collectAsState() ?: remember { mutableStateOf(emptyMap<String, NativeMessagePresentation>()) })
    val queues by (vm?.queuedMessages?.collectAsState() ?: remember { mutableStateOf(emptyList<QueuedNativeMessage>()) })
    val liveOptions by (vm?.composerOptions?.collectAsState() ?: remember { mutableStateOf<ComposerOptions?>(null) })
    val composerOptionsReady by (vm?.composerOptionsReady?.collectAsState() ?: remember { mutableStateOf(true) })
    val messageVersions by (vm?.messageVersions?.collectAsState() ?: remember { mutableStateOf(emptyMap<String, NativeMessageVersionState>()) })
    val conversationVersions by (vm?.conversationVersions?.collectAsState() ?: remember { mutableStateOf(emptyMap<String, NativeConversationVersions>()) })
    val messageOperation by (vm?.messageOperation?.collectAsState() ?: remember { mutableStateOf<String?>(null) })
    val openSession by (vm?.openSession?.collectAsState() ?: remember { mutableStateOf<NativeOpenSession?>(null) })
    val sessionClosed by (vm?.sessionClosed?.collectAsState() ?: remember { mutableStateOf(false) })
    var draftOptionsJson by rememberSaveable { mutableStateOf(Json.encodeToString(ComposerOptions.serializer(), ComposerOptions())) }
    val storedOptions = liveOptions ?: remember(draftOptionsJson) { Json.decodeFromString(ComposerOptions.serializer(), draftOptionsJson) }
    // Wait for local restoration and the first session snapshot before filling
    // gaps; a temporary product default must never overwrite a saved choice.
    val modelPrefsReady = composerOptionsReady && (modelSnapshotReady || !storedOptions.providerId.isNullOrEmpty() && !storedOptions.modelId.isNullOrEmpty())
    val options = if (modelPrefsReady) resolveNativeTextModelOptions(storedOptions, modelSnapshot, modelCatalog) else storedOptions
    fun hasResolvedModel(value: ComposerOptions) = if (value.generationMode == "image")
        !value.imageProviderId.isNullOrEmpty() && !value.imageModelId.isNullOrEmpty()
    else modelCatalog != null && !value.providerId.isNullOrEmpty() && !value.modelId.isNullOrEmpty()
    val modelSelectionReady = modelPrefsReady && hasResolvedModel(options)
    fun updateOptions(next: ComposerOptions) { if (vm != null) vm.setComposerOptions(next) else draftOptionsJson = Json.encodeToString(ComposerOptions.serializer(), next) }
    LaunchedEffect(modelCatalog, modelSnapshot, storedOptions, modelPrefsReady) {
        if (modelCatalog != null && modelPrefsReady && options != storedOptions) updateOptions(options)
    }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    val context = LocalContext.current
    val composerFocus = LocalFocusManager.current
    val composerKeyboard = LocalSoftwareKeyboardController.current
    val app = context.applicationContext as LearnGraphApp
    val sidebarActivityScope = com.learngraph.mobile.data.NativeSidebarActivityStore.scope(app.api)
    val sidebarActivity = remember(app, sidebarActivityScope) {
        sidebarActivityScope?.let { com.learngraph.mobile.data.NativeSidebarActivityStore.get(context).observe(it) }
            ?: kotlinx.coroutines.flow.MutableStateFlow(com.learngraph.mobile.data.NativeSidebarActivitySnapshot())
    }
    val activitySnapshot by sidebarActivity.collectAsState()
    val suggestionAnchor = remember(messages) { nativeSuggestionAnchor(messages) }
    val showFollowUpPrompts = vm != null && !loading && !sending && error == null && !sessionClosed &&
        suggestionAnchor != null && vm.sessionId !in activitySnapshot.voiceActiveSessionIds
    val tailIndex = messages.size + 1 + if (showFollowUpPrompts) 1 else 0
    var input by rememberSaveable { mutableStateOf("") }
    var retryTarget by remember { mutableStateOf<Message?>(null) }
    fun openSharedComposer(command: String, content: String = input) {
        NativeComposerHandoff.queue(context, app.api, vm?.sessionId, command, content, options)?.let {
            onNavigateWeb(it)
        }
    }
    fun copyText(value: String, label: String) {
        val clipboard = context.getSystemService(android.content.Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
        clipboard.setPrimaryClip(android.content.ClipData.newPlainText(label, value))
        android.widget.Toast.makeText(context, label, android.widget.Toast.LENGTH_SHORT).show()
    }
    var modelsOpen by remember { mutableStateOf(false) }
    var pageToolsOpen by remember { mutableStateOf(false) }
    var enlarged by rememberSaveable { mutableStateOf(false) }
    var composerExpanded by remember { mutableStateOf(false) }
    var composerFocused by remember { mutableStateOf(false) }
    var scrollMode by rememberSaveable { mutableStateOf("FOLLOW_ACTIVITY") }
    var viewportHeight by remember { mutableIntStateOf(0) }
    var viewportTop by remember { mutableFloatStateOf(0f) }
    var tailSpace by remember { mutableFloatStateOf(0f) }
    var anchoredTurn by remember { mutableStateOf<String?>(null) }
    var answerAnchored by remember { mutableStateOf<String?>(null) }
    val answerPositions = remember { mutableStateMapOf<String, Float>() }
    val dragging by listState.interactionSource.collectIsDraggedAsState()
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val refreshOnResume by rememberUpdatedState { if (!loading && !sending) vm?.loadMessages() }
    val consumeTransferredDraft by rememberUpdatedState {
        NativeComposerHandoff.takeReturnedDraft(context, app.api, vm?.sessionId)?.let { returned ->
            if (input == returned.original || input.isBlank()) input = returned.current
            returned.options?.let(::updateOptions)
        }
    }
    DisposableEffect(lifecycle, vm) {
        if (lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) consumeTransferredDraft()
        vm?.setHapticsForeground(lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED))
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) { refreshOnResume(); consumeTransferredDraft() }
            vm?.setHapticsForeground(lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED))
        }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer); vm?.setHapticsForeground(false) }
    }
    LaunchedEffect(initialMessage, initialOptions, loading, offline, vm, modelCatalog, loadingModels, modelPrefsReady) {
        if (!initialMessage.isNullOrBlank()) {
            val pendingOptions = resolveNativeTextModelOptions(initialOptions ?: options, modelSnapshot, modelCatalog)
            if (vm != null && !loading && modelPrefsReady && !offline && hasResolvedModel(pendingOptions)) {
                vm.setComposerOptions(pendingOptions)
                vm.send(initialMessage); onInitialMessageSent()
            } else if (offline || !loading && !loadingModels && (!hasResolvedModel(pendingOptions) || modelCatalogError != null)) {
                input = initialMessage; onInitialMessageSent()
            }
        }
    }
    LaunchedEffect(failedDraft) { failedDraft?.let { if (input.isBlank()) { input = it; vm?.consumeFailedDraft() } } }
    LaunchedEffect(requestedDraft) { requestedDraft?.let { input = it; vm?.consumeRequestedComposerDraft() } }
    LaunchedEffect(input.isEmpty()) { if (input.isEmpty()) enlarged = false }
    LaunchedEffect(openSession) { openSession?.let { onOpenSession(it.sessionId, it.prompt, it.options); vm?.consumeOpenSession() } }
    val nearBottom by remember {
        derivedStateOf {
            val layout = listState.layoutInfo
            val last = layout.visibleItemsInfo.lastOrNull()
            last == null || last.index == layout.totalItemsCount - 1 &&
                last.offset + last.size - layout.viewportEndOffset <= with(density) { 96.dp.toPx() }
        }
    }
    LaunchedEffect(dragging) { if (dragging) scrollMode = "MANUAL_READING" }
    val lastUser = messages.lastOrNull { it.role == "user" }
    val turnKey = messages.count { it.role == "user" }.toString() + "|" + lastUser?.content.orEmpty()
    LaunchedEffect(turnKey, sending) {
        if (sending && lastUser != null && anchoredTurn != turnKey && viewportHeight > 0) {
            anchoredTurn = turnKey; answerAnchored = null; scrollMode = "FOLLOW_ACTIVITY"
            tailSpace = maxOf(tailSpace, viewportHeight * .85f)
            withFrameNanos { }
            listState.scrollToItem(messages.indexOf(lastUser) + 1, -(viewportHeight * .15f).toInt())
        }
    }
    val activeAnswer = messages.lastOrNull { it.role == "assistant" && presentations[it.id]?.answerStarted == true }
    val activeAnswerY = activeAnswer?.id?.let { answerPositions[it] }
    LaunchedEffect(activeAnswer?.id, activeAnswerY, sending) {
        val message = activeAnswer
        if (sending && message != null && answerAnchored != message.id && activeAnswerY != null && scrollMode != "MANUAL_READING") {
            // Let the process-chain collapse settle before committing the one-time answer anchor.
            delay(200)
            if (scrollMode == "MANUAL_READING") return@LaunchedEffect
            val fraction = if (with(density) { viewportHeight.toDp() } < 640.dp) .23f else .30f
            tailSpace = maxOf(tailSpace, viewportHeight * .85f)
            withFrameNanos { }
            listState.scroll { scrollBy(activeAnswerY - viewportTop - viewportHeight * fraction) }
            answerAnchored = message.id; scrollMode = "ANSWER_ANCHORED"
        }
    }
    val revision = messages.sumOf { message -> message.parts.sumOf { it.content.orEmpty().length } + message.parts.size }
    LaunchedEffect(revision, messages.size) {
        if (scrollMode == "FOLLOW_ACTIVITY" && nearBottom && !dragging && messages.isNotEmpty()) listState.scrollToItem(tailIndex)
    }
    LaunchedEffect(loading) {
        if (!loading && messages.isNotEmpty() && anchoredTurn == null) listState.scrollToItem(tailIndex)
    }

    val dark = MaterialTheme.colorScheme.background != Color.White
    val chromeInk = if (dark) Color(0xFF222222) else Color(0xFF333333)
    val composerBorder by animateColorAsState(if (dark) MaterialTheme.colorScheme.outline else MaterialTheme.colorScheme.onBackground.copy(alpha = if (composerFocused) .22f else .09f), webMotionTween(160, easing = CubicBezierEasing(.25f, .1f, .25f, 1f)), label = "web-composer-border")
    // Web assigns textarea height synchronously; only its padding/min-height
    // declarations transition. Animating the whole dock delayed every soft wrap.
    val paddingMotion = webMotionTween<androidx.compose.ui.unit.Dp>(240, easing = CubicBezierEasing(.4f, 0f, .2f, 1f))
    val editorStartPadding by animateDpAsState(if (enlarged) 10.dp else 8.dp, paddingMotion, label = "web-editor-left-padding")
    val editorEndPadding by animateDpAsState(if (composerExpanded || enlarged) 48.dp else 8.dp, paddingMotion, label = "web-editor-right-padding")
    val editorBottomPadding by animateDpAsState(if (enlarged) 10.dp else 2.dp, paddingMotion, label = "web-editor-bottom-padding")
    BoxWithConstraints(Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background)) {
        val viewportWidth = maxWidth
        val bottomInset = maxOf(WindowInsets.ime.getBottom(density), WindowInsets.navigationBars.getBottom(density))
        val availableHeight = (maxHeight - with(density) { (WindowInsets.statusBars.getTop(density) + bottomInset).toDp() }).coerceAtLeast(0.dp)
        val modelError = modelCatalogError?.takeIf { modelCatalog == null }
            ?: if (modelCatalog != null && modelPrefsReady && !hasResolvedModel(options)) "当前服务商没有可用模型，请在模型菜单中选择或检查配置" else null
        val shownError = createError ?: error ?: modelError
        val extrasKey = Triple(shownError, !failedDraft.isNullOrBlank(), queues.size)
        val hasExtras = shownError != null || extrasKey.second || queues.isNotEmpty()
        val editorMinimum = minOf(44.dp, (availableHeight - 129.dp).coerceAtLeast(0.dp))
        val extrasMaximum = minOf(availableHeight * .25f, (availableHeight - 129.dp - editorMinimum).coerceAtLeast(0.dp))
        var extrasHeightPx by remember(extrasKey, viewportWidth, extrasMaximum, density.fontScale) { mutableIntStateOf(-1) }
        // Reserve the bounded maximum until this content is measured, so the first
        // frame of a new error/queue cannot temporarily displace the send row.
        val extrasHeight = if (!hasExtras) 0.dp else if (extrasHeightPx < 0) extrasMaximum else minOf(extrasMaximum, with(density) { extrasHeightPx.toDp() })
        val extrasScroll = rememberScrollState()
        LaunchedEffect(shownError) { if (shownError != null) extrasScroll.scrollTo(0) }
        val editorBudget = (availableHeight - 129.dp - extrasHeight).coerceAtLeast(0.dp)
        // Web's enlarged dock is chat-height - 96px. Remove toolbar, border,
        // padding and the 40px action row from that budget, including the IME.
        val editorMaximum = if (enlarged) (availableHeight - 209.dp - extrasHeight).coerceAtLeast(editorMinimum).coerceAtMost(editorBudget) else minOf(132.dp, editorBudget)
        val editorHeight = if (enlarged) editorMaximum else minOf(44.dp, editorMaximum)
        val composerMinimum = if (enlarged) 0.dp else minOf(if (composerExpanded) 112.dp else 100.dp, (availableHeight - 69.dp - extrasHeight).coerceAtLeast(0.dp))
        val workbenchScroll = rememberScrollState()
        Scaffold(
            containerColor = MaterialTheme.colorScheme.background,
            contentWindowInsets = WindowInsets(0, 0, 0, 0),
            bottomBar = {
                Column(Modifier.fillMaxWidth().navigationBarsPadding().imePadding().padding(start = 12.dp, end = 12.dp, bottom = 28.dp)) {
                    if (hasExtras) key(extrasKey, viewportWidth, extrasMaximum, density.fontScale) {
                        Column(Modifier.fillMaxWidth().heightIn(max = extrasMaximum).onSizeChanged { extrasHeightPx = it.height }.verticalScroll(extrasScroll)) {
                            if (shownError != null) Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                                Text(shownError, color = MaterialTheme.colorScheme.error, fontSize = 12.sp, lineHeight = 18.sp, modifier = Modifier.weight(1f).padding(vertical = 6.dp))
                                if (vm != null && !sending) TextButton(onClick = { vm.loadMessages(force = true) }) { Text("重连", fontSize = 14.sp, lineHeight = 21.sp) }
                            }
                            if (!failedDraft.isNullOrBlank()) TextButton(onClick = {
                                input = listOf(failedDraft.orEmpty(), input).filter { it.isNotBlank() }.joinToString("\n\n"); vm?.consumeFailedDraft()
                            }) { Text("合并恢复未发送内容", fontSize = 14.sp, lineHeight = 21.sp) }
                            queues.forEach { queued -> Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                                Text("待发送", fontSize = 10.sp, lineHeight = 15.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(end = 8.dp))
                                Text(queued.content, maxLines = 1, overflow = TextOverflow.Ellipsis, fontSize = 12.sp, lineHeight = 18.sp, modifier = Modifier.weight(1f))
                                SmallAction("取消待发送消息", { vm?.removeQueuedMessage(queued.id) }) { WebGlyph(WebIcon.Close, modifier = Modifier.size(16.dp)) }
                            } }
                        }
                    }
                    Row(Modifier.fillMaxWidth().height(32.dp).webDrawerHorizontalScrollable(workbenchScroll).horizontalScroll(workbenchScroll).padding(horizontal = 4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                        WorkbenchAction("现场拍照并作为附件", "拍照", WebIcon.Camera, enabled = !sending && !creatingSession && !sessionClosed) { openSharedComposer("photo") }
                        WorkbenchAction("在当前对话中设定学习目标", "目标", WebIcon.Target, enabled = !sending && !sessionClosed) { openSharedComposer("goal") }
                        WorkbenchAction(if (options.searchRoute == "disabled") "下一条消息联网搜索" else "关闭本轮联网搜索", if (options.searchRoute == "disabled") "联网" else "联网中", WebIcon.Search, pressed = options.searchRoute != "disabled", enabled = !sessionClosed) {
                            updateOptions(options.copy(searchRoute = if (options.searchRoute == "disabled") "auto" else "disabled"))
                        }
                    }
                    Spacer(Modifier.height(7.dp))
                    Surface(shape = RoundedCornerShape(20.dp), color = if (MaterialTheme.colorScheme.background == Color.White) Color.White else MaterialTheme.colorScheme.surfaceVariant,
                        border = BorderStroke(1.dp, composerBorder), shadowElevation = 1.dp) {
                        Column(Modifier.padding(1.dp).fillMaxWidth().heightIn(min = composerMinimum)
                            .padding(8.dp).webDrawerSwipeExcluded()) {
                            Box(Modifier.fillMaxWidth()) {
                                val composerStyle = TextStyle(fontFamily = WebGeistFontFamily, fontSize = 15.sp, lineHeight = 22.5.sp, color = MaterialTheme.colorScheme.onSurface).withWebCssLineBox()
                                val composerMaxLines = if (enlarged) Int.MAX_VALUE else 6
                                WebTextFieldLineBox(style = composerStyle, maxLines = composerMaxLines,
                                    modifier = Modifier.fillMaxWidth().heightIn(min = editorHeight, max = editorMaximum).onFocusChanged { composerFocused = it.isFocused }
                                        .padding(start = editorStartPadding, end = editorEndPadding, top = 10.dp, bottom = editorBottomPadding)) { recordLayout ->
                                    BasicTextField(
                                        value = input, onValueChange = { input = it }, modifier = Modifier.fillMaxWidth(),
                                        textStyle = composerStyle,
                                        onTextLayout = { layout -> recordLayout(layout); composerExpanded = layout.lineCount > 1 },
                                        cursorBrush = SolidColor(MaterialTheme.colorScheme.onSurface), maxLines = composerMaxLines,
                                        decorationBox = { field -> Box { if (input.isEmpty()) androidx.compose.material3.Text("输入消息，或输入 @ 选择模式 / 文件…", maxLines = 1, overflow = TextOverflow.Ellipsis, style = composerStyle, color = MaterialTheme.colorScheme.onSurfaceVariant); field() } },
                                    )
                                }
                                if (composerExpanded || enlarged) Box(Modifier.align(Alignment.TopEnd).padding(top = 2.dp, end = 2.dp)) {
                                    SmallAction(if (enlarged) "收起输入框" else "展开输入框", { enlarged = !enlarged }, 32, translateOnPress = false) { WebGlyph(if (enlarged) WebIcon.Collapse else WebIcon.Expand, modifier = Modifier.size(18.dp)) }
                                }
                            }
                            Spacer(Modifier.height(if (enlarged) 4.dp else 2.dp))
                            Row(Modifier.fillMaxWidth().height(40.dp), verticalAlignment = Alignment.CenterVertically) {
                                // Reuse the original menu so its file item receives a real
                                // user activation and retains every registered workflow.
                                SmallAction("打开对话功能菜单", { openSharedComposer("tools") }, hasPopup = true) { WebGlyph(WebIcon.Plus, modifier = Modifier.size(20.dp)) }
                                Spacer(Modifier.weight(1f))
                                SmallAction("语音输入", { openSharedComposer("dictation") }, enabled = !sending && !creatingSession) { WebGlyph(WebIcon.Microphone, modifier = Modifier.size(22.dp)) }
                                Spacer(Modifier.width(2.dp))
                                if (input.isBlank() && !sending && !creatingSession) SmallAction("语音对话", { openSharedComposer("voice") }, background = MaterialTheme.colorScheme.onSurface.copy(alpha = .06f)) { WebGlyph(WebIcon.Wave, modifier = Modifier.size(22.dp)) }
                                else {
                                    val stopping = sending && input.isBlank()
                                    val canSend = stopping || !creatingSession && !offline && input.isNotBlank() && modelSelectionReady
                                    val bg = when { stopping -> Color(0xFFEDEDED); !canSend -> Color(0xFFECECEC); else -> Color.Black }
                                    SmallAction(if (stopping) "中断回答" else if (sending) "发送消息（加入队列）" else "发送消息", {
                                        if (stopping) vm?.stop() else if (!creatingSession && input.isNotBlank()) {
                                            val text = input.trim()
                                            if (vm == null) onSendNew(text, options) else { vm.setComposerOptions(options); vm.send(text); input = "" }
                                        }
                                    }, background = bg, enabled = canSend) {
                                        if (creatingSession) WebSpinner(size = 14.dp, periodMillis = 900)
                                        else if (stopping) Box(Modifier.size(14.dp).background(Color(0xFF111111), RoundedCornerShape(2.dp)))
                                        else WebGlyph(WebIcon.ArrowUp, modifier = Modifier.size(16.dp), color = if (canSend) Color.White else Color(0xFF8B8B8B))
                                    }
                                }
                            }
                        }
                    }
                }
            },
        ) { padding ->
            Column(Modifier.fillMaxSize().padding(padding).statusBarsPadding()) {
                if (offline) Text("当前离线 · 正在显示本机缓存", fontSize = 11.sp, lineHeight = 16.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.surfaceVariant).padding(horizontal = 12.dp, vertical = 6.dp))
                else if (refreshing && messages.isNotEmpty()) LinearProgressIndicator(Modifier.fillMaxWidth().height(2.dp), color = MaterialTheme.colorScheme.outline)
                Box(Modifier.fillMaxSize().onSizeChanged { viewportHeight = it.height }.onGloballyPositioned { viewportTop = it.positionInRoot().y }) {
                    when {
                        loading && messages.isEmpty() -> CircularProgressIndicator(Modifier.align(Alignment.Center).size(22.dp), strokeWidth = 2.dp)
                        // Web reserves a 10dp scrollbar gutter on both edges; its
                        // 12dp content inset + 16dp empty-state inset starts at 38dp.
                        messages.isEmpty() -> Column(Modifier.fillMaxWidth().padding(start = 38.dp, end = 38.dp, top = 54.dp)) {
                            Text("新会话", fontSize = 12.sp, lineHeight = 18.sp, letterSpacing = .12.sp, fontWeight = FontWeight.Medium, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.fillMaxWidth())
                            Spacer(Modifier.height(6.dp))
                            // The device Web line box rounds 26px / 1.2 to 32px.
                            // CJK fallback otherwise raises Compose's natural height to 34px.
                            Text("从一个问题开始", fontSize = 26.sp, lineHeight = 31.2.sp, fontWeight = FontWeight(650), letterSpacing = (-.26).sp, color = MaterialTheme.colorScheme.onBackground, overflow = TextOverflow.Visible, modifier = Modifier.fillMaxWidth(), debugTag = "empty-title")
                            // .chat-suggestions overrides the prompt grid's 24px margin.
                            Spacer(Modifier.height(20.dp))
                            listOf("我现在最应该先学什么？", "请根据我当前的学习目标，帮我梳理可学的主题。").forEach { prompt ->
                                Surface(shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.onBackground.copy(alpha = .04f), modifier = Modifier.fillMaxWidth().padding(bottom = 10.dp).webClickFeedback(translateOnPress = false) { input = prompt }) {
                                    Box(Modifier.fillMaxWidth().heightIn(min = 52.dp).padding(horizontal = 14.dp, vertical = 12.dp), contentAlignment = Alignment.CenterStart) {
                                        Text(prompt, fontSize = 14.sp, lineHeight = 19.6.sp)
                                    }
                                }
                            }
                        }
                        // Web ConversationContent reserves a 10px stable scrollbar
                        // gutter on both edges, then the message column adds 12px.
                        // The composer and empty state own their separate insets.
                        else -> LazyColumn(state = listState, modifier = Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 22.dp, end = 22.dp, top = 74.dp, bottom = 40.dp), verticalArrangement = Arrangement.spacedBy(28.dp)) {
                            item("older") { if (hasOlder) Box(Modifier.fillMaxWidth(), contentAlignment = Alignment.Center) { TextButton({ vm?.loadOlderMessages() }, enabled = !loadingOlder) { Text(if (loadingOlder) "正在加载…" else "加载更早消息", fontSize = 12.sp, lineHeight = 18.sp) } } }
                            items(messages, key = { it.id }) { msg ->
                                val versionState = messageVersions[msg.id]
                                val shown = versionState?.snapshot ?: msg
                                val persisted = msg.isPersistedMessage()
                                val actionsEnabled = !sending && messageOperation == null && persisted
                                MessageBubble(message = shown, baseUrl = app.api.baseUrl, onLinkClick = { url ->
                                    val destination = NativeRendererPolicy.workspaceDestination(url, app.api.baseUrl, app.api.workspaceId.orEmpty())
                                    if (destination != null) onNavigateWeb(destination)
                                    else if (android.net.Uri.parse(url).scheme in listOf("http", "https", "mailto", "tel")) EmbeddedBrowserActivity.open(context, url)
                                }, onOpenWeb = onOpenWeb,
                                    onRetry = if (msg.role == "assistant" && persisted && !sessionClosed) ({
                                        val imageAnswer = shown.parts.any { it.type == "image" } && shown.providerTrace.traceString("generation_mode") == "image"
                                        if (imageAnswer) {
                                            val prompt = messages.firstOrNull { it.id == msg.parentMessageId }?.let(::messageCopyText)
                                            if (!prompt.isNullOrBlank()) openSharedComposer("image", prompt)
                                            else android.widget.Toast.makeText(context, "未找到原始绘图提示词", android.widget.Toast.LENGTH_SHORT).show()
                                        } else { retryTarget = msg; onLoadModels() }
                                    }) else null,
                                    presentation = if (versionState?.selectedVersionId == null) presentations[msg.id] else null,
                                    onFinalAnswerTop = { answerPositions[msg.id] = it },
                                    onSendAction = { vm?.sendRenderedAction(it) }, onRefresh = { vm?.loadMessages(force = true) },
                                    onCopy = { copyText(messageCopyText(shown), if (msg.role == "user") "已复制消息" else "已复制回答") },
                                    onCopyId = if (persisted) ({ copyText(msg.id, "消息 ID 已复制") }) else null,
                                    onEdit = if (msg.role == "user" && persisted) ({ content -> vm?.branchFrom(msg, content) }) else null,
                                    onBranch = if (msg.sessionId == vm?.sessionId && persisted) ({ vm?.branchFrom(msg) }) else null,
                                    actionsEnabled = actionsEnabled, versionState = versionState, currentVersion = msg.version,
                                    onRequestVersions = { vm?.requestMessageVersions(msg, force = versionState?.error != null) },
                                    onSelectVersion = { vm?.selectMessageVersion(msg, it) },
                                    conversationVersions = conversationVersions[msg.id], onSelectConversation = { vm?.openConversationVersion(it) })
                            }
                            if (showFollowUpPrompts && suggestionAnchor != null && vm != null) item("suggested-prompts") {
                                SharedMessageBody("${suggestionAnchor.id}-suggestions", vm.sessionId, emptyList(), emptyList(), false,
                                    onSendAction = { vm.sendRenderedAction(it) }, onRefresh = { vm.loadMessages(force = true) },
                                    onNavigate = onNavigateWeb, onOpenWeb = onOpenWeb, surface = "suggestions",
                                    suggestions = NativeSuggestedPromptsInfo(suggestionAnchor.id, suggestionAnchor.sessionId,
                                        suggestionAnchor.version ?: 1, completedSuggestionFingerprint(messages), options.providerId, options.modelId))
                            }
                            item("tail") { Spacer(Modifier.height(with(density) { tailSpace.toDp() })) }
                        }
                    }
                    if (!nearBottom && messages.isNotEmpty()) Surface(modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 24.dp).size(32.dp).webClickFeedback(translateOnPress = false) {
                        tailSpace = 0f
                        scrollMode = if (activeAnswer != null) "MANUAL_READING" else "FOLLOW_ACTIVITY"
                        // use-conversation-scroll-controller assigns scrollTop directly.
                        scope.launch { withFrameNanos { }; listState.scrollToItem(tailIndex) }
                    }, shape = CircleShape, border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline), color = MaterialTheme.colorScheme.background, shadowElevation = 2.dp, ) {
                        Box(contentAlignment = Alignment.Center) { WebGlyph(WebIcon.ArrowUp, modifier = Modifier.size(16.dp).graphicsLayer { rotationZ = 180f }) }
                    }
                }
            }
        }
        // Floating chrome overlays the canvas exactly as workspace-topbar does on phones.
        Row(Modifier.fillMaxWidth().statusBarsPadding().padding(start = 12.dp, end = 12.dp, top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            FloatingAction("打开导航", onBack) { WebGlyph(WebIcon.Menu, modifier = Modifier.size(20.dp), color = chromeInk) }
            Spacer(Modifier.width(4.dp))
            Box {
                Surface(shape = CircleShape, color = Color.White.copy(alpha = if (dark) .92f else .94f), shadowElevation = 1.dp, modifier = Modifier.size(width = 56.dp, height = 32.dp)
                    .semantics { contentDescription = if (options.generationMode == "image") "选择绘图模型" else "选择响应模式、思考力度和模型" }
                    .webClickFeedback(hasPopup = true) {
                        composerFocus.clearFocus(force = true)
                        composerKeyboard?.hide()
                        if (options.generationMode == "image") openSharedComposer("models") else { modelsOpen = true; onLoadModels() }
                    }) {
                    Box(contentAlignment = Alignment.Center) { Text(if (options.generationMode == "image") "绘图" else if (options.responseMode == "fast") "极速" else "智能", fontSize = 13.sp, lineHeight = 17.3333.sp, fontWeight = FontWeight(650), color = chromeInk, debugTag = "model-trigger") }
                }
                NativeModelMenu(
                    expanded = modelsOpen, options = options, onOptionsChange = ::updateOptions,
                    choices = modelCatalog?.choices ?: modelChoices, loading = loadingModels, onDismiss = { modelsOpen = false },
                    availableHeight = availableHeight, availableWidth = viewportWidth,
                )
            }
            Spacer(Modifier.weight(1f))
            FloatingAction("展开图谱面板", onOpenGraph, 36) { WebGlyph(WebIcon.Network, modifier = Modifier.size(16.dp), color = chromeInk) }
            Spacer(Modifier.width(4.dp))
            Box {
                FloatingAction("打开页面工具", { pageToolsOpen = true }, hasPopup = true) { WebGlyph(WebIcon.Sliders, modifier = Modifier.size(16.dp), color = chromeInk) }
                DropdownMenu(pageToolsOpen, { pageToolsOpen = false }) {
                    DropdownMenuItem(text = { Text("新对话", fontSize = 14.sp, lineHeight = 21.sp) }, onClick = { pageToolsOpen = false; onNewChat() })
                    DropdownMenuItem(text = { Text("图谱绑定与学习活动", fontSize = 14.sp, lineHeight = 21.sp) }, enabled = !sending, onClick = { pageToolsOpen = false; openSharedComposer("tools") })
                    DropdownMenuItem(text = { Text(title, fontSize = 12.sp, lineHeight = 18.sp, maxLines = 1) }, enabled = false, onClick = {})
                }
            }
        }
        retryTarget?.let { target -> NativeRetryDialog(
            message = messageVersions[target.id]?.snapshot ?: target, defaults = options, models = modelChoices,
            loadingModels = loadingModels, onLoadModels = onLoadModels,
            onConfigure = { retryTarget = null; onNavigateWeb("settings/models") },
            onDismiss = { retryTarget = null },
            onRetry = { selected, domains -> retryTarget = null; vm?.retry(target.id, selected, domains) },
        ) }
    }
}

/** The Web retry menu's one-shot settings, kept separate from the live composer. */
@Composable
private fun NativeRetryDialog(
    message: Message,
    defaults: ComposerOptions,
    models: List<NativeModelChoice>,
    loadingModels: Boolean,
    onLoadModels: () -> Unit,
    onConfigure: () -> Unit,
    onDismiss: () -> Unit,
    onRetry: (ComposerOptions, String) -> Unit,
) {
    var selected by remember(message.id) { mutableStateOf(defaults) }
    var domains by rememberSaveable(message.id) { mutableStateOf("") }
    var menu by remember { mutableStateOf<String?>(null) }
    var validationError by remember { mutableStateOf<String?>(null) }
    val choice = models.firstOrNull { it.providerId == selected.providerId && it.modelId == selected.modelId }
    val usesDefaultModel = selected.providerId == null && selected.modelId == null
    val previousModel = message.providerTrace.traceString("model_id") ?: "模型未知"
    val changed = selected != defaults || domains.isNotBlank()
    AlertDialog(onDismissRequest = onDismiss,
        title = { Text("重新生成回答", fontSize = 18.sp, lineHeight = 27.sp) },
        text = { Column(Modifier.fillMaxWidth().heightIn(max = 480.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("上次回答：$previousModel", fontSize = 12.sp, lineHeight = 18.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("本次重试：${selected.modelId ?: "使用会话默认模型"}\n${if (selected.responseMode == "fast") "极速 · 思考关闭" else "智能体 · 思考${thinkingLabel(selected.thinkingMode)}"} · ${if (selected.searchRoute == "disabled") "联网已关闭" else "联网已开启"}", fontSize = 12.sp, lineHeight = 19.sp)
            HorizontalDivider()
            Text("重试设置 · 仅本次生效", fontSize = 12.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold)
            Box {
                TextButton(onClick = { menu = "model"; onLoadModels() }) { Text("使用模型：${selected.modelId ?: "选择模型"}", fontSize = 13.sp, lineHeight = 19.5.sp) }
                DropdownMenu(menu == "model", { menu = null }, modifier = Modifier.heightIn(max = 300.dp)) {
                    DropdownMenuItem(text = { Text("使用会话默认模型", fontSize = 12.sp, lineHeight = 18.sp) }, onClick = { selected = selected.copy(providerId = null, modelId = null); menu = null })
                    if (loadingModels) DropdownMenuItem(text = { Text("正在载入模型…", fontSize = 12.sp, lineHeight = 18.sp) }, enabled = false, onClick = {})
                    models.forEach { model -> DropdownMenuItem(text = { Column { Text(model.modelId, fontSize = 12.sp, lineHeight = 18.sp); Text(model.providerName, fontSize = 10.sp, lineHeight = 15.sp) } }, onClick = {
                        selected = selected.copy(providerId = model.providerId, modelId = model.modelId, responseMode = if (model.supportsAgent) selected.responseMode else "fast")
                        menu = null
                    }) }
                    if (models.isEmpty() && !loadingModels) DropdownMenuItem(text = { Text("暂无可用模型，点击重新载入", fontSize = 12.sp, lineHeight = 18.sp) }, onClick = onLoadModels)
                    DropdownMenuItem(text = { Text("管理已配置模型…", fontSize = 12.sp, lineHeight = 18.sp) }, onClick = { menu = null; onConfigure() })
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("响应模式", modifier = Modifier.weight(1f), fontSize = 13.sp, lineHeight = 19.5.sp)
                TextButton(onClick = { selected = selected.copy(responseMode = "fast", thinkingMode = "off") }) { Text("极速", fontWeight = if (selected.responseMode == "fast") FontWeight.Bold else FontWeight.Normal, fontSize = 13.sp, lineHeight = 19.5.sp) }
                TextButton(enabled = choice?.supportsAgent != false, onClick = { selected = selected.copy(responseMode = "agentic", thinkingMode = selected.thinkingMode.takeUnless { it == "off" } ?: "medium") }) { Text("智能体", fontWeight = if (selected.responseMode != "fast") FontWeight.Bold else FontWeight.Normal, fontSize = 13.sp, lineHeight = 19.5.sp) }
            }
            Box {
                TextButton(enabled = selected.responseMode != "fast", onClick = { menu = "thinking" }) { Text("思考强度：${thinkingLabel(if (selected.responseMode == "fast") "off" else selected.thinkingMode)}", fontSize = 13.sp, lineHeight = 19.5.sp) }
                DropdownMenu(menu == "thinking", { menu = null }) {
                    listOf("off", "low", "medium", "high", "xhigh").forEach { mode -> DropdownMenuItem(text = { Text(thinkingLabel(mode), fontSize = 13.sp, lineHeight = 19.5.sp) }, onClick = { selected = selected.copy(thinkingMode = mode); menu = null }) }
                }
            }
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("联网搜索", modifier = Modifier.weight(1f), fontSize = 13.sp, lineHeight = 19.5.sp)
                Switch(checked = selected.searchRoute != "disabled", onCheckedChange = { selected = selected.copy(searchRoute = if (it) defaults.searchRoute.takeUnless { route -> route == "disabled" } ?: "auto" else "disabled") })
            }
            if (selected.searchRoute != "disabled") OutlinedTextField(domains, { domains = it; validationError = null }, label = { Text("搜索来源域名（可选）", fontSize = 12.sp, lineHeight = 18.sp) }, placeholder = { Text("react.dev, python.org", fontSize = 12.sp, lineHeight = 18.sp) }, supportingText = { Text("逗号或空格分隔，留空不限定来源", fontSize = 11.sp, lineHeight = 16.5.sp) }, singleLine = true, modifier = Modifier.fillMaxWidth())
            validationError?.let { Text(it, color = MaterialTheme.colorScheme.error, fontSize = 12.sp, lineHeight = 18.sp) }
            Text("重试后保留原回答及历史版本", fontSize = 11.sp, lineHeight = 16.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        } },
        confirmButton = {
            val closeAndRetry = rememberWebDialogCloseAction { onRetry(selected, domains) }
            TextButton(enabled = !loadingModels && (usesDefaultModel || choice != null) && (selected.responseMode == "fast" || choice?.supportsAgent != false), onClick = {
            try { nativeRetryBody(selected, domains); closeAndRetry() }
            catch (e: IllegalArgumentException) { validationError = e.message }
        }) { Text(if (changed) "更改后重试" else "直接重试", fontSize = 14.sp, lineHeight = 21.sp) } },
        dismissButton = { TextButton(onClick = rememberWebDialogCloseAction(onDismiss)) { Text("取消", fontSize = 14.sp, lineHeight = 21.sp) } },
    )
}

private fun thinkingLabel(mode: String): String = when (mode) { "off" -> "关闭"; "low" -> "低"; "medium" -> "中"; "high" -> "高"; "xhigh" -> "极高"; else -> mode }

@Composable
private fun SmallAction(label: String, onClick: () -> Unit, size: Int = 40, background: Color = Color.Transparent, enabled: Boolean = true, hasPopup: Boolean = false, translateOnPress: Boolean = true, content: @Composable () -> Unit) {
    Box(Modifier.size(size.dp).clip(CircleShape).background(background).webClickFeedback(enabled = enabled, hasPopup = hasPopup, translateOnPress = translateOnPress, onClick = onClick).semantics { contentDescription = label }, contentAlignment = Alignment.Center) { content() }
}
@Composable
private fun FloatingAction(label: String, onClick: () -> Unit, size: Int = 34, hasPopup: Boolean = false, content: @Composable () -> Unit) {
    val dark = MaterialTheme.colorScheme.background != Color.White
    Surface(shape = CircleShape, color = Color.White.copy(alpha = if (dark) .92f else .94f), shadowElevation = 1.dp, modifier = Modifier.size(size.dp).webClickFeedback(hasPopup = hasPopup, onClick = onClick).semantics { contentDescription = label }) {
        Box(contentAlignment = Alignment.Center) { content() }
    }
}

@Composable
private fun WorkbenchAction(description: String, label: String, icon: WebIcon, pressed: Boolean = false, enabled: Boolean = true, onClick: () -> Unit) {
    val ink = if (pressed) MaterialTheme.colorScheme.onBackground else MaterialTheme.colorScheme.onSurfaceVariant
    Row(Modifier.heightIn(min = 32.dp).clip(CircleShape).background(if (pressed) MaterialTheme.colorScheme.onBackground.copy(alpha = .09f) else Color.Transparent)
        .webClickFeedback(enabled = enabled, translateOnPress = false, onClick = onClick).semantics { contentDescription = description }.padding(horizontal = 6.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        WebGlyph(icon, modifier = Modifier.size(16.dp), color = ink.copy(alpha = if (enabled) 1f else .42f), strokeWidth = 1.9f)
        Text(label, fontSize = 13.sp, lineHeight = 13.sp, fontWeight = if (pressed) FontWeight.SemiBold else FontWeight.Medium, color = ink.copy(alpha = if (enabled) 1f else .42f))
    }
}
