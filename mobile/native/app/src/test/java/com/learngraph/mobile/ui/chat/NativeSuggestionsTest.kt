package com.learngraph.mobile.ui.chat

import com.learngraph.mobile.data.Message
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.*
import org.junit.Test

class NativeSuggestionsTest {
    private fun message(id: String, version: Int = 1, status: String = "completed", role: String = "assistant") =
        Message(id = id, sessionId = "source-session", role = role, version = version, status = status)
    @Test fun onlyTheLatestCompletedTextAssistantCanAnchorFollowUps() {
        val old = message("old")
        assertEquals("new", nativeSuggestionAnchor(listOf(old, message("user", role = "user"), message("new")))?.id)
        assertNull(nativeSuggestionAnchor(listOf(old, message("stream", status = "streaming"))))
        assertNull(nativeSuggestionAnchor(listOf(old, message("failed", status = "failed"))))
        assertNull(nativeSuggestionAnchor(emptyList()))
        assertNull(nativeSuggestionAnchor(listOf(message("local-assistant-a"))))
    }
    @Test fun durableAndOptimisticVoiceTracesNeverBecomeTextSuggestionAnchors() {
        listOf("voice", "voice_turn").forEach { key ->
            assertNull(nativeSuggestionAnchor(listOf(message("old"), message("voice").copy(providerTrace = Json.parseToJsonElement("""{"$key":true}""").jsonObject))))
        }
        assertNotNull(nativeSuggestionAnchor(listOf(message("text").copy(providerTrace = Json.parseToJsonElement("""{"voice":"true"}""").jsonObject))))
    }
    @Test fun anchorKeepsSourceSessionAndCurrentVersionAndIgnoresIncompleteFingerprintRows() {
        val messages = listOf(message("u", role = "user"), message("a", version = 2), message("pending", status = "pending", role = "user"))
        val anchor = nativeSuggestionAnchor(messages)!!
        assertEquals("source-session", anchor.sessionId)
        assertEquals(2, anchor.version)
        assertEquals("u:1:completed|a:2:completed", completedSuggestionFingerprint(messages))
    }
}
