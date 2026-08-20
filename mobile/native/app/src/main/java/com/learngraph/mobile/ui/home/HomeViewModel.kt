package com.learngraph.mobile.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.learngraph.mobile.data.ApiClient
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.data.Session
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json

@Serializable
private data class BatchDeleteImpact(
    val resource_type: String? = null,
    val resource_id: String? = null,
    val title: String? = null,
    val confirmation_text: String? = null,
)

class HomeViewModel(
    private val api: ApiClient,
    private val authStore: AuthStore,
) : ViewModel() {

    private val _sessions = MutableStateFlow<List<Session>>(emptyList())
    val sessions: StateFlow<List<Session>> = _sessions

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error

    private val json = Json { ignoreUnknownKeys = true }

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch {
            _loading.value = true
            _error.value = null
            try {
                val raw = api.get("/sessions")
                _sessions.value = json.decodeFromString<List<Session>>(raw)
            } catch (e: Exception) {
                _error.value = e.message ?: "加载失败"
            } finally {
                _loading.value = false
            }
        }
    }

    suspend fun createSession(): Session? = try {
        val raw = api.post("/sessions", "{}")
        json.decodeFromString<Session>(raw)
    } catch (e: Exception) {
        _error.value = e.message ?: "新建失败"
        null
    }

    /** 删除会话：先查影响（拿确认文案），调用方弹确认后再执行 */
    suspend fun deleteImpact(sessionId: String): String? = try {
        val raw = api.post("/sessions/batch-delete-impact", """{"session_ids":["$sessionId"]}""")
        json.decodeFromString<BatchDeleteImpact>(raw).confirmation_text
    } catch (e: Exception) {
        _error.value = e.message ?: "查询失败"
        null
    }

    suspend fun deleteSession(sessionId: String, confirmationText: String): Boolean = try {
        val body = """{"session_ids":["$sessionId"],"confirmation_text":${jsonStr(confirmationText)}}"""
        api.post("/sessions/batch-delete", body)
        refresh()
        true
    } catch (e: Exception) {
        _error.value = e.message ?: "删除失败"
        false
    }

    fun logout() {
        viewModelScope.launch {
            try {
                api.post("/auth/logout", "{}", auth = true, workspace = false)
            } catch (_: Exception) {
            }
            authStore.clearAll()
        }
    }

    private fun jsonStr(s: String): String =
        "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n") + "\""
}
