package com.learngraph.mobile.ui.chat

import com.learngraph.mobile.data.Message
import com.learngraph.mobile.data.SseEvent
import org.junit.Assert.*
import org.junit.Test

class NativeStreamReducerTest {
    private val seen = mutableSetOf<String>()
    private fun apply(messages: List<Message>, raw: String) = NativeStreamReducer.reduce("s", messages, SseEvent("message", null, raw), seen)

    @Test fun `answer boundary uses durable payload and reconciles optimistic assistant`() {
        val placeholder = Message(id = "local-assistant-1", sessionId = "s", role = "assistant", status = "streaming")
        val started = apply(listOf(placeholder), """{"type":"message.started","message_id":"answer"}""")
        assertEquals(listOf("answer"), started.messages.map { it.id })
        val boundary = apply(started.messages, """{"type":"answer.started","message_id":"answer","payload":{"final_part_id":"final","boundary_sequence":4,"thinking_duration_ms":1530}}""")
        assertEquals(NativeMessagePresentation("final", 4, 1530, true), boundary.presentation)
        val interrupted = apply(boundary.messages, """{"type":"message.interrupted","message_id":"answer","payload":{"status":"interrupted"}}""")
        assertEquals("interrupted", interrupted.messages.single().status)
    }

    @Test fun `durable envelope reconciles local user and streams answer without waiting for reload`() {
        val local = Message(id = "local-1", sessionId = "s", role = "user", content = "question")
        val started = apply(listOf(local), """{"event_id":"1","type":"message.started","message_id":"answer","payload":{"user_message_id":"user"}}""")
        assertEquals(listOf("user", "answer"), started.messages.map { it.id })
        val delta = """{"event_id":"2","type":"part.delta","message_id":"answer","part":{"id":"p","type":"text","content_delta":"你好","sequence":1}}"""
        val first = apply(started.messages, delta)
        assertEquals("你好", first.messages.last().parts.single().content)
        assertEquals(first.messages, apply(first.messages, delta).messages)
        val second = apply(first.messages, """{"event_id":"3","event":"message.part.delta","message_id":"answer","part":{"id":"p","type":"text","content_delta":"世界","sequence":1}}""")
        val completed = apply(second.messages, """{"event_id":"4","type":"part.completed","message_id":"answer","part":{"id":"p","type":"text","status":"completed"}}""")
        assertEquals("你好世界", completed.messages.last().parts.single().content)
        val terminal = apply(completed.messages, """{"event_id":"5","type":"message.completed","message_id":"answer","status":"completed"}""")
        assertEquals("completed", terminal.messages.last().status)
    }

    @Test fun `retry clears previous answer and failure exposes server error`() {
        val previous = Message(id = "answer", sessionId = "s", role = "assistant", content = "old")
        val started = apply(listOf(previous), """{"type":"message.started","message_id":"answer"}""")
        assertEquals("", started.messages.single().content)
        val failed = apply(started.messages, """{"type":"message.failed","message_id":"answer","payload":{"status":"failed","error":{"message":"budget exceeded"}}}""")
        assertEquals("failed", failed.messages.single().status)
        assertEquals("budget exceeded", failed.error)
    }

    @Test fun `replacement is authoritative and preserves part order`() {
        val later = apply(emptyList(), """{"type":"part.started","message_id":"a","part":{"id":"later","type":"text","content":"later","sequence":2}}""")
        val earlier = apply(later.messages, """{"type":"part.started","message_id":"a","part":{"id":"earlier","type":"text","content":"early","sequence":1}}""")
        val replaced = apply(earlier.messages, """{"type":"part.replaced","message_id":"a","part":{"id":"later","type":"text","content":"corrected","sequence":2}}""")
        assertEquals(listOf("early", "corrected"), replaced.messages.single().parts.map { it.content })
    }
}
