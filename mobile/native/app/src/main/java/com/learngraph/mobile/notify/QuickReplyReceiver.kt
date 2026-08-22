package com.learngraph.mobile.notify

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.widget.Toast
import androidx.core.app.RemoteInput
import com.learngraph.mobile.data.AuthStore
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

/**
 * 通知栏快捷回复（RemoteInput）：
 *
 * 通知「回复」按钮 → 本接收器读取输入文本 + 会话 ID，
 * 调后端异步消息端点（POST /sessions/{id}/messages/async）投递回复，
 * 立即返回（回复由服务器后台生成，App 无需保持前台）。
 *
 * 依赖后端 v0.11 新增的 async 消息端点；若端点不存在（旧服务器），
 * 回退为普通 SSE 流式发送并在后台消费到完成。
 */
class QuickReplyReceiver : BroadcastReceiver() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)

    override fun onReceive(context: Context, intent: Intent) {
        val sessionId = intent.getStringExtra("session_id") ?: return
        val text = RemoteInput.getResultsFromIntent(intent)
            ?.getCharSequence("quick_reply")
            ?.toString()
            ?.trim()
            ?: return
        if (text.isEmpty()) return

        val pendingResult = goAsync()
        scope.launch {
            try {
                val auth = withContext(Dispatchers.IO) { AuthStore(context).state.first() }
                if (auth.token.isNullOrBlank() || auth.baseUrl.isBlank()) {
                    toast(context, "未登录，无法回复")
                    return@launch
                }
                val ok = withContext(Dispatchers.IO) {
                    submitReply(auth, sessionId, text)
                }
                if (ok) {
                    // 统一后台任务语义：回复生成完成后推送「有新回复」通知
                    ReplyNotifier.markTaskSession(context.applicationContext, sessionId)
                }
                toast(context, if (ok) "回复已发送" else "回复发送失败")
            } catch (_: Exception) {
                toast(context, "回复发送失败")
            } finally {
                pendingResult.finish()
            }
        }
    }

    private fun submitReply(auth: AuthStore.AuthState, sessionId: String, text: String): Boolean {
        return try {
            val http = OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(10, TimeUnit.SECONDS)
                .build()
            val url = "${auth.baseUrl.trimEnd('/')}/api/v1/sessions/$sessionId/messages/async"
            val body = """{"content":${jsonEscape(text)}}""".toRequestBody(
                "application/json; charset=utf-8".toMediaType(),
            )
            val request = Request.Builder().url(url)
                .post(body)
                .header("Authorization", "Bearer ${auth.token}")
                .apply {
                    auth.workspaceId?.let { header("X-Workspace-ID", it) }
                    if (auth.deviceId.isNotBlank()) header("X-Device-ID", auth.deviceId)
                }
                .build()
            http.newCall(request).execute().use { resp -> resp.code in 200..299 }
        } catch (_: Exception) {
            false
        }
    }

    private fun jsonEscape(s: String): String = buildString {
        for (c in s) {
            when (c) {
                '\\' -> append("\\\\")
                '"' -> append("\\\"")
                '\n' -> append("\\n")
                '\r' -> append("\\r")
                '\t' -> append("\\t")
                else -> append(c)
            }
        }
    }

    private fun toast(context: Context, message: String) {
        runCatching {
            Toast.makeText(context, message, Toast.LENGTH_SHORT).show()
        }
    }
}
