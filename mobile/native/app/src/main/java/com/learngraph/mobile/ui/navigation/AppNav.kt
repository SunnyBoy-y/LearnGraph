package com.learngraph.mobile.ui.navigation

import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.ui.chat.ChatScreen
import com.learngraph.mobile.ui.chat.ChatViewModel
import com.learngraph.mobile.ui.connect.ConnectScreen
import com.learngraph.mobile.ui.home.HomeScreen
import com.learngraph.mobile.ui.home.HomeViewModel
import com.learngraph.mobile.ui.login.LoginScreen

object Routes {
    const val CONNECT = "connect"
    const val LOGIN = "login"
    const val HOME = "home"
    const val CHAT = "chat/{sessionId}"
    fun chat(sessionId: String) = "chat/$sessionId"
}

/** ViewModel 工厂：从 Application 容器取依赖 */
inline fun <reified T : androidx.lifecycle.ViewModel> appViewModelFactory(
    app: LearnGraphApp,
    noinline create: (app: LearnGraphApp) -> T,
) = viewModelFactory { initializer { create(app) } }

@Composable
fun AppNav() {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp
    val navController = rememberNavController()

    val authState by app.authStore.state.collectAsState(initial = AuthStore.AuthState())

    // 同步 API 客户端上下文（baseUrl/token/workspaceId/deviceId）
    LaunchedEffect(authState) {
        app.api.baseUrl = authState.baseUrl
        app.api.token = authState.token
        app.api.workspaceId = authState.workspaceId
        app.api.deviceId = authState.deviceId
    }

    // 启动路由决策
    val start = remember(authState.baseUrl, authState.token) {
        when {
            authState.baseUrl.isBlank() -> Routes.CONNECT
            authState.token == null -> Routes.LOGIN
            else -> Routes.HOME
        }
    }

    // 认证状态变化 → 重定向
    LaunchedEffect(authState.baseUrl, authState.token) {
        val current = navController.currentDestination?.route
        when {
            authState.baseUrl.isBlank() -> {
                if (current != Routes.CONNECT) {
                    navController.navigate(Routes.CONNECT) { popUpTo(0) { inclusive = true } }
                }
            }
            authState.token == null -> {
                if (current != Routes.LOGIN && current != Routes.CONNECT) {
                    navController.navigate(Routes.LOGIN) { popUpTo(0) { inclusive = true } }
                }
            }
        }
    }

    NavHost(navController = navController, startDestination = start) {
        composable(Routes.CONNECT) {
            ConnectScreen(
                onConnected = {
                    navController.navigate(Routes.LOGIN) { popUpTo(Routes.CONNECT) { inclusive = true } }
                },
            )
        }
        composable(Routes.LOGIN) {
            LoginScreen(
                onLoggedIn = {
                    navController.navigate(Routes.HOME) { popUpTo(0) { inclusive = true } }
                },
            )
        }
        composable(Routes.HOME) {
            val vm: HomeViewModel = viewModel(
                factory = appViewModelFactory(app) { HomeViewModel(it.api, it.authStore) },
            )
            HomeScreen(
                vm = vm,
                onOpenChat = { sessionId -> navController.navigate(Routes.chat(sessionId)) },
                onLogout = {
                    navController.navigate(Routes.CONNECT) { popUpTo(0) { inclusive = true } }
                },
            )
        }
        composable(
            route = Routes.CHAT,
            arguments = listOf(navArgument("sessionId") { type = NavType.StringType }),
        ) { entry ->
            val sessionId = entry.arguments?.getString("sessionId").orEmpty()
            val vm: ChatViewModel = viewModel(
                factory = appViewModelFactory(app) { ChatViewModel(it.api, sessionId) },
            )
            ChatScreen(vm = vm, onBack = { navController.popBackStack() })
        }
    }
}
