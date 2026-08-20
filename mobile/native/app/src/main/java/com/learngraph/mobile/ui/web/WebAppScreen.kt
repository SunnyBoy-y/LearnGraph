package com.learngraph.mobile.ui.web

import android.annotation.SuppressLint
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.view.ViewGroup
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.web.EmbeddedBrowserActivity

/**
 * 内嵌全套网页版（仿 ChatGPT 内嵌体验）：
 *  - 全屏 WebView 加载服务器网页版（图谱/沙箱/记忆/设置/Provider/MCP/Skills 等全部功能）
 *  - 登录态注入：onPageFinished 后写入网页版 localStorage（learngraph.*）并 reload 一次
 *  - 原生顶栏：返回 / 标题 / 刷新 / 菜单（在浏览器打开、复制链接）
 *  - 异形屏：顶栏 statusBars 安全区，内容全屏（网页版 viewport-fit=cover 自适配）
 *  - 外链（非本服务器 host）→ 内嵌浏览器，绝不跳系统浏览器
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun WebAppScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp
    val authState by app.authStore.state.collectAsState(initial = AuthStore.AuthState())

    var menuOpen by remember { mutableStateOf(false) }
    val webViewRef = remember { mutableStateOf<WebView?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(Color.White),
    ) {
        // 原生顶栏（异形屏：statusBars 安全区避让）
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .windowInsetsPadding(WindowInsets.statusBars)
                .height(48.dp)
                .background(Color(0xFF1F232A)),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) {
                Icon(
                    Icons.AutoMirrored.Filled.ArrowBack,
                    contentDescription = "返回",
                    tint = Color.White,
                )
            }
            Text(
                text = "全部功能",
                color = Color.White,
                style = MaterialTheme.typography.titleMedium,
                modifier = Modifier.weight(1f),
            )
            IconButton(onClick = { webViewRef.value?.reload() }) {
                Icon(Icons.Default.Refresh, contentDescription = "刷新", tint = Color.White)
            }
            IconButton(onClick = { menuOpen = true }) {
                Icon(Icons.Default.MoreVert, contentDescription = "菜单", tint = Color.White)
            }
            DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                DropdownMenuItem(
                    text = { Text("在浏览器打开") },
                    onClick = {
                        menuOpen = false
                        val url = webViewRef.value?.url ?: app.api.baseUrl
                        try {
                            context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
                        } catch (e: ActivityNotFoundException) {
                            Toast.makeText(context, "没有可打开该链接的浏览器", Toast.LENGTH_SHORT).show()
                        }
                    },
                )
                DropdownMenuItem(
                    text = { Text("复制链接") },
                    onClick = {
                        menuOpen = false
                        val url = webViewRef.value?.url ?: app.api.baseUrl
                        (context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                            .setPrimaryClip(ClipData.newPlainText("URL", url))
                        Toast.makeText(context, "链接已复制", Toast.LENGTH_SHORT).show()
                    },
                )
            }
        }

        // 网页版 WebView（全屏，无浏览器痕迹）
        Box(
            modifier = Modifier
                .fillMaxSize()
                .windowInsetsPadding(WindowInsets.navigationBars),
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
                        settings.loadWithOverviewMode = true
                        settings.useWideViewPort = true
                        overScrollMode = android.view.View.OVER_SCROLL_NEVER

                        webViewClient = object : WebViewClient() {
                            private var injected = false

                            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                                val url = request.url
                                val scheme = url.scheme?.lowercase()
                                if (scheme == "http" || scheme == "https") {
                                    // 非本服务器主机 → 内嵌浏览器（仿 ChatGPT）
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
                                // 登录态注入（仅一次）：网页版 localStorage 键与 auth-store 对齐
                                if (!injected && !authState.token.isNullOrBlank()) {
                                    injected = true
                                    val js = buildString {
                                        append("localStorage.setItem('learngraph.access_token', '")
                                        append(escapeJs(authState.token.orEmpty()))
                                        append("');")
                                        append("localStorage.setItem('learngraph.workspace_id', '")
                                        append(escapeJs(authState.workspaceId.orEmpty()))
                                        append("');")
                                        append("localStorage.setItem('learngraph.device_id', '")
                                        append(escapeJs(authState.deviceId))
                                        append("');")
                                    }
                                    view.evaluateJavascript(js, null)
                                    // 注入后 reload 一次让 SPA 启动时读到登录态
                                    view.post { view.reload() }
                                }
                            }
                        }
                        webViewRef.value = this
                        loadUrl(app.api.baseUrl)
                    }
                },
                update = { },
            )
        }
    }
}

private fun isSameServer(url: Uri, baseUrl: String): Boolean {
    val base = Uri.parse(baseUrl.trimEnd('/'))
    val host = url.host?.lowercase() ?: return false
    val baseHost = base.host?.lowercase() ?: return false
    if (host != baseHost) return false
    val port = url.port
    val basePort = base.port
    val effectivePort = { p: Int, scheme: String? -> if (p != -1) p else if (scheme == "https") 443 else 80 }
    return effectivePort(port, url.scheme) == effectivePort(basePort, base.scheme)
}

private fun escapeJs(s: String): String =
    s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
