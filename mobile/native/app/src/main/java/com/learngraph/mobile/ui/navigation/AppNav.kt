package com.learngraph.mobile.ui.navigation

import android.content.Context
import android.net.Uri
import androidx.compose.animation.EnterTransition
import androidx.compose.animation.ExitTransition
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.ViewModelStore
import androidx.lifecycle.ViewModelStoreOwner
import androidx.lifecycle.viewmodel.compose.LocalViewModelStoreOwner
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.MainActivity
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.data.ShareInbox
import com.learngraph.mobile.ui.connect.ConnectScreen
import com.learngraph.mobile.ui.downloads.DownloadsScreen
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
    const val SETTINGS = "settings"
    const val WEBAPP = "webapp?sessionId={sessionId}&feature={feature}"
    const val DOWNLOADS = "downloads"
}

/** Native authentication and conversations are the normal path; web features remain an explicit destination. */
@Composable
fun AppNav(startAtConnect: Boolean = false) {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp
    val authState by app.authStore.state.collectAsState(initial = null)
    var syncedAuth by remember { mutableStateOf<AuthStore.AuthState?>(null) }

    LaunchedEffect(authState) {
        authState?.let {
            app.api.baseUrl = it.baseUrl
            app.api.token = it.token
            app.api.workspaceId = it.workspaceId
            app.api.deviceId = it.deviceId
            app.api.accountId = it.userId ?: it.username
            syncedAuth = it
        }
    }
    val auth = syncedAuth
    if (auth == null) {
        Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    val navController = rememberNavController()
    val currentEntry by navController.currentBackStackEntryAsState()
    val navigationEpoch by MainActivity.navigationEpoch.collectAsState()
    var webEpoch by remember { mutableIntStateOf(0) }
    val nativeScope = remember(auth.baseUrl, auth.workspaceId, auth.userId, auth.username, auth.token) {
        val identity = listOf(auth.baseUrl, auth.workspaceId.orEmpty(), auth.userId ?: auth.username ?: auth.token.orEmpty()).joinToString("|")
        java.security.MessageDigest.getInstance("SHA-256").digest(identity.toByteArray()).joinToString("") { "%02x".format(it) }
    }
    // Keep scoped VMs while visiting compatibility/settings, and clear them immediately on account changes.
    val nativeOwner = remember(nativeScope) { object : ViewModelStoreOwner { override val viewModelStore = ViewModelStore() } }
    DisposableEffect(nativeOwner) { onDispose { nativeOwner.viewModelStore.clear() } }
    val start = remember {
        val pending = context.getSharedPreferences("lg_shortcuts", Context.MODE_PRIVATE).getString("pending_action", "").orEmpty()
        val needsWeb = pending.isNotBlank() && pending != "new-chat" && pending != "connect" && !pending.startsWith("open-session:")
        when {
            startAtConnect || auth.baseUrl.isBlank() -> Routes.CONNECT
            auth.token.isNullOrBlank() || auth.workspaceId.isNullOrBlank() -> Routes.LOGIN
            needsWeb || ShareInbox.count(context) > 0 -> "webapp"
            else -> Routes.HOME
        }
    }
    val goHome = { navController.navigate(Routes.HOME) { popUpTo(0) { inclusive = true } }; Unit }
    val goLogin = { navController.navigate(Routes.LOGIN) { popUpTo(0) { inclusive = true } }; Unit }
    val openWeb = { sessionId: String? ->
        navController.navigate(if (sessionId == null) "webapp" else "webapp?sessionId=${Uri.encode(sessionId)}")
        Unit
    }

    LaunchedEffect(auth.token, currentEntry?.destination?.route) {
        val route = currentEntry?.destination?.route
        if (auth.token.isNullOrBlank() && route in listOf(Routes.HOME, Routes.SETTINGS)) {
            goLogin()
        } else if (route == Routes.HOME) {
            val pending = context.getSharedPreferences("lg_shortcuts", Context.MODE_PRIVATE).getString("pending_action", "").orEmpty()
            if (pending.isNotBlank() && pending != "new-chat" && pending != "connect" && !pending.startsWith("open-session:")) openWeb(null)
        }
    }
    LaunchedEffect(navigationEpoch) {
        if (navigationEpoch > 0) {
            val pending = context.getSharedPreferences("lg_shortcuts", Context.MODE_PRIVATE).getString("pending_action", "").orEmpty()
            when {
                pending == "connect" -> {
                    com.learngraph.mobile.util.ShortcutActions.consume(context)
                    navController.navigate(Routes.CONNECT) { launchSingleTop = true }
                }
                pending == "new-chat" || pending.startsWith("open-session:") -> {
                    if (auth.token.isNullOrBlank()) goLogin()
                    else if (currentEntry?.destination?.route != Routes.HOME) {
                        if (!navController.popBackStack(Routes.HOME, false)) goHome()
                    }
                }
                pending.isNotBlank() -> {
                    if (currentEntry?.destination?.route == Routes.WEBAPP) webEpoch++ else openWeb(null)
                }
            }
        }
    }

    val foregroundEpoch by MainActivity.foregroundEpoch.collectAsState()
    LaunchedEffect(foregroundEpoch) {
        if (foregroundEpoch > 0 && currentEntry?.destination?.route == Routes.WEBAPP) webEpoch++
    }

    // BrowserRouter changes documents without a route-level cross-fade.
    // Feature surfaces own their own CSS/Compose motion and must not be faded twice.
    NavHost(navController = navController, startDestination = start,
        enterTransition = { EnterTransition.None }, exitTransition = { ExitTransition.None },
        popEnterTransition = { EnterTransition.None }, popExitTransition = { ExitTransition.None }) {
        composable(Routes.CONNECT) {
            LaunchedEffect(Unit) {
                val pending = context.getSharedPreferences("lg_shortcuts", Context.MODE_PRIVATE).getString("pending_action", "")
                if (pending == "connect") com.learngraph.mobile.util.ShortcutActions.consume(context)
            }
            ConnectScreen(onConnected = {
                if (app.api.token.isNullOrBlank() || app.api.workspaceId.isNullOrBlank()) goLogin() else goHome()
            })
        }
        composable(Routes.LOGIN) {
            LoginScreen(onLoggedIn = goHome, onRegister = { navController.navigate(Routes.REGISTER) }, onChangeServer = { navController.navigate(Routes.CONNECT) })
        }
        composable(Routes.REGISTER) { RegisterScreen(onRegistered = goHome, onBack = { navController.popBackStack() }) }
        composable(Routes.HOME) {
            key(nativeScope) { CompositionLocalProvider(LocalViewModelStoreOwner provides nativeOwner) {
            val vm = viewModel<HomeViewModel>(key = "native-home-$nativeScope", factory = object : ViewModelProvider.Factory {
                @Suppress("UNCHECKED_CAST")
                override fun <T : ViewModel> create(modelClass: Class<T>): T = HomeViewModel(app.api, app.authStore, app.repository, app.appearanceStore) as T
            })
            HomeScreen(
                vm = vm,
                onLogout = goLogin,
                onOpenWeb = openWeb,
                onOpenSettings = { navController.navigate(Routes.SETTINGS) },
                onOpenDownloads = { navController.navigate(Routes.DOWNLOADS) },
                onSwitchServer = { navController.navigate(Routes.CONNECT) },
                onOpenFeature = { feature -> navController.navigate("webapp?feature=${Uri.encode(feature)}") },
            )
            } }
        }
        composable(
            Routes.WEBAPP,
            arguments = listOf(
                navArgument("sessionId") { type = NavType.StringType; nullable = true; defaultValue = null },
                navArgument("feature") { type = NavType.StringType; nullable = true; defaultValue = null },
            ),
        ) { entry ->
            key(webEpoch) {
                var resumeCheckEpoch by remember { mutableIntStateOf(0) }
                LaunchedEffect(foregroundEpoch) {
                    if (foregroundEpoch > 0) resumeCheckEpoch++
                }
                WebAppScreen(
                    initialAuth = auth,
                    sessionId = entry.arguments?.getString("sessionId"),
                    initialFeature = entry.arguments?.getString("feature"),
                    resumeCheckEpoch = resumeCheckEpoch,
                    onBackToNative = {
                        if (!navController.popBackStack(Routes.HOME, false)) goHome()
                    },
                    onOpenDownloads = {
                        // The WebView hands its current draft back before opening downloads.
                        if (!navController.popBackStack(Routes.HOME, false)) goHome()
                        navController.navigate(Routes.DOWNLOADS)
                    },
                    onLoadFailed = {
                        if (!navController.popBackStack(Routes.HOME, false)) goHome()
                    },
                )
            }
        }
        composable(Routes.SETTINGS) {
            SettingsScreen(onLogout = goLogin, onBack = { navController.popBackStack() })
        }
        composable(Routes.DOWNLOADS) { DownloadsScreen(onBack = { navController.popBackStack() }) }
    }
}
