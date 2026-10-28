package com.learngraph.mobile.ui.web

import android.content.Context
import com.learngraph.mobile.data.ApiClient
import com.learngraph.mobile.data.NativeCacheStore
import com.learngraph.mobile.data.ComposerOptions
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.json.JSONObject
import java.net.URI
import java.util.UUID

/** One-shot, account-scoped commands for the original voice/attachment/workflow UI. */
object NativeComposerHandoff {
    private const val PREFS = "native-composer-handoff"
    private val commands = setOf("attachments", "photo", "goal", "graph", "image", "tools", "dictation", "voice", "models")

    fun queue(context: Context, api: ApiClient, sessionId: String?, command: String, draft: String, options: ComposerOptions? = null): String? {
        if (command !in commands || draft.length > 50_000) return null
        val identity = NativeCacheStore.scope(api.baseUrl, api.workspaceId, api.accountId, api.token) ?: return null
        val session = sessionId ?: "new"
        if (!session.matches(Regex("[A-Za-z0-9_-]{1,100}"))) return null
        val webPrefs = options?.let { runCatching { JSONObject(it.toWebPrefs().toString()) }.getOrNull() ?: return null }
        val payload = JSONObject().apply {
            put("id", UUID.randomUUID().toString()); put("scope", identity)
            put("workspaceId", api.workspaceId); put("sessionId", session)
            put("command", command); put("draft", draft); put("createdAt", System.currentTimeMillis())
            if (webPrefs != null) put("options", webPrefs)
        }
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().remove("returned").remove("active").putString("pending", payload.toString()).apply()
        return "chat/$session"
    }

    fun pending(context: Context, api: ApiClient, url: String?): String? = runCatching {
        if (url == null || !NativeRendererPolicy.sameOrigin(url, api.baseUrl)) return null
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val value = JSONObject(prefs.getString("pending", null) ?: return null)
        val scope = NativeCacheStore.scope(api.baseUrl, api.workspaceId, api.accountId, api.token)
        val age = System.currentTimeMillis() - value.optLong("createdAt")
        if (scope != value.optString("scope") || age !in 0..120_000) {
            prefs.edit().remove("pending").apply(); return null
        }
        if (URI(url).path != "/w/${api.workspaceId}/chat/${value.optString("sessionId")}") return null
        value.remove("scope")
        value.toString()
    }.getOrNull()

    fun complete(context: Context, id: String, draftTransferred: Boolean) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val current = runCatching { JSONObject(prefs.getString("pending", "").orEmpty()) }.getOrNull() ?: return
        if (id == current.optString("id")) {
            val editor = prefs.edit().remove("pending")
            if (draftTransferred) editor.putString("active", current.toString())
            editor.apply()
        }
    }

    data class ReturnedDraft(
        val original: String,
        val current: String,
        val options: ComposerOptions? = null,
        val prefsSource: String? = null,
        val limitedPrefs: Boolean = false,
    )

    /** Called before destroying the WebView; its current draft may have been edited or sent. */
    fun captureReturn(context: Context, api: ApiClient, url: String, draft: String?, composer: JSONObject? = null) = runCatching {
        if (draft != null && draft.length > 50_000 || !NativeRendererPolicy.sameOrigin(url, api.baseUrl)) return@runCatching
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val current = JSONObject(prefs.getString("active", null) ?: return@runCatching)
        if (current.optString("scope") != NativeCacheStore.scope(api.baseUrl, api.workspaceId, api.accountId, api.token)) return@runCatching
        val prefix = "/w/${api.workspaceId}/chat/"
        val path = URI(url).path
        if (!path.startsWith(prefix)) return@runCatching
        val session = path.removePrefix(prefix)
        if (!session.matches(Regex("[A-Za-z0-9_-]{1,100}"))) return@runCatching
        val validComposer = composer?.takeIf {
            it.optInt("version") == 1 && it.optString("workspaceId") == api.workspaceId && it.optString("sessionId") == session
        }
        val options = validComposer?.optJSONObject("prefs")?.let(::parseOptions)
        if (draft == null && options == null) return@runCatching
        current.put("returnedDraft", draft ?: current.optString("draft")); current.put("returnedSessionId", session)
        if (options != null) current.put("returnedOptions", JSONObject(options.toWebPrefs().toString()))
        current.put("prefsSource", validComposer?.optString("source") ?: "unavailable")
        current.put("limitedPrefs", validComposer?.optBoolean("limited", true) ?: true)
        prefs.edit().remove("active").putString("returned", current.toString()).apply()
        if (session != "new" && session != current.optString("sessionId")) {
            com.learngraph.mobile.util.ShortcutActions.ingestOpenSession(context, session)
        }
    }.let { Unit }

    fun takeReturnedDraft(context: Context, api: ApiClient, sessionId: String?): ReturnedDraft? = runCatching {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val current = JSONObject(prefs.getString("returned", null) ?: return null)
        if (current.optString("scope") != NativeCacheStore.scope(api.baseUrl, api.workspaceId, api.accountId, api.token)) return null
        val session = sessionId ?: "new"
        if (session != current.optString("sessionId") && session != current.optString("returnedSessionId")) return null
        // The newly created session consumes this draft; do not consume it in the old blank chat.
        if (current.optString("returnedSessionId") != current.optString("sessionId") && session != current.optString("returnedSessionId")) return null
        prefs.edit().remove("returned").apply()
        ReturnedDraft(current.optString("draft"), current.optString("returnedDraft"),
            current.optJSONObject("returnedOptions")?.let(::parseOptions), current.optString("prefsSource"), current.optBoolean("limitedPrefs"))
    }.getOrNull()

    private fun parseOptions(value: JSONObject): ComposerOptions? = runCatching {
        ComposerOptions.fromWebPrefs(Json.parseToJsonElement(value.toString()) as JsonObject)
    }.getOrNull()
}
