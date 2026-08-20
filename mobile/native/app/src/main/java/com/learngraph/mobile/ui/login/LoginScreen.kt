package com.learngraph.mobile.ui.login

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.data.LoginResponse
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import java.util.UUID

/**
 * 登录页：账号密码认证，成功后写入 AuthStore 并进入会话列表。
 * 未注册账号可跳转注册页（服务器开启注册时）。
 */
@Composable
fun LoginScreen(onLoggedIn: () -> Unit, onRegister: () -> Unit) {
    val app = LocalContext.current.applicationContext as LearnGraphApp
    val scope = rememberCoroutineScope()

    var username by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 28.dp),
    ) {
        Spacer(Modifier.height(72.dp))
        Text("登录", style = MaterialTheme.typography.headlineMedium)
        Text(
            text = "服务器：${app.api.baseUrl}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(28.dp))

        OutlinedTextField(
            value = username,
            onValueChange = { username = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("用户名") },
            singleLine = true,
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = password,
            onValueChange = { password = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("密码") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Password),
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
                if (username.isBlank() || password.isBlank()) {
                    error = "请输入用户名和密码"
                    return@Button
                }
                loading = true
                error = null
                scope.launch {
                    try {
                        val body = """{"username":${jsonStr(username)},"password":${jsonStr(password)}}"""
                        val resp = app.api.post("/auth/login", body, auth = false, workspace = false)
                        val login = Json { ignoreUnknownKeys = true }.decodeFromString<LoginResponse>(resp)
                        val workspace = login.defaultWorkspaceId
                        if (workspace.isNullOrBlank()) {
                            error = "该账号没有可用的工作区"
                        } else {
                            val deviceId = UUID.randomUUID().toString()
                            app.api.token = login.accessToken
                            app.api.workspaceId = workspace
                            app.api.deviceId = deviceId
                            app.authStore.setAuth(
                                token = login.accessToken,
                                workspaceId = workspace,
                                username = login.username ?: username,
                                displayName = login.displayName,
                                sessionId = login.sessionId,
                                deviceId = deviceId,
                            )
                            onLoggedIn()
                        }
                    } catch (e: Exception) {
                        error = e.message ?: "登录失败"
                    } finally {
                        loading = false
                    }
                }
            },
            enabled = !loading,
            modifier = Modifier.fillMaxWidth().height(48.dp),
        ) {
            if (loading) {
                CircularProgressIndicator(modifier = Modifier.height(20.dp), strokeWidth = 2.dp)
            } else {
                Text("登录")
            }
        }

        Spacer(Modifier.height(12.dp))
        androidx.compose.material3.TextButton(
            onClick = onRegister,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("没有账号？注册新账号")
        }
    }
}

private fun jsonStr(s: String): String =
    "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\""
