package com.learngraph.mobile.ui.navigation

import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
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
import com.learngraph.mobile.ui.register.RegisterScreen
import com.learngraph.mobile.ui.settings.SettingsScreen
import com.learngraph.mobile.ui.web.WebAppScreen

object Routes {
    const val CONNECT = "connect"
    const val LOGIN = "login"
    const val REGISTER = "register"
    const val HOME = "home"
    const val WEBAPP = "webapp"
    const val SETTINGS = "settings"
    const val CHAT = "chat/{sessionId}"
    fun chat(sessionId: String) = "chat/$sessionId"
}

/** 底部导航的三个顶层 Tab */
private val TOP_LEVEL = setOf(Routes.HOME, Routes.WEBAPP, Routes.SETTINGS)

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
                if (current != Routes.LOGIN && current != Routes.CONNECT && current != Routes.REGISTER) {
                    navController.navigate(Routes.LOGIN) { popUpTo(0) { inclusive = true } }
                }
            }
        }
    }

    val navBackStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = navBackStackEntry?.destination?.route
    val showBottomBar = currentRoute in TOP_LEVEL

    Scaffold(
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        bottomBar = {
            if (showBottomBar) {
                NavigationBar {
                    NavigationBarItem(
                        selected = currentRoute == Routes.HOME,
                        onClick = {
                            navController.navigate(Routes.HOME) {
                                popUpTo(Routes.HOME) { inclusive = false }
                                launchSingleTop = true
                            }
                        },
                        icon = { Icon(Icons.Default.List, contentDescription = null) },
                        label = { Text("会话") },
                    )
                    NavigationBarItem(
                        selected = currentRoute == Routes.WEBAPP,
                        onClick = {
                            navController.navigate(Routes.WEBAPP) {
                                popUpTo(Routes.HOME) { inclusive = false }
                                launchSingleTop = true
                            }
                        },
                        icon = { Icon(Icons.Default.Menu, contentDescription = null) },
                        label = { Text("全部功能") },
                    )
                    NavigationBarItem(
                        selected = currentRoute == Routes.SETTINGS,
                        onClick = {
                            navController.navigate(Routes.SETTINGS) {
                                popUpTo(Routes.HOME) { inclusive = false }
                                launchSingleTop = true
                            }
                        },
                        icon = { Icon(Icons.Default.Settings, contentDescription = null) },
                        label = { Text("设置") },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = start,
            modifier = Modifier.padding(bottom = if (showBottomBar) padding.calculateBottomPadding() else 0.dp),
        ) {
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
                    onRegister = {
                        navController.navigate(Routes.REGISTER)
                    },
                )
            }
            composable(Routes.REGISTER) {
                RegisterScreen(
                    onRegistered = {
                        navController.navigate(Routes.HOME) { popUpTo(0) { inclusive = true } }
                    },
                    onBack = { navController.popBackStack() },
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
            composable(Routes.WEBAPP) {
                WebAppScreen(onBack = { navController.popBackStack() })
            }
            composable(Routes.SETTINGS) {
                SettingsScreen(
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
                ChatScreen(
                    vm = vm,
                    onBack = { navController.popBackStack() },
                    onOpenWeb = { navController.navigate(Routes.WEBAPP) },
                )
            }
        }
    }
}
