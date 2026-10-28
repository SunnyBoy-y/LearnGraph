package com.learngraph.mobile.ui.web

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.fragment.app.FragmentActivity
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.BuildConfig
import com.learngraph.mobile.data.NativeCacheStore
import com.learngraph.mobile.data.NativeSidebarActivityStore
import com.learngraph.mobile.util.UiParityDiagnostics
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.data.DownloadStatus
import com.learngraph.mobile.data.DownloadStore
import com.learngraph.mobile.util.PermissionGate
import com.learngraph.mobile.util.PhotoCapture
import com.learngraph.mobile.util.WebMicPermissionBridge
import com.learngraph.mobile.util.parseTrustedOrigin
import com.learngraph.mobile.util.Haptics
import kotlinx.coroutines.launch
import java.util.UUID

/**
 * 纯网页模式（v0.8.0）：
 *  - 无原生控件：没有底栏、没有顶栏按钮；顶部仅一层很薄的白色
 *  - 全屏 WebView 承载网页版；Cookie 保存（登录态跨重启保持）
 *  - 内置下载器：网页下载链接由 DownloadStore 接管（进度/通知/打开/管理），
 *    右下角悬浮入口（仅存在下载任务时显示）进入下载管理页
 *  - 登录态注入：localStorage（learngraph.*）写入后 reload 一次
 *  - 系统返回键：WebView 可后退则后退，否则退出
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun WebAppScreen(
    onOpenDownloads: () -> Unit,
    onLoadFailed: () -> Unit,
    onBackToNative: () -> Unit,
    sessionId: String? = null,
    initialFeature: String? = null,
    initialAuth: AuthStore.AuthState = AuthStore.AuthState(),
    resumeCheckEpoch: Int = 0,
) {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp
    val authState by app.authStore.state.collectAsState(initial = initialAuth)

    val webViewRef = remember { mutableStateOf<WebView?>(null) }
    val downloadTasks by DownloadStore.tasks.collectAsState()
    val activeDownloads = downloadTasks.count { it.status == DownloadStatus.DOWNLOADING }
    val scope = rememberCoroutineScope()
    val sidebarActivity = remember { NativeSidebarActivityStore.get(context) }
    val activityScope = remember(authState) {
        NativeCacheStore.scope(authState.baseUrl, authState.workspaceId, authState.userId ?: authState.username, authState.token)
    }
    var documentOwner by remember { mutableStateOf(UUID.randomUUID().toString()) }
    DisposableEffect(activityScope) {
        val ownerAtEntry = documentOwner
        onDispose { activityScope?.let { sidebarActivity.releaseOwner(it, ownerAtEntry); sidebarActivity.releaseOwner(it, documentOwner) } }
    }
    LaunchedEffect(activityScope) {
        val key = activityScope ?: return@LaunchedEffect
        sidebarActivity.observe(key).collect {
            val view = webViewRef.value ?: return@collect
            val current = authState
            if (NativeSidebarActivityStore.scope(app.api) != key || app.api.token != current.token) return@collect
            if (NativeRendererPolicy.workspaceDestination(view.url.orEmpty(), current.baseUrl, current.workspaceId.orEmpty()) == null) return@collect
            val token = NativeRendererPolicy.scriptJson(org.json.JSONObject.quote(current.token.orEmpty()))
            val owner = NativeRendererPolicy.scriptJson(org.json.JSONObject.quote(documentOwner))
            val seed = NativeRendererPolicy.scriptJson(sidebarActivity.snapshotJson(key))
            view.evaluateJavascript("(function(){if(window.top!==window||localStorage.getItem('learngraph.access_token')!==$token||window.__lgSidebarOwner!==$owner)return;window.__lgSidebarSeed?.($seed);})()", null)
        }
    }
    val hapticOwner = remember { Haptics.newOwnerToken("web") }
    DisposableEffect(hapticOwner) { onDispose { Haptics.releaseOwner(context, hapticOwner) } }

    var offlineDocumentActive by remember { mutableStateOf(false) }
    var knownWebClientVersion by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(resumeCheckEpoch, webViewRef.value) {
        val view = webViewRef.value ?: return@LaunchedEffect
        val current = authState
        val currentUrl = view.url ?: return@LaunchedEffect
        if (!NativeRendererPolicy.shouldRevalidateOnResume(currentUrl, current.baseUrl, offlineDocumentActive)) return@LaunchedEffect
        val response = runCatching { app.api.getWebClientVersion() }.getOrNull() ?: return@LaunchedEffect
        val prior = knownWebClientVersion
        val version = runCatching {
            response.body?.let { org.json.JSONObject(it).optString("version") }
        }.getOrNull()?.takeIf { it.isNotBlank() } ?: return@LaunchedEffect
        knownWebClientVersion = version
        if (prior != null && prior != version) view.reload()
    }

    // 麦克风授权桥：把网页的 getUserMedia 与系统运行时权限串起来。
    // 不变量 —— 只有在系统真的授予 RECORD_AUDIO 之后，才对网页放行麦克风。
    // trustedOrigins 懒解析：服务器地址可能在本次组合之后被连接页改写。
    val micBridge = remember {
        WebMicPermissionBridge(
            activity = { context as? FragmentActivity },
            trustedOrigins = {
                listOf(
                    webViewRef.value?.url,
                    app.api.baseUrl,
                ).mapNotNull { parseTrustedOrigin(it) }
            },
        )
    }

    // 断网检测：离线时显示横幅，网络恢复后自动 reload 网页版
    var offline by remember { mutableStateOf(false) }
    val networkMonitor = remember {
        ConnectivityMonitor(
            webViewProvider = { webViewRef.value },
            onOffline = { offline = true },
            onOnline = { wv ->
                offline = false
                wv?.reload()
            },
        )
    }
    DisposableEffect(Unit) {
        networkMonitor.register(context)
        onDispose { networkMonitor.unregister(context) }
    }

    // 文件选择器（网页版「添加资料」按钮 → input[type=file] → 系统文件管理器）
    var fileCallback by remember { mutableStateOf<ValueCallback<Array<Uri>>?>(null) }
    val fileLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri ->
        fileCallback?.onReceiveValue(uri?.let { arrayOf(it) })
        fileCallback = null
    }
    val multiFileLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments(),
    ) { uris ->
        fileCallback?.onReceiveValue(uris.toTypedArray())
        fileCallback = null
    }

    // 系统返回键：优先 WebView 后退，无历史时退出应用。
    // ⚠️ 禁止在回调里调用 onBackPressedDispatcher.onBackPressed()：会重新
    // 触发本 BackHandler 回调造成无限递归（StackOverflowError 闪退，实机复现）。
    var leavingWeb by remember { mutableStateOf(false) }
    fun leaveWeb(destination: () -> Unit) {
        if (leavingWeb) return
        leavingWeb = true
        val view = webViewRef.value
        if (view == null) { destination(); return }
        var finished = false
        fun finish() { if (!finished) { finished = true; destination() } }
        // A crashed renderer must not trap Android's Back action.
        view.postDelayed({ finish() }, 1000)
        val captureScript = context.assets.open("native-composer-state.js").bufferedReader().use { it.readText() }
        view.evaluateJavascript(captureScript) { raw ->
            runCatching {
                val value = org.json.JSONTokener(raw).nextValue() as? String ?: return@runCatching
                val result = org.json.JSONObject(value)
                NativeComposerHandoff.captureReturn(context, app.api, result.optString("url"), if (result.isNull("draft")) null else result.optString("draft"), result.optJSONObject("composer"))
            }
            finish()
        }
    }
    BackHandler {
        val wv = webViewRef.value
        if (wv != null && wv.canGoBack()) {
            wv.goBack()        } else {
            leaveWeb(onBackToNative)
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize().windowInsetsPadding(WindowInsets.statusBars)) {

            // 全屏网页（imePadding：键盘弹出时 WebView 整体抬升，输入框不被键盘遮挡；
            // navigationBarsPadding：三键导航时底部内容显示在导航键上方）
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .imePadding()
                    .navigationBarsPadding(),
            ) {
                // 断网横幅（网络恢复后自动消失并 reload）
                if (offline) {
                    Box(
                        modifier = Modifier
                            .fillMaxWidth()
                            .align(Alignment.TopCenter)
                            .background(Color(0xFFB3261E))
                            .statusBarsPadding()
                            .padding(horizontal = 16.dp, vertical = 10.dp),
                    ) {
                        Text(
                            text = "网络已断开，正在等待恢复…",
                            color = Color.White,
                            fontSize = 13.sp,
                        )
                    }
                }
                AndroidView(
                    factory = { ctx ->
                        WebView(ctx).apply {
                            layoutParams = ViewGroup.LayoutParams(
                                ViewGroup.LayoutParams.MATCH_PARENT,
                                ViewGroup.LayoutParams.MATCH_PARENT,
                            )
                            settings.javaScriptEnabled = true
                            settings.domStorageEnabled = true
                            settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                            // WebRTC：bot 的声音走 <audio autoplay>，断线自动重连时没有
                            // 新的用户手势；默认 mediaPlaybackRequiresUserGesture=true
                            // 会把远端音频挡掉。
                            settings.mediaPlaybackRequiresUserGesture = false
                            settings.textZoom = 100
                            // 宽度定死为手机真实宽度：尊重 viewport meta（device-width），
                            // 初始缩放强制 100%（1:1 不缩放），禁用双指/页面缩放，不缩略显示
                            settings.useWideViewPort = true
                            settings.loadWithOverviewMode = false
                            this.setInitialScale(100)
                            settings.setSupportZoom(false)
                            settings.builtInZoomControls = false
                            settings.displayZoomControls = false
                            overScrollMode = android.view.View.OVER_SCROLL_NEVER

                            // Cookie 保存（登录态跨重启保持）
                            CookieManager.getInstance().setAcceptCookie(true)
                            CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)

                            // 原生 JS bridge：
                            //  - clearAuth：网页登出/会话失效时清空 DataStore（防注入死循环）
                            //  - download：网页真实 URL 下载走原生 OkHttp（自动附 Bearer/进度/管理页）
                            //  - saveBase64：网页纯前端生成的 blob 下载（导出 md/svg/ics 等）
                            addJavascriptInterface(
                                NativeBridge(
                                    context = ctx,
                                    hapticOwner = hapticOwner,
                                    onThemeChange = { theme -> app.appearanceStore.setTheme(authState, theme) },
                                    onThinkingChainDefaultChange = { raw ->
                                        scope.launch {
                                            if (raw.length > 80_000) return@launch
                                            val value = runCatching { org.json.JSONObject(raw) }.getOrNull() ?: return@launch
                                            val open = value.opt("open") as? Boolean ?: return@launch
                                            val current = authState
                                            if (app.api.token != current.token || app.api.workspaceId != current.workspaceId ||
                                                app.api.accountId != (current.userId ?: current.username) ||
                                                !NativeRendererPolicy.sameOrigin(app.api.baseUrl, current.baseUrl)) return@launch
                                            if (!NativeRendererPolicy.acceptsPreferenceScope(current.baseUrl, current.token, current.workspaceId,
                                                    value.optString("origin"), value.optString("token"), value.optString("workspaceId"))) return@launch
                                            val page = webViewRef.value?.url ?: return@launch
                                            if (NativeRendererPolicy.workspaceDestination(page, current.baseUrl, current.workspaceId.orEmpty()) == null) return@launch
                                            app.appearanceStore.setThinkingChainDefaultOpen(com.learngraph.mobile.data.NativeAppearanceStore.scope(current), open)
                                        }
                                    },
                                    onSidebarActivity = { raw ->
                                        scope.launch {
                                            if (raw.length > 16_000) return@launch
                                            val value = runCatching { org.json.JSONObject(raw) }.getOrNull() ?: return@launch
                                            val current = authState
                                            val key = activityScope ?: return@launch
                                            if (value.optString("owner") != documentOwner || NativeSidebarActivityStore.scope(app.api) != key) return@launch
                                            if (app.api.token != current.token || app.api.workspaceId != current.workspaceId ||
                                                app.api.accountId != (current.userId ?: current.username) ||
                                                !NativeRendererPolicy.sameOrigin(app.api.baseUrl, current.baseUrl)) return@launch
                                            if (!NativeRendererPolicy.acceptsPreferenceScope(current.baseUrl, current.token, current.workspaceId,
                                                    value.optString("origin"), value.optString("token"), value.optString("workspaceId"))) return@launch
                                            val page = webViewRef.value?.url ?: return@launch
                                            if (NativeRendererPolicy.workspaceDestination(page, current.baseUrl, current.workspaceId.orEmpty()) == null) return@launch
                                            sidebarActivity.applyWebEvent(key, documentOwner, raw)
                                        }
                                    },
                                    onClearAuth = {
                                        scope.launch { runCatching { app.authStore.clearAuth() } }
                                    },
                                    onDownload = { url, fileName ->
                                        val appCtx = ctx.applicationContext
                                        val token = if (isSameServer(Uri.parse(url), app.api.baseUrl)) {
                                            authState.token
                                        } else {
                                            null
                                        }
                                        scope.launch {
                                            runCatching {
                                                DownloadStore.enqueue(
                                                    appCtx,
                                                    url,
                                                    authToken = token,
                                                    fileNameOverride = fileName,
                                                    workspaceId = authState.workspaceId,
                                                    deviceId = authState.deviceId,
                                                )
                                            }
                                        }
                                    },
                                    onSaveBase64 = { dataUrl, fileName ->
                                        scope.launch {
                                            runCatching {
                                                DownloadStore.saveBase64(ctx.applicationContext, dataUrl, fileName)
                                            }
                                        }
                                    },
                                    onGetInbox = {
                                        com.learngraph.mobile.data.ShareInbox
                                            .encode(com.learngraph.mobile.data.ShareInbox.list(ctx.applicationContext))
                                    },
                                    onClearInboxItem = { id ->
                                        scope.launch {
                                            runCatching {
                                                com.learngraph.mobile.data.ShareInbox.remove(ctx.applicationContext, id)
                                            }
                                        }
                                    },
                                    onClearInbox = {
                                        scope.launch {
                                            runCatching {
                                                com.learngraph.mobile.data.ShareInbox.clear(ctx.applicationContext)
                                            }
                                        }
                                    },
                                    onGetInboxImageDataUrl = { id ->
                                        com.learngraph.mobile.data.ShareInbox.imageDataUrl(ctx.applicationContext, id)
                                    },
                                    onGetInboxFileDataUrl = { id ->
                                        com.learngraph.mobile.data.ShareInbox.fileDataUrl(ctx.applicationContext, id)
                                    },
                                    onTakePhoto = {
                                        com.learngraph.mobile.util.PhotoCapture.launch(ctx)
                                    },
                                    onShortcutAction = {
                                        com.learngraph.mobile.util.ShortcutActions.consume(ctx.applicationContext)
                                    },
                                ),
                                "LearnGraphNative",
                            )

                            webViewClient = object : WebViewClient() {
                                private var loadFailed = false
                                private var offlineFallbackUrl: String? = null

                                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                                    val current = authState
                                    if (current.token != app.api.token || current.workspaceId != app.api.workspaceId ||
                                        !NativeRendererPolicy.sameOrigin(current.baseUrl, app.api.baseUrl)) return null
                                    val requested = request.url.toString()
                                    return NativeWebAssets.response(
                                        ctx,
                                        request,
                                        app.api.baseUrl,
                                        current,
                                        offlineDocument = request.isForMainFrame && requested == offlineFallbackUrl,
                                    )
                                }

                                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                                    val url = request.url
                                    val scheme = url.scheme?.lowercase()
                                    if (scheme == "http" || scheme == "https") {
                                        // 非本服务器主机（外链）→ 跳系统浏览器，防闪退
                                        if (!isSameServer(url, app.api.baseUrl)) {
                                            openInSystemBrowser(ctx, url.toString())
                                            return true
                                        }
                                        return false
                                    }
                                    return false
                                }

                                override fun onPageFinished(view: WebView, url: String?) {
                                    super.onPageFinished(view, url)
                                    try {
                                        if (!offlineDocumentActive) {
                                            NativeRendererPolicy.recordServedWebShell(url, app.api.baseUrl)
                                        }
                                        if (!offlineDocumentActive) {
                                            scope.launch {
                                                runCatching { app.api.getWebClientVersion() }.getOrNull()
                                                    ?.body?.let { body ->
                                                        org.json.JSONObject(body).optString("version")
                                                            .takeIf { it.isNotBlank() }
                                                    }?.let { knownWebClientVersion = it }
                                            }
                                        }
                                        syncLoginState(view)
                                        if (url != null && NativeRendererPolicy.workspaceDestination(url, app.api.baseUrl, app.api.workspaceId.orEmpty()) != null) {
                                            view.evaluateJavascript(ctx.assets.open("native-navigation.js").bufferedReader().use { it.readText() }, null)
                                            activityScope?.takeIf { NativeSidebarActivityStore.scope(app.api) == it }?.let { key ->
                                                val request = org.json.JSONObject().put("owner", documentOwner).put("origin", NativeRendererPolicy.origin(authState.baseUrl))
                                                    .put("token", authState.token.orEmpty()).put("workspaceId", authState.workspaceId.orEmpty())
                                                    .put("seed", org.json.JSONObject(sidebarActivity.snapshotJson(key)))
                                                val activityScript = ctx.assets.open("native-sidebar-activity.js").bufferedReader().use { it.readText() }
                                                val token = NativeRendererPolicy.scriptJson(org.json.JSONObject.quote(authState.token.orEmpty()))
                                                view.evaluateJavascript("(function(){if(window.top!==window||localStorage.getItem('learngraph.access_token')!==$token)return;($activityScript)(${NativeRendererPolicy.scriptJson(request.toString())});})()", null)
                                            }
                                            if (BuildConfig.DEBUG) view.evaluateJavascript(ctx.assets.open("native-ui-metrics.js").bufferedReader().use { it.readText() }, null)
                                        }
                                        NativeComposerHandoff.pending(ctx, app.api, url)?.let { request ->
                                            val script = ctx.assets.open("native-composer.js").bufferedReader().use { it.readText() }
                                            val expectedToken = NativeRendererPolicy.scriptJson(org.json.JSONObject.quote(app.api.token.orEmpty()))
                                            view.evaluateJavascript("(function(){if(window.top!==window||localStorage.getItem('learngraph.access_token')!==$expectedToken)return;$script(${NativeRendererPolicy.scriptJson(request)});})()", null)
                                        }
                                    } catch (_: Exception) {
                                        // JS 桥/注入异常不打断页面加载
                                    }
                                    // Cookie 强制写盘：登录态持久化（重启免登录）
                                    try {
                                        CookieManager.getInstance().flush()
                                    } catch (_: Exception) {
                                        // flush 失败不影响页面
                                    }
                                }

                                override fun onPageStarted(view: WebView, url: String?, favicon: android.graphics.Bitmap?) {
                                    super.onPageStarted(view, url, favicon)
                                    offlineDocumentActive = url != null && url == offlineFallbackUrl
                                    activityScope?.let { sidebarActivity.releaseOwner(it, documentOwner) }
                                    documentOwner = UUID.randomUUID().toString()
                                    loadFailed = false
                                    if (url != offlineFallbackUrl) offlineFallbackUrl = null
                                }

                                // 网页加载失败（服务器不可达/网络异常）→ 自动调回连接页
                                override fun onReceivedError(
                                    view: WebView,
                                    request: WebResourceRequest,
                                    error: WebResourceError,
                                ) {
                                    super.onReceivedError(view, request, error)
                                    if (request.isForMainFrame && !loadFailed) {
                                        loadFailed = true
                                        val failedUrl = request.url.toString()
                                        val current = authState
                                        if (error.errorCode !in setOf(
                                                WebViewClient.ERROR_UNSUPPORTED_SCHEME,
                                                WebViewClient.ERROR_HOST_LOOKUP,
                                            ) &&
                                            NativeRendererPolicy.canUseOfflineApplication(
                                                failedUrl, app.api.baseUrl, current.workspaceId.orEmpty(),
                                            )
                                        ) {
                                            offlineFallbackUrl = failedUrl
                                            offlineDocumentActive = true
                                            view.postDelayed({
                                                if (offlineFallbackUrl == failedUrl && view.url == failedUrl) {
                                                    android.widget.Toast.makeText(
                                                        ctx,
                                                        "服务器暂不可达，已打开离线界面",
                                                        android.widget.Toast.LENGTH_SHORT,
                                                    ).show()
                                                }
                                            }, 1_500)
                                            view.loadUrl(failedUrl)
                                        } else {
                                            onLoadFailed()
                                        }
                                    }
                                }

                                /**
                                 * WebView renderer 进程崩溃接管（API 26+，Android 8+）。
                                 *
                                 * renderer（渲染/JS 进程）是独立进程，内存不足或渲染错误时可能
                                 * 被杀。默认行为是连带杀掉整个应用 → 表现为「进网页时闪退」。
                                 * 返回 true 表示应用自行处理：记录日志并在原 URL 上重新加载
                                 * （reload 会重新拉起 renderer 进程）。Android 6/7 为单进程
                                 * WebView，无此回调，崩溃只能靠减少内存压力规避。
                                 */
                                @android.annotation.SuppressLint("WebViewClientOnRenderProcessGone")
                                override fun onRenderProcessGone(
                                    view: WebView,
                                    detail: android.webkit.RenderProcessGoneDetail,
                                ): Boolean {
                                    try {
                                        android.util.Log.e(
                                            "LearnGraphWeb",
                                            "WebView renderer 崩溃：crash=${detail.didCrash()} 已接管恢复",
                                        )
                                    } catch (_: Exception) {
                                    }
                                    loadFailed = true
                                    val target = view.url ?: app.api.baseUrl
                                    val current = authState
                                    val retryUrl = target
                                        .replace("?__lg_offline_shell=1", "")
                                        .replace("&__lg_offline_shell=1", "")
                                    if (NativeRendererPolicy.canRetryPackagedApplication(
                                            retryUrl, app.api.baseUrl, current.workspaceId.orEmpty(),
                                        )
                                    ) {
                                        offlineFallbackUrl = retryUrl
                                        offlineDocumentActive = true
                                    }
                                    view.post {
                                        try {
                                            view.loadUrl(retryUrl)
                                        } catch (_: Exception) {
                                            onLoadFailed()
                                        }
                                    }
                                    return true
                                }

                                /**
                                 * 登录态同步（免登录核心）：
                                 *  - 网页版未登录（localStorage 无 token）且原生 DataStore 有 token
                                 *    → 注入原生 token + reload（自愈）
                                 *  - 网页版已登录且 token 与原生一致 → 无事
                                 *  - 网页版 token 与原生不同（首次登录/网页版内重登/换账号）
                                 *    → 回写 DataStore（token + workspace），持续免登录
                                 *  reload 后再次 onPageFinished：token 已一致 → 停止，无循环。
                                 * 说明：前端 auth-store 登录时会把 token 双写 sessionStorage +
                                 * localStorage（见 frontend/src/api/auth-store.ts），这里从
                                 * localStorage 读取即为网页真实登录态；登出时前端会通过
                                 * LearnGraphNative.clearAuth() 通知本端清空 DataStore，
                                 * 避免旧 token 被重新注入。
                                 */
                                private fun syncLoginState(view: WebView) {
                                    // Keep the native host on the Web user's explicit theme, including SPA settings changes.
                                    view.evaluateJavascript("""(function(){if(window.__lgThemeObserver)return;function sync(){LearnGraphNative.setTheme(document.documentElement.classList.contains('dark')?'dark':'light')}window.__lgThemeObserver=new MutationObserver(sync);window.__lgThemeObserver.observe(document.documentElement,{attributes:true,attributeFilter:['class']});sync();})()""", null)
                                    val nativeToken = authState.token
                                    val js = buildString {
                                        append("(function(){")
                                        append("var cur=localStorage.getItem('learngraph.access_token')||'';")
                                        append("var curWs=localStorage.getItem('learngraph.workspace_id')||'';")
                                        append("var want='")
                                        append(escapeJs(nativeToken.orEmpty()))
                                        append("';")
                                        append("if(!cur){")
                                        append("if(!want){return 'none';}")
                                        append("localStorage.setItem('learngraph.access_token',want);")
                                        append("localStorage.setItem('learngraph.workspace_id','")
                                        append(escapeJs(authState.workspaceId.orEmpty()))
                                        append("');")
                                        append("localStorage.setItem('learngraph.device_id','")
                                        append(escapeJs(authState.deviceId))
                                        append("');")
                                        append("return 'injected';")
                                        append("}")
                                        append("if(cur!==want){return 'token:'+cur+'|ws:'+curWs;}")
                                        append("return 'ok';")
                                        append("})()")
                                    }
                                    try {
                                        view.evaluateJavascript(js) { result ->
                                            val trimmed = result?.trim()?.trim('"') ?: return@evaluateJavascript
                                            when {
                                                trimmed == "injected" -> view.post { view.reload() }
                                                trimmed.startsWith("token:") -> {
                                                    val payload = trimmed.removePrefix("token:")
                                                    val sep = payload.indexOf("|ws:")
                                                    val newToken = if (sep >= 0) payload.substring(0, sep) else payload
                                                    val newWs = if (sep >= 0) payload.substring(sep + 4).takeIf { it.isNotBlank() } else null
                                                    if (newToken.isNotBlank()) {
                                                        scope.launch {
                                                            runCatching {
                                                                app.authStore.updateToken(newToken)
                                                                if (newWs != null) app.authStore.updateWorkspace(newWs)
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    } catch (_: Exception) {
                                        // WebView 已销毁/状态异常时静默跳过同步
                                    }
                                }
                            }

                            // 内置下载器：拦截网页下载
                            setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
                                requestNotifPermissionIfNeeded()
                                val token = if (isSameServer(Uri.parse(url), app.api.baseUrl)) authState.token else null
                                DownloadStore.enqueue(
                                    context = ctx,
                                    url = url,
                                    contentDisposition = contentDisposition,
                                    mimeType = mimeType,
                                    userAgent = userAgent,
                                    authToken = token,
                                    workspaceId = authState.workspaceId,
                                    deviceId = authState.deviceId,
                                )
                            }

                            // 文件选择：网页版「添加资料」→ input[type=file] → 系统文件管理器
                            webChromeClient = object : WebChromeClient() {
                                override fun onShowFileChooser(
                                    webView: WebView,
                                    filePathCallback: ValueCallback<Array<Uri>>,
                                    fileChooserParams: FileChooserParams,
                                ): Boolean {
                                    // 网页 input[type=file][capture] → 直接走系统相机（拍照即问）
                                    if (fileChooserParams.isCaptureEnabled) {
                                        PhotoCapture.setWebView(webView)
                                        PhotoCapture.launch(ctx)
                                        return true
                                    }
                                    // 取消上一次未消费的回调，避免 WebView 卡住
                                    fileCallback?.onReceiveValue(null)
                                    fileCallback = filePathCallback
                                    val mimeTypes = fileChooserParams.acceptTypes
                                        .filter { it.isNotBlank() }
                                        .toTypedArray()
                                        .ifEmpty { arrayOf("*/*") }
                                    if (fileChooserParams.mode == FileChooserParams.MODE_OPEN_MULTIPLE) {
                                        multiFileLauncher.launch(mimeTypes)
                                    } else {
                                        fileLauncher.launch(mimeTypes)
                                    }
                                    return true
                                }

                                // 麦克风（语音模式 / 长按语音条 / 听写）：网页 getUserMedia({audio})
                                //
                                // ⚠️ 不能在这里无条件 grant。grant() 只表示「网页被允许用
                                // 麦克风」，不代表本 App 持有系统录音权限；系统没授权就放行，
                                // chromium 建不出音频输入流，getUserMedia 会 reject 成
                                // NotAllowedError（message 就是 "Permission denied"）。
                                // 这里全部交给 WebMicPermissionBridge：它先把系统权限拿到手
                                // （用时才申请、可反复申请、永久拒绝时引导去设置），再放行网页。
                                override fun onPermissionRequest(request: android.webkit.PermissionRequest) {
                                    micBridge.onPermissionRequest(request)
                                }

                                // 网页跳转 / 重复申请时 WebView 会取消挂起的申请：
                                // 必须清引用，否则对已取消的 request 调 grant() 会抛异常。
                                override fun onPermissionRequestCanceled(request: android.webkit.PermissionRequest) {
                                    micBridge.onPermissionRequestCanceled(request)
                                }
                            }

                            webViewRef.value = this
                            knownWebClientVersion = null
                            settings.cacheMode = WebSettings.LOAD_DEFAULT
                            PhotoCapture.setWebView(this)
                            val workspace = app.api.workspaceId
                            val featureTarget = if (!workspace.isNullOrBlank() && initialFeature != null) NativeRendererPolicy.featureUrl(app.api.baseUrl, workspace, initialFeature) else null
                            val target = if (featureTarget != null) {
                                featureTarget
                            } else if (!sessionId.isNullOrBlank() && !workspace.isNullOrBlank()) {
                                Uri.parse(app.api.baseUrl).buildUpon().appendPath("w").appendPath(workspace)
                                    .appendPath("chat").appendPath(sessionId).build().toString()
                            } else app.api.baseUrl
                            // Establish native identity before remote application JavaScript can run.
                            // Both stores are required: the web auth store reads sessionStorage first.
                            val handoff = org.json.JSONObject().apply {
                                put("learngraph.access_token", app.api.token ?: org.json.JSONObject.NULL)
                                put("learngraph.workspace_id", app.api.workspaceId ?: org.json.JSONObject.NULL)
                                put("learngraph.device_id", app.api.deviceId)
                                put("learngraph.user_id", authState.userId ?: org.json.JSONObject.NULL)
                                put("learngraph.username", authState.username ?: org.json.JSONObject.NULL)
                                put("learngraph.display_name", authState.displayName ?: org.json.JSONObject.NULL)
                                put("learngraph.session_id", authState.sessionId ?: org.json.JSONObject.NULL)
                                put("learngraph.avatar_url", org.json.JSONObject.NULL)
                                put("learngraph.must_change_password", org.json.JSONObject.NULL)
                            }.toString().replace("<", "\\u003c")
                            val quotedTarget = org.json.JSONObject.quote(target).replace("<", "\\u003c")
                            val bootstrap = """<!doctype html><meta charset="utf-8"><script>(function(){var a=$handoff;Object.keys(a).forEach(function(k){[localStorage,sessionStorage].forEach(function(s){if(a[k]===null)s.removeItem(k);else s.setItem(k,a[k]);});});location.replace($quotedTarget);})()</script>"""
                            loadDataWithBaseURL(app.api.baseUrl, bootstrap, "text/html", "UTF-8", null)
                        }
                    },
                    update = { },
                    // 页面离开组合时销毁 WebView，避免每次进出网页累积内存
                    // （WebView 不销毁会持续占用内存，最终 OOM 触发 renderer 崩溃）
                    onRelease = { wv ->
                        webViewRef.value = null
                        PhotoCapture.setWebView(null)
                        try {
                            wv.stopLoading()
                            wv.removeAllViews()
                            wv.destroy()
                        } catch (_: Exception) {
                            // destroy 失败静默
                        }
                    },
                )
            }
        }

        // 右下角悬浮下载入口（仅存在下载任务时显示，不破坏纯网页体验）
        if (downloadTasks.isNotEmpty()) {
            Row(
                modifier = Modifier
                    .align(Alignment.BottomEnd)
                    .navigationBarsPadding()
                    .padding(16.dp)
                    .clip(RoundedCornerShape(999.dp))
                    .background(Color(0xEFFFFFFF))
                    .clickable(onClick = { leaveWeb(onOpenDownloads) })
                    .padding(horizontal = 14.dp, vertical = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(
                    text = "↓",
                    fontSize = 16.sp,
                    color = MaterialTheme.colorScheme.primary,
                )
                Spacer(Modifier.width(6.dp))
                Text(
                    text = if (activeDownloads > 0) "$activeDownloads 个下载中" else "下载",
                    fontSize = 13.sp,
                    color = Color(0xFF2A2E35),
                )
            }
        }
    }
}

/** 通知权限（仅 API 33+ 需要）：统一走 PermissionGate，避免与其它权限申请互相挤掉。 */
private fun requestNotifPermissionIfNeeded() {
    if (Build.VERSION.SDK_INT < 33) return
    PermissionGate.request(Manifest.permission.POST_NOTIFICATIONS)
}

/** 网页 → 原生 JS bridge（Android 4.2+ 需要 @JavascriptInterface 才暴露） */
private class NativeBridge(
    private val context: Context,
    private val hapticOwner: String,
    private val onThemeChange: (String) -> Unit,
    private val onThinkingChainDefaultChange: (String) -> Unit,
    private val onSidebarActivity: (String) -> Unit,
    private val onClearAuth: () -> Unit,
    private val onDownload: (url: String, fileName: String?) -> Unit,
    private val onSaveBase64: (dataUrl: String, fileName: String?) -> Unit,
    private val onGetInbox: () -> String,
    private val onClearInboxItem: (id: String) -> Unit,
    private val onClearInbox: () -> Unit,
    private val onGetInboxImageDataUrl: (id: String) -> String?,
    private val onGetInboxFileDataUrl: (id: String) -> String?,
    private val onTakePhoto: () -> Unit,
    private val onShortcutAction: () -> String?,
) {
    @android.webkit.JavascriptInterface
    fun setTheme(theme: String) { onThemeChange(theme) }
    @android.webkit.JavascriptInterface
    fun setThinkingChainDefaultOpen(payload: String) { onThinkingChainDefaultChange(payload) }
    @android.webkit.JavascriptInterface
    fun sidebarActivity(payload: String) { onSidebarActivity(payload) }
    @android.webkit.JavascriptInterface
    fun reportUiParity(payload: String) { UiParityDiagnostics.web(payload) }
    @android.webkit.JavascriptInterface
    fun clearAuth() {
        onClearAuth()
    }

    @android.webkit.JavascriptInterface
    fun download(url: String, fileName: String?) {
        onDownload(url, fileName)
    }

    @android.webkit.JavascriptInterface
    fun saveBase64(dataUrl: String, fileName: String?) {
        onSaveBase64(dataUrl, fileName)
    }

    /** 返回分享收件箱 JSON 数组（[{id,kind,text,imagePath,mime,source,created_at}]） */
    @android.webkit.JavascriptInterface
    fun getInboxItems(): String = onGetInbox()

    @android.webkit.JavascriptInterface
    fun clearInboxItem(id: String) {
        onClearInboxItem(id)
    }

    @android.webkit.JavascriptInterface
    fun clearInbox() {
        onClearInbox()
    }

    /** 收件箱图片转 base64 data URL（无则返回空字符串） */
    @android.webkit.JavascriptInterface
    fun getInboxImageDataUrl(id: String): String = onGetInboxImageDataUrl(id) ?: ""

    /** 收件箱文件（任意类型）转 base64 data URL（无则返回空字符串） */
    @android.webkit.JavascriptInterface
    fun getInboxFileDataUrl(id: String): String = onGetInboxFileDataUrl(id) ?: ""

    /** 打开系统相机拍照（结果经 window.__lgPhotoCallback 回调网页版） */
    @android.webkit.JavascriptInterface
    fun takePhoto() {
        onTakePhoto()
    }

    /** 读取待消费的快捷动作（如 "new-chat"），消费后返回空字符串 */
    @android.webkit.JavascriptInterface
    fun consumeShortcutAction(): String = onShortcutAction() ?: ""

    @android.webkit.JavascriptInterface
    fun completeComposerAction(id: String, draftTransferred: Boolean) { NativeComposerHandoff.complete(context, id, draftTransferred) }

    // ------------------------------------------------------------------ //
    // A 类触觉 / 提示音：网页版在渲染关键时刻触发
    // ------------------------------------------------------------------ //

    /** 触觉反馈（轻微震动）：intensity 0/1/2 */
    @android.webkit.JavascriptInterface
    fun haptic(intensity: Int) {
        Haptics.haptic(context, intensity, hapticOwner)
    }

    /** 最终回复到达轻震（思维链结束后第一帧正文） */
    @android.webkit.JavascriptInterface
    fun replyHaptic() {
        Haptics.replyHaptic(context, hapticOwner)
    }

    /** 开始最终回答渲染期「答答答」持续震动 */
    @android.webkit.JavascriptInterface
    fun startReplyVibration() {
        Haptics.startReplyVibration(context, hapticOwner)
    }

    /** 结束回答渲染期持续震动 */
    @android.webkit.JavascriptInterface
    fun stopReplyVibration() {
        Haptics.stopReplyVibration(context, hapticOwner)
    }

    /** agent 工具/步骤完成弱震 */
    @android.webkit.JavascriptInterface
    fun stepHaptic() {
        Haptics.stepHaptic(context, hapticOwner)
    }

    /** 目标/掌握度达成庆祝（短-长-短） */
    @android.webkit.JavascriptInterface
    fun celebration() {
        Haptics.celebration(context, hapticOwner)
    }

    /** 提示音（可选，默认关） */
    @android.webkit.JavascriptInterface
    fun chime() {
        com.learngraph.mobile.util.Haptics.chime()
    }

    /** 朗读文本（B4 耳机自动朗读 / 手动播报） */
    @android.webkit.JavascriptInterface
    fun speak(text: String) {
        com.learngraph.mobile.util.TtsSynth.speak(context, text)
    }
}

private fun isSameServer(url: Uri, baseUrl: String): Boolean {
    val base = Uri.parse(baseUrl.trimEnd('/'))
    val host = url.host?.lowercase() ?: return false
    val baseHost = base.host?.lowercase() ?: return false
    if (host != baseHost) return false
    val effectivePort = { p: Int, scheme: String? -> if (p != -1) p else if (scheme == "https") 443 else 80 }
    return effectivePort(url.port, url.scheme) == effectivePort(base.port, base.scheme)
}

private fun escapeJs(s: String): String =
    s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")

/**
 * 网络状态监测（断线重连）：
 *  - 离线 → onOffline（显示横幅）
 *  - 恢复在线 → onOnline（自动 reload 网页版）
 * 仅关注传输层连通性（Wi-Fi/蜂窝），与「服务器可达性」解耦——
 * 服务器不可达仍由 WebView 的 onReceivedError 处理（跳连接页）。
 * 所有回调统一 post 到主线程；注册后首次 onAvailable（当前已有网络）不触发 reload。
 */
private class ConnectivityMonitor(
    private val webViewProvider: () -> WebView?,
    private val onOffline: () -> Unit,
    private val onOnline: (webView: WebView?) -> Unit,
) {
    private val mainHandler = android.os.Handler(android.os.Looper.getMainLooper())
    @Volatile
    private var registered = false

    private val callback = object : android.net.ConnectivityManager.NetworkCallback() {
        override fun onLost(network: android.net.Network) {
            mainHandler.post { if (registered) onOffline() }
        }

        override fun onAvailable(network: android.net.Network) {
            // 注册本身会立即触发一次 onAvailable：仅当之后真的断开又恢复才 reload
            if (!registered) return
            mainHandler.post { if (registered) onOnline(webViewProvider()) }
        }
    }

    fun register(context: Context) {
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as android.net.ConnectivityManager
        registered = true
        runCatching {
            cm.registerDefaultNetworkCallback(callback)
        }
    }

    fun unregister(context: Context) {
        registered = false
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as android.net.ConnectivityManager
        runCatching { cm.unregisterNetworkCallback(callback) }
    }
}


/** 外链跳系统浏览器：异常兜底，绝不闪退 */
private fun openInSystemBrowser(context: Context, url: String) {
    try {
        val intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
    } catch (e: android.content.ActivityNotFoundException) {
        android.widget.Toast.makeText(
            context, "没有可打开该链接的浏览器", android.widget.Toast.LENGTH_SHORT,
        ).show()
    } catch (_: Exception) {
        // 其他异常静默，不闪退
    }
}
