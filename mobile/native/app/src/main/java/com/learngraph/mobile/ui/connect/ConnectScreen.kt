package com.learngraph.mobile.ui.connect

import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.R
import com.learngraph.mobile.data.AuthStore
import kotlinx.coroutines.launch

fun normalizeBaseUrl(input: String): String {
    var value = input.trim()
    if (value.isEmpty()) return ""
    if (!value.startsWith("http://") && !value.startsWith("https://")) value = "http://$value"
    return value.trimEnd('/')
}

/**
 * 连接页：填写服务器地址（默认 18000），连通性测试通过后进入登录。
 * 异形屏：statusBarsPadding 顶安全区 + navigationBarsPadding 底安全区。
 */
@Composable
fun ConnectScreen(onConnected: () -> Unit) {
    val app = LocalContext.current.applicationContext as LearnGraphApp
    val scope = rememberCoroutineScope()
    val authState by app.authStore.state.collectAsState(initial = AuthStore.AuthState())

    var address by rememberSaveable { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    // 预填已保存的地址
    LaunchedEffect(authState.baseUrl) {
        if (address.isBlank() && authState.baseUrl.isNotBlank()) address = authState.baseUrl
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 28.dp),
    ) {
        Spacer(Modifier.height(48.dp))
        // App 图标（与桌面图标一致）
        Image(
            painter = painterResource(R.mipmap.ic_launcher),
            contentDescription = "LearnGraph 图标",
            modifier = Modifier
                .size(88.dp)
                .clip(RoundedCornerShape(22.dp)),
        )
        Spacer(Modifier.height(12.dp))
        Text(
            text = "连接你的 LearnGraph 服务器",
            style = MaterialTheme.typography.titleLarge,
        )
        Text(
            text = "输入电脑上 LearnGraph 服务的地址（默认端口 18000）",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(28.dp))

        OutlinedTextField(
            value = address,
            onValueChange = { address = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("服务器地址") },
            placeholder = { Text("http://192.168.1.5:18000") },
            singleLine = true,
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Uri),
        )
        Spacer(Modifier.height(8.dp))

        if (error != null) {
            Text(
                text = error.orEmpty(),
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
            Spacer(Modifier.height(8.dp))
        }

        Button(
            onClick = {
                val normalized = normalizeBaseUrl(address)
                if (normalized.isEmpty()) {
                    error = "请输入服务器地址"
                    return@Button
                }
                loading = true
                error = null
                app.api.baseUrl = normalized
                scope.launch {
                    try {
                        app.api.get("/deployment/profile", auth = false, workspace = false)
                        app.authStore.setBaseUrl(normalized)
                        onConnected()
                    } catch (e: Exception) {
                        error = e.message ?: "连接失败"
                    } finally {
                        loading = false
                    }
                }
            },
            enabled = !loading,
            modifier = Modifier.fillMaxWidth().height(48.dp),
        ) {
            if (loading) {
                CircularProgressIndicator(modifier = Modifier.height(20.dp).padding(0.dp), strokeWidth = 2.dp)
            } else {
                Text("连接并继续")
            }
        }

        Spacer(Modifier.height(16.dp))
        Text(
            text = "APK 连接的是你自己部署的服务器，数据不经过第三方。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
