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
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.activity.compose.LocalOnBackPressedDispatcherOwner
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
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
import com.learngraph.mobile.web.EmbeddedBrowserActivity
import kotlinx.coroutines.launch

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
fun WebAppScreen(onOpenDownloads: () -> Unit) {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp
    val authState by app.authStore.state.collectAsState(initial = AuthStore.AuthState())

    val webViewRef = remember { mutableStateOf<WebView?>(null) }
    val downloadTasks by DownloadStore.tasks.collectAsState()
    val activeDownloads = downloadTasks.count { it.status == DownloadStatus.DOWNLOADING }
    val scope = rememberCoroutineScope()

    val backDispatcher = LocalOnBackPressedDispatcherOwner.current?.onBackPressedDispatcher

    // 系统返回键：优先 WebView 后退，无历史时交由系统（退出应用）
    BackHandler {
        val wv = webViewRef.value
        if (wv != null && wv.canGoBack()) {
            wv.goBack()
        } else {
            backDispatcher?.onBackPressed()
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

            // 全屏网页（imePadding：键盘弹出时 WebView 整体抬升，输入框不被键盘遮挡）
            Box(
                modifier = Modifier
                    .fillMaxSize()
                    .imePadding(),
            ) {
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

                            webViewClient = object : WebViewClient() {
                                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                                    val url = request.url
                                    val scheme = url.scheme?.lowercase()
                                    if (scheme == "http" || scheme == "https") {
                                        // 非本服务器主机 → 内嵌浏览器（不离开应用）
                                        if (!isSameServer(url, app.api.baseUrl)) {
                                            EmbeddedBrowserActivity.open(ctx, url.toString())
                                            return true
                                        }
                                        return false
                                    }
                                    return false
                                }

                                override fun onPageFinished(view: WebView, url: String?) {
                                    super.onPageFinished(view, url)
                                    syncLoginState(view)
                                    // Cookie 强制写盘：登录态持久化（重启免登录）
                                    CookieManager.getInstance().flush()
                                }

                                /**
                                 * 登录态同步（免登录核心）：
                                 *  - 网页版未登录（localStorage 无 token）→ 注入原生 token + reload（自愈）
                                 *  - 网页版已登录且 token 与原生一致 → 无事
                                 *  - 网页版 token 不同（用户在网页版内重新登录）→ 回写 DataStore，持续免登录
                                 *  reload 后再次 onPageFinished：token 已一致 → 停止，无循环。
                                 */
                                private fun syncLoginState(view: WebView) {
                                    val token = authState.token
                                    if (token.isNullOrBlank()) return
                                    val js = buildString {
                                        append("(function(){")
                                        append("var cur=localStorage.getItem('learngraph.access_token')||'';")
                                        append("var want='")
                                        append(escapeJs(token))
                                        append("';")
                                        append("if(!cur){")
                                        append("localStorage.setItem('learngraph.access_token',want);")
                                        append("localStorage.setItem('learngraph.workspace_id','")
                                        append(escapeJs(authState.workspaceId.orEmpty()))
                                        append("');")
                                        append("localStorage.setItem('learngraph.device_id','")
                                        append(escapeJs(authState.deviceId))
                                        append("');")
                                        append("return 'injected';")
                                        append("}")
                                        append("if(cur!==want){return 'token:'+cur;}")
                                        append("return 'ok';")
                                        append("})()")
                                    }
                                    view.evaluateJavascript(js) { result ->
                                        val trimmed = result?.trim()?.trim('"') ?: return@evaluateJavascript
                                        when {
                                            trimmed == "injected" -> view.post { view.reload() }
                                            trimmed.startsWith("token:") -> {
                                                val newToken = trimmed.removePrefix("token:")
                                                if (newToken.isNotBlank()) {
                                                    scope.launch { app.authStore.updateToken(newToken) }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // 内置下载器：拦截网页下载
                            setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
                                requestNotifPermissionIfNeeded(ctx)
                                val token = if (isSameServer(Uri.parse(url), app.api.baseUrl)) authState.token else null
                                DownloadStore.enqueue(
                                    context = ctx,
                                    url = url,
                                    contentDisposition = contentDisposition,
                                    mimeType = mimeType,
                                    userAgent = userAgent,
                                    authToken = token,
                                )
                            }

                            webViewRef.value = this
                            loadUrl(app.api.baseUrl)
                        }
                    },
                    update = { },
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
