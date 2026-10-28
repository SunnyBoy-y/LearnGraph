package com.learngraph.mobile.ui.chat

import com.learngraph.mobile.data.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class NativeTimelineSyncTest {
    private val json = Json { ignoreUnknownKeys = true }
    private fun snapshot() = json.parseToJsonElement("""{"id":"a","session_id":"s","role":"assistant","status":"streaming","message_version_id":"version-1","last_event_id":"e10","last_sequence":10,"parts":[{"id":"p","type":"text","content":"already"}]}""").jsonObject
    @Test fun snapshotCursorSkipsAlreadyAppliedDeltasAndLocksTheRetryVersion() {
        val cursor = NativeRemoteEventCursor(snapshot())
        fun event(id: String, seq: Int, version: String = "version-1") = json.parseToJsonElement("""{"event_id":"$id","sequence":$seq,"message_id":"a","message_version_id":"$version","type":"part.delta","part":{"id":"p","type":"text","content_delta":" new"}}""").jsonObject
        assertNull(cursor.accept(event("e10", 10)))
        assertNull(cursor.accept(event("foreign", 11, "version-2")))
        val next = cursor.accept(event("e11", 11))!!
        val update = NativeStreamReducer.reduce("s", listOf(cursor.message(snapshot())), next, mutableSetOf())
        assertEquals("already new", update.messages.single().parts.single().content)
        assertNull(cursor.accept(event("e11", 11)))
        assertTrue(cursor.eventsPath("s", "a").contains("after_event_id=e11"))
        assertTrue(cursor.eventsPath("s", "a").contains("message_version_id=version-1"))
    }
    @Test fun quietReplayBackoffRemainsBoundedAndNewOutputRestoresFastDelivery() {
        assertEquals(800L, NativeRemoteEventCursor.nextDelay(400, false))
        assertEquals(4_000L, NativeRemoteEventCursor.nextDelay(4_000, false))
        assertEquals(400L, NativeRemoteEventCursor.nextDelay(4_000, true))
        assertFalse("interrupted" in NativeRemoteEventCursor.inFlight)
    }
    @Test fun returningAfterRemoteCompletionResumesQueuedDraftsButNeverOverridesStopOrAnotherStream() {
        val completed = listOf(Message("a", sessionId = "s", role = "assistant", status = "completed"))
        fun canResume(foreground: Boolean = true, sending: Boolean = false, stopped: Boolean = false,
            error: Boolean = false, closed: Boolean = false, busy: Boolean = false, messages: List<Message> = completed) =
            canResumeQueuedAfterSync(foreground, sending, stopped, error, closed, busy, messages)
        assertTrue(canResume())
        assertFalse(canResume(foreground = false)); assertFalse(canResume(sending = true))
        assertFalse(canResume(stopped = true)); assertFalse(canResume(error = true))
        assertFalse(canResume(closed = true)); assertFalse(canResume(busy = true))
        assertFalse(canResume(messages = listOf(completed.single().copy(status = "streaming"))))
    }
    @Test fun loadedOlderPagesRevalidateByEtagAndReplaceEditsAndDeletedRows() = runBlocking {
        fun message(id: String, text: String = id) = Message(id = id, sessionId = "s", role = "assistant", content = text)
        var requests = 0
        var validator: String? = null
        val reader = NativeTimelineWindowReader { path, etag ->
            assertTrue(path.contains("compact=false")); assertTrue(path.contains("before_id=m51"))
            validator = etag
            requests++
            if (requests == 1) ConditionalResponse("""{"items":[{"id":"m49","session_id":"s","role":"assistant","content":"edited"}],"has_more_before":false}""", "older-v2")
            else ConditionalResponse(null, "older-v2", true)
        }
        val latest = MessageListPage((51..100).map { message("m$it") }, true)
        val previous = (1..100).map { message("m$it") }
        val updated = reader.readOlder("s", latest, previous)
        assertEquals(51, updated.items.size)
        assertEquals("edited", updated.items.first().content)
        assertFalse(updated.items.any { it.id == "m1" })
        assertFalse(updated.hasMoreBefore)
        assertEquals(updated, reader.readOlder("s", latest, previous))
        assertEquals("older-v2", validator)
    }
}
