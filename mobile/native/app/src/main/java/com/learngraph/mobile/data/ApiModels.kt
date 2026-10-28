package com.learngraph.mobile.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.net.URI
import okhttp3.HttpUrl.Companion.toHttpUrl

// 与 backend/app/domain/schemas 对齐的 API 契约子集（自包含快照）

@Serializable
data class DeploymentProfile(
    @SerialName("deployment_profile") val deploymentProfile: String? = null,
    @SerialName("single_user") val singleUser: Boolean? = null,
    @SerialName("registration_enabled") val registrationEnabled: Boolean? = null,
    @SerialName("demo_login_enabled") val demoLoginEnabled: Boolean? = null,
    @SerialName("sandbox_enabled") val sandboxEnabled: Boolean? = null,
    @SerialName("app_version") val appVersion: String? = null,
)

@Serializable
data class LoginResponse(
    @SerialName("access_token") val accessToken: String,
    @SerialName("token_type") val tokenType: String? = null,
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("session_id") val sessionId: String? = null,
    @SerialName("user_id") val userId: String? = null,
    @SerialName("username") val username: String? = null,
    @SerialName("display_name") val displayName: String? = null,
    @SerialName("default_workspace_id") val defaultWorkspaceId: String? = null,
    @SerialName("must_change_password") val mustChangePassword: Boolean? = null,
)

@Serializable
data class Session(
    val id: String,
    @SerialName("workspace_id") val workspaceId: String,
    val title: String,
    @SerialName("goal_id") val goalId: String? = null,
    @SerialName("graph_id") val graphId: String? = null,
    @SerialName("parent_session_id") val parentSessionId: String? = null,
    val pinned: Boolean? = null,
    val status: String? = null,
    @SerialName("session_kind") val sessionKind: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
    @SerialName("project_id") val projectId: String? = null,
    @SerialName("source_message_id") val sourceMessageId: String? = null,
    @SerialName("model_snapshot") val modelSnapshot: JsonObject? = null,
)

@Serializable
data class NativeProject(
    val id: String,
    val title: String,
    val status: String = "active",
    @SerialName("workspace_id") val workspaceId: String = "",
    @SerialName("primary_goal_id") val primaryGoalId: String? = null,
    @SerialName("primary_graph_id") val primaryGraphId: String? = null,
    val position: Int = 0,
    @SerialName("archived_at") val archivedAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("updated_at") val updatedAt: String? = null,
)

@Serializable
data class NativeImpactItem(
    @SerialName("resource_type") val resourceType: String,
    val count: Int,
    val action: String,
)

@Serializable
data class NativeDeleteImpact(
    @SerialName("resource_type") val resourceType: String,
    @SerialName("resource_id") val resourceId: String,
    val title: String,
    @SerialName("confirmation_text") val confirmationText: String,
    val impacts: List<NativeImpactItem> = emptyList(),
    @SerialName("session_ids") val sessionIds: List<String> = emptyList(),
)

data class NativeDeletionPlan internal constructor(
    val projectIds: List<String>,
    val sessionIds: List<String>,
    val impacts: List<NativeDeleteImpact>,
    internal val identity: List<String?>,
)

@Serializable
data class NativeSessionDeleteResult(
    @SerialName("deleted_session_ids") val deletedSessionIds: List<String>,
    @SerialName("deleted_count") val deletedCount: Int = 0,
)

@Serializable
data class NativeSessionShareToken(
    val id: String,
    @SerialName("token_prefix") val tokenPrefix: String = "",
    val label: String = "",
    @SerialName("expires_at") val expiresAt: String? = null,
    @SerialName("max_views") val maxViews: Int? = null,
    @SerialName("view_count") val viewCount: Int = 0,
    @SerialName("last_viewed_at") val lastViewedAt: String? = null,
    @SerialName("revoked_at") val revokedAt: String? = null,
    @SerialName("created_at") val createdAt: String = "",
    val token: String? = null,
)

@Serializable
data class NativeSessionShare(
    val id: String,
    val title: String,
    val scope: String,
    @SerialName("message_count") val messageCount: Int = 0,
    val status: String,
    @SerialName("created_at") val createdAt: String = "",
    val tokens: List<NativeSessionShareToken> = emptyList(),
)

@Serializable
data class NativeWorkspaceSearchHit(
    val kind: String,
    val id: String,
    val title: String,
    val snippet: String = "",
    @SerialName("session_id") val sessionId: String? = null,
    @SerialName("updated_at") val updatedAt: String = "",
    @SerialName("match_field") val matchField: String = "content",
)

@Serializable
data class NativeWorkspaceSearchPage(
    val items: List<NativeWorkspaceSearchHit> = emptyList(),
    @SerialName("has_more") val hasMore: Boolean = false,
    @SerialName("next_offset") val nextOffset: Int = 0,
)

/** Pure request construction shared by native sidebar mutations and their contract tests. */
internal object NativeSidebarRequests {
    fun searchWorkspace(query: String, kind: String, offset: Int): String {
        require(query.codePointCount(0, query.length) <= 240) { "搜索内容最多 240 个字符" }
        require(kind in setOf("all", "session", "message", "file")) { "Invalid search kind" }
        require(offset in 0..10_000) { "Invalid search offset" }
        val url = "https://request.invalid/workspace/search".toHttpUrl().newBuilder()
            .addQueryParameter("q", query).addQueryParameter("kind", kind)
            .addQueryParameter("offset", offset.toString()).addQueryParameter("limit", "20").build()
        return url.encodedPath + "?" + url.encodedQuery
    }

    fun path(vararg segments: String): String = "https://request.invalid".toHttpUrl().newBuilder().apply {
        segments.forEach { require(it.isNotBlank()) { "Resource ID is required" }; addPathSegment(it) }
    }.build().encodedPath

    fun title(value: String): String = value.trim().also {
        require(it.codePointCount(0, it.length) in 1..240) { "名称须为 1 到 240 个字符" }
    }

    fun createSession(project: NativeProject?): String = buildJsonObject {
        put("title", "新会话")
        put("memory_enabled", true)
        put("project_id", project?.id?.let(::JsonPrimitive) ?: JsonNull)
        put("goal_id", project?.primaryGoalId?.let(::JsonPrimitive) ?: JsonNull)
        put("graph_id", project?.primaryGraphId?.let(::JsonPrimitive) ?: JsonNull)
    }.toString()

    fun rename(value: String): String = buildJsonObject { put("title", title(value)) }.toString()
    fun pin(value: Boolean): String = buildJsonObject { put("pinned", value) }.toString()
    fun move(projectId: String?): String = buildJsonObject {
        put("project_id", projectId?.let { require(it.isNotBlank()); JsonPrimitive(it) } ?: JsonNull)
    }.toString()

    fun sessionIds(values: List<String>): List<String> {
        require(values.size in 1..100) { "请选择 1 到 100 个会话" }
        return values.map { it.trim().also { id -> require(id.length in 1..36) { "Invalid session ID" } } }.distinct()
    }

    fun deleteSessions(values: List<String>, confirmation: String? = null): String = buildJsonObject {
        put("session_ids", JsonArray(sessionIds(values).map(::JsonPrimitive)))
        confirmation?.let { require(it.length in 1..128) { "Invalid deletion confirmation" }; put("confirmation_text", it) }
    }.toString()

    fun confirmation(value: String): String = buildJsonObject { put("confirmation_text", value) }.toString()

    fun share(scope: String, label: String, maxViews: Int?, fromMessageId: String?, toMessageId: String?, expiresAt: String? = null): String {
        require(scope in setOf("full", "range", "answers")) { "Invalid share scope" }
        require(label.trim().codePointCount(0, label.trim().length) <= 120) { "分享备注最多 120 个字符" }
        require(maxViews == null || maxViews >= 1) { "访问次数必须大于零" }
        if (scope == "range") require(!fromMessageId.isNullOrBlank() && !toMessageId.isNullOrBlank()) { "请选择区间起点和终点消息" }
        return buildJsonObject {
            put("scope", scope)
            if (scope == "answers") put("answers_only", true)
            if (scope == "range") { put("from_message_id", fromMessageId!!); put("to_message_id", toMessageId!!) }
            if (label.trim().isNotEmpty()) put("label", label.trim())
            maxViews?.let { put("max_views", it) }
            expiresAt?.let { put("expires_at", it) }
        }.toString()
    }

    fun shareUrl(server: String, token: String): String {
        require(token.isNotBlank()) { "分享令牌缺失" }
        val origin = URI(server)
        require(origin.scheme in setOf("http", "https") && origin.host != null && origin.userInfo == null) { "Invalid server URL" }
        return URI(origin.scheme, null, origin.host, origin.port, null, null, null).toString().toHttpUrl().newBuilder()
            .addPathSegment("share").addPathSegment(token).build().toString()
    }
}

@Serializable
data class MessageListPage(
    val items: List<Message> = emptyList(),
    @SerialName("has_more_before") val hasMoreBefore: Boolean = false,
    @SerialName("oldest_id") val oldestId: String? = null,
    @SerialName("newest_id") val newestId: String? = null,
    @SerialName("total_count") val totalCount: Int = 0,
)

@Serializable
data class Message(
    val id: String,
    @SerialName("workspace_id") val workspaceId: String? = null,
    @SerialName("session_id") val sessionId: String,
    @SerialName("parent_message_id") val parentMessageId: String? = null,
    val role: String,
    val version: Int? = null,
    val status: String? = null,
    val content: String = "",
    val parts: List<MessagePart> = emptyList(),
    @SerialName("created_at") val createdAt: String? = null,
    @SerialName("provider_trace") val providerTrace: JsonObject? = null,
)

@Serializable
data class MessagePart(
    val id: String,
    val type: String,
    val status: String? = null,
    val content: String? = null,
    @SerialName("content_delta") val contentDelta: String? = null,
    val sequence: Int? = null,
    val data: JsonObject? = null,
)

/** 消息创建请求（仅原生端用到的字段） */
@Serializable
data class MessageCreateRequest(
    val content: String,
    @SerialName("parent_message_id") val parentMessageId: String? = null,
    @SerialName("thinking_mode") val thinkingMode: String? = null,
    @SerialName("agent_mode") val agentMode: Boolean? = null,
    @SerialName("web_search") val webSearch: Boolean? = null,
)

@Serializable
data class StreamEvent(
    val type: String? = null,
    val event: String? = null,
    @SerialName("event_id") val eventId: String? = null,
    @SerialName("message_id") val messageId: String? = null,
    val part: MessagePart? = null,
    val status: String? = null,
    @SerialName("final_part_id") val finalPartId: String? = null,
    @SerialName("boundary_sequence") val boundarySequence: Int? = null,
    @SerialName("thinking_duration_ms") val thinkingDurationMs: Long? = null,
)

@Serializable
data class StatusResponse(val status: String? = null)

@Serializable
data class LogoutResponse(val status: String? = null)
