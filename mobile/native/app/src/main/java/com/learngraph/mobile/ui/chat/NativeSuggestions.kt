package com.learngraph.mobile.ui.chat

import com.learngraph.mobile.data.Message
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull

/** Same latest-assistant/voice/version gate as chat-pages; never anchor a historical selection. */
internal fun nativeSuggestionAnchor(messages: List<Message>): Message? {
    val latest = messages.lastOrNull { it.role == "assistant" } ?: return null
    val trace = latest.providerTrace
    if ((trace?.get("voice") as? JsonPrimitive)?.takeUnless { it.isString }?.booleanOrNull == true ||
        (trace?.get("voice_turn") as? JsonPrimitive)?.takeUnless { it.isString }?.booleanOrNull == true) return null
    return latest.takeIf { it.status == "completed" && it.isPersistedMessage() && (it.version ?: 0) > 0 }
}

internal fun completedSuggestionFingerprint(messages: List<Message>): String = messages
    .filter { it.status == "completed" }.joinToString("|") { "${it.id}:${it.version}:${it.status}" }
