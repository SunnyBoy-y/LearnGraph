package com.learngraph.mobile.ui.chat

import com.learngraph.mobile.data.*
import kotlinx.serialization.json.*
import okhttp3.HttpUrl.Companion.toHttpUrl

internal class NativeTimelineCursorMoved : Exception("会话刚有更新，正在重新同步")

internal fun canResumeQueuedAfterSync(foreground: Boolean, sending: Boolean, pausedByStop: Boolean,
    hasError: Boolean, sessionClosed: Boolean, messageOperation: Boolean, messages: List<Message>): Boolean =
    foreground && !sending && !pausedByStop && !hasError && !sessionClosed && !messageOperation &&
        messages.none { it.role == "assistant" && it.status in NativeRemoteEventCursor.inFlight }

/** Only already opened pages are checked; unchanged pages transfer no body. */
internal class NativeTimelineWindowReader(private val read: suspend (String, String?) -> ConditionalResponse) {
    private data class Cached(val etag: String?, val page: MessageListPage)
    private val pages = linkedMapOf<String, Cached>()
    private val json = Json { ignoreUnknownKeys = true }

    suspend fun readOlder(sessionId: String, newest: MessageListPage, previous: List<Message>): MessageListPage {
        var last = newest
        var items = newest.items
        val known = previous.map { it.id }.toSet()
        val target = maxOf(50, previous.size + items.count { it.id !in known })
        while (items.size < target && last.hasMoreBefore && items.isNotEmpty()) {
            val path = "https://timeline.invalid".toHttpUrl().newBuilder().addPathSegment("sessions").addPathSegment(sessionId)
                .addPathSegment("messages").addQueryParameter("limit", "50").addQueryParameter("compact", "false")
                .addQueryParameter("before_id", items.first().id).build().let { it.encodedPath + "?" + it.encodedQuery }
            val cached = pages[path]
            val response = try { read(path, cached?.etag) }
            catch (e: ApiException) { if (e.code == 404) throw NativeTimelineCursorMoved() else throw e }
            val older = if (response.notModified && cached != null) cached.page else
                json.decodeFromString<MessageListPage>(response.body ?: throw IllegalStateException("同步页面内容缺失"))
            pages[path] = Cached(response.etag ?: cached?.etag, older)
            if (pages.size > 64) pages.remove(pages.keys.first())
            val ids = items.map { it.id }.toSet()
            val added = older.items.filter { it.id !in ids }
            last = older
            if (added.isEmpty()) break
            items = added + items
        }
        return last.copy(items = items, oldestId = items.firstOrNull()?.id, newestId = items.lastOrNull()?.id)
    }
}

/** A snapshot's durable cursor prevents replaying deltas that are already in its parts. */
internal class NativeRemoteEventCursor(snapshot: JsonObject) {
    private val json = Json { ignoreUnknownKeys = true }
    val versionId = snapshot["message_version_id"]?.jsonPrimitive?.contentOrNull
    var afterEventId: String? = snapshot["last_event_id"]?.jsonPrimitive?.contentOrNull
        private set
    private var sequence = snapshot["last_sequence"]?.jsonPrimitive?.longOrNull ?: 0
    private val seen = mutableSetOf<String>()
    fun message(snapshot: JsonObject): Message = json.decodeFromJsonElement(snapshot)
    fun accept(event: JsonObject): SseEvent? {
        val version = event["message_version_id"]?.jsonPrimitive?.contentOrNull
        if (versionId != null && version != null && versionId != version) return null
        val nextSequence = event["sequence"]?.jsonPrimitive?.longOrNull ?: 0
        val id = event["event_id"]?.jsonPrimitive?.contentOrNull
        if (nextSequence > 0) { if (nextSequence <= sequence) return null; sequence = nextSequence }
        else if (id != null && !seen.add(id)) return null
        if (id != null) afterEventId = id
        return SseEvent(event["type"]?.jsonPrimitive?.contentOrNull ?: "message", id, event.toString())
    }
    fun eventsPath(sessionId: String, messageId: String): String =
        "https://timeline.invalid".toHttpUrl().newBuilder().addPathSegment("sessions").addPathSegment(sessionId)
            .addPathSegment("messages").addPathSegment(messageId).addPathSegment("events")
            .apply { afterEventId?.let { addQueryParameter("after_event_id", it) }; versionId?.let { addQueryParameter("message_version_id", it) } }
            .build().let { it.encodedPath + if (it.encodedQuery != null) "?" + it.encodedQuery else "" }
    companion object {
        val inFlight = setOf("pending", "submitted", "streaming")
        fun nextDelay(current: Long, advanced: Boolean): Long = if (advanced) 400 else (current * 2).coerceAtMost(4_000)
    }
}
