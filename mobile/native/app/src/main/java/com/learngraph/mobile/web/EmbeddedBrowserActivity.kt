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
import android.webkit.MimeTypeMap
import android.webkit.URLUtil
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import com.learngraph.mobile.ui.web.GeckoRuntimeHolder
import org.mozilla.geckoview.AllowOrDeny
import org.mozilla.geckoview.GeckoResult
import org.mozilla.geckoview.GeckoSession
import org.mozilla.geckoview.GeckoView
import org.mozilla.geckoview.WebResponse

/**
 * 原生版内嵌浏览器（仿 ChatGPT，v0.14.0 内核换成内嵌 GeckoView）：
 *  - 打开外部链接 / 网页版兜底（URL 由 EXTRA_URL 传入）
 *  - 异形屏适配：工具条避开状态栏（含挖孔），页面底部避开导航栏（手势/三键）
 *  - 工具条：关闭 ✕ | 后退 ◀ | 前进 ▶ | URL（点按复制） | 刷新 ⟳ | 菜单 ⋯
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

    private lateinit var session: GeckoSession
    private lateinit var urlText: TextView
    private lateinit var btnBack: TextView
    private lateinit var btnForward: TextView
    private lateinit var progressBar: ProgressBar

    // GeckoView 139 无同步 canGoBack/canGoForward，用回调维护
    private var canGoBackState = false
    private var canGoForwardState = false
    private var currentUrl: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val url = intent.getStringExtra(EXTRA_URL)
        if (url.isNullOrBlank()) {
            finish()
            return
        }

        GeckoRuntimeHolder.init(this)
        session = GeckoSession()
        session.open(GeckoRuntimeHolder.runtime)
        tuneSession()

        val geckoView = GeckoView(this)
        geckoView.setSession(session)

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.WHITE)
        }
        root.addView(buildToolbar())
        root.addView(geckoView, LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, 0, 1f))
        setContentView(root)

        // 异形屏：页面底部避让导航栏
        geckoView.setOnApplyWindowInsetsListener { v, insets ->
            val bottom = insets.getSystemWindowInsetBottom()
            v.setPadding(0, 0, 0, bottom)
            insets
        }

        session.loadUri(url)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun tuneSession() {
        // 导航：http(s) 继续；mailto:/tel: 等交给系统应用
        session.navigationDelegate = object : GeckoSession.NavigationDelegate {
            override fun onCanGoBack(session: GeckoSession, canGoBack: Boolean) {
                canGoBackState = canGoBack
                updateNavState()
            }

            override fun onCanGoForward(session: GeckoSession, canGoForward: Boolean) {
                canGoForwardState = canGoForward
                updateNavState()
            }

            override fun onLoadRequest(
                session: GeckoSession,
                request: GeckoSession.NavigationDelegate.LoadRequest,
            ): GeckoResult<AllowOrDeny>? {
                val uri = Uri.parse(request.uri)
                val scheme = uri.scheme?.lowercase()
                if (scheme == "http" || scheme == "https") {
                    return GeckoResult.fromValue(AllowOrDeny.ALLOW)
                }
                if (scheme == "mailto" || scheme == "tel" || scheme == "sms" || scheme == "intent" || scheme == "market") {
                    try {
                        startActivity(Intent(Intent.ACTION_VIEW, uri))
                    } catch (e: ActivityNotFoundException) {
                        Toast.makeText(this@EmbeddedBrowserActivity, "没有可处理该链接的应用", Toast.LENGTH_SHORT).show()
                    }
                    return GeckoResult.fromValue(AllowOrDeny.DENY)
                }
                return GeckoResult.fromValue(AllowOrDeny.DENY)
            }
        }

        // 进度 / URL 显示
        session.progressDelegate = object : GeckoSession.ProgressDelegate {
            override fun onPageStart(session: GeckoSession, url: String) {
                currentUrl = url
                progressBar.visibility = View.VISIBLE
                updateUrlText(url)
            }

            override fun onPageStop(session: GeckoSession, success: Boolean) {
                progressBar.visibility = View.GONE
                updateNavState()
            }

            override fun onProgressChange(session: GeckoSession, progress: Int) {
                progressBar.progress = progress
            }
        }

        // 下载：网页下载走系统 DownloadManager
        session.contentDelegate = object : GeckoSession.ContentDelegate {
            override fun onExternalResponse(session: GeckoSession, response: WebResponse) {
                try {
                    val uri = Uri.parse(response.uri)
                    val mimeType =
                        response.headers["Content-Type"] ?: response.headers["content-type"]
                    val request = DownloadManager.Request(uri)
                    request.setMimeType(mimeType)
                    request.setDescription("LearnGraph 正在下载文件")
                    val filename = URLUtil.guessFileName(response.uri, null, mimeType)
                    request.setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, filename)
                    request.setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    (getSystemService(Context.DOWNLOAD_SERVICE) as DownloadManager).enqueue(request)
                    Toast.makeText(this@EmbeddedBrowserActivity, "已开始下载：$filename", Toast.LENGTH_SHORT).show()
                } catch (e: Exception) {
                    Toast.makeText(this@EmbeddedBrowserActivity, "下载失败", Toast.LENGTH_SHORT).show()
                }
            }
        }
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

        btnBack = toolButton("◀", "后退") { if (canGoBackState) session.goBack() }
        btnForward = toolButton("▶", "前进") { if (canGoForwardState) session.goForward() }

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
        bar.addView(toolButton("⟳", "刷新", { session.reload() }))
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
        btnBack.alpha = if (canGoBackState) 1f else 0.35f
        btnForward.alpha = if (canGoForwardState) 1f else 0.35f
    }

    private fun showMenu() {
        val items = arrayOf("后退", "前进", "刷新", "在系统浏览器打开", "复制链接")
        AlertDialog.Builder(this)
            .setTitle("浏览器菜单")
            .setItems(items) { _, which ->
                when (which) {
                    0 -> if (canGoBackState) session.goBack()
                    1 -> if (canGoForwardState) session.goForward()
                    2 -> session.reload()
                    3 -> {
                        val u = currentUrl ?: return@setItems
                        try {
                            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(u)))
                        } catch (e: ActivityNotFoundException) {
                            Toast.makeText(this, "没有可打开该链接的浏览器", Toast.LENGTH_SHORT).show()
                        }
                    }
                    4 -> {
                        val u = currentUrl ?: return@setItems
                        (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
                            .setPrimaryClip(ClipData.newPlainText("URL", u))
                        Toast.makeText(this, "链接已复制", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            .show()
    }

    override fun onBackPressed() {
        if (canGoBackState) {
            session.goBack()
        } else {
            super.onBackPressed()
        }
    }

    override fun onDestroy() {
        runCatching { session.close() }
        super.onDestroy()
    }

    private fun dp(v: Int): Int = (v * resources.displayMetrics.density).toInt()
}
