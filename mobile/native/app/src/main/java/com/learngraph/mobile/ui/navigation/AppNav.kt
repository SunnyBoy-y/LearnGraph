package com.learngraph.mobile.ui.navigation

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.platform.LocalContext
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.ui.connect.ConnectScreen
import com.learngraph.mobile.ui.downloads.DownloadsScreen
import com.learngraph.mobile.ui.web.WebAppScreen

object Routes {
    const val CONNECT = "connect"
    const val WEBAPP = "webapp"
    const val DOWNLOADS = "downloads"
}

/**
 * 极简纯网页外壳导航：
 *  - 未配置服务器 → 连接页（填地址，记住）
 *  - 已配置 → 直接全屏网页版（无原生底栏/控件，顶栏为很薄的白色）
 */
@Composable
fun AppNav() {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp

    val authState by app.authStore.state.collectAsState(initial = AuthStore.AuthState())

    LaunchedEffect(authState) {
        app.api.baseUrl = authState.baseUrl
        app.api.token = authState.token
        app.api.workspaceId = authState.workspaceId
        app.api.deviceId = authState.deviceId
    }

    // 启动路由：始终从连接页开始（地址自动预填），用户点击「连接并继续」才进入网页
    val start = Routes.CONNECT
    val navController = rememberNavController()

    NavHost(navController = navController, startDestination = start) {
        composable(Routes.CONNECT) {
            ConnectScreen(
                onConnected = {
                    navController.navigate(Routes.WEBAPP) { popUpTo(Routes.CONNECT) { inclusive = true } }
                },
            )
        }
        composable(Routes.WEBAPP) {
            WebAppScreen(
                onOpenDownloads = { navController.navigate(Routes.DOWNLOADS) },
                onLoadFailed = {
                    navController.navigate(Routes.CONNECT) { popUpTo(0) { inclusive = true } }
                },
            )
        }
        composable(Routes.DOWNLOADS) {
            DownloadsScreen(onBack = { navController.popBackStack() })
        }
    }
}
