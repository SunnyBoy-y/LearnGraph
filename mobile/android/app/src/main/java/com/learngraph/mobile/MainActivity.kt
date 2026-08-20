package com.learngraph.mobile

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.Gravity
import android.view.View
import android.webkit.WebSettings
import android.webkit.WebView
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
