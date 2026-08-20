package com.learngraph.mobile.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore(name = "lg_native")

/**
 * 原生版持久化（DataStore）。键与壳版（@capacitor/preferences）对齐，便于用户切换版本时复用连接。
 */
class AuthStore(private val context: Context) {

    private object Keys {
        val BASE_URL = stringPreferencesKey("lg.baseUrl")
        val TOKEN = stringPreferencesKey("lg.token")
        val WORKSPACE_ID = stringPreferencesKey("lg.workspaceId")
        val USERNAME = stringPreferencesKey("lg.username")
        val DISPLAY_NAME = stringPreferencesKey("lg.displayName")
        val SESSION_ID = stringPreferencesKey("lg.sessionId")
        val DEVICE_ID = stringPreferencesKey("lg.deviceId")
    }

    data class AuthState(
        val baseUrl: String = "",
        val token: String? = null,
        val workspaceId: String? = null,
        val username: String? = null,
        val displayName: String? = null,
        val sessionId: String? = null,
        val deviceId: String = "",
    )

    val state: Flow<AuthState> = context.dataStore.data.map { p ->
        AuthState(
            baseUrl = p[Keys.BASE_URL] ?: "",
            token = p[Keys.TOKEN],
            workspaceId = p[Keys.WORKSPACE_ID],
            username = p[Keys.USERNAME],
            displayName = p[Keys.DISPLAY_NAME],
            sessionId = p[Keys.SESSION_ID],
            deviceId = p[Keys.DEVICE_ID] ?: "",
        )
    }

    suspend fun setBaseUrl(baseUrl: String) {
        context.dataStore.edit { it[Keys.BASE_URL] = baseUrl }
    }

    suspend fun setAuth(
        token: String,
        workspaceId: String?,
        username: String?,
        displayName: String?,
        sessionId: String?,
        deviceId: String,
    ) {
        context.dataStore.edit {
            it[Keys.TOKEN] = token
            if (workspaceId != null) it[Keys.WORKSPACE_ID] = workspaceId
            if (username != null) it[Keys.USERNAME] = username
            if (displayName != null) it[Keys.DISPLAY_NAME] = displayName
            if (sessionId != null) it[Keys.SESSION_ID] = sessionId
            it[Keys.DEVICE_ID] = deviceId
        }
    }

    suspend fun clearAuth() {
        context.dataStore.edit {
            it.remove(Keys.TOKEN)
            it.remove(Keys.WORKSPACE_ID)
            it.remove(Keys.SESSION_ID)
        }
    }

    suspend fun clearAll() {
        context.dataStore.edit { it.clear() }
    }
}
