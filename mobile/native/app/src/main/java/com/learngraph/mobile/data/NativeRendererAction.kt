package com.learngraph.mobile.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.longOrNull
import kotlinx.serialization.json.put

/** The renderer can request a text follow-up, never an arbitrary API request. */
data class NativeRendererAction(
    val content: String,
    val analysisOnly: Boolean = false,
    val uiAction: NativeRendererUiAction? = null,
    val graphAction: String = "none",
    val graphId: String? = null,
    val nodeIds: List<String>? = null,
) {
    init {
        require(content.isNotBlank() && content.length <= 50_000) { "Content must contain 1 to 50000 characters" }
        require(graphAction == "none" || graphAction == "propose_update") { "Unsupported graph action" }
        require(graphId == null || (graphAction == "propose_update" && graphId.length in 1..36)) { "Invalid graph_id" }
        require(nodeIds == null || nodeIds.size <= 8) { "node_ids can contain at most 8 strings" }
    }

    val requestExtras: JsonObject get() = buildJsonObject {
        put("graph_action", graphAction)
        put("generation_mode", "text")
        graphId?.let { put("graph_id", it) }
        nodeIds?.let { put("node_ids", JsonArray(it.map(::JsonPrimitive))) }
        if (analysisOnly) put("analysis_only", true)
        uiAction?.let { put("ui_action", it.toJson()) }
    }

    companion object {
        private val fields = setOf("content", "graph_action", "generation_mode", "graph_id", "node_ids", "analysis_only", "ui_action")

        fun parse(raw: String): NativeRendererAction {
            val value = Json.parseToJsonElement(raw) as? JsonObject
                ?: throw IllegalArgumentException("Action must be an object")
            require(value.keys.all { it in fields }) { "Unsupported action field" }
            require(value.requiredString("generation_mode") == "text") { "Unsupported generation mode" }
            val analysisOnly = value["analysis_only"]?.let {
                require(it is JsonPrimitive && !it.isString && it.content == "true") { "analysis_only must be true" }
                true
            } ?: false
            val uiAction = value["ui_action"]?.let {
                NativeRendererUiAction.parse(it as? JsonObject
                    ?: throw IllegalArgumentException("ui_action must be an object"))
            }
            val nodeIds = value["node_ids"]?.let { item ->
                val nodes = item as? JsonArray ?: throw IllegalArgumentException("node_ids must be an array of strings")
                require(nodes.size <= 8) { "node_ids can contain at most 8 strings" }
                nodes.map { node ->
                    require(node is JsonPrimitive && node.isString) { "node_ids must contain only strings" }
                    node.content
                }
            }
            return NativeRendererAction(
                content = value.requiredString("content"), analysisOnly = analysisOnly, uiAction = uiAction,
                graphAction = value.requiredString("graph_action"),
                graphId = if (value.containsKey("graph_id")) value.requiredString("graph_id") else null,
                nodeIds = nodeIds,
            )
        }
    }
}

/** UI-only delivery intent is validated before the pure API-action parser sees the request. */
data class NativeRendererSubmission(val action: NativeRendererAction, val autoSend: Boolean = true) {
    companion object {
        fun parse(raw: String): NativeRendererSubmission {
            val value = Json.parseToJsonElement(raw) as? JsonObject
                ?: throw IllegalArgumentException("Action must be an object")
            val autoSend = value["auto_send"]?.let {
                require(it is JsonPrimitive && !it.isString && it.content in setOf("true", "false")) { "auto_send must be a boolean" }
                it.content == "true"
            } ?: true
            return NativeRendererSubmission(NativeRendererAction.parse(JsonObject(value - "auto_send").toString()), autoSend)
        }
    }
}

data class NativeRendererUiAction(
    val partId: String,
    val revision: Long,
    val action: String,
    val nodeId: String? = null,
) {
    init {
        require(partId.length in 1..100) { "Invalid part_id" }
        require(revision >= 0) { "revision must be a nonnegative integer" }
        require(action == "explain" || action == "graph.node.explain") { "Unsupported UI action" }
        require(nodeId == null || (nodeId.length in 1..100 && nodeId.matches(Regex("^[a-zA-Z0-9_-]+$")) &&
            nodeId !in setOf("__proto__", "prototype", "constructor"))) { "Invalid node_id" }
    }

    internal fun toJson(): JsonObject = buildJsonObject {
        put("part_id", partId)
        put("revision", revision)
        put("action", action)
        nodeId?.let { put("node_id", it) }
    }

    companion object {
        internal fun parse(value: JsonObject): NativeRendererUiAction {
            require(value.keys.all { it in setOf("part_id", "revision", "action", "node_id") }) { "Unsupported UI action field" }
            val revisionValue = value["revision"] as? JsonPrimitive
            // UIAction is strict: JSON floating-point tokens (including 1e2) are not integers.
            val revision = revisionValue?.takeIf { !it.isString && it.content.matches(Regex("-?(0|[1-9][0-9]*)")) }?.longOrNull
                ?: throw IllegalArgumentException("revision must be a nonnegative integer")
            return NativeRendererUiAction(
                partId = value.requiredString("part_id"), revision = revision,
                action = value.requiredString("action"),
                nodeId = if (value.containsKey("node_id")) value.requiredString("node_id") else null,
            )
        }
    }
}

/** Queue snapshots keep their own action extras and composer settings. */
internal fun nativeMessageRequestBody(
    options: ComposerOptions,
    content: String,
    requestExtras: JsonObject = JsonObject(emptyMap()),
): String {
    val extras = if (requestExtras.isEmpty()) requestExtras else {
        require(!requestExtras.containsKey("content")) { "Action extras cannot replace content" }
        NativeRendererAction.parse(JsonObject(requestExtras + ("content" to JsonPrimitive(content))).toString()).requestExtras
    }
    // Registered renderer actions are text follow-ups even if the composer
    // currently holds a drawing model. Preserve the text model selection.
    val effectiveOptions = if (extras.isEmpty()) options else options.copy(generationMode = "text")
    val body = Json.parseToJsonElement(effectiveOptions.toBody(content)) as JsonObject
    return buildJsonObject {
        body.forEach { (key, value) -> put(key, value) }
        extras.forEach { (key, value) -> put(key, value) }
        // Web's analysis-only path disables both agent tools and network search.
        if (extras["analysis_only"] == JsonPrimitive(true)) {
            put("agent_mode", false)
            put("search_route", "disabled")
            put("web_search", false)
        }
    }.toString()
}

private fun JsonObject.requiredString(key: String): String {
    val value = get(key) as? JsonPrimitive
    require(value != null && value.isString) { "$key must be a string" }
    return value.content
}
