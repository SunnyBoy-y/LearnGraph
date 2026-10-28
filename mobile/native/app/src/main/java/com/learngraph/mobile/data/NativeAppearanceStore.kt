package com.learngraph.mobile.data

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

data class NativeAppearanceSnapshot(val settings: JsonArray, val checkedAt: Long)

/** The same explicit light/dark preference as Web, isolated by the active private data scope. */
class NativeAppearanceStore(context: Context) {
    private val preferences = context.applicationContext.getSharedPreferences("lg_native_appearance", Context.MODE_PRIVATE)
    private val themes = mutableMapOf<String, MutableStateFlow<String>>()
    private val defaultTheme = MutableStateFlow("light")
    private val thinkingChainDefaults = mutableMapOf<String, MutableStateFlow<Boolean>>()
    private val defaultThinkingChain = MutableStateFlow(false)

    @Synchronized
    fun observe(scope: String?): StateFlow<String> = if (scope == null) defaultTheme else themes.getOrPut(scope) {
        MutableStateFlow(preferences.getString("$scope.theme", "light").takeIf { it == "dark" } ?: "light")
    }

    @Synchronized
    fun observeThinkingChainDefaultOpen(scope: String?): StateFlow<Boolean> = if (scope == null) defaultThinkingChain else thinkingChainDefaults.getOrPut(scope) {
        MutableStateFlow(preferences.getBoolean("$scope.thinking_chain_default_open", false))
    }

    /** Optional live Web bridge; normal restoration uses the existing shared settings fetch. */
    @Synchronized
    fun setThinkingChainDefaultOpen(scope: String?, open: Boolean): Boolean {
        if (scope == null) return false
        preferences.edit().putBoolean("$scope.thinking_chain_default_open", open)
            .putLong("$scope.thinking_chain_local_changed_at", System.currentTimeMillis()).apply()
        thinkingChainDefaults.getOrPut(scope) { MutableStateFlow(open) }.value = open
        return true
    }

    fun setTheme(auth: AuthStore.AuthState, theme: String): Boolean = setTheme(scope(auth), theme)

    /** Web's live theme changes take precedence over an older settings response/cache. */
    @Synchronized
    fun setTheme(scope: String?, theme: String): Boolean {
        if (scope == null || theme !in setOf("light", "dark")) return false
        preferences.edit().putString("$scope.theme", theme)
            .putLong("$scope.local_changed_at", System.currentTimeMillis()).apply()
        themes.getOrPut(scope) { MutableStateFlow(theme) }.value = theme
        return true
    }

    @Synchronized
    fun applySettings(scope: String?, snapshot: NativeAppearanceSnapshot): Boolean {
        if (scope == null) return false
        val editor = preferences.edit()
        var applied = false
        val theme = themeFromSettings(snapshot.settings)
        if (theme != null && acceptsSettings(snapshot.checkedAt, preferences.getLong("$scope.local_changed_at", 0))) {
            editor.putString("$scope.theme", theme)
            themes.getOrPut(scope) { MutableStateFlow(theme) }.value = theme
            applied = true
        }
        if (acceptsSettings(snapshot.checkedAt, preferences.getLong("$scope.thinking_chain_local_changed_at", 0))) {
            val open = thinkingChainDefaultFromSettings(snapshot.settings)
            editor.putBoolean("$scope.thinking_chain_default_open", open)
            thinkingChainDefaults.getOrPut(scope) { MutableStateFlow(open) }.value = open
            applied = true
        }
        if (applied) editor.apply()
        return applied
    }

    companion object {
        const val SETTINGS_TTL_MILLIS = 30_000L

        fun scope(auth: AuthStore.AuthState): String? = NativeCacheStore.scope(
            auth.baseUrl, auth.workspaceId, auth.userId ?: auth.username, auth.token,
        )

        /** Missing theme leaves Web's saved lg-theme unchanged; a present non-dark value means light. */
        internal fun themeFromSettings(settings: JsonArray): String? {
            val preference = settings.firstOrNull {
                val key = (it as? JsonObject)?.get("key") as? JsonPrimitive
                key?.isString == true && key.content == "ui.preferences"
            } as? JsonObject ?: return null
            val value = preference["value"] as? JsonObject ?: return null
            if (!value.containsKey("theme")) return null
            val theme = value["theme"] as? JsonPrimitive
            return if (theme?.isString == true && theme.content == "dark") "dark" else "light"
        }

        /** Matches readChatThinkingChainDefault: only explicit open opts into expanded live folds. */
        internal fun thinkingChainDefaultFromSettings(settings: JsonArray): Boolean {
            val preference = settings.firstOrNull {
                val key = (it as? JsonObject)?.get("key") as? JsonPrimitive
                key?.isString == true && key.content == "chat.thinking_chain_default"
            } as? JsonObject
            val value = preference?.get("value") as? JsonObject
            val state = value?.get("default_state") as? JsonPrimitive
            return state?.isString == true && state.content == "open"
        }

        internal fun acceptsSettings(checkedAt: Long, localChangedAt: Long): Boolean =
            localChangedAt == 0L || checkedAt - localChangedAt >= SETTINGS_TTL_MILLIS
    }
}
