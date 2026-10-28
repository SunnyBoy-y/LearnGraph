package com.learngraph.mobile.ui.home

import android.content.Context
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.data.ComposerOptions
import com.learngraph.mobile.data.ShareInbox
import com.learngraph.mobile.data.NativeRendererSubmission
import com.learngraph.mobile.data.NativeSidebarActivityStore
import com.learngraph.mobile.data.resolveNativeTextModelOptions
import com.learngraph.mobile.ui.chat.ChatScreen
import com.learngraph.mobile.ui.chat.ChatViewModel
import com.learngraph.mobile.ui.chat.SharedGraphPanel
import com.learngraph.mobile.ui.components.*
import com.learngraph.mobile.ui.components.WebDropdownMenu as DropdownMenu
import com.learngraph.mobile.ui.components.WebDropdownMenuItem as DropdownMenuItem
import com.learngraph.mobile.ui.theme.WebText as Text
import com.learngraph.mobile.util.ShortcutActions
import kotlinx.coroutines.launch
import kotlinx.coroutines.joinAll
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.*
import java.util.Locale

/** Phone sidebar order and dimensions follow workspace-shell.tsx and its final mobile CSS. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
fun HomeScreen(
    vm: HomeViewModel,
    onLogout: () -> Unit,
    onOpenWeb: (String?) -> Unit,
    onOpenSettings: () -> Unit,
    onOpenDownloads: () -> Unit,
    onSwitchServer: () -> Unit,
    onOpenFeature: (String) -> Unit = { onOpenWeb(null) },
) {
    val context = LocalContext.current
    val focusManager = LocalFocusManager.current
    val keyboard = LocalSoftwareKeyboardController.current
    val app = context.applicationContext as LearnGraphApp
    val sessions by vm.sessions.collectAsState()
    val projects by vm.projects.collectAsState()
    val loading by vm.loading.collectAsState()
    val error by vm.error.collectAsState()
    val modelChoices by vm.modelChoices.collectAsState()
    val modelCatalog by vm.modelCatalog.collectAsState()
    val modelCatalogError by vm.modelCatalogError.collectAsState()
    val loadingModels by vm.loadingModels.collectAsState()
    val auth by app.authStore.state.collectAsState(initial = AuthStore.AuthState())
    val scope = rememberCoroutineScope()
    val drawer = rememberWebDrawerState()
    val graphDrawer = rememberWebGraphDrawerState()
    val sidebarIdentity = rememberSidebarIdentity(app, auth, drawer.isVisible)
    val accountName = sidebarIdentity.displayName ?: auth.displayName?.takeIf { it.isNotBlank() }
        ?: sidebarIdentity.username ?: auth.username?.takeIf { it.isNotBlank() } ?: "我的账号"
    val workspaceName = sidebarIdentity.workspaceName ?: auth.workspaceId.orEmpty()
    var selectedId by rememberSaveable { mutableStateOf<String?>(null) }
    val activityStore = remember(context) { NativeSidebarActivityStore.get(context) }
    val activityScope = NativeSidebarActivityStore.scope(app.api)
    val activityViewOwner = remember { "native-view:${java.util.UUID.randomUUID()}" }
    DisposableEffect(vm, activityStore, activityScope, selectedId) {
        if (activityScope != null) {
            vm.attachSidebarActivity(activityStore, activityScope)
            activityStore.viewed(activityScope, activityViewOwner, selectedId)
        }
        onDispose { if (activityScope != null) activityStore.releaseOwner(activityScope, activityViewOwner) }
    }
    var selectedProjectId by rememberSaveable { mutableStateOf<String?>(null) }
    var routedSessionId by remember { mutableStateOf<String?>(null) }
    var previousProjectIds by remember { mutableStateOf(emptySet<String>()) }
    var pendingMessage by rememberSaveable { mutableStateOf<String?>(null) }
    var pendingOptionsJson by rememberSaveable { mutableStateOf<String?>(null) }
    var pendingRendererAction by rememberSaveable { mutableStateOf<String?>(null) }
    var pendingRendererSessionId by rememberSaveable { mutableStateOf<String?>(null) }
    var creating by remember { mutableStateOf(false) }
    var accountMenu by remember { mutableStateOf(false) }
    var moreMenu by remember { mutableStateOf(false) }
    fun closeDrawer() { scope.launch { drawer.close() } }
    fun newChat() { selectedId = null; pendingMessage = null; pendingRendererAction = null; pendingRendererSessionId = null; closeDrawer(); scope.launch { graphDrawer.close() } }
    val openPendingAction by rememberUpdatedState(newValue = {
        val prefs = context.getSharedPreferences("lg_shortcuts", Context.MODE_PRIVATE)
        val action = prefs.getString("pending_action", "").orEmpty()
        when {
            action == ShortcutActions.ACTION_NEW_CHAT -> { ShortcutActions.consume(context); selectedId = null }
            action.startsWith("open-session:") -> { ShortcutActions.consume(context); selectedId = action.removePrefix("open-session:") }
            action == ShortcutActions.ACTION_CONNECT -> { ShortcutActions.consume(context); onSwitchServer() }
            else -> Unit
        }
    })
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val navigationEpoch by com.learngraph.mobile.MainActivity.navigationEpoch.collectAsState()
    LaunchedEffect(navigationEpoch) { if (navigationEpoch > 0) openPendingAction() }
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_RESUME) { vm.refresh(); openPendingAction() } }
        lifecycle.addObserver(observer); onDispose { lifecycle.removeObserver(observer) }
    }
    val chatVm = selectedId?.let { id ->
        viewModel<ChatViewModel>(key = "native-chat-$id", factory = object : ViewModelProvider.Factory {
            @Suppress("UNCHECKED_CAST")
            override fun <T : ViewModel> create(modelClass: Class<T>): T = ChatViewModel(app.api, id, app.repository, { scope.launch { app.authStore.clearAuth() } }, feedback = com.learngraph.mobile.util.Haptics.nativeFeedback(context), activityStore = activityStore) as T
        })
    }
    DisposableEffect(lifecycle, chatVm) {
        fun update() { chatVm?.setSynchronizationForeground(lifecycle.currentState.isAtLeast(Lifecycle.State.RESUMED)) }
        update()
        val observer = LifecycleEventObserver { _, _ -> update() }
        lifecycle.addObserver(observer)
        onDispose { lifecycle.removeObserver(observer); chatVm?.setSynchronizationForeground(false) }
    }
    val graphComposerReady by (chatVm?.composerOptionsReady?.collectAsState() ?: remember { mutableStateOf(false) })
    val graphComposerOptions by (chatVm?.composerOptions?.collectAsState() ?: remember { mutableStateOf(ComposerOptions()) })
    LaunchedEffect(selectedId, sessions) {
        if (selectedId == null) routedSessionId = null
        else if (selectedId != routedSessionId) sessions.firstOrNull { it.id == selectedId }?.let { selectedProjectId = it.projectId; routedSessionId = selectedId }
    }
    LaunchedEffect(projects) {
        val ids = projects.map { it.id }.toSet()
        if (selectedProjectId in previousProjectIds && selectedProjectId !in ids) selectedProjectId = null
        previousProjectIds = ids
    }
    fun handleGraphAction(action: String) {
        if (chatVm != null) {
            pendingRendererAction = action; pendingRendererSessionId = chatVm.sessionId
        }
        else {
            // Retain the action during session creation/failure; it is consumed only by its VM.
            pendingRendererAction = action
            if (!creating) {
                creating = true
                scope.launch {
                    try {
                        vm.createSession(selectedProjectId)?.let { session ->
                            pendingMessage = null; pendingOptionsJson = null
                            pendingRendererSessionId = session.id; selectedId = session.id
                        }
                    } finally { creating = false }
                }
            }
        }
    }
    LaunchedEffect(chatVm, pendingRendererAction, pendingMessage, modelCatalog, graphComposerReady, graphComposerOptions, loading, sessions) {
        if (chatVm != null && selectedId == pendingRendererSessionId && pendingMessage == null) {
            pendingRendererAction?.let { action ->
                val submission = runCatching { NativeRendererSubmission.parse(action) }.getOrNull()
                if (submission?.autoSend == true) {
                    val catalog = modelCatalog ?: return@LaunchedEffect
                    if (!graphComposerReady || loading && (graphComposerOptions.providerId.isNullOrEmpty() || graphComposerOptions.modelId.isNullOrEmpty())) return@LaunchedEffect
                    val resolved = resolveNativeTextModelOptions(graphComposerOptions, sessions.firstOrNull { it.id == selectedId }?.modelSnapshot, catalog)
                    // Rendered actions submit text even if the composer was in image mode.
                    if (resolved.providerId.isNullOrEmpty() || resolved.modelId.isNullOrEmpty()) return@LaunchedEffect
                    chatVm.setComposerOptions(resolved)
                }
                chatVm.sendRenderedAction(action)
                pendingRendererAction = null; pendingRendererSessionId = null
            }
        }
    }
    BackHandler(enabled = drawer.isVisible || graphDrawer.isVisible || selectedId != null) {
        when {
            drawer.isVisible -> closeDrawer()
            graphDrawer.isVisible -> scope.launch { graphDrawer.close() }
            else -> selectedId = null
        }
    }

    WebNavigationDrawer(state = drawer, graphState = graphDrawer, graphContent = {
        SharedGraphPanel(
            sessionId = selectedId ?: "new",
            onNavigate = { path -> scope.launch { graphDrawer.close(); onOpenFeature(path) } },
            onSendAction = ::handleGraphAction,
            onRefresh = { chatVm?.loadMessages(force = true) },
            onOpenWeb = { scope.launch { graphDrawer.close(); onOpenWeb(selectedId) } },
        )
    }, drawerContent = {
        Column(Modifier.fillMaxWidth()) {
            Row(Modifier.fillMaxWidth().clickable { closeDrawer(); onOpenFeature("home") }.padding(start = 8.dp, end = 8.dp, top = 8.dp, bottom = 3.dp), verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(32.dp).clip(RoundedCornerShape(12.dp)).background(MaterialTheme.colorScheme.primary), contentAlignment = Alignment.Center) {
                    WebGlyph(WebIcon.Network, modifier = Modifier.size(16.dp).graphicsLayer { scaleX = 1.1f; scaleY = 1.1f }, color = MaterialTheme.colorScheme.onPrimary)
                }
                Text("LearnGraph", fontSize = 18.sp, lineHeight = 24.sp, fontWeight = FontWeight.SemiBold, modifier = Modifier.padding(start = 8.dp))
            }
            Text(sidebarIdentity.version ?: "版本信息", fontSize = 11.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 48.dp, bottom = 2.dp).clip(RoundedCornerShape(6.dp))
                    .background(MaterialTheme.colorScheme.surfaceVariant)
                    .clickable { closeDrawer(); onOpenFeature("settings/about") }
                    .semantics { contentDescription = "版本与更新：${sidebarIdentity.version ?: "版本信息"}" }
                    .padding(horizontal = 8.dp, vertical = 3.dp))
        }
        Spacer(Modifier.height(16.dp))
        SidebarNavRow("新对话", WebIcon.MessageSquareText, onClick = ::newChat)
        NativeSidebarContent(
            vm = vm, sessions = sessions, projects = projects, loading = loading,
            selectedId = selectedId, selectedProjectId = selectedProjectId,
            storageScope = com.learngraph.mobile.data.NativeCacheStore.scope(app.api.baseUrl, app.api.workspaceId, app.api.accountId, app.api.token) ?: "signed-out",
            onSelect = { session -> selectedId = session.id; selectedProjectId = session.projectId; pendingMessage = null; pendingOptionsJson = null; pendingRendererAction = null; pendingRendererSessionId = null; closeDrawer() },
            onSelectProject = { selectedProjectId = it },
            onNewChat = { projectId -> selectedProjectId = projectId; newChat() },
            onSearchSession = { id -> selectedId = id; pendingMessage = null; pendingOptionsJson = null; pendingRendererAction = null; pendingRendererSessionId = null; vm.refresh(); closeDrawer() },
            onNavigate = { feature -> closeDrawer(); onOpenFeature(feature) },
            onDeletedCurrent = { selectedId = null; pendingMessage = null },
            modifier = Modifier.weight(1f),
        ) {
            SidebarNavRow("图谱", WebIcon.Network, onClick = { closeDrawer(); onOpenFeature("chat/new?mode=goal") })
            Spacer(Modifier.height(4.dp))
            SidebarNavRow("资料", WebIcon.Files, onClick = { closeDrawer(); onOpenFeature("sources") })
            Spacer(Modifier.height(4.dp))
            SidebarNavRow("产物", WebIcon.Package, onClick = { closeDrawer(); onOpenFeature("artifacts") })
            Spacer(Modifier.height(4.dp))
            Box {
                SidebarNavRow("更多", WebIcon.More, compact = true, onClick = { moreMenu = true })
                DropdownMenu(moreMenu, { moreMenu = false }) {
                    DropdownMenuItem(text = { Text("练习") }, leadingIcon = { WebGlyph(WebIcon.GraduationCap, modifier = Modifier.size(16.dp)) }, onClick = { moreMenu = false; closeDrawer(); onOpenFeature("practice") })
                    DropdownMenuItem(text = { Text("记忆") }, leadingIcon = { WebGlyph(WebIcon.Brain, modifier = Modifier.size(16.dp)) }, onClick = { moreMenu = false; closeDrawer(); onOpenFeature("memory") })
                    DropdownMenuItem(text = { Text("分享收件箱（${ShareInbox.count(context)}）") }, onClick = { moreMenu = false; closeDrawer(); onOpenWeb(selectedId) })
                    HorizontalDivider()
                    listOf("工作台" to "home", "图谱工作台" to "graphs", "能力图谱" to "capabilities", "共同学习" to "learn/joint", "证据审阅" to "evidence/review").forEach { (label, path) ->
                        DropdownMenuItem(text = { Text(label) }, onClick = { moreMenu = false; closeDrawer(); onOpenFeature(path) })
                    }
                }
            }
        }
        if (error != null) Text(error.orEmpty(), fontSize = 11.sp, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(8.dp))
        Box {
            Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).clickable { accountMenu = true }.padding(horizontal = 8.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                WebUserAvatar(name = accountName, avatarUrl = sidebarIdentity.avatarUrl,
                    cacheScope = com.learngraph.mobile.data.NativeAppearanceStore.scope(auth), baseUrl = auth.baseUrl)
                Column(Modifier.weight(1f).padding(start = 12.dp)) {
                    Text(accountName, fontSize = 14.sp, lineHeight = 21.sp, fontWeight = FontWeight.Medium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(workspaceName, fontSize = 11.sp, lineHeight = 16.5.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
            DropdownMenu(accountMenu, { accountMenu = false }) {
                DropdownMenuItem(text = { Text("设置") }, leadingIcon = { WebGlyph(WebIcon.Settings, modifier = Modifier.size(16.dp)) }, onClick = { accountMenu = false; closeDrawer(); onOpenFeature("settings/general") })
                DropdownMenuItem(text = { Text("工作区记忆") }, leadingIcon = { WebGlyph(WebIcon.Archive, modifier = Modifier.size(16.dp)) }, onClick = { accountMenu = false; closeDrawer(); onOpenFeature("memory") })
                listOf("对话设置" to "conversation", "记忆设置" to "memory", "语音设置" to "voice", "模型设置" to "models", "工具设置" to "tools", "工作区设置" to "workspace", "账号设置" to "account", "数据设置" to "data", "关于" to "about").forEach { (label, path) ->
                    DropdownMenuItem(text = { Text(label) }, onClick = { accountMenu = false; closeDrawer(); onOpenFeature("settings/$path") })
                }
                HorizontalDivider()
                DropdownMenuItem(text = { Text("设备设置") }, leadingIcon = { WebGlyph(WebIcon.Settings, modifier = Modifier.size(16.dp)) }, onClick = { accountMenu = false; closeDrawer(); onOpenSettings() })
                DropdownMenuItem(text = { Text("下载管理") }, leadingIcon = { WebGlyph(WebIcon.Download, modifier = Modifier.size(16.dp)) }, onClick = { accountMenu = false; closeDrawer(); onOpenDownloads() })
                DropdownMenuItem(text = { Text("连接与设备偏好") }, leadingIcon = { WebGlyph(WebIcon.Server, modifier = Modifier.size(16.dp)) }, onClick = { accountMenu = false; closeDrawer(); onSwitchServer() })
                DropdownMenuItem(text = { Text("退出登录") }, leadingIcon = { WebGlyph(WebIcon.LogOut, modifier = Modifier.size(16.dp)) }, onClick = { accountMenu = false; vm.logout(onLogout) })
            }
        }
    }) {
        key(selectedId ?: "new-chat") {
            ChatScreen(vm = chatVm, title = sessions.firstOrNull { it.id == selectedId }?.title ?: "LearnGraph",
                onBack = { focusManager.clearFocus(); keyboard?.hide(); scope.launch { drawer.open() } }, onNewChat = ::newChat, onOpenWeb = { onOpenWeb(selectedId) },
                onNavigateWeb = onOpenFeature,
                onOpenGraph = { focusManager.clearFocus(); keyboard?.hide(); scope.launch { graphDrawer.open() } },
                onOpenSession = { id, prompt, options ->
                    pendingRendererAction = null; pendingRendererSessionId = null
                    pendingMessage = prompt; pendingOptionsJson = Json.encodeToString(ComposerOptions.serializer(), options)
                    selectedId = id; selectedProjectId = sessions.firstOrNull { it.id == id }?.projectId ?: selectedProjectId
                    vm.refresh()
                },
                creatingSession = creating, createError = if (chatVm == null) error else null,
                initialMessage = pendingMessage,
                initialOptions = pendingOptionsJson?.let { Json.decodeFromString(ComposerOptions.serializer(), it) },
                onInitialMessageSent = { pendingMessage = null; pendingOptionsJson = null },
                modelChoices = modelChoices, loadingModels = loadingModels, onLoadModels = vm::loadModels,
                modelCatalog = modelCatalog, modelSnapshot = sessions.firstOrNull { it.id == selectedId }?.modelSnapshot,
                modelSnapshotReady = selectedId == null || !loading,
                modelCatalogError = modelCatalogError,
                onSendNew = { text, options -> if (!creating) {
                    creating = true
                    scope.launch {
                        try { val session = vm.createSession(selectedProjectId); if (session != null) { pendingMessage = text; pendingOptionsJson = Json.encodeToString(ComposerOptions.serializer(), options); if (pendingRendererAction != null) pendingRendererSessionId = session.id; selectedId = session.id } }
                        finally { creating = false }
                    }
                } },
            )
        }
    }

}

private data class SidebarIdentity(val displayName: String? = null, val workspaceName: String? = null, val version: String? = null,
    val username: String? = null, val avatarUrl: String? = null)

/** Small identity metadata, cached before paint; opening the drawer does not reload conversations. */
@Composable
private fun rememberSidebarIdentity(app: LearnGraphApp, auth: AuthStore.AuthState, visible: Boolean): SidebarIdentity {
    val context = LocalContext.current
    val prefs = remember(context) { context.getSharedPreferences("lg_native_sidebar_identity", Context.MODE_PRIVATE) }
    val storageScope = com.learngraph.mobile.data.NativeCacheStore.scope(auth.baseUrl, auth.workspaceId, auth.userId ?: auth.username, auth.token)
    fun read(field: String) = storageScope?.let { prefs.getString("$it.$field", null)?.takeIf(String::isNotBlank) }
    var identity by remember(storageScope) { mutableStateOf(SidebarIdentity(read("displayName"), read("workspaceName"), read("version"), read("username"), read("avatarUrl"))) }
    LaunchedEffect(visible, storageScope, auth.token) {
        if (!visible || storageScope == null) return@LaunchedEffect
        val now = System.currentTimeMillis()
        val checkedAt = prefs.getLong("$storageScope.checkedAt", 0)
        if (checkedAt > 0 && now >= checkedAt && now - checkedAt < 60_000) return@LaunchedEffect
        val requestedAs = listOf(app.api.baseUrl, app.api.token, app.api.workspaceId, app.api.accountId)
        fun current() = requestedAs == listOf(app.api.baseUrl, app.api.token, app.api.workspaceId, app.api.accountId)
        val profileRequest = launch {
            try {
                val profile = Json.parseToJsonElement(app.api.get("/auth/me", workspace = false)).jsonObject
                if (!current()) return@launch
                val name = (profile["display_name"] as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)
                    ?: (profile["username"] as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)
                val username = (profile["username"] as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)
                val avatar = (profile["avatar_url"] as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)
                prefs.edit().putString("$storageScope.displayName", name).putString("$storageScope.username", username)
                    .putString("$storageScope.avatarUrl", avatar).apply()
                identity = identity.copy(displayName = name, username = username, avatarUrl = avatar)
            } catch (e: CancellationException) { throw e } catch (_: Exception) { }
        }
        val workspaceRequest = launch {
            try {
                val workspaces = Json.parseToJsonElement(app.api.get("/workspaces", workspace = false)).jsonArray
                if (!current()) return@launch
                val workspace = workspaces.mapNotNull { it as? JsonObject }.firstOrNull { (it["id"] as? JsonPrimitive)?.content == auth.workspaceId }
                (workspace?.get("name") as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)?.let {
                    prefs.edit().putString("$storageScope.workspaceName", it).apply(); identity = identity.copy(workspaceName = it)
                }
            } catch (e: CancellationException) { throw e } catch (_: Exception) { }
        }
        val versionRequest = launch {
            try {
                val version = Json.parseToJsonElement(app.api.get("/version", auth = false, workspace = false)).jsonObject
                if (!current()) return@launch
                (version["version"] as? JsonPrimitive)?.contentOrNull?.takeIf(String::isNotBlank)?.let {
                    prefs.edit().putString("$storageScope.version", it).apply(); identity = identity.copy(version = it)
                }
            } catch (e: CancellationException) { throw e } catch (_: Exception) { }
        }
        listOf(profileRequest, workspaceRequest, versionRequest).joinAll()
        if (current()) prefs.edit().putLong("$storageScope.checkedAt", System.currentTimeMillis()).apply()
    }
    return identity
}

@Composable
private fun SidebarNavRow(label: String, icon: WebIcon, compact: Boolean = false, onClick: () -> Unit) {
    // Web's mobile span keeps text-sm's unitless 20/14 line height at 16px.
    // The "more" trigger alone uses Button size=sm (h-7), without py-2.
    Row(Modifier.fillMaxWidth().then(if (compact) Modifier.height(28.dp) else Modifier)
        .clip(RoundedCornerShape(12.dp)).clickable(onClick = onClick)
        .padding(horizontal = 12.dp, vertical = if (compact) 0.dp else 8.dp), verticalAlignment = Alignment.CenterVertically) {
        WebGlyph(icon, modifier = Modifier.size(18.dp).graphicsLayer { scaleX = 1.1f; scaleY = 1.1f }, strokeWidth = 2.25f)
        Text(label, fontSize = 16.sp, lineHeight = (16f * 20f / 14f).sp, fontWeight = FontWeight.Normal, modifier = Modifier.padding(start = 12.dp))
    }
}
