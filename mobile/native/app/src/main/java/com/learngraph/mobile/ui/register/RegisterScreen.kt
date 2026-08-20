package com.learngraph.mobile.ui.register

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
 * 注册页（与网页版 /auth/register 契约一致）：
 *  username / email(可选) / display_name / password → LoginResponse
 */
@Composable
fun RegisterScreen(onRegistered: () -> Unit, onBack: () -> Unit) {
    val app = LocalContext.current.applicationContext as LearnGraphApp
    val scope = rememberCoroutineScope()

    var username by rememberSaveable { mutableStateOf("") }
    var email by rememberSaveable { mutableStateOf("") }
    var displayName by rememberSaveable { mutableStateOf("") }
    var password by rememberSaveable { mutableStateOf("") }
    var password2 by rememberSaveable { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .statusBarsPadding()
            .navigationBarsPadding()
            .padding(horizontal = 28.dp),
    ) {
        Spacer(Modifier.height(48.dp))
        Text("注册新账号", style = MaterialTheme.typography.headlineMedium)
        Text(
            text = "服务器：${app.api.baseUrl}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(24.dp))

        OutlinedTextField(
            value = username,
            onValueChange = { username = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("用户名") },
            singleLine = true,
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = displayName,
            onValueChange = { displayName = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("显示名称") },
            singleLine = true,
        )
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = email,
            onValueChange = { email = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("邮箱（可选）") },
            singleLine = true,
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(keyboardType = KeyboardType.Email),
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
        Spacer(Modifier.height(12.dp))
        OutlinedTextField(
            value = password2,
            onValueChange = { password2 = it },
            modifier = Modifier.fillMaxWidth(),
            label = { Text("确认密码") },
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
                when {
                    username.isBlank() -> error = "请输入用户名"
                    displayName.isBlank() -> error = "请输入显示名称"
                    password.length < 6 -> error = "密码至少 6 位"
                    password != password2 -> error = "两次输入的密码不一致"
                    else -> {
                        loading = true
                        error = null
                        scope.launch {
                            try {
                                val body = buildString {
                                    append("{\"username\":${jsonStr(username)},")
                                    append("\"display_name\":${jsonStr(displayName)},")
                                    if (email.isNotBlank()) append("\"email\":${jsonStr(email)},")
                                    append("\"password\":${jsonStr(password)}}")
                                }
                                val resp = app.api.post("/auth/register", body, auth = false, workspace = false)
                                val reg = Json { ignoreUnknownKeys = true }.decodeFromString<LoginResponse>(resp)
                                val workspace = reg.defaultWorkspaceId
                                if (workspace.isNullOrBlank()) {
                                    error = "注册成功但账号没有可用工作区，请稍后重试"
                                } else {
                                    val deviceId = UUID.randomUUID().toString()
                                    app.api.token = reg.accessToken
                                    app.api.workspaceId = workspace
                                    app.api.deviceId = deviceId
                                    app.authStore.setAuth(
                                        token = reg.accessToken,
                                        workspaceId = workspace,
                                        username = reg.username ?: username,
                                        displayName = reg.displayName,
                                        sessionId = reg.sessionId,
                                        deviceId = deviceId,
                                    )
                                    onRegistered()
                                }
                            } catch (e: Exception) {
                                error = e.message ?: "注册失败"
                            } finally {
                                loading = false
                            }
                        }
                    }
                }
            },
            enabled = !loading,
            modifier = Modifier.fillMaxWidth().height(48.dp),
        ) {
            if (loading) {
                CircularProgressIndicator(modifier = Modifier.height(20.dp), strokeWidth = 2.dp)
            } else {
                Text("注册并进入")
            }
        }

        Spacer(Modifier.height(12.dp))
        androidx.compose.material3.TextButton(
            onClick = onBack,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Text("已有账号？返回登录")
        }
    }
}

private fun jsonStr(s: String): String =
    "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\""
