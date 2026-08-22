package com.learngraph.mobile

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.widget.Toast
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.biometric.BiometricManager
import androidx.biometric.BiometricPrompt
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.content.edit
import androidx.fragment.app.FragmentActivity
import com.learngraph.mobile.notify.ReplyNotifier
import com.learngraph.mobile.ui.navigation.AppNav
import com.learngraph.mobile.ui.theme.LearnGraphTheme
import com.learngraph.mobile.util.PhotoCapture
import com.learngraph.mobile.util.ShortcutActions
import java.util.concurrent.Executors

/**
 * LearnGraph 原生版入口。
 *
 * 异形屏适配：
 *  - enableEdgeToEdge()：状态栏/导航栏透明，内容全屏延伸（API 35 起系统强制）
 *  - 各页面通过 Compose WindowInsets（statusBars/navigationBars/displayCutout）处理安全区
 *  - values-v27/styles.xml 已开启 windowLayoutInDisplayCutoutMode=shortEdges（挖孔/刘海）
 *
 * 回复通知（非常驻）：
 *  - onCreate 启动 AlarmManager 周期轮询（60s），无常驻服务/常驻通知
 *  - onResume/onPause 维护前台标志：仅 App 在后台且有新回复时弹通知
 *  - API 33+ 请求通知权限
 *
 * v0.11.0 增强：
 *  - 生物识别解锁（设置页开关开启后，冷启动需指纹/面容验证）
 *  - 长按图标快捷方式（新对话/记笔记/投递任务/切换服务器）
 *  - 拍照即问（PhotoCapture launcher 注册）
 *  - 分享收件箱快捷动作注入
 */
class MainActivity : FragmentActivity() {

    private val biometricExecutor = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        PhotoCapture.init(this)
        PhotoCapture.setWebView(null)
        ShortcutActions.register(this)
        // 从启动 Intent（快捷方式/分享）读取动作
        ShortcutActions.ingestFromIntent(this, intent)
        if (intent?.action == "com.learngraph.mobile.OPEN_SESSION") {
            val sessionId = intent.getStringExtra("session_id")
            if (!sessionId.isNullOrBlank()) {
                ShortcutActions.ingestOpenSession(this, sessionId)
            }
        }
        enableEdgeToEdge()

        val shortcut = intent?.getStringExtra("lg.shortcut")
        if (shortcut == ShortcutActions.ACTION_CONNECT) {
            // 切换服务器：直接回连接页（AppNav 默认从 connect 开始）
            ReplyNotifier.start(this)
            requestNotifPermissionIfNeeded()
            setContent {
                LearnGraphTheme {
                    AppNav(startAtConnect = true)
                }
            }
            return
        }

        val biometricOn = prefs().getBoolean(KEY_BIOMETRIC, false)
        if (biometricOn && canUseBiometric() && savedInstanceState == null) {
            showBiometricGate { proceed() }
        } else {
            proceed()
        }
    }

    private fun proceed() {
        ReplyNotifier.start(this)
        requestNotifPermissionIfNeeded()
        setContent {
            LearnGraphTheme {
                AppNav()
            }
        }
    }

    // ------------------------------------------------------------------ //
    // 生物识别门禁
    // ------------------------------------------------------------------ //

    private fun showBiometricGate(onSuccess: () -> Unit) {
        val prompt = BiometricPrompt(
            this,
            biometricExecutor,
            object : BiometricPrompt.AuthenticationCallback() {
                override fun onAuthenticationSucceeded(result: BiometricPrompt.AuthenticationResult) {
                    runOnUiThread { onSuccess() }
                }

                override fun onAuthenticationError(errorCode: Int, errString: CharSequence) {
                    runOnUiThread {
                        if (errorCode == BiometricPrompt.ERROR_NEGATIVE_BUTTON ||
                            errorCode == BiometricPrompt.ERROR_USER_CANCELED
                        ) {
                            Toast.makeText(this@MainActivity, "需要验证才能进入", Toast.LENGTH_SHORT).show()
                        } else {
                            Toast.makeText(this@MainActivity, "验证失败：$errString", Toast.LENGTH_SHORT).show()
                        }
                        finish()
                    }
                }

                override fun onAuthenticationFailed() {
                    runOnUiThread {
                        Toast.makeText(this@MainActivity, "验证失败，请重试", Toast.LENGTH_SHORT).show()
                    }
                }
            },
        )
        val info = BiometricPrompt.PromptInfo.Builder()
            .setTitle("LearnGraph 解锁")
            .setSubtitle("验证身份以访问你的私有数据")
            .setAllowedAuthenticators(
                BiometricManager.Authenticators.BIOMETRIC_WEAK or
                    BiometricManager.Authenticators.DEVICE_CREDENTIAL,
            )
            .build()
        prompt.authenticate(info)
    }

    private fun canUseBiometric(): Boolean {
        val manager = BiometricManager.from(this)
        return when (manager.canAuthenticate(
            BiometricManager.Authenticators.BIOMETRIC_WEAK or
                BiometricManager.Authenticators.DEVICE_CREDENTIAL,
        )) {
            BiometricManager.BIOMETRIC_SUCCESS -> true
            else -> false
        }
    }

    // ------------------------------------------------------------------ //
    // 设置（生物识别开关由连接页写入，这里读取）
    // ------------------------------------------------------------------ //

    companion object {
        const val PREFS = "lg_mobile"
        const val KEY_BIOMETRIC = "biometric_enabled"
    }

    private fun prefs() = getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    override fun onResume() {
        super.onResume()
        ReplyNotifier.setForeground(true)
    }

    override fun onPause() {
        super.onPause()
        // 传 context：后台化立即轮询一次（刚投递的后台任务完成后尽快通知）
        ReplyNotifier.setForeground(false, this)
        // 后台前强制 Cookie 写盘（重启免登录）
        android.webkit.CookieManager.getInstance().flush()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        // 快捷方式/分享在 App 已运行时点击 → 注入动作（网页版消费）
        ShortcutActions.ingestFromIntent(this, intent)
        // 通知「打开会话」→ 注入待打开会话
        if (intent?.action == "com.learngraph.mobile.OPEN_SESSION") {
            val sessionId = intent.getStringExtra("session_id")
            if (!sessionId.isNullOrBlank()) {
                ShortcutActions.ingestOpenSession(this, sessionId)
            }
        }
    }

    private fun requestNotifPermissionIfNeeded() {
        if (
            Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.POST_NOTIFICATIONS),
                1001,
            )
        }
    }
}
