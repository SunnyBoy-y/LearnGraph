package com.learngraph.mobile.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/** Local composer preferences mapped onto the existing message-create contract. */
@Serializable
data class ComposerOptions(
    val responseMode: String = "agentic",
    val thinkingMode: String = "medium",
    val searchRoute: String = "auto",
    val providerId: String? = null,
    val modelId: String? = null,
    val generationMode: String = "text",
    val imageProviderId: String? = null,
    val imageModelId: String? = null,
) {
    private fun validate() {
        require(responseMode in setOf("fast", "thinking", "agentic")) { "Unsupported response mode" }
        require(thinkingMode in setOf("off", "low", "medium", "high", "xhigh")) { "Unsupported thinking mode" }
        require(searchRoute in setOf("disabled", "model_native", "external", "local", "auto")) { "Unsupported search route" }
        require(generationMode in setOf("text", "image")) { "Unsupported generation mode" }
        require(listOfNotNull(providerId, imageProviderId).all { it.length <= 36 }) { "Provider ID is too long" }
        require(listOfNotNull(modelId, imageModelId).all { it.length <= 160 }) { "Model ID is too long" }
    }

    /** Same public preference shape as frontend/lib/session-composer-prefs. */
    fun toWebPrefs(): JsonObject {
        validate()
        return buildJsonObject {
            put("responseMode", if (responseMode == "thinking") "agentic" else responseMode)
            put("thinkingMode", thinkingMode); put("searchRoute", searchRoute); put("generationMode", generationMode)
            providerId?.let { put("providerId", it) }; modelId?.let { put("modelId", it) }
            imageProviderId?.let { put("imageProviderId", it) }; imageModelId?.let { put("imageModelId", it) }
        }
    }

    fun toBody(content: String): String {
        validate()
        require(content.isNotBlank() && content.length <= 50_000) { "Message content must contain 1 to 50000 characters" }
        val image = generationMode == "image"
        val provider = (if (image) imageProviderId else providerId)?.trim()?.takeIf(String::isNotEmpty)
        val model = (if (image) imageModelId else modelId)?.trim()?.takeIf(String::isNotEmpty)
        require(!image || provider != null && model != null) { "请选择绘图模型" }
        return buildJsonObject {
            put("content", content)
            // Web retired the old thinking-only mode; old saved values now mean agentic.
            put("agent_mode", !image && responseMode != "fast")
            put("thinking_mode", if (image || responseMode == "fast") "off" else thinkingMode)
            put("search_route", if (image) "disabled" else searchRoute)
            put("web_search", !image && searchRoute != "disabled")
            if (image) put("generation_mode", "image")
            provider?.let { put("provider_id", it) }
            model?.let { put("model_id", it) }
        }.toString()
    }

    companion object {
        /** Reject malformed snapshots instead of silently resetting a user's explicit choices. */
        fun fromWebPrefs(value: JsonObject): ComposerOptions? = runCatching {
            fun required(key: String): String {
                val item = value[key] as? JsonPrimitive
                require(item != null && item.isString) { "Missing preference $key" }
                return item.content
            }
            fun optional(key: String): String? {
                val item = value[key] ?: return null
                if (item == JsonNull) return null
                require(item is JsonPrimitive && item.isString) { "Invalid preference $key" }
                return item.contentOrNull?.takeIf(String::isNotEmpty)
            }
            ComposerOptions(
                responseMode = required("responseMode").let { if (it == "thinking") "agentic" else it },
                thinkingMode = required("thinkingMode"), searchRoute = required("searchRoute"),
                providerId = optional("providerId"), modelId = optional("modelId"),
                generationMode = optional("generationMode") ?: "text",
                imageProviderId = optional("imageProviderId"), imageModelId = optional("imageModelId"),
            ).also { it.validate() }
        }.getOrNull()
    }
}
