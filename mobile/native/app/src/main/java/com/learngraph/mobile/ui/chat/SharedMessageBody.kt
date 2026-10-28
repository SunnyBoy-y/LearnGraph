package com.learngraph.mobile.ui.chat

import android.annotation.SuppressLint
import android.graphics.Color as AndroidColor
import android.net.Uri
import android.view.ViewGroup
import android.widget.FrameLayout
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.imePadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalView
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.DialogWindowProvider
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.data.DownloadStore
import com.learngraph.mobile.data.MessagePart
import com.learngraph.mobile.data.NativeCacheStore
import com.learngraph.mobile.ui.components.webDrawerSwipeExcluded
import com.learngraph.mobile.ui.components.LocalWebDrawerState
import com.learngraph.mobile.ui.components.LocalWebGraphDrawerState
import com.learngraph.mobile.ui.web.NativeRendererPolicy
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import org.json.JSONObject
import java.io.ByteArrayInputStream

/** State from the existing native SSE/history chain, rendered by Web's ThinkingChain. */
data class NativeThinkingChainInfo(
    val messageStatus: String,
    val startedAt: String? = null,
    val finalAnswerStarted: Boolean = false,
    val thinkingDurationMs: Long? = null,
)

data class NativeSuggestedPromptsInfo(
    val anchorMessageId: String,
    val anchorSessionId: String,
    val anchorVersion: Int,
    val completedMessageFingerprint: String,
    val providerId: String? = null,
    val modelId: String? = null,
)

/** Native scrolling/composer, with the *same* AnswerParts/Streamdown/registered components as Web. */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun SharedMessageBody(
    messageId: String,
    sessionId: String,
    parts: List<MessagePart>,
    siblingParts: List<MessagePart>,
    streaming: Boolean,
    onSendAction: (String) -> Unit,
    onRefresh: () -> Unit,
    onNavigate: (String) -> Unit,
    onOpenWeb: () -> Unit,
    surface: String = "message",
    fullViewport: Boolean = false,
    showStreamIndicator: Boolean = false,
    providerTrace: kotlinx.serialization.json.JsonObject? = null,
    thinkingChain: NativeThinkingChainInfo? = null,
    suggestions: NativeSuggestedPromptsInfo? = null,
) {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp
    val scope = rememberCoroutineScope()
    val drawer = LocalWebDrawerState.current
    val graphDrawer = LocalWebGraphDrawerState.current
    val server = app.api.baseUrl
    val token = app.api.token
    val workspace = app.api.workspaceId.orEmpty()
    val device = app.api.deviceId
    val origin = remember(server) { NativeRendererPolicy.origin(server) }
    val dark = MaterialTheme.colorScheme.background != androidx.compose.ui.graphics.Color.White
    val thinkingDefaultOpen = if (thinkingChain != null) {
        val privateScope = NativeCacheStore.scope(server, workspace, app.api.accountId, token)
        val preference = remember(app, privateScope) { app.appearanceStore.observeThinkingChainDefaultOpen(privateScope) }
        val value by preference.collectAsState()
        value
    } else true
    val callbacks by rememberUpdatedState(Triple(onSendAction, onRefresh, onNavigate))
    var height by remember(messageId) { mutableFloatStateOf(if (surface == "suggestions") 1f else 48f) }
    var error by remember(messageId) { mutableStateOf<String?>(null) }
    var ready by remember(messageId) { mutableStateOf(false) }
    var webView by remember { mutableStateOf<WebView?>(null) }
    var inlineHost by remember { mutableStateOf<FrameLayout?>(null) }
    var overlay by remember { mutableStateOf(false) }
    val json = remember { Json { encodeDefaults = true; explicitNulls = false } }
    val payload = remember(messageId, sessionId, parts, siblingParts, streaming, dark, workspace, surface, showStreamIndicator, providerTrace, thinkingChain, thinkingDefaultOpen, suggestions) {
        JSONObject().apply {
            put("messageId", messageId); put("sessionId", sessionId); put("workspaceId", workspace)
            put("parts", org.json.JSONArray(json.encodeToString(kotlinx.serialization.builtins.ListSerializer(MessagePart.serializer()), parts)))
            put("siblingParts", org.json.JSONArray(json.encodeToString(kotlinx.serialization.builtins.ListSerializer(MessagePart.serializer()), siblingParts)))
            put("streaming", streaming); put("dark", dark)
            put("surface", surface)
            put("showStreamIndicator", showStreamIndicator)
            put("providerTrace", providerTrace?.let { JSONObject(it.toString()) } ?: JSONObject())
            suggestions?.let { info -> put("suggestions", JSONObject().apply {
                put("anchorMessageId", info.anchorMessageId); put("anchorSessionId", info.anchorSessionId)
                put("anchorVersion", info.anchorVersion); put("completedMessageFingerprint", info.completedMessageFingerprint)
                info.providerId?.let { put("providerId", it) }; info.modelId?.let { put("modelId", it) }
            }) }
            thinkingChain?.let { chain ->
                put("thinkingChain", JSONObject().apply {
                    put("messageStatus", chain.messageStatus)
                    chain.startedAt?.let { put("startedAt", it) }
                    put("finalAnswerStarted", chain.finalAnswerStarted)
                    chain.thinkingDurationMs?.let { put("thinkingDurationMs", it) }
                    put("defaultOpen", thinkingDefaultOpen)
                })
            }
        }.toString()
    }
    val currentPayload by rememberUpdatedState(payload)
    val supported = remember { WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER) }
    if (!supported || origin == null) {
        Column {
            Text("系统 WebView 暂不能显示此内容", style = MaterialTheme.typography.bodySmall)
            TextButton(onClick = onOpenWeb) { Text("打开完整会话") }
        }
        return
    }

    val bodySize = if (fullViewport) Modifier.fillMaxSize() else Modifier.fillMaxWidth().height(height.dp)
    Box(bodySize.webDrawerSwipeExcluded()) {
        key(server, token, workspace, messageId) {
            AndroidView(
                modifier = bodySize,
                factory = { ctx ->
                    val host = FrameLayout(ctx)
                    inlineHost = host
                    val renderer = WebView(ctx).apply {
                        webView = this
                        layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
                        setBackgroundColor(AndroidColor.TRANSPARENT)
                        settings.javaScriptEnabled = true
                        settings.domStorageEnabled = true
                        settings.allowFileAccess = false
                        settings.allowContentAccess = false
                        settings.useWideViewPort = true
                        settings.loadWithOverviewMode = false
                        settings.textZoom = 100
                        settings.setSupportZoom(false)
                        settings.cacheMode = WebSettings.LOAD_DEFAULT
                        // Assets are local, while file tickets, API calls and sandbox runtimes use the selected server.
                        settings.mixedContentMode = WebSettings.MIXED_CONTENT_COMPATIBILITY_MODE
                        isVerticalScrollBarEnabled = false
                        overScrollMode = android.view.View.OVER_SCROLL_NEVER
                        WebViewCompat.addWebMessageListener(this, "LearnGraphRendererChannel", setOf(origin)) { view, message, sourceOrigin, isMainFrame, _ ->
                            // Opaque sandbox frames cannot send native actions; even trusted subframes are excluded.
                            if (!isMainFrame || !NativeRendererPolicy.sameOrigin(sourceOrigin.toString(), server)) return@addWebMessageListener
                            if (app.api.token != token || app.api.workspaceId != workspace || app.api.baseUrl != server) return@addWebMessageListener
                            val raw = message.data ?: return@addWebMessageListener
                            // Match downloadViaNative's 20 MiB Blob cap, including base64 expansion.
                            if (raw.length > 29_000_000) return@addWebMessageListener
                            val event = runCatching { JSONObject(raw) }.getOrNull() ?: return@addWebMessageListener
                            if (raw.length > 2_000_000 && event.optString("type") != "saveBase64") return@addWebMessageListener
                            when (event.optString("type")) {
                                "ready" -> { ready = true; error = null; view.evaluateJavascript("window.__lgRender(${NativeRendererPolicy.scriptJson(currentPayload)})", null) }
                                "resize" -> {
                                    val value = event.optDouble("height", 0.0)
                                    if (!fullViewport && !overlay && value.isFinite() && value in 1.0..100_000.0) height = value.toFloat()
                                }
                                "overlay" -> overlay = event.optBoolean("open")
                                "drawer" -> if (!overlay) {
                                    val gesture = event.optJSONObject("gesture")
                                    val targetDrawer = if (gesture?.optString("side") == "right") graphDrawer else drawer
                                    val dx = gesture?.optDouble("dx", 0.0) ?: 0.0
                                    if (dx.isFinite() && kotlin.math.abs(dx) <= 10_000) when (gesture?.optString("phase")) {
                                        "start" -> targetDrawer?.beginRendererDrag()
                                        "move" -> targetDrawer?.progressRendererDrag(dx.toFloat())
                                        "end", "cancel" -> scope.launch { targetDrawer?.finishRendererDrag(gesture.optString("phase") == "cancel") }
                                    }
                                }
                                "send" -> event.optString("payload").takeIf { it.length <= 80_000 }?.let(callbacks.first)
                                "refresh" -> callbacks.second()
                                "navigate" -> callbacks.third(event.optString("path"))
                                "failed" -> { error = event.optString("message").take(300); height = maxOf(height, 80f) }
                                "clearAuth" -> scope.launch { if (app.api.token == token) app.authStore.clearAuth() }
                                "download" -> {
                                    val url = runCatching { java.net.URI(server.trimEnd('/') + "/").resolve(event.optString("url")).toString() }.getOrNull().orEmpty()
                                    if (Uri.parse(url).scheme in listOf("http", "https")) {
                                        val same = NativeRendererPolicy.sameOrigin(url, server)
                                        DownloadStore.enqueue(ctx, url, authToken = if (same) token else null,
                                            workspaceId = if (same) workspace else null, deviceId = if (same) device else "",
                                            fileNameOverride = event.optString("fileName").takeIf { it.isNotBlank() })
                                    }
                                }
                                "saveBase64" -> {
                                    val data = event.optString("dataUrl")
                                    scope.launch { runCatching { DownloadStore.saveBase64(ctx.applicationContext, data, event.optString("fileName").takeIf { it.isNotBlank() }) } }
                                }
                            }
                        }
                        webViewClient = object : WebViewClient() {
                            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                                val asset = NativeRendererPolicy.assetPath(request.url.toString(), server) ?: return null
                                val mime = when (asset.substringAfterLast('.').lowercase()) {
                                    "js", "mjs" -> "text/javascript"; "css" -> "text/css"; "html" -> "text/html"
                                    "woff2" -> "font/woff2"; "woff" -> "font/woff"; "ttf" -> "font/ttf"
                                    "wasm" -> "application/wasm"; "svg" -> "image/svg+xml"; "png" -> "image/png"
                                    "json" -> "application/json"; else -> "application/octet-stream"
                                }
                                return runCatching { WebResourceResponse(mime, "UTF-8", ctx.assets.open(asset)) }.getOrElse {
                                    WebResourceResponse("text/plain", "UTF-8", 404, "Not Found", emptyMap(), ByteArrayInputStream(byteArrayOf()))
                                }
                            }
                            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                                if (!request.isForMainFrame) return false
                                callbacks.third(request.url.toString())
                                return true
                            }
                        }
                        val auth = JSONObject().apply {
                            put("learngraph.access_token", token ?: JSONObject.NULL)
                            put("learngraph.workspace_id", workspace)
                            put("learngraph.device_id", device)
                            put("learngraph.user_id", app.api.accountId ?: JSONObject.NULL)
                            listOf("username", "display_name", "session_id", "avatar_url", "must_change_password").forEach { put("learngraph.$it", JSONObject.NULL) }
                        }
                        val bootstrap = """<script>(function(){
                          var a=${NativeRendererPolicy.scriptJson(auth.toString())};Object.keys(a).forEach(function(k){[sessionStorage,localStorage].forEach(function(s){if(a[k]===null)s.removeItem(k);else s.setItem(k,a[k]);});});
                          function post(type,data){LearnGraphRendererChannel.postMessage(JSON.stringify(Object.assign({type:type},data||{})));}
                          window.LearnGraphRenderer={ready:function(){post('ready')},resize:function(h){post('resize',{height:h})},overlay:function(o){post('overlay',{open:o})},drawer:function(g){post('drawer',{gesture:g})},send:function(p){post('send',{payload:p})},refresh:function(){post('refresh')},navigate:function(p){post('navigate',{path:p})},failed:function(m){post('failed',{message:m})},download:function(u,n){post('download',{url:u,fileName:n})},saveBase64:function(d,n){post('saveBase64',{dataUrl:d,fileName:n})},clearAuth:function(){post('clearAuth')}};
                          window.LearnGraphNative={download:LearnGraphRenderer.download,saveBase64:LearnGraphRenderer.saveBase64,clearAuth:LearnGraphRenderer.clearAuth};
                          window.__lgNativeInitial=${NativeRendererPolicy.scriptJson(currentPayload)};
                        })();</script>"""
                        runCatching { ctx.assets.open("renderer/native-renderer.html").bufferedReader().use { it.readText() } }.fold(
                            onSuccess = { html -> loadDataWithBaseURL("$origin${NativeRendererPolicy.PREFIX}native-renderer.html", html.replace("<!--native-bootstrap-->", bootstrap), "text/html", "UTF-8", null) },
                            onFailure = { error = "共享渲染资源未打包，请使用完整会话"; height = 80f },
                        )
                    }
                    host.addView(renderer)
                    host
                },
                onRelease = { host ->
                    val view = webView
                    ready = false; overlay = false; webView = null; inlineHost = null
                    host.removeAllViews()
                    if (view != null) {
                        (view.parent as? ViewGroup)?.removeView(view)
                        view.stopLoading(); view.removeAllViews(); WebViewCompat.removeWebMessageListener(view, "LearnGraphRendererChannel"); view.destroy()
                    }
                },
            )
        }
    }
    LaunchedEffect(payload, ready) {
        if (ready) webView?.evaluateJavascript("window.__lgRender&&window.__lgRender(${NativeRendererPolicy.scriptJson(payload)})", null)
    }
    // Radix dialogs/lightboxes need the phone viewport, rather than a short message's height.
    // Move the same WebView; mounting another instance would lose the active form/selection.
    if (overlay && webView != null) Dialog(
        onDismissRequest = { webView?.evaluateJavascript("window.__lgDismissOverlay&&window.__lgDismissOverlay()", null) },
        properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false),
    ) {
        val dialogView = LocalView.current
        SideEffect {
            (dialogView.parent as? DialogWindowProvider)?.window?.let { window ->
                window.clearFlags(android.view.WindowManager.LayoutParams.FLAG_DIM_BEHIND)
                // The retained WebView already plays Radix/Streamdown motion.
                // An Android dialog-window fade would multiply its opacity again.
                if (window.attributes.windowAnimations != 0) window.setWindowAnimations(0)
            }
        }
        AndroidView(
            modifier = Modifier.fillMaxSize().statusBarsPadding().navigationBarsPadding().imePadding(),
            factory = { ctx -> FrameLayout(ctx).apply {
                webView?.let { view -> (view.parent as? ViewGroup)?.removeView(view); addView(view) }
            } },
            onRelease = { fullscreen ->
                val view = webView
                fullscreen.removeAllViews()
                if (view != null && inlineHost != null) {
                    (view.parent as? ViewGroup)?.removeView(view)
                    inlineHost?.addView(view)
                    view.evaluateJavascript("window.dispatchEvent(new Event('resize'))", null)
                }
            },
        )
    }
    if (error != null) Column {
        Text(error.orEmpty(), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error)
        TextButton(onClick = onOpenWeb) { Text("打开完整会话") }
    }
}

/** Graph inspector content is the original ContextRail, hosted in the native right drawer. */
@Composable
fun SharedGraphPanel(
    sessionId: String,
    onNavigate: (String) -> Unit,
    onSendAction: (String) -> Unit,
    onRefresh: () -> Unit,
    onOpenWeb: () -> Unit = {},
) {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp
    SharedMessageBody(
        messageId = "graph:$sessionId", sessionId = sessionId,
        parts = emptyList(), siblingParts = emptyList(), streaming = false,
        onSendAction = onSendAction, onRefresh = onRefresh,
        onNavigate = { url ->
            val feature = NativeRendererPolicy.workspaceDestination(url, app.api.baseUrl, app.api.workspaceId.orEmpty())
            if (feature != null) onNavigate(feature)
            else if (Uri.parse(url).scheme in listOf("http", "https")) com.learngraph.mobile.web.EmbeddedBrowserActivity.open(context, url)
        },
        onOpenWeb = onOpenWeb, surface = "graph", fullViewport = true,
    )
}
