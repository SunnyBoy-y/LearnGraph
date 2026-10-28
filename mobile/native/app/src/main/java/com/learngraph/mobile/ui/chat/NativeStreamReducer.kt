package com.learngraph.mobile.ui.chat

import com.learngraph.mobile.data.Message
import com.learngraph.mobile.data.SseEvent
import com.learngraph.mobile.data.StreamEvent
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull

data class NativeMessagePresentation(
    val finalPartId: String? = null,
    val boundarySequence: Int? = null,
    val thinkingDurationMs: Long? = null,
    val answerStarted: Boolean = false,
)

/** Pure reducer for durable event envelopes and legacy SSE names. */
internal object NativeStreamReducer {
    private val json = Json { ignoreUnknownKeys = true }
    data class Update(val messages: List<Message>, val assistantId: String? = null, val error: String? = null,
        val presentation: NativeMessagePresentation? = null)

    fun reduce(sessionId: String, messages: List<Message>, sse: SseEvent, seen: MutableSet<String>): Update {
        val envelope = runCatching { json.parseToJsonElement(sse.rawData) as? JsonObject }.getOrNull()
            ?: return Update(messages)
        val event = runCatching { json.decodeFromString<StreamEvent>(sse.rawData) }.getOrNull()
            ?: return Update(messages)
        val eventId = event.eventId ?: sse.id
        if (eventId != null && !seen.add(eventId)) return Update(messages)
        val type = event.type ?: event.event ?: sse.event
        val payload = envelope["payload"] as? JsonObject
        val mid = event.messageId
        val userId = payload?.get("user_message_id")?.jsonPrimitive?.contentOrNull
        var list = messages
        if (userId != null) {
            val pending = list.indexOfLast { it.role == "user" && it.id.startsWith("local-") }
            if (pending >= 0) list = list.toMutableList().also {
                it[pending] = it[pending].copy(id = userId, status = "completed")
            }
        }
        val error = (payload?.get("error") as? JsonObject)?.get("message")?.jsonPrimitive?.contentOrNull
        if (mid == null) return Update(list, error = error)
        val placeholder = list.indexOfLast { it.role == "assistant" && it.id.startsWith("local-assistant-") }
        if (placeholder >= 0 && list.none { it.id == mid }) {
            list = list.toMutableList().also { it[placeholder] = it[placeholder].copy(id = mid) }
        }
        val idx = list.indexOfFirst { it.id == mid }
        var message = list.getOrNull(idx) ?: Message(id = mid, sessionId = sessionId, role = "assistant", status = "streaming")
        when (type) {
            "answer.started" -> return Update(list, mid, error, NativeMessagePresentation(
                finalPartId = event.finalPartId ?: payload?.get("final_part_id")?.jsonPrimitive?.contentOrNull,
                boundarySequence = event.boundarySequence ?: payload?.get("boundary_sequence")?.jsonPrimitive?.intOrNull,
                thinkingDurationMs = event.thinkingDurationMs ?: payload?.get("thinking_duration_ms")?.jsonPrimitive?.longOrNull,
                answerStarted = true,
            ))
            "message.started" -> message = message.copy(status = "streaming", content = "", parts = emptyList(), providerTrace = null)
            "part.started", "part.replaced", "part.completed", "part.failed", "part.delta", "message.part.delta" -> {
                val part = event.part ?: return Update(list, mid)
                val parts = message.parts.toMutableList()
                val pi = parts.indexOfFirst { it.id == part.id }
                val previous = parts.getOrNull(pi)
                val next = if (type in listOf("part.delta", "message.part.delta") && part.contentDelta != null) {
                    (previous ?: part).copy(content = (previous?.content ?: "") + part.contentDelta, status = part.status ?: "streaming")
                } else part.copy(content = part.content ?: previous?.content, data = part.data ?: previous?.data)
                if (pi < 0) parts.add(next) else parts[pi] = next
                message = message.copy(parts = parts.sortedBy { it.sequence ?: Int.MAX_VALUE })
            }
            "message.completed", "message.failed", "message.cancelled", "message.interrupted" -> {
                val terminal = event.status ?: payload?.get("status")?.jsonPrimitive?.contentOrNull ?: type.removePrefix("message.")
                message = message.copy(status = terminal, providerTrace = envelope["provider_trace"] as? JsonObject ?: message.providerTrace, parts = message.parts.map {
                    if (it.status in listOf("pending", "streaming")) it.copy(status = terminal) else it
                })
            }
            else -> return Update(list, mid, error)
        }
        list = if (idx < 0) list + message else list.toMutableList().also { it[idx] = message }
        return Update(list, mid, error)
    }
}
