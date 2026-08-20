package com.learngraph.mobile

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Message
import android.view.Gravity
import android.view.View
import android.webkit.ConsoleMessage
import android.webkit.GeolocationPermissions
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.Switch
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.getcapacitor.BridgeActivity

/**
 * LearnGraph 手机助手 —— 网页版包裹器。
 *
 * 主 WebView 承载网页版（全功能）；本 Activity 负责：
 *  - 左侧悬浮「手机助手」按钮：通知/震动设置、刷新、浏览器打开、切换服务器
 *  - 键盘弹出时输入区跟随（manifest adjustResize）
 *  - 启动/停止 NotificationService（后台轮询新回复 → 通知 + 震动）
 */
class MainActivity : BridgeActivity() {

    companion object {
        const val PREFS = "lg_mobile"
        const val KEY_NOTIFY = "notify_enabled"
        const val KEY_VIBRATE = "vibrate_enabled"
        const val KEY_INTERVAL = "poll_interval_seconds"
        /** Capacitor Preferences 默认存储组（store.ts 通过 @capacitor/preferences 写入） */
        const val CAPACITOR_PREFS = "CapacitorStorage"
        /** 服务器地址键（store.ts K.baseUrl） */
        const val KEY_BASE_URL = "lg.baseUrl"
        /** 本机助手页（连接配置/返回入口） */
        const val LOCAL_HOME = "https://localhost/index.html?from=webapp"

        @Volatile
        var appInForeground = true
            private set

        @Volatile
        var webViewRef: WebView? = null
            private set
    }

    private lateinit var prefs: SharedPreferences

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        webViewRef = bridge.getWebView()
        tuneWebView()
        installExternalLinkGuard()
        installControlPill()
        maybeRequestNotificationPermission()
        syncNotificationService()
    }

    override fun onResume() {
        super.onResume()
        appInForeground = true
    }

    override fun onPause() {
        super.onPause()
        appInForeground = false
    }

    // ------------------------------------------------------------------ //
    // WebView 调优
    // ------------------------------------------------------------------ //

    private fun tuneWebView() {
        val wv = bridge.getWebView() ?: return
        wv.post {
            wv.settings.mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
            wv.settings.setSupportZoom(false)
            wv.settings.textZoom = 100
            wv.overScrollMode = View.OVER_SCROLL_NEVER
        }
    }

    // ------------------------------------------------------------------ //
    // 外跳拦截：外部 http(s) 一律内嵌浏览器打开，绝不跳系统浏览器（仿 ChatGPT）
    // ------------------------------------------------------------------ //

    private fun installExternalLinkGuard() {
        val wv = bridge.getWebView() ?: return
        wv.post {
            val baseClient = wv.webViewClient
            val baseChrome = wv.webChromeClient

            wv.webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                    val url = request.url
                    if (url.scheme == "http" || url.scheme == "https") {
                        if (isSameServer(url)) return false // 属于配置的服务器：留在主 WebView
                        EmbeddedBrowserActivity.open(this@MainActivity, url.toString())
                        return true
                    }
                    return baseClient?.shouldOverrideUrlLoading(view, request) ?: false
                }

                @Deprecated("Deprecated in Java")
                override fun shouldOverrideUrlLoading(view: WebView, url: String?): Boolean {
                    val u = url ?: return false
                    val uri = Uri.parse(u)
                    if (uri.scheme == "http" || uri.scheme == "https") {
                        if (isSameServer(uri)) return false
                        EmbeddedBrowserActivity.open(this@MainActivity, u)
                        return true
                    }
                    return baseClient?.shouldOverrideUrlLoading(view, u) ?: false
                }

                override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                    baseClient?.shouldInterceptRequest(view, request) ?: super.shouldInterceptRequest(view, request)

                override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                    baseClient?.onPageStarted(view, url, favicon) ?: super.onPageStarted(view, url, favicon)
                }

                override fun onPageFinished(view: WebView, url: String?) {
                    baseClient?.onPageFinished(view, url) ?: super.onPageFinished(view, url)
                }

                override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                    baseClient?.onReceivedError(view, request, error) ?: super.onReceivedError(view, request, error)
                }

                override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, errorResponse: WebResourceResponse) {
                    baseClient?.onReceivedHttpError(view, request, errorResponse) ?: super.onReceivedHttpError(view, request, errorResponse)
                }

                override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean =
                    baseClient?.onRenderProcessGone(view, detail) ?: super.onRenderProcessGone(view, detail)
            }

            wv.webChromeClient = object : WebChromeClient() {
                // target="_blank" / window.open → 用临时 WebView 接住弹窗请求并捕获 URL，转内嵌浏览器
                override fun onCreateWindow(view: WebView, isDialog: Boolean, isUserGesture: Boolean, resultMsg: Message): Boolean {
                    val transport = resultMsg.obj as? WebView.WebViewTransport ?: run {
                        return baseChrome?.onCreateWindow(view, isDialog, isUserGesture, resultMsg) ?: false
                    }
                    val temp = WebView(this@MainActivity)
                    temp.webViewClient = object : WebViewClient() {
                        private var captured = false

                        private fun capture(url: String) {
                            if (captured) return
                            captured = true
                            if (url.startsWith("http://") || url.startsWith("https://")) {
                                EmbeddedBrowserActivity.open(this@MainActivity, url)
                            }
                            temp.post {
                                try {
                                    (temp.parent as? android.view.ViewGroup)?.removeView(temp)
                                    temp.destroy()
                                } catch (_: Exception) {
                                    // 忽略清理期异常
                                }
                            }
                        }

                        override fun shouldOverrideUrlLoading(v: WebView, request: WebResourceRequest): Boolean {
                            capture(request.url.toString())
                            return true
                        }

                        @Deprecated("Deprecated in Java")
                        override fun shouldOverrideUrlLoading(v: WebView, url: String?): Boolean {
                            capture(url ?: "")
                            return true
                        }

                        override fun onPageStarted(v: WebView, url: String?, favicon: Bitmap?) {
                            capture(url ?: "")
                        }
                    }
                    // 挂到视图树（1×1 不可见）确保弹窗加载被驱动，捕获后立即拆除
                    addContentView(temp, FrameLayout.LayoutParams(1, 1))
                    temp.visibility = View.GONE
                    transport.setWebView(temp)
                    resultMsg.sendToTarget()
                    return true
                }

                override fun onProgressChanged(view: WebView?, newProgress: Int) {
                    baseChrome?.onProgressChanged(view, newProgress) ?: super.onProgressChanged(view, newProgress)
                }

                override fun onJsAlert(view: WebView, url: String, message: String, result: JsResult): Boolean =
                    baseChrome?.onJsAlert(view, url, message, result) ?: super.onJsAlert(view, url, message, result)

                override fun onJsConfirm(view: WebView, url: String, message: String, result: JsResult): Boolean =
                    baseChrome?.onJsConfirm(view, url, message, result) ?: super.onJsConfirm(view, url, message, result)

                override fun onJsPrompt(view: WebView, url: String, message: String, defaultValue: String, result: JsPromptResult): Boolean =
                    baseChrome?.onJsPrompt(view, url, message, defaultValue, result) ?: super.onJsPrompt(view, url, message, defaultValue, result)

                override fun onPermissionRequest(request: PermissionRequest) {
                    baseChrome?.onPermissionRequest(request) ?: super.onPermissionRequest(request)
                }

                override fun onGeolocationPermissionsShowPrompt(origin: String, callback: GeolocationPermissions.Callback) {
                    baseChrome?.onGeolocationPermissionsShowPrompt(origin, callback) ?: super.onGeolocationPermissionsShowPrompt(origin, callback)
                }

                @Suppress("DEPRECATION")
                override fun onShowFileChooser(
                    webView: WebView,
                    filePathCallback: ValueCallback<Array<Uri>>,
                    fileChooserParams: WebChromeClient.FileChooserParams,
                ): Boolean =
                    baseChrome?.onShowFileChooser(webView, filePathCallback, fileChooserParams) ?: super.onShowFileChooser(webView, filePathCallback, fileChooserParams)

                override fun onShowCustomView(view: View, callback: WebChromeClient.CustomViewCallback) {
                    baseChrome?.onShowCustomView(view, callback) ?: super.onShowCustomView(view, callback)
                }

                override fun onHideCustomView() {
                    baseChrome?.onHideCustomView() ?: super.onHideCustomView()
                }

                override fun onConsoleMessage(consoleMessage: ConsoleMessage): Boolean =
                    baseChrome?.onConsoleMessage(consoleMessage) ?: super.onConsoleMessage(consoleMessage)
            }
        }
    }

    /** 读取用户配置的服务器地址（store.ts 通过 Capacitor Preferences 写入 CapacitorStorage） */
    private fun configuredServer(): Pair<String, Int>? {
        val base = getSharedPreferences(CAPACITOR_PREFS, Context.MODE_PRIVATE)
            .getString(KEY_BASE_URL, "")?.trim().orEmpty()
        if (base.isBlank()) return null
        val uri = Uri.parse(base)
        val host = uri.host?.lowercase() ?: return null
        return host to effectivePort(uri)
    }

    private fun effectivePort(uri: Uri): Int {
        val p = uri.port
        if (p != -1) return p
        return if (uri.scheme == "https") 443 else 80
    }

    /** http(s) 链接是否属于已配置服务器（留在主 WebView 内导航），否则进内嵌浏览器 */
    private fun isSameServer(url: Uri): Boolean {
        val scheme = url.scheme?.lowercase()
        if (scheme != "http" && scheme != "https") return false
        val server = configuredServer() ?: return false
        val host = url.host?.lowercase() ?: return false
        return host == server.first && effectivePort(url) == server.second
    }

    private fun currentRemoteUrl(): String? {
        val url = bridge.getWebView()?.url ?: return null
        if (url.startsWith("https://localhost") || url.startsWith("capacitor://") || url.startsWith("file:")) {
            return null
        }
        return url
    }

    // ------------------------------------------------------------------ //
    // 悬浮按钮 + 控制面板
    // ------------------------------------------------------------------ //

    private fun installControlPill() {
        val size = dp(38)
        val pill = TextView(this).apply {
            text = "⚙"
            textSize = 16f
            gravity = Gravity.CENTER
            setTextColor(Color.WHITE)
            background = GradientDrawable().apply {
                shape = GradientDrawable.RECTANGLE
                cornerRadius = size / 2f
                setColor(0xCC1F232A.toInt())
            }
            elevation = dp(6).toFloat()
            alpha = 0.88f
            contentDescription = "LearnGraph 手机助手"
            setOnClickListener { showControlDialog() }
        }
        val lp = FrameLayout.LayoutParams(size, size)
        lp.gravity = Gravity.LEFT or Gravity.CENTER_VERTICAL
        lp.leftMargin = dp(2)
        addContentView(pill, lp)
    }

    private fun showControlDialog() {
        val url = currentRemoteUrl()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(22), dp(10), dp(22), dp(6))
        }

        fun row(title: String, subtitle: String?, switchValue: Boolean, onChanged: (Boolean) -> Unit) {
            val sw = Switch(this).apply {
                isChecked = switchValue
                setOnCheckedChangeListener { _, checked -> onChanged(checked) }
            }
            val text = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
                addView(
                    TextView(this@MainActivity).apply {
                        text = title
                        textSize = 15f
                        setTextColor(Color.parseColor("#1F232A"))
                    },
                )
                if (subtitle != null) {
                    addView(
                        TextView(this@MainActivity).apply {
                            text = subtitle
                            textSize = 11f
                            setTextColor(Color.parseColor("#71757D"))
                        },
                    )
                }
            }
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = Gravity.CENTER_VERTICAL
                addView(text, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
                addView(sw)
            }
            root.addView(row, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT).apply { bottomMargin = dp(14) })
        }

        val notifyOn = prefs.getBoolean(KEY_NOTIFY, true)
        val vibrateOn = prefs.getBoolean(KEY_VIBRATE, true)

        row("新回复通知", "后台检测到新回复时推送系统通知", notifyOn) { checked ->
            prefs.edit().putBoolean(KEY_NOTIFY, checked).apply()
            if (checked) maybeRequestNotificationPermission()
            syncNotificationService()
            Toast.makeText(this, if (checked) "通知提醒已开启" else "通知提醒已关闭", Toast.LENGTH_SHORT).show()
        }
        row("震动提醒", "收到新回复时震动", vibrateOn) { checked ->
            prefs.edit().putBoolean(KEY_VIBRATE, checked).apply()
        }

        root.addView(actionButton("刷新页面") { bridge.getWebView()?.reload() })
        root.addView(actionButton("在浏览器打开") {
            val u = currentRemoteUrl()
            if (u != null) {
                startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(u)))
            } else {
                Toast.makeText(this, "当前不在网页版页面", Toast.LENGTH_SHORT).show()
            }
        })
        root.addView(actionButton("切换服务器") {
            bridge.getWebView()?.loadUrl(LOCAL_HOME)
        })

        val info = TextView(this).apply {
            text = buildString {
                append("服务器：${url ?: "未连接"}\n")
                append("通知轮询：${prefs.getInt(KEY_INTERVAL, 30)}s\n")
                append("版本：${packageManager.getPackageInfo(packageName, 0).versionName}")
            }
            textSize = 11f
            setTextColor(Color.parseColor("#71757D"))
            setPadding(0, dp(4), 0, 0)
        }
        root.addView(info)

        AlertDialog.Builder(this)
            .setTitle("LearnGraph 手机助手")
            .setView(root)
            .setPositiveButton("完成", null)
            .show()
    }

    private fun actionButton(title: String, onClick: () -> Unit): TextView =
        TextView(this).apply {
            text = title
            textSize = 15f
            setTextColor(Color.parseColor("#2F54D0"))
            typeface = Typeface.DEFAULT_BOLD
            setPadding(0, dp(12), 0, dp(12))
            setOnClickListener { onClick() }
        }

    // ------------------------------------------------------------------ //
    // 通知权限 + 服务
    // ------------------------------------------------------------------ //

    private fun maybeRequestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1001)
        }
    }

    private fun syncNotificationService() {
        val enabled = prefs.getBoolean(KEY_NOTIFY, true)
        val intent = Intent(this, NotificationService::class.java)
        if (enabled) {
            if (Build.VERSION.SDK_INT >= 26) startForegroundService(intent) else startService(intent)
        } else {
            stopService(intent)
        }
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
