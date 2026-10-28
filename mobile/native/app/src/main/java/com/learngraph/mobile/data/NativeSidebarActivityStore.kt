package com.learngraph.mobile.data

import android.content.Context
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.*

/** Account/server/workspace partitioned activity; never infer badges from server status. */
class NativeSidebarActivityStore private constructor(context: Context) {
    private val prefs = context.getSharedPreferences("lg_native_sidebar_activity", Context.MODE_PRIVATE)
    private val json = Json { ignoreUnknownKeys = true; encodeDefaults = true }
    private val ledgers = mutableMapOf<String, NativeSidebarActivityLedger>()
    @Synchronized private fun ledger(scope: String): NativeSidebarActivityLedger = ledgers.getOrPut(scope) {
        val saved = runCatching { json.decodeFromString<Map<String, NativeSessionActivity>>(prefs.getString(scope, null) ?: "{}") }.getOrDefault(emptyMap())
        NativeSidebarActivityLedger(saved.filterKeys { NativeSidebarActivityLedger.validSession(it) }) { state ->
            prefs.edit().putString(scope, json.encodeToString(state)).apply()
        }
    }
    fun observe(scope: String): StateFlow<NativeSidebarActivitySnapshot> = ledger(scope).state
    fun snapshotJson(scope: String): String = json.encodeToString(observe(scope).value)
    fun viewed(scope: String, owner: String, sessionId: String?) = ledger(scope).viewed(owner, sessionId)
    fun touched(scope: String, sessionId: String, at: Long = System.currentTimeMillis()) = ledger(scope).touched(sessionId, at)
    fun running(scope: String, owner: String, sessionId: String, running: Boolean) = ledger(scope).running(owner, sessionId, running)
    fun finished(scope: String, owner: String, sessionId: String, viewing: Boolean = false, at: Long = System.currentTimeMillis()) = ledger(scope).finished(owner, sessionId, viewing, at)
    fun releaseOwner(scope: String, owner: String) = ledger(scope).release(owner)
    fun removeSessions(scope: String, sessionIds: Collection<String>) = ledger(scope).remove(sessionIds.toSet())

    /** Host validates origin/token/workspace and its own owner token before calling. */
    @Synchronized fun applyWebEvent(scope: String, owner: String, payload: String): Boolean = runCatching {
        if (payload.length > 16_000 || owner.length !in 1..160) return false
        val envelope = json.parseToJsonElement(payload) as? JsonObject ?: return false
        if (envelope["version"]?.jsonPrimitive?.intOrNull != 1 || envelope["owner"]?.jsonPrimitive?.contentOrNull != owner) return false
        val sequence = envelope["sequence"]?.jsonPrimitive?.longOrNull ?: return false
        val event = envelope["event"] as? JsonObject ?: return false
        val kind = event["kind"]?.jsonPrimitive?.contentOrNull ?: return false
        if (kind !in setOf("touched", "viewed", "running", "finished", "voice", "view", "release")) return false
        val id = event["sessionId"]?.takeUnless { it == JsonNull }?.jsonPrimitive?.contentOrNull
        if (kind !in setOf("view", "release") && (id == null || !NativeSidebarActivityLedger.validSession(id))) return false
        if (id != null && !NativeSidebarActivityLedger.validSession(id)) return false
        val current = ledger(scope)
        if (!current.acknowledge(owner, sequence)) return false
        val at = event["at"]?.jsonPrimitive?.longOrNull?.coerceIn(0, System.currentTimeMillis() + 60_000) ?: System.currentTimeMillis()
        when (kind) {
            "touched" -> current.touched(id!!, at)
            "viewed" -> current.viewed(id!!)
            "view" -> current.viewed(owner, id)
            "running" -> current.running(owner, id!!, event["running"]?.jsonPrimitive?.booleanOrNull == true)
            "finished" -> current.finished(owner, id!!, event["viewing"]?.jsonPrimitive?.booleanOrNull == true, at)
            "voice" -> current.voice(owner, id!!, event["active"]?.jsonPrimitive?.booleanOrNull == true)
            "release" -> current.release(owner)
        }
        current.acknowledged()
        true
    }.getOrDefault(false)

    companion object {
        @Volatile private var instance: NativeSidebarActivityStore? = null
        fun get(context: Context): NativeSidebarActivityStore = instance ?: synchronized(this) {
            instance ?: NativeSidebarActivityStore(context.applicationContext).also { instance = it }
        }
        fun scope(api: ApiClient): String? = NativeCacheStore.scope(api.baseUrl, api.workspaceId, api.accountId, api.token)
    }
}
