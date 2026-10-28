package com.learngraph.mobile.data

import android.util.Log
import com.learngraph.mobile.BuildConfig

import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.buffer
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
        set(value) {
            // A newly assigned bearer must never inherit the previous account's cache identity.
            if (field != value) accountId = null
            field = value
        }
    var workspaceId: String? = null
    var deviceId: String = ""
    /** Stable server user ID (or username). Used only for private cache isolation. */
    var accountId: String? = null

    private val http = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS) // SSE 长连接
        .writeTimeout(30, TimeUnit.SECONDS)
        .build()
    private val restHttp = http.newBuilder().readTimeout(30, TimeUnit.SECONDS).build()

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

    /** Same-origin shell metadata; no account/workspace headers are needed. */
    suspend fun getWebClientVersion(): ConditionalResponse =
        executeResponse(
            Request.Builder()
                .url(url("/web-client/version"))
                .get()
                .header("Cache-Control", "no-cache")
                .build(),
            allowNotFound = true,
        )

    /** Revalidate private data without downloading an unchanged JSON body. */
    suspend fun getConditional(
        path: String,
        etag: String? = null,
        auth: Boolean = true,
        workspace: Boolean = true,
    ): ConditionalResponse {
        val started = System.nanoTime()
        val builder = Request.Builder().url(url(path)).get().auth(auth, workspace)
        etag?.takeIf { it.isNotBlank() }?.let { builder.header("If-None-Match", it) }
        val response = executeResponse(builder.build())
        if (BuildConfig.DEBUG && path.substringBefore('?').matches(Regex("/sessions(?:/[^/]+/messages)?"))) {
            val bytes = if (response.notModified) 0 else response.body?.toByteArray(Charsets.UTF_8)?.size ?: 0
            runCatching { Log.d("LGSyncMetrics", "kind=timeline status=${response.status} responseBytes=$bytes elapsedMs=${(System.nanoTime() - started) / 1_000_000} conditional=${!etag.isNullOrBlank()}") }
        }
        return response
    }

    suspend fun post(path: String, body: String? = null, auth: Boolean = true, workspace: Boolean = true): String =
        execute(
            Request.Builder().url(url(path))
                .post(body?.let { bodyOf(it) } ?: ByteArray(0).toRequestBody(null))
                .auth(auth, workspace)
                .build(),
        )

    suspend fun delete(path: String, auth: Boolean = true, workspace: Boolean = true): String =
        execute(Request.Builder().url(url(path)).delete().auth(auth, workspace).build())

    suspend fun patch(path: String, body: String): String =
        execute(Request.Builder().url(url(path)).patch(bodyOf(body)).auth(true, true).build())

    suspend fun put(path: String, body: String): String =
        execute(Request.Builder().url(url(path)).put(bodyOf(body)).auth(true, true).build())

    private suspend fun execute(request: Request): String = executeResponse(request).body.orEmpty()

    private suspend fun executeResponse(
        request: Request,
        allowNotFound: Boolean = false,
    ): ConditionalResponse = suspendCancellableCoroutine { cont ->
        val call = restHttp.newCall(request)
        cont.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (cont.isCancelled) return
                cont.resumeWith(Result.failure(ApiException("网络错误：${e.message}", null)))
            }

            override fun onResponse(call: Call, response: Response) {
                response.use {
                    if (cont.isCancelled) return
                    try {
                        val code = it.code
                        val text = it.body?.string().orEmpty()
                        when {
                            code in 200..299 -> cont.resumeWith(Result.success(ConditionalResponse(text, it.header("ETag"), status = code)))
                            code == 304 -> cont.resumeWith(Result.success(ConditionalResponse(null, it.header("ETag"), true)))
                            code == 404 && allowNotFound -> cont.resumeWith(Result.success(ConditionalResponse("", it.header("ETag"), status = code)))
                            code == 401 -> cont.resumeWith(Result.failure(ApiException("登录已失效，请重新登录", 401)))
                            else -> cont.resumeWith(Result.failure(ApiException(extractError(text) ?: "请求失败（HTTP $code）", code)))
                        }
                    } catch (e: IOException) {
                        if (!cont.isCancelled) cont.resumeWith(Result.failure(ApiException("网络错误：${e.message}", null)))
                    }
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
                val code = response?.code?.takeIf { it >= 400 }
                val errorBody = try { response?.body?.string().orEmpty() } catch (_: IOException) { "" }
                onError(when {
                    code == 401 -> ApiException("登录已失效，请重新登录", 401)
                    code != null -> ApiException(extractError(errorBody) ?: "请求失败（HTTP $code）", code)
                    t is ApiException -> t
                    else -> ApiException(t?.message ?: "SSE 连接中断", null)
                })
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
    }.buffer(Channel.UNLIMITED)
}

data class ConditionalResponse(val body: String?, val etag: String?, val notModified: Boolean = false, val status: Int = if (notModified) 304 else 200)

data class SseEvent(val event: String, val id: String?, val rawData: String)

class ApiException(message: String, val code: Int?) : Exception(message)
