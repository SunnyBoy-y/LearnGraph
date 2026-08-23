package com.learngraph.mobile.ui.web

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.view.ViewGroup
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
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.data.DownloadStatus
import com.learngraph.mobile.data.DownloadStore
import com.learngraph.mobile.util.PhotoCapture
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import org.mozilla.geckoview.AllowOrDeny
import org.mozilla.geckoview.GeckoResult
import org.mozilla.geckoview.GeckoSession
import org.mozilla.geckoview.GeckoView
import org.mozilla.geckoview.WebResponse

/**
 * 纯网页模式（v0.8.0；v0.14.0 内核换成内嵌 GeckoView）：
 *  - 无原生控件：没有底栏、没有顶栏按钮；顶部仅一层很薄的白色
 *  - 全屏 GeckoView 承载网页版，内核随 APK 分发，不再受手机自带 WebView 影响
 *  - 内置下载器：网页下载由 DownloadStore 接管（进度/通知/打开/管理），
 *    右下角悬浮入口（仅存在下载任务时显示）进入下载管理页
 *  - 登录态注入：localStorage（learngraph.*）写入后 reload 一次
 *  - 系统返回键：GeckoSession 可后退则后退，否则退出
 *  - JS 桥：GeckoView 无 addJavascriptInterface，改走内置 WebExtension
 *    （assets/messaging/）→ window.LearnGraphNative，消息由 BridgeHandlerImpl 处理
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun WebAppScreen(
    onOpenDownloads: () -> Unit,
    onLoadFailed: () -> Unit,
) {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp
    val authState by app.authStore.state.collectAsState(initial = AuthStore.AuthState())

    val sessionRef = remember { mutableStateOf<GeckoSession?>(null) }
    val downloadTasks by DownloadStore.tasks.collectAsState()
    val activeDownloads = downloadTasks.count { it.status == DownloadStatus.DOWNLOADING }
    val scope = rememberCoroutineScope()

    // 导航状态（GeckoView 139 无 canGoBack() 同步查询，用回调维护）
    var canGoBackState by remember { mutableStateOf(false) }
    // 当前 URL（onCrash 恢复用；GeckoSession 139 无 url 属性）
    var currentUrl by remember { mutableStateOf<String?>(null) }
    // 主框架加载失败防抖
    var loadFailed by remember { mutableStateOf(false) }
    // 外链已跳系统浏览器（防止其 DENY 导航触发 onPageStop(false) 误判加载失败）
    var externalNav by remember { mutableStateOf(false) }

    // 断网检测：离线时显示横幅，网络恢复后自动 reload 网页版
    var offline by remember { mutableStateOf(false) }
    val networkMonitor = remember {
        ConnectivityMonitor(
            sessionProvider = { sessionRef.value },
            onOffline = { offline = true },
            onOnline = { s ->
                offline = false
                s?.reload()
            },
        )
    }
    DisposableEffect(Unit) {
        networkMonitor.register(context)
        onDispose { networkMonitor.unregister(context) }
    }

    // 文件选择器（网页版「添加资料」按钮 → input[type=file] → 系统文件管理器）
    var pendingFilePrompt by remember {
        mutableStateOf<GeckoSession.PromptDelegate.FilePrompt?>(null)
    }
    var pendingFileResult by remember {
        mutableStateOf<GeckoResult<GeckoSession.PromptDelegate.PromptResponse>?>(null)
    }
    fun completeFileResult(uris: Array<Uri>) {
        val prompt = pendingFilePrompt
        pendingFileResult?.complete(prompt?.confirm(context, uris) ?: return)
        pendingFilePrompt = null
        pendingFileResult = null
    }
    val fileLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenDocument(),
    ) { uri -> completeFileResult(if (uri != null) arrayOf(uri) else emptyArray()) }
    val multiFileLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.OpenMultipleDocuments(),
    ) { uris -> completeFileResult(uris.toTypedArray()) }

    // 原生 JS 桥处理器（注册到 GeckoRuntimeHolder，页面加载期间有效）
    val bridgeHandler = remember {
        BridgeHandlerImpl(
            context = context,
            onClearAuth = {
                scope.launch { runCatching { app.authStore.clearAuth() } }
            },
            onDownload = { url, fileName ->
                requestNotifPermissionIfNeeded(context)
                val token = if (isSameServer(Uri.parse(url), app.api.baseUrl)) authState.token else null
                scope.launch {
                    runCatching {
                        DownloadStore.enqueue(
                            context = context,
                            url = url,
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
                    runCatching { DownloadStore.saveBase64(context, dataUrl, fileName) }
                }
            },
            onTakePhoto = { PhotoCapture.launch(context) },
            onReportToken = { token, ws ->
                scope.launch {
                    runCatching {
                        app.authStore.updateToken(token)
                        if (ws != null) app.authStore.updateWorkspace(ws)
                    }
                }
            },
        )
    }
    DisposableEffect(Unit) {
        GeckoRuntimeHolder.bridgeHandler = bridgeHandler
        onDispose {
            if (GeckoRuntimeHolder.bridgeHandler === bridgeHandler) {
                GeckoRuntimeHolder.bridgeHandler = null
            }
        }
    }

    // 登录态同步（免登录核心）：经 WebExtension 广播执行 JS，
    // localStorage 注入后由页面自身 reload；token 不一致时经 __reportToken 回写原生
    fun syncLoginState() {
        val nativeToken = authState.token
        val js = buildString {
            append("(function(){")
            append("var cur=localStorage.getItem('learngraph.access_token')||'';")
            append("var curWs=localStorage.getItem('learngraph.workspace_id')||'';")
            append("var want='")
            append(escapeJs(nativeToken.orEmpty()))
            append("';")
            append("if(!cur){")
            append("if(!want){return;}")
            append("localStorage.setItem('learngraph.access_token',want);")
            append("localStorage.setItem('learngraph.workspace_id','")
            append(escapeJs(authState.workspaceId.orEmpty()))
            append("');")
            append("localStorage.setItem('learngraph.device_id','")
            append(escapeJs(authState.deviceId))
            append("');")
            append("location.reload();")
            append("return;")
            append("}")
            append("if(cur!==want){")
            append("try{window.LearnGraphNative&&window.LearnGraphNative.__reportToken(cur,curWs||'');}catch(e){}")
            append("}")
            append("})()")
        }
        GeckoRuntimeHolder.evalJs(js)
    }

    // 系统返回键：优先 GeckoSession 后退，无历史时退出应用。
    BackHandler {
        val s = sessionRef.value
        if (s != null && canGoBackState) {
            s.goBack()
        } else {
            (context as? Activity)?.finish()
        }
    }

    Box(modifier = Modifier.fillMaxSize()) {
        Column(modifier = Modifier.fillMaxSize()) {
            // 很薄的白色顶栏：状态栏区域白底 + 2dp 白条（无任何按钮/控件）
            Box(
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Color.White)
                    .windowInsetsPadding(WindowInsets.statusBars)
                    .height(2.dp),
            ) {}

            // 全屏网页（imePadding：键盘弹出时整体抬升，输入框不被键盘遮挡）
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .imePadding(),
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
                        GeckoRuntimeHolder.init(ctx)
                        val geckoView = GeckoView(ctx).apply {
                            layoutParams = ViewGroup.LayoutParams(
                                ViewGroup.LayoutParams.MATCH_PARENT,
                                ViewGroup.LayoutParams.MATCH_PARENT,
                            )
                        }
                        val session = GeckoSession()
                        session.open(GeckoRuntimeHolder.runtime)

                        // 导航：外链跳系统浏览器、主框架加载失败 → 连接页
                        session.navigationDelegate = object : GeckoSession.NavigationDelegate {
                            override fun onCanGoBack(session: GeckoSession, canGoBack: Boolean) {
                                canGoBackState = canGoBack
                            }

                            override fun onLoadRequest(
                                session: GeckoSession,
                                request: GeckoSession.NavigationDelegate.LoadRequest,
                            ): GeckoResult<AllowOrDeny>? {
                                val url = request.uri
                                val scheme = Uri.parse(url).scheme?.lowercase()
                                if (scheme == "http" || scheme == "https") {
                                    // 非本服务器主机（外链）→ 跳系统浏览器
                                    if (!isSameServer(Uri.parse(url), app.api.baseUrl)) {
                                        externalNav = true
                                        openInSystemBrowser(ctx, url.toString())
                                        return GeckoResult.fromValue(AllowOrDeny.DENY)
                                    }
                                    return GeckoResult.fromValue(AllowOrDeny.ALLOW)
                                }
                                return GeckoResult.fromValue(AllowOrDeny.DENY)
                            }
                        }

                        // 进度：页面加载完成 → 登录态同步
                        session.progressDelegate = object : GeckoSession.ProgressDelegate {
                            override fun onPageStart(session: GeckoSession, url: String) {
                                loadFailed = false
                                currentUrl = url
                            }

                            override fun onPageStop(session: GeckoSession, success: Boolean) {
                                // 主文档加载失败（网络错误/服务器不可达）→ 连接页；
                                // 外链 DENY 后跳过的导航不算失败
                                if (!success) {
                                    if (externalNav) {
                                        externalNav = false
                                        return
                                    }
                                    if (!loadFailed) {
                                        loadFailed = true
                                        onLoadFailed()
                                    }
                                    return
                                }
                                try {
                                    syncLoginState()
                                } catch (_: Exception) {
                                    // 同步失败不影响页面
                                }
                            }

                            override fun onProgressChange(session: GeckoSession, progress: Int) {}
                        }

                        // 内容：下载（onExternalResponse）、内核崩溃接管（onCrash）
                        session.contentDelegate = object : GeckoSession.ContentDelegate {
                            override fun onExternalResponse(session: GeckoSession, response: WebResponse) {
                                requestNotifPermissionIfNeeded(ctx)
                                val url = response.uri
                                val contentType =
                                    response.headers["Content-Type"] ?: response.headers["content-type"]
                                val token = if (isSameServer(Uri.parse(url), app.api.baseUrl)) {
                                    authState.token
                                } else {
                                    null
                                }
                                DownloadStore.enqueue(
                                    context = ctx,
                                    url = url,
                                    contentDisposition = null,
                                    mimeType = contentType,
                                    userAgent = null,
                                    authToken = token,
                                    workspaceId = authState.workspaceId,
                                    deviceId = authState.deviceId,
                                )
                            }

                            override fun onCrash(session: GeckoSession) {
                                val target = currentUrl ?: app.api.baseUrl
                                session.loadUri(target)
                            }
                        }

                        // 文件选择：网页版「添加资料」→ input[type=file] → 系统文件管理器
                        session.promptDelegate = object : GeckoSession.PromptDelegate {
                            override fun onFilePrompt(
                                session: GeckoSession,
                                prompt: GeckoSession.PromptDelegate.FilePrompt,
                            ): GeckoResult<GeckoSession.PromptDelegate.PromptResponse> {
                                val result = GeckoResult<GeckoSession.PromptDelegate.PromptResponse>()
                                // 取消上一次未消费的回调，避免页面卡住
                                val old = pendingFilePrompt
                                if (old != null) {
                                    pendingFileResult?.complete(old.confirm(ctx, emptyArray<Uri>()))
                                }
                                pendingFilePrompt = prompt
                                pendingFileResult = result
                                // 网页 input[type=file][capture] → 直接走系统相机（拍照即问）
                                if (prompt.capture != 0) {
                                    PhotoCapture.launch(ctx)
                                    return result
                                }
                                val mimeTypes = (prompt.mimeTypes ?: emptyArray())
                                    .filter { it.isNotBlank() }
                                    .toTypedArray()
                                    .ifEmpty { arrayOf("*/*") }
                                // 多选兼容单选（单选时用户选一个文件即可）
                                multiFileLauncher.launch(mimeTypes)
                                return result
                            }
                        }

                        geckoView.setSession(session)
                        sessionRef.value = session
                        PhotoCapture.setSession(session)

                        // 等 WebExtension 就绪后再加载（content script 需在导航前注入；
                        // 即使扩展失败也照常加载页面，仅桥功能不可用）
                        GeckoRuntimeHolder.ensureExtension()
                            .accept { session.loadUri(app.api.baseUrl) }
                        geckoView
                    },
                    update = { },
                    // 页面离开组合时销毁 session，避免每次进出累积内存
                    onRelease = { gv ->
                        sessionRef.value = null
                        PhotoCapture.setSession(null)
                        try {
                            gv.session?.close()
                        } catch (_: Exception) {
                            // close 失败静默
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
                    .clickable(onClick = onOpenDownloads)
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

private fun requestNotifPermissionIfNeeded(ctx: Context) {
    if (Build.VERSION.SDK_INT >= 33) {
        val activity = ctx as? Activity ?: return
        if (
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(activity, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 2001)
        }
    }
}

/**
 * 网页 → 原生 JS 桥（GeckoView 版）：
 *  - 消息格式：{id, method, args}（JSON string）
 *  - 响应格式：{id, result}（JSON string）
 *  - 与旧版 addJavascriptInterface 的 NativeBridge 方法一一对应；
 *    getInboxItems / getInboxImageDataUrl / consumeShortcutAction 返回
 *    JSON 字符串结果，前端保持原有解析方式。
 */
private class BridgeHandlerImpl(
    private val context: Context,
    private val onClearAuth: () -> Unit,
    private val onDownload: (url: String, fileName: String?) -> Unit,
    private val onSaveBase64: (dataUrl: String, fileName: String?) -> Unit,
    private val onTakePhoto: () -> Unit,
    private val onReportToken: (token: String, workspaceId: String?) -> Unit,
) : BridgeMessageHandler {

    override fun handle(messageJson: String): GeckoResult<Any>? {
        return try {
            val obj = JSONObject(messageJson)
            val id = obj.optLong("id")
            val method = obj.optString("method")
            val args = obj.optJSONArray("args") ?: JSONArray()
            val result = dispatch(method, args)
            GeckoResult.fromValue(
                JSONObject().put("id", id).put("result", result ?: JSONObject.NULL).toString(),
            )
        } catch (_: Exception) {
            null
        }
    }

    private fun dispatch(method: String, args: JSONArray): Any? = when (method) {
        "clearAuth" -> {
            onClearAuth()
            null
        }
        "download" -> {
            onDownload(args.optString(0), args.optString(1).ifBlank { null })
            null
        }
        "saveBase64" -> {
            onSaveBase64(args.optString(0), args.optString(1).ifBlank { null })
            null
        }
        "getInboxItems" ->
            com.learngraph.mobile.data.ShareInbox.encode(
                com.learngraph.mobile.data.ShareInbox.list(context),
            )
        "clearInboxItem" -> {
            com.learngraph.mobile.data.ShareInbox.remove(context, args.optString(0))
            null
        }
        "clearInbox" -> {
            com.learngraph.mobile.data.ShareInbox.clear(context)
            null
        }
        "getInboxImageDataUrl" ->
            com.learngraph.mobile.data.ShareInbox.imageDataUrl(context, args.optString(0)) ?: ""
        "takePhoto" -> {
            onTakePhoto()
            null
        }
        "consumeShortcutAction" ->
            com.learngraph.mobile.util.ShortcutActions.consume(context) ?: ""
        "__reportToken" -> {
            val token = args.optString(0)
            val ws = args.optString(1).ifBlank { null }
            if (token.isNotBlank()) onReportToken(token, ws)
            null
        }
        "haptic" -> {
            com.learngraph.mobile.util.Haptics.haptic(context, args.optInt(0))
            null
        }
        "replyHaptic" -> {
            com.learngraph.mobile.util.Haptics.replyHaptic(context)
            null
        }
        "startReplyVibration" -> {
            com.learngraph.mobile.util.Haptics.startReplyVibration(context)
            null
        }
        "stopReplyVibration" -> {
            com.learngraph.mobile.util.Haptics.stopReplyVibration(context)
            null
        }
        "stepHaptic" -> {
            com.learngraph.mobile.util.Haptics.stepHaptic(context)
            null
        }
        "celebration" -> {
            com.learngraph.mobile.util.Haptics.celebration(context)
            null
        }
        "chime" -> {
            com.learngraph.mobile.util.Haptics.chime()
            null
        }
        "speak" -> {
            com.learngraph.mobile.util.TtsSynth.speak(context, args.optString(0))
            null
        }
        else -> null
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
 * 服务器不可达仍由 NavigationDelegate.onLoadError 处理（跳连接页）。
 * 所有回调统一 post 到主线程；注册后首次 onAvailable（当前已有网络）不触发 reload。
 */
private class ConnectivityMonitor(
    private val sessionProvider: () -> GeckoSession?,
    private val onOffline: () -> Unit,
    private val onOnline: (session: GeckoSession?) -> Unit,
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
            mainHandler.post { if (registered) onOnline(sessionProvider()) }
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
