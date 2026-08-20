package com.learngraph.mobile

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.learngraph.mobile.ui.navigation.AppNav
import com.learngraph.mobile.ui.theme.LearnGraphTheme

/**
 * LearnGraph 原生版入口。
 *
 * 异形屏适配：
 *  - enableEdgeToEdge()：状态栏/导航栏透明，内容全屏延伸（API 35 起系统强制）
 *  - 各页面通过 Compose WindowInsets（statusBars/navigationBars/displayCutout）处理安全区
 *  - values-v27/styles.xml 已开启 windowLayoutInDisplayCutoutMode=shortEdges（挖孔/刘海）
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            LearnGraphTheme {
                AppNav()
            }
        }
    }
}
