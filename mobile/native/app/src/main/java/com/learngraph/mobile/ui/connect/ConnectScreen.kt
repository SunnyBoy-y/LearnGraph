package com.learngraph.mobile.ui.connect

import android.content.Context
import android.graphics.Bitmap
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
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
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
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
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.MainActivity
import com.learngraph.mobile.R
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.data.ShareInbox
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
 *
 * v0.11.0 增强：
 *  - 扫码连接：扫描服务器地址二维码自动填充
 *  - 生物识别解锁开关（冷启动需指纹/面容验证）
 *  - 收件箱提示：有未处理的分享内容时提示
 */
@Composable
fun ConnectScreen(onConnected: () -> Unit) {
    val context = LocalContext.current
    val app = context.applicationContext as LearnGraphApp
    val scope = rememberCoroutineScope()
    val authState by app.authStore.state.collectAsState(initial = AuthStore.AuthState())

    var address by rememberSaveable { mutableStateOf("") }
    var loading by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var biometricOn by rememberSaveable { mutableStateOf(readBiometric(context)) }
    var hapticOn by rememberSaveable { mutableStateOf(readHaptic(context)) }
    val inboxCount = remember { mutableStateOf(ShareInbox.count(context.applicationContext)) }

    // 扫码结果回填
    val qrLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == android.app.Activity.RESULT_OK) {
            val url = result.data?.getStringExtra(com.learngraph.mobile.QrScanActivity.EXTRA_RESULT)
            if (!url.isNullOrBlank()) address = url
        }
    }

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
        // App 图标（与桌面图标一致）。
        // ⚠️ 不能用 painterResource(R.mipmap.ic_launcher)：API 26+ 该资源是
        // adaptive-icon XML，painterResource 不支持（启动即崩 IllegalArgumentException）。
        // 先渲染成 Bitmap 再显示。
        val launcherBitmap = remember {
            val d = androidx.core.content.ContextCompat.getDrawable(context, R.mipmap.ic_launcher)
            val w = (d?.intrinsicWidth ?: 192).coerceAtLeast(1)
            val h = (d?.intrinsicHeight ?: 192).coerceAtLeast(1)
            val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
            val canvas = android.graphics.Canvas(bmp)
            d?.setBounds(0, 0, w, h)
            d?.draw(canvas)
            bmp.asImageBitmap()
        }
        Image(
            bitmap = launcherBitmap,
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

        // 扫码连接
        OutlinedButton(
            onClick = {
                qrLauncher.launch(
                    android.content.Intent(context, com.learngraph.mobile.QrScanActivity::class.java),
                )
            },
            modifier = Modifier.fillMaxWidth().height(44.dp),
        ) {
            Text("📷 扫描服务器二维码")
        }
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

        // 生物识别解锁开关
        Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "生物识别解锁",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    text = "开启后打开 App 需指纹/面容验证（保护私有数据）",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Switch(
                checked = biometricOn,
                onCheckedChange = { checked ->
                    biometricOn = checked
                    writeBiometric(context, checked)
                },
            )
        }

        // 回复震动开关（A1/A2/A3：最终回复 / agent 步骤 / 达成时刻的轻震）
        Row(modifier = Modifier.fillMaxWidth(), verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = "回复震动",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Text(
                    text = "收到回复时轻轻震动（思维链阶段不震）",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Switch(
                checked = hapticOn,
                onCheckedChange = { checked ->
                    hapticOn = checked
                    writeHaptic(context, checked)
                },
            )
        }

        Spacer(Modifier.height(12.dp))

        if (inboxCount.value > 0) {
            Text(
                text = "📥 收件箱有 ${inboxCount.value} 条待处理分享，连接后可在网页版处理",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.primary,
            )
            Spacer(Modifier.height(12.dp))
        }

        Text(
            text = "APK 连接的是你自己部署的服务器，数据不经过第三方。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

private fun readBiometric(context: Context): Boolean =
    context.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE)
        .getBoolean(MainActivity.KEY_BIOMETRIC, false)

private fun writeBiometric(context: Context, enabled: Boolean) {
    context.getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE)
        .edit().putBoolean(MainActivity.KEY_BIOMETRIC, enabled).apply()
}

private const val HAPTIC_PREFS = "lg_mobile"
private fun readHaptic(context: Context): Boolean =
    context.getSharedPreferences(HAPTIC_PREFS, Context.MODE_PRIVATE)
        .getBoolean(com.learngraph.mobile.util.Haptics.KEY_VIBRATE, true)

private fun writeHaptic(context: Context, enabled: Boolean) {
    context.getSharedPreferences(HAPTIC_PREFS, Context.MODE_PRIVATE)
        .edit().putBoolean(com.learngraph.mobile.util.Haptics.KEY_VIBRATE, enabled).apply()
}
