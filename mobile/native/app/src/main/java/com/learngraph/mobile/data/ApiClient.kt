package com.learngraph.mobile.data

import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import okhttp3.Call
import okhttp3.Callback
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.sse.EventSource
import okhttp3.sse.EventSourceListener
import okhttp3.sse.EventSources
import java.io.IOException
import java.util.concurrent.TimeUnit

/**
 * OkHttp 封装：REST JSON + SSE 流式。
 * 认证头：Authorization: Bearer、X-Workspace-ID、X-Device-ID（与网页版一致）。
 */
class ApiClient(
    private val json: Json,
) {

    var baseUrl: String = ""
    var token: String? = null
    var workspaceId: String? = null
    var deviceId: String = ""

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS) // SSE 长连接
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()

    private fun url(path: String): String {
        val base = baseUrl.trimEnd('/')
        return if (path.startsWith("http")) path else "$base/api/v1$path"
    }

    private fun Request.Builder.auth(auth: Boolean, workspace: Boolean): Request.Builder {
        if (auth) token?.let { header("Authorization", "Bearer $it") }
        if (workspace) workspaceId?.let { header("X-Workspace-ID", it) }
        if (deviceId.isNotBlank()) header("X-Device-ID", deviceId)
        return this
    }

    private fun bodyOf(content: String): okhttp3.RequestBody =
        content.toRequestBody("application/json; charset=utf-8".toMediaType())

    // ------------------------------------------------------------------ //
    // REST
    // ------------------------------------------------------------------ //

    suspend fun get(path: String, auth: Boolean = true, workspace: Boolean = true): String =
        execute(Request.Builder().url(url(path)).get().auth(auth, workspace).build())

    suspend fun post(path: String, body: String? = null, auth: Boolean = true, workspace: Boolean = true): String =
        execute(
            Request.Builder().url(url(path))
                .post(body?.let { bodyOf(it) } ?: ByteArray(0).toRequestBody(null))
                .auth(auth, workspace)
                .build(),
        )

    suspend fun delete(path: String, auth: Boolean = true, workspace: Boolean = true): String =
        execute(Request.Builder().url(url(path)).delete().auth(auth, workspace).build())

    private suspend fun execute(request: Request): String = suspendCancellableCoroutine { cont ->
        http.newCall(request).enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (cont.isCancelled) return
                cont.resumeWith(Result.failure(ApiException("网络错误：${e.message}", null)))
            }

            override fun onResponse(call: Call, response: Response) {
                val code = response.code
                val text = response.body?.string().orEmpty()
                response.close()
                if (code in 200..299) {
                    cont.resumeWith(Result.success(text))
                } else if (code == 401) {
                    cont.resumeWith(Result.failure(ApiException("登录已失效，请重新登录", 401)))
                } else {
                    cont.resumeWith(Result.failure(ApiException(extractError(text) ?: "请求失败（HTTP $code）", code)))
                }
            }
        })
    }

    private fun extractError(body: String): String? = try {
        val obj = json.parseToJsonElement(body).jsonObject
        obj["detail"]?.let { if (it is kotlinx.serialization.json.JsonPrimitive) it.content else it.toString() }
    } catch (_: Exception) {
        null
    }

    // ------------------------------------------------------------------ //
    // SSE（POST 流式；与网页版 parseSseResponse 语义一致）
    // ------------------------------------------------------------------ //

    private fun postSseRaw(
        path: String,
        body: String,
        onEvent: (event: String, id: String?, rawData: String) -> Unit,
        onError: (Throwable) -> Unit,
        onClosed: () -> Unit,
    ): EventSource {
        val request = Request.Builder().url(url(path))
            .post(bodyOf(body))
            .auth(true, true)
            .header("Accept", "text/event-stream")
            .build()

        return EventSources.createFactory(http).newEventSource(request, object : EventSourceListener() {
            override fun onEvent(eventSource: EventSource, id: String?, type: String?, data: String) {
                onEvent(type ?: "message", id, data)
            }

            override fun onFailure(eventSource: EventSource, t: Throwable?, response: Response?) {
                onError(t ?: ApiException("SSE 连接中断", response?.code))
            }

            override fun onClosed(eventSource: EventSource) {
                onClosed()
            }
        })
    }

    /** 流式发送（协程友好）：事件经 Flow 发射，取消时断开连接 */
    fun streamSse(path: String, body: String): Flow<SseEvent> = callbackFlow {
        val source = postSseRaw(
            path = path,
            body = body,
            onEvent = { event, id, rawData -> trySend(SseEvent(event, id, rawData)) },
            onError = { e -> close(e) },
            onClosed = { close() },
        )
        awaitClose { source.cancel() }
    }
}

data class SseEvent(val event: String, val id: String?, val rawData: String)

class ApiException(message: String, val code: Int?) : Exception(message)
