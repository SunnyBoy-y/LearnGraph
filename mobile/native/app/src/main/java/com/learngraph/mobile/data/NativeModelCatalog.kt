package com.learngraph.mobile.data

import kotlinx.coroutines.CancellationException
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import okhttp3.HttpUrl.Companion.toHttpUrl

data class NativeModelChoice(
    val providerId: String,
    val providerName: String,
    val modelId: String,
    val label: String,
    val supportsAgent: Boolean,
    val providerType: String = "",
    val supportsImageInput: Boolean = false,
) {
    /** Same labels as frontend/lib/model-choices.ts. */
    val protocolLabel: String get() = when (providerType) {
        "openai_responses" -> "Responses"
        "anthropic_messages" -> "Anthropic Messages"
        "ollama" -> "Ollama"
        else -> "Compatible Chat"
    }
}

/** Preserve /providers order, including providers whose text-model list is empty. */
data class NativeModelCatalog(
    val providerIds: List<String>,
    val choices: List<NativeModelChoice>,
)

/**
 * chat-pages restores local IDs, fills missing IDs from model_snapshot, then
 * validates provider and model separately against the original server order.
 * Only model IDs change here; mode/thinking/search/image preferences stay explicit.
 */
fun resolveNativeTextModelOptions(options: ComposerOptions, snapshot: JsonObject?, catalog: NativeModelCatalog?): ComposerOptions {
    if (catalog == null) return options
    fun snapshotId(key: String) = (snapshot?.get(key) as? JsonPrimitive)
        ?.takeIf { it.isString }?.contentOrNull?.takeIf { it.isNotEmpty() }
    val requestedProvider = options.providerId?.takeIf { it.isNotEmpty() } ?: snapshotId("provider_id")
    val requestedModel = options.modelId?.takeIf { it.isNotEmpty() } ?: snapshotId("model_id")
    val provider = requestedProvider?.takeIf { it in catalog.providerIds } ?: catalog.providerIds.firstOrNull()
    val models = catalog.choices.filter { it.providerId == provider }
    val model = requestedModel?.takeIf { candidate -> models.any { it.modelId == candidate } } ?: models.firstOrNull()?.modelId
    return options.copy(providerId = provider, modelId = model)
}

/** Web fuzzyModelMatch searches model, provider and protocol as one string. */
internal fun nativeModelMatchesQuery(choice: NativeModelChoice, query: String): Boolean {
    val needle = query.trim().lowercase()
    if (needle.isEmpty()) return true
    val value = "${choice.modelId} ${choice.providerName} ${choice.protocolLabel}".lowercase()
    if (needle in value) return true
    var cursor = 0
    for (character in value) {
        if (character == needle[cursor] && ++cursor == needle.length) return true
    }
    return false
}

/** Existing catalog read endpoints only: no generation or capability-probe call. */
suspend fun loadModelChoices(api: ApiClient): List<NativeModelChoice> {
    return loadModelCatalog(api).choices
}

suspend fun loadModelCatalog(api: ApiClient): NativeModelCatalog {
    val identity = listOf(api.baseUrl, api.token, api.workspaceId, api.accountId)
    return loadNativeModelCatalog { path ->
        if (identity != listOf(api.baseUrl, api.token, api.workspaceId, api.accountId)) {
            throw CancellationException("Account or server changed")
        }
        api.get(path).also {
            if (identity != listOf(api.baseUrl, api.token, api.workspaceId, api.accountId)) {
                throw CancellationException("Account or server changed")
            }
        }
    }
}

// Matches frontend/types/providers.ts MODEL_PROVIDER_TYPES and the backend catalog's model role.
private val textProviderTypes = setOf(
    "openai_responses", "openai_compatible_chat", "qwen", "codex_chatgpt",
    "deepseek_chat", "anthropic_messages", "github_copilot", "ollama",
)
private val catalogJson = Json { ignoreUnknownKeys = true }

@Serializable
private data class CatalogProvider(
    val id: String,
    @SerialName("display_name") val displayName: String,
    @SerialName("provider_type") val providerType: String,
    val enabled: Boolean,
    @SerialName("remote_capability") val remoteCapability: Boolean,
    val capabilities: JsonObject = JsonObject(emptyMap()),
)

@Serializable
private data class CatalogModel(
    val id: String,
    val remote: Boolean = false,
    val enabled: Boolean = true,
    val capabilities: JsonObject? = null,
)

@Serializable
private data class CatalogModels(
    @SerialName("provider_id") val providerId: String? = null,
    val models: List<CatalogModel>,
)

/** Injectable transport keeps catalog rules testable without Android or live vendors. */
internal suspend fun loadNativeModelChoices(get: suspend (String) -> String): List<NativeModelChoice> {
    return loadNativeModelCatalog(get).choices
}

internal suspend fun loadNativeModelCatalog(get: suspend (String) -> String): NativeModelCatalog {
    val providers = decodeCatalog<List<CatalogProvider>>(get("/providers"))
        .filter { it.enabled && it.remoteCapability && it.providerType in textProviderTypes }
    val choices = buildList {
        for (provider in providers) {
            val models = try {
                // Provider IDs are path segments, not interpolated URL syntax.
                val path = "https://catalog.invalid".toHttpUrl().newBuilder()
                    .addPathSegment("providers").addPathSegment(provider.id).addPathSegment("models")
                    .build().encodedPath
                val response = decodeCatalog<CatalogModels>(get(path))
                if (response.providerId != null && response.providerId != provider.id) {
                    throw ApiException("模型目录返回了其他供应商的数据", null)
                }
                modelOptions(provider, response.models)
            } catch (e: CancellationException) {
                throw e
            } catch (e: ApiException) {
                // Authentication failures must never become a seemingly usable saved catalog.
                if (e.code == 401 || e.code == 403) throw e
                // Like providerModelOptions(provider, undefined), retain manually configured
                // and persisted models when a vendor discovery endpoint is unavailable.
                modelOptions(provider, null).ifEmpty {
                    throw ApiException("无法读取 ${provider.displayName} 的模型：${e.message}", e.code)
                }
            }
            addAll(models)
        }
    }
    return NativeModelCatalog(providers.map { it.id }, choices)
}

private fun modelOptions(provider: CatalogProvider, discovered: List<CatalogModel>?): List<NativeModelChoice> {
    val capabilities = provider.capabilities
    val persistedIds = (capabilities["discovered_model_ids"] as? JsonArray).orEmpty()
        .mapNotNull { (it as? JsonPrimitive)?.takeIf { item -> item.isString }?.contentOrNull }
        .filter { it.isNotBlank() }
    val saved = capabilities["models"] as? JsonObject ?: JsonObject(emptyMap())
    fun savedModel(id: String) = CatalogModel(id, remote = true, capabilities = saved[id] as? JsonObject)
    val byId = linkedMapOf<String, CatalogModel>()
    (discovered ?: persistedIds.map(::savedModel)).forEach { byId[it.id] = it }
    // Web adds manual snapshots even if the upstream vendor never reported the ID.
    saved.keys.filter { it !in persistedIds }.forEach { byId[it] = savedModel(it) }
    val configured = (capabilities["default_model"] as? JsonPrimitive)
        ?.takeIf { it.isString }?.contentOrNull?.trim().orEmpty()
    if (configured.isNotBlank() && configured !in byId) byId[configured] = CatalogModel(configured, remote = true)
    val states = capabilities["model_states"] as? JsonObject
    return byId.values.filter { model ->
        model.id.isNotBlank() && model.enabled && states?.get(model.id).isNotFalse() &&
            model.capabilities?.get("supports_text_output").isNotFalse()
    }.map { model ->
        NativeModelChoice(
            providerId = provider.id,
            providerName = provider.displayName,
            modelId = model.id,
            label = model.id,
            supportsAgent = model.remote && capabilities["supports_agent_tools"].isNotFalse() &&
                model.capabilities?.get("supports_agent_tools").isNotFalse(),
            providerType = provider.providerType,
            supportsImageInput = (model.capabilities?.get("supports_image_input") as? JsonPrimitive)
                ?.takeUnless { it.isString }?.booleanOrNull == true,
        )
    }
}

private fun kotlinx.serialization.json.JsonElement?.isNotFalse(): Boolean =
    (this as? JsonPrimitive)?.takeUnless { it.isString }?.booleanOrNull != false

private inline fun <reified T> decodeCatalog(raw: String): T = try {
    catalogJson.decodeFromString<T>(raw)
} catch (e: Exception) {
    throw ApiException("模型目录格式无效：${e.message}", null)
}
