package com.learngraph.mobile.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.learngraph.mobile.data.ApiClient
import com.learngraph.mobile.data.Message
import com.learngraph.mobile.data.MessageListPage
import com.learngraph.mobile.data.MessagePart
import com.learngraph.mobile.data.SseEvent
import com.learngraph.mobile.data.StreamEvent
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json

/**
 * 聊天页：消息列表 + SSE 流式生成。
 *
 * 流式策略（与网页版语义一致）：
 *  - part.started / part.replaced → 按 part.id upsert
 *  - part.delta → content += content_delta
 *  - part.completed / part.failed → 更新状态
 *  - message.completed → 重新拉取最终列表（替换本地组装）
 */
class ChatViewModel(
    private val api: ApiClient,
    private val sessionId: String,
) : ViewModel() {

    private val _messages = MutableStateFlow<List<Message>>(emptyList())
    val messages: StateFlow<List<Message>> = _messages

    private val _loading = MutableStateFlow(true)
    val loading: StateFlow<Boolean> = _loading

    private val _sending = MutableStateFlow(false)
    val sending: StateFlow<Boolean> = _sending

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error

    private val json = Json { ignoreUnknownKeys = true }
    private var streamJob: Job? = null

    init {
        loadMessages()
    }

    fun loadMessages() {
        viewModelScope.launch {
            _loading.value = true
            _error.value = null
            try {
                val raw = api.get("/sessions/$sessionId/messages?limit=50")
                val page = json.decodeFromString<MessageListPage>(raw)
                _messages.value = page.items
            } catch (e: Exception) {
                _error.value = e.message ?: "加载失败"
            } finally {
                _loading.value = false
            }
        }
    }

    fun send(content: String) {
        if (content.isBlank() || _sending.value) return
        _sending.value = true
        _error.value = null
        val ts = System.currentTimeMillis()

        // 乐观插入用户消息
        val userMsg = Message(
            id = "local-$ts",
            sessionId = sessionId,
            role = "user",
            status = "completed",
            content = content,
            parts = listOf(
                MessagePart(id = "local-$ts-p", type = "text", status = "completed", content = content),
            ),
        )
        _messages.update { it + userMsg }

        streamJob = viewModelScope.launch {
            val body = """{"content":${jsonStr(content)}}"""
            try {
                api.streamSse("/sessions/$sessionId/messages/stream", body).collect { sse ->
                    handleEvent(sse)
                }
            } catch (e: Exception) {
                _error.value = e.message ?: "发送失败"
            } finally {
                _sending.value = false
            }
        }
    }

    fun retry(messageId: String) {
        if (_sending.value) return
        _sending.value = true
        _error.value = null
        streamJob = viewModelScope.launch {
            val body = "{}"
            try {
                api.streamSse("/sessions/$sessionId/messages/$messageId/retry", body).collect { sse ->
                    handleEvent(sse)
                }
            } catch (e: Exception) {
                _error.value = e.message ?: "重试失败"
            } finally {
                _sending.value = false
            }
        }
    }

    fun stop() {
        streamJob?.cancel()
        streamJob = null
        _sending.value = false
        loadMessages()
    }

    // ------------------------------------------------------------------ //
    // 流式事件处理
    // ------------------------------------------------------------------ //

    private fun handleEvent(sse: SseEvent) {
        val evt = try {
            json.decodeFromString<StreamEvent>(sse.rawData)
        } catch (_: Exception) {
            return
        }
        when (evt.type ?: evt.event) {
            "part.started", "part.replaced" -> evt.part?.let { upsertPart(evt.messageId, it) }
            "part.delta" -> evt.part?.let { appendDelta(evt.messageId, it) }
            "part.completed" -> evt.part?.let { setPartStatus(evt.messageId, it) }
            "part.failed" -> evt.part?.let { setPartStatus(evt.messageId, it) }
            "message.completed" -> loadMessages()
        }
    }

    /** 按 messageId 找消息，没有则创建 assistant 占位；按 part.id upsert part */
    private fun upsertPart(messageId: String?, part: MessagePart) {
        val mid = messageId ?: return
        _messages.update { list ->
            val idx = list.indexOfFirst { it.id == mid }
            val msg = if (idx >= 0) {
                list[idx]
            } else {
                Message(
                    id = mid,
                    sessionId = sessionId,
                    role = "assistant",
                    status = "streaming",
                    content = "",
                    parts = emptyList(),
                )
            }
            val parts = msg.parts.toMutableList()
            val pi = parts.indexOfFirst { it.id == part.id }
            if (pi >= 0) {
                parts[pi] = part
            } else {
                parts.add(part)
            }
            parts.sortBy { it.sequence ?: Int.MAX_VALUE }
            val updated = msg.copy(parts = parts, status = "streaming")
            if (idx >= 0) list.toMutableList().also { it[idx] = updated } else list + updated
        }
    }

    private fun appendDelta(messageId: String?, part: MessagePart) {
        val mid = messageId ?: return
        _messages.update { list ->
            val idx = list.indexOfFirst { it.id == mid }
            if (idx < 0) return@update list
            val msg = list[idx]
            val parts = msg.parts.toMutableList()
            val pi = parts.indexOfFirst { it.id == part.id }
            if (pi < 0) return@update list
            val old = parts[pi]
            val delta = part.contentDelta ?: return@update list
            parts[pi] = old.copy(
                content = (old.content ?: "") + delta,
                status = "streaming",
            )
            list.toMutableList().also { it[idx] = msg.copy(parts = parts, status = "streaming") }
        }
    }

    private fun setPartStatus(messageId: String?, part: MessagePart) {
        val mid = messageId ?: return
        _messages.update { list ->
            val idx = list.indexOfFirst { it.id == mid }
            if (idx < 0) return@update list
            val msg = list[idx]
            val parts = msg.parts.toMutableList()
            val pi = parts.indexOfFirst { it.id == part.id }
            if (pi < 0) return@update list
            parts[pi] = part
            list.toMutableList().also { it[idx] = msg.copy(parts = parts) }
        }
    }

    private fun jsonStr(s: String): String =
        "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\""
}
