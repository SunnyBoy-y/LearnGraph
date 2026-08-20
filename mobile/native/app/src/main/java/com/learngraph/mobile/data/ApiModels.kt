package com.learngraph.mobile.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonObject

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
)

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
