package com.learngraph.mobile

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.learngraph.mobile.notify.ReplyNotifier
import com.learngraph.mobile.ui.navigation.AppNav
import com.learngraph.mobile.ui.theme.LearnGraphTheme

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
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        ReplyNotifier.start(this)
        requestNotifPermissionIfNeeded()
        setContent {
            LearnGraphTheme {
                AppNav()
            }
        }
    }

    override fun onResume() {
        super.onResume()
        ReplyNotifier.setForeground(true)
    }

    override fun onPause() {
        super.onPause()
        ReplyNotifier.setForeground(false)
        // 后台前强制 Cookie 写盘（重启免登录）
        android.webkit.CookieManager.getInstance().flush()
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
