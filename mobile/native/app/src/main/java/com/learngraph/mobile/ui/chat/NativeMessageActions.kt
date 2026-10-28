package com.learngraph.mobile.ui.chat

import com.learngraph.mobile.data.ComposerOptions
import com.learngraph.mobile.data.Message
import com.learngraph.mobile.data.Session
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

@Serializable
data class NativeMessageVersion(
    val id: String,
    @SerialName("message_id") val messageId: String,
    val version: Int,
    val status: String,
    @SerialName("provider_trace") val providerTrace: JsonObject? = null,
)

data class NativeMessageVersionState(
    val versions: List<NativeMessageVersion> = emptyList(),
    val selectedVersionId: String? = null,
    val snapshot: Message? = null,
    val loading: Boolean = false,
    val loaded: Boolean = false,
    val error: String? = null,
)

data class NativeConversationVersions(val sessionIds: List<String>, val currentIndex: Int)
data class NativeOpenSession(val sessionId: String, val prompt: String?, val options: ComposerOptions)

internal fun Message.isPersistedMessage(): Boolean =
    id.isNotBlank() && !id.startsWith("local-") && !id.startsWith("temp") && id != "welcome-local"

/** Copy the same public text projection as AssistantMessage, without tool or reasoning data. */
internal fun messageCopyText(message: Message): String {
    if (message.role == "user") return message.content.ifBlank {
        message.parts.filter { it.type == "text" }.joinToString("\n") { it.content.orEmpty() }
    }
    val text = message.parts.filter { it.type in setOf("text", "acknowledgement") }
        .mapNotNull { it.content?.takeIf { value -> value.isNotBlank() && value.trim() != "正在思考" } }
        .joinToString("\n")
    return text.ifBlank { message.content }
}

/** Retry settings are one-shot; do not overwrite the session composer's saved preferences. */
internal fun nativeRetryBody(options: ComposerOptions, domainsText: String = ""): String {
    val optionsBody = Json.parseToJsonElement(options.copy(generationMode = "text").toBody("retry")) as JsonObject
    val domains = if (options.searchRoute == "disabled") emptyList() else domainsText.split(Regex("[\\s,，]+"))
        .map(String::trim).filter(String::isNotEmpty).distinct()
    require(domains.size <= 50) { "搜索来源最多 50 个域名" }
    require(domains.all { it.length <= 253 && !it.contains('/') && !it.contains(':') }) { "请填写域名，例如 react.dev，不含网址前缀或路径" }
    return buildJsonObject {
        optionsBody.filterKeys { it != "content" }.forEach { (key, value) -> put(key, value) }
        put("allowed_domains", JsonArray(if (options.searchRoute == "disabled") emptyList() else domains.map(::JsonPrimitive)))
    }.toString()
}

/** A user-message version is a sibling edited branch, not an assistant snapshot. */
internal fun conversationVersions(messages: List<Message>, current: Session, sessions: List<Session>, hasMoreBefore: Boolean = false): Map<String, NativeConversationVersions> {
    val firstLocal = messages.firstOrNull { it.role == "user" && it.sessionId == current.id }?.id
    return messages.filter { it.role == "user" }.mapNotNull { message ->
        val editedRoot = !hasMoreBefore && current.parentSessionId != null && current.sourceMessageId != null && message.id == firstLocal
        val sourceSession = if (editedRoot) current.parentSessionId else current.id.takeIf { message.sessionId == it }
        val sourceMessage = if (editedRoot) current.sourceMessageId else message.id.takeIf { message.sessionId == current.id }
        if (sourceSession == null || sourceMessage == null) return@mapNotNull null
        val siblings = sessions.filter { it.parentSessionId == sourceSession && it.sourceMessageId == sourceMessage }
            .sortedWith(compareBy({ it.createdAt.orEmpty() }, { it.id })).map { it.id }
        val ids = listOf(sourceSession) + siblings
        val index = ids.indexOf(current.id)
        if (ids.size <= 1 || index < 0) null else message.id to NativeConversationVersions(ids, index)
    }.toMap()
}

internal fun JsonObject?.traceString(key: String): String? = (this?.get(key) as? JsonPrimitive)?.contentOrNull
