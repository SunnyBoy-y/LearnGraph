package com.learngraph.mobile.ui.chat

import com.learngraph.mobile.data.ComposerOptions
import com.learngraph.mobile.data.Message
import com.learngraph.mobile.data.MessagePart
import com.learngraph.mobile.data.Session
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class NativeMessageActionsTest {
    @Test fun `copy projects visible answer text without reasoning tools or placeholder`() {
        val assistant = Message("a", sessionId = "s", role = "assistant", parts = listOf(
            MessagePart("ack", "acknowledgement", content = "正在思考"),
            MessagePart("r", "reasoning_content", content = "private reasoning"),
            MessagePart("tool", "tool_call", content = "private tool payload"),
            MessagePart("body", "text", content = "**正文**"),
        ))
        assertEquals("**正文**", messageCopyText(assistant))
        assertEquals("原始提问", messageCopyText(assistant.copy(role = "user", content = "原始提问")))
        assertEquals("旧版内容", messageCopyText(assistant.copy(parts = emptyList(), content = "旧版内容")))
    }

    @Test fun `retry uses current one-shot options and backend fields only`() {
        val options = ComposerOptions("fast", "high", "auto", "provider", "model")
        val body = Json.parseToJsonElement(nativeRetryBody(options, "react.dev, python.org react.dev，kotlinlang.org")) as JsonObject
        assertFalse(body.containsKey("content"))
        assertFalse(body.containsKey("response_mode"))
        assertEquals("false", body["agent_mode"]?.jsonPrimitive?.content)
        assertEquals("off", body["thinking_mode"]?.jsonPrimitive?.content)
        assertEquals("provider", body["provider_id"]?.jsonPrimitive?.content)
        assertEquals(listOf("react.dev", "python.org", "kotlinlang.org"), body["allowed_domains"]?.jsonArray?.map { it.jsonPrimitive.content })
        assertEquals("high", options.thinkingMode) // Existing preferences remain untouched.
    }

    @Test fun `search off clears domain restrictions and rejects oversized domain lists`() {
        val body = Json.parseToJsonElement(nativeRetryBody(ComposerOptions(searchRoute = "disabled"), "https://ignored.example/path")) as JsonObject
        assertEquals(0, body["allowed_domains"]?.jsonArray?.size)
        assertThrows(IllegalArgumentException::class.java) { nativeRetryBody(ComposerOptions(), "https://example.com/path") }
        assertThrows(IllegalArgumentException::class.java) { nativeRetryBody(ComposerOptions(), (0..50).joinToString(",") { "d$it.com" }) }
    }

    @Test fun `user version navigation follows source branch and edited root without mixing unrelated turns`() {
        val source = Session("source", "w", "原会话")
        val branch1 = Session("branch1", "w", "分支1", parentSessionId = "source", sourceMessageId = "u", createdAt = "2026-10-18T01:00:00Z")
        val branch2 = Session("branch2", "w", "分支2", parentSessionId = "source", sourceMessageId = "u", createdAt = "2026-10-18T02:00:00Z")
        val unrelated = Session("other", "w", "其他", parentSessionId = "source", sourceMessageId = "u-other")
        val sessions = listOf(branch2, unrelated, source, branch1)
        val original = Message("u", sessionId = "source", role = "user")
        assertEquals(NativeConversationVersions(listOf("source", "branch1", "branch2"), 0), conversationVersions(listOf(original), source, sessions)["u"])
        val edited = Message("u-edited", sessionId = "branch2", role = "user")
        val inherited = original.copy(id = "earlier")
        assertEquals(mapOf("u-edited" to NativeConversationVersions(listOf("source", "branch1", "branch2"), 2)), conversationVersions(listOf(inherited, edited), branch2, sessions))
        assertTrue(conversationVersions(listOf(edited), branch2, sessions, hasMoreBefore = true).isEmpty())
    }

    @Test fun `local optimistic rows cannot create durable version actions`() {
        val message = Message("local-123", sessionId = "s", role = "user")
        assertFalse(message.isPersistedMessage())
        assertFalse(message.copy(id = "temp-answer").isPersistedMessage())
        assertTrue(message.copy(id = "server-id").isPersistedMessage())
    }
}
