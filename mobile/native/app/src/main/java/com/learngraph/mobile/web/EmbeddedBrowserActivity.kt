package com.learngraph.mobile.web

import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.view.Gravity
import android.view.View
import android.webkit.DownloadListener
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.learngraph.mobile.util.PermissionGate
import com.learngraph.mobile.util.WebMicPermissionBridge
import com.learngraph.mobile.util.parseTrustedOrigin

/**
 * 原生版内嵌浏览器（仿 ChatGPT）：
 *  - 打开外部链接 / 网页版兜底（URL 由 EXTRA_URL 传入）
 *  - 异形屏适配：工具条避开状态栏（含挖孔），WebView 底部避开导航栏（手势/三键）
 *  - 工具条：关闭 ✕ | 后退 ◀ | 前进 ▶ | URL（点按复制） | 刷新 ⟳ | 菜单 ⋯
 *
 * v0.16.0：补齐麦克风链路。此前本 Activity 的 WebChromeClient 没覆写
 * `onPermissionRequest`（WebView 默认拒绝），也没有任何运行时权限申请能力 ——
 * 走「网页版兜底」这条路时麦克风 100% 不可用。现在与主壳共用
 * WebMicPermissionBridge + PermissionGate。
 */
class EmbeddedBrowserActivity : AppCompatActivity() {

    companion object {
        const val EXTRA_URL = "url"

        fun open(context: Context, url: String) {
            if (url.isBlank()) return
            val intent = Intent(context, EmbeddedBrowserActivity::class.java)
            intent.putExtra(EXTRA_URL, url)
            context.startActivity(intent)
        }
    }

    private lateinit var webView: WebView
    private lateinit var urlText: TextView
    private lateinit var btnBack: TextView
    private lateinit var btnForward: TextView
    private lateinit var progressBar: ProgressBar

    private var micBridge: WebMicPermissionBridge? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val url = intent.getStringExtra(EXTRA_URL)
        if (url.isNullOrBlank()) {
            finish()
            return
        }

        // 权限闸门必须在 onStart 之前注册
        PermissionGate.install(this)
        // 只信任本页地址：内嵌浏览器可能停在任意站点，不能让别的站点要麦克风
        micBridge = WebMicPermissionBridge(
            activity = { this },
            trustedOrigins = { listOfNotNull(parseTrustedOrigin(webView.url), parseTrustedOrigin(url)) },
        )

        webView = WebView(this)
        tuneWebView()

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.WHITE)
        }
        root.addView(buildToolbar())
        root.addView(webView, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)

        // 异形屏：WebView 底部避让导航栏
        webView.setOnApplyWindowInsetsListener { v, insets ->
            val bottom = insets.getSystemWindowInsetBottom()
            v.setPadding(0, 0, 0, bottom)
            insets
        }

        webView.loadUrl(url)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun tuneWebView() {
        val s = webView.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true
        s.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        s.textZoom = 100
        s.setSupportZoom(true)
        s.builtInZoomControls = true
        s.displayZoomControls = false
        s.loadWithOverviewMode = true
        s.useWideViewPort = true
        // WebRTC：远端音频走 <audio autoplay>，默认手势门禁会把它挡掉
        s.mediaPlaybackRequiresUserGesture = false
        webView.overScrollMode = View.OVER_SCROLL_NEVER

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean =
                handleExternal(request.url)

            @Deprecated("Deprecated in Java")
            override fun shouldOverrideUrlLoading(view: WebView, url: String?): Boolean {
                val u = url ?: return false
                return handleExternal(Uri.parse(u))
            }

            override fun onPageStarted(view: WebView, url: String?, favicon: android.graphics.Bitmap?) {
                progressBar.visibility = View.VISIBLE
                updateUrlText(url ?: "")
            }

            override fun onPageFinished(view: WebView, url: String?) {
                progressBar.visibility = View.GONE
                updateUrlText(url ?: "")
                updateNavState()
            }
        }

        webView.webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView?, newProgress: Int) {
                progressBar.progress = newProgress
            }

            // 麦克风（语音/听写）：不能直接 grant —— 必须先拿到系统录音权限，
            // 否则 chromium 建不出音频输入流，网页侧只会得到 "Permission denied"。
            override fun onPermissionRequest(request: PermissionRequest) {
                micBridge?.onPermissionRequest(request) ?: runCatching { request.deny() }
            }

            override fun onPermissionRequestCanceled(request: PermissionRequest) {
                micBridge?.onPermissionRequestCanceled(request)
            }
        }

        webView.setDownloadListener(DownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            try {
                val request = DownloadManager.Request(Uri.parse(url))
                request.setMimeType(mimeType)
                request.addRequestHeader("User-Agent", userAgent)
                request.setDescription("LearnGraph 正在下载文件")
                val filename = URLUtil.guessFileName(url, contentDisposition, mimeType)
                request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename)
                request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
                Toast.makeText(this, "已开始下载：$filename", Toast.LENGTH_SHORT).show()
            } catch (e: Exception) {
                Toast.makeText(this, "下载失败", Toast.LENGTH_SHORT).show()
            }
        })
    }

    /** 页面内 http(s) 继续导航；mailto:/tel: 等交给系统应用 */
    private fun handleExternal(uri: Uri): Boolean {
        val scheme = uri.scheme?.lowercase()
        if (scheme == "http" || scheme == "https") return false
        if (scheme == "mailto" || scheme == "tel" || scheme == "sms" || scheme == "intent" || scheme == "market") {
            try {
                startActivity(Intent(Intent.ACTION_VIEW, uri))
            } catch (e: ActivityNotFoundException) {
                Toast.makeText(this, "没有可处理该链接的应用", Toast.LENGTH_SHORT).show()
            }
            return true
        }
        return false
    }

    // ------------------------------------------------------------------ //
    // 工具条（状态栏安全区适配：挖孔屏下工具条下移，不遮挡）
    // ------------------------------------------------------------------ //

    private fun buildToolbar(): View {
        val statusBarHeight = if (Build.VERSION.SDK_INT >= 21) {
            val res = resources.getIdentifier("status_bar_height", "dimen", "android")
            if (res > 0) resources.getDimensionPixelSize(res) else dp(24)
        } else {
            dp(24)
        }

        val bar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
            setBackgroundColor(0xFF1F232A.toInt())
            setPadding(dp(2), statusBarHeight + dp(4), dp(2), dp(6))
        }

        fun toolButton(symbol: String, desc: String, onClick: () -> Unit): TextView =
            TextView(this).apply {
                text = symbol
                textSize = 17f
                gravity = Gravity.CENTER
                setTextColor(Color.WHITE)
                contentDescription = desc
                setPadding(dp(10), dp(6), dp(10), dp(6))
                setOnClickListener { onClick() }
            }

        btnBack = toolButton("◀", "后退") { if (webView.canGoBack()) webView.goBack() }
        btnForward = toolButton("▶", "前进") { if (webView.canGoForward()) webView.goForward() }

        urlText = TextView(this).apply {
            textSize = 13f
            maxLines = 1
            ellipsize = android.text.TextUtils.TruncateAt.START
            gravity = Gravity.CENTER_VERTICAL
            setTextColor(0xFFB8BDC7.toInt())
            setPadding(dp(8), 0, dp(8), 0)
            contentDescription = "当前地址，点按复制"
            setOnClickListener {
                (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                    .setPrimaryClip(ClipData.newPlainText("URL", text.toString()))
                Toast.makeText(this@EmbeddedBrowserActivity, "链接已复制", Toast.LENGTH_SHORT).show()
            }
        }

        bar.addView(toolButton("✕", "关闭", { finish() }))
        bar.addView(btnBack)
        bar.addView(btnForward)
        bar.addView(urlText, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
        bar.addView(toolButton("⟳", "刷新", { webView.reload() }))
        bar.addView(toolButton("⋯", "更多", { showMenu() }))

        progressBar = ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal).apply {
            max = 100
            progress = 0
            visibility = View.GONE
        }
        return LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(bar, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT))
            addView(progressBar, FrameLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(2)))
        }
    }

    private fun updateUrlText(url: String) {
        urlText.text = url
        updateNavState()
    }

    private fun updateNavState() {
        btnBack.alpha = if (webView.canGoBack()) 1f else 0.35f
        btnForward.alpha = if (webView.canGoForward()) 1f else 0.35f
    }

    private fun showMenu() {
        val items = arrayOf("后退", "前进", "刷新", "在系统浏览器打开", "复制链接")
        AlertDialog.Builder(this)
            .setTitle("浏览器菜单")
            .setItems(items) { _, which ->
                when (which) {
                    0 -> if (webView.canGoBack()) webView.goBack()
                    1 -> if (webView.canGoForward()) webView.goForward()
                    2 -> webView.reload()
                    3 -> {
                        val u = webView.url ?: return@setItems
                        try {
                            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(u)))
                        } catch (e: ActivityNotFoundException) {
                            Toast.makeText(this, "没有可打开该链接的浏览器", Toast.LENGTH_SHORT).show()
                        }
                    }
                    4 -> {
                        val u = webView.url ?: return@setItems
                        (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                            .setPrimaryClip(ClipData.newPlainText("URL", u))
                        Toast.makeText(this, "链接已复制", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .show()
    }

    override fun onResume() {
        super.onResume()
        // 权限申请路由切回本页（主壳可能刚在本页之下请求过权限）
        PermissionGate.activate(this)
    }

    override fun onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        webView.destroy()
        super.onDestroy()
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
