package com.learngraph.mobile.data

import android.content.Context
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import okhttp3.HttpUrl.Companion.toHttpUrl
import java.io.File
import java.io.IOException

/**
 * Native conversation data. ViewModels paint cached* first, then refresh*.
 * The latest message window is authoritative: replacing it also discards removed branches.
 * Older history is requested explicitly; opening a chat never downloads its full history.
 */
class NativeRepository internal constructor(private val cache: NativeCacheStore, private val api: ApiClient) {
    constructor(context: Context, api: ApiClient) : this(
        NativeCacheStore(File(context.noBackupFilesDir, "native-cache-v1"), Json { ignoreUnknownKeys = true }), api,
    )
    private val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    private val sessionMutex = Mutex()
    private val messageMutex = Mutex()
    private val appearanceMutex = Mutex()
    private val projectMutex = Mutex()
    private val projectKey = "projects:include-archived"

    suspend fun cachedProjects(): List<NativeProject>? = cached<List<NativeProject>>(projectKey)

    suspend fun refreshProjects(force: Boolean = false): List<NativeProject> {
        val scope = requireScope()
        return projectMutex.withLock {
            checkScope(scope)
            refresh<List<NativeProject>>(scope, projectKey, "/projects?include_archived=true", force, 30_000)
        }
    }

    /** Serialize confirmed mutations after any older in-flight list response, on disk as well as in memory. */
    suspend fun rememberProject(project: NativeProject) {
        val scope = requireScope()
        projectMutex.withLock {
            checkScope(scope)
            val current = readDecoded<List<NativeProject>>(scope, projectKey).orEmpty()
            val updated = (current.filterNot { it.id == project.id } + project).sortedWith(compareBy({ it.position }, { it.createdAt ?: "" }))
            store(scope, projectKey, json.encodeToString(updated), null, 0)
        }
    }

    suspend fun removeProjects(projectIds: Collection<String>) {
        val scope = requireScope()
        projectMutex.withLock {
            checkScope(scope)
            val ids = projectIds.toSet()
            val current = readDecoded<List<NativeProject>>(scope, projectKey).orEmpty()
            store(scope, projectKey, json.encodeToString(current.filterNot { it.id in ids }), null, 0)
        }
    }

    suspend fun cachedAppearanceSettings(): NativeAppearanceSnapshot? {
        val scope = currentScope() ?: return null
        val entry = withContext(Dispatchers.IO) { cache.read(scope, "appearance-settings") } ?: return null
        checkScope(scope)
        val settings = try { json.decodeFromString<JsonArray>(entry.body) } catch (_: Exception) { return null }
        return NativeAppearanceSnapshot(settings, entry.checkedAt)
    }

    /** One settings request per workspace/account per 30 seconds, independent of message rendering. */
    suspend fun refreshAppearanceSettings(): NativeAppearanceSnapshot = appearanceMutex.withLock {
        val scope = requireScope()
        val saved = cachedAppearanceSettings()
        checkScope(scope)
        val requestedAt = System.currentTimeMillis()
        if (saved != null && saved.checkedAt > 0 && requestedAt >= saved.checkedAt &&
            requestedAt - saved.checkedAt < NativeAppearanceStore.SETTINGS_TTL_MILLIS) return@withLock saved
        val settings = refresh<JsonArray>(scope, "appearance-settings", "/settings", true, NativeAppearanceStore.SETTINGS_TTL_MILLIS)
        checkScope(scope)
        NativeAppearanceSnapshot(settings, requestedAt)
    }

    suspend fun cachedSessions(): List<Session>? = cached<List<Session>>(NativeCacheStore.SESSIONS_KEY)

    suspend fun refreshSessions(force: Boolean = false): List<Session> = sessionMutex.withLock {
        val scope = requireScope()
        val result = refresh<List<Session>>(scope, NativeCacheStore.SESSIONS_KEY, "/sessions", force, 30_000)
        withContext(Dispatchers.IO) { cache.pruneMessagePages(scope, result.map { it.id }.toSet()) }
        checkScope(scope)
        result
    }

    suspend fun cachedMessages(sessionId: String): MessageListPage? =
        cached<MessageListPage>(messageKey(sessionId))

    suspend fun cachedComposerOptions(sessionId: String): ComposerOptions? = cached<ComposerOptions>("composer:$sessionId")

    suspend fun saveComposerOptions(sessionId: String, options: ComposerOptions) {
        val scope = requireScope()
        store(scope, "composer:$sessionId", json.encodeToString(options), null)
    }

    suspend fun refreshMessages(sessionId: String, force: Boolean = false): MessageListPage = messageMutex.withLock {
        val scope = requireScope()
        try {
            refresh<MessageListPage>(scope, messageKey(sessionId), messagePath(sessionId), force, 10_000)
        } catch (e: ApiException) {
            if (e.code == 404 || e.code == 403) {
                withContext(Dispatchers.IO) { cache.remove(scope, messageKey(sessionId)) }
            }
            throw e
        }
    }

    suspend fun loadOlderMessages(sessionId: String, beforeId: String): MessageListPage {
        val scope = requireScope()
        val raw = api.get(messagePath(sessionId, beforeId))
        checkScope(scope)
        return json.decodeFromString<MessageListPage>(raw)
    }

    /** Call after successful creation; checkedAt=0 makes the next list refresh revalidate. */
    suspend fun rememberSession(session: Session) = sessionMutex.withLock {
        val scope = requireScope()
        val current = readDecoded<List<Session>>(scope, NativeCacheStore.SESSIONS_KEY).orEmpty()
        val updated = listOf(session) + current.filterNot { it.id == session.id }
        store(scope, NativeCacheStore.SESSIONS_KEY, json.encodeToString(updated), null, 0)
    }

    /** Call only after the server confirms deletion, before updating the visible list. */
    suspend fun removeSessions(sessionIds: Collection<String>) = sessionMutex.withLock {
        val scope = requireScope()
        val ids = sessionIds.toSet()
        val current = readDecoded<List<Session>>(scope, NativeCacheStore.SESSIONS_KEY).orEmpty()
        store(scope, NativeCacheStore.SESSIONS_KEY, json.encodeToString(current.filterNot { it.id in ids }), null, 0)
        withContext(Dispatchers.IO) { ids.forEach { cache.remove(scope, messageKey(it)); cache.remove(scope, "composer:$it") } }
    }

    /** Clear before clearing auth, while the account scope is still available. */
    suspend fun clearCurrentAccount() {
        currentScope()?.let { scope -> withContext(Dispatchers.IO) { cache.clear(scope) } }
    }

    private suspend inline fun <reified T> cached(key: String): T? {
        val scope = currentScope() ?: return null
        return readDecoded<T>(scope, key).also { checkScope(scope) }
    }

    private suspend inline fun <reified T> readDecoded(scope: String, key: String): T? {
        val entry = withContext(Dispatchers.IO) { cache.read(scope, key) } ?: return null
        return try { json.decodeFromString<T>(entry.body) } catch (_: Exception) { null }
    }

    private suspend inline fun <reified T> refresh(
        scope: String, key: String, path: String, force: Boolean, maxAgeMillis: Long,
    ): T {
        val existing = withContext(Dispatchers.IO) { cache.read(scope, key) }
        val decoded = existing?.let { try { json.decodeFromString<T>(it.body) } catch (_: Exception) { null } }
        checkScope(scope)
        if (!force && decoded != null && existing?.isFresh(System.currentTimeMillis(), maxAgeMillis) == true) return decoded
        val response = api.getConditional(path, etag = if (decoded != null) existing?.etag else null)
        checkScope(scope)
        if (response.notModified && decoded != null) {
            store(scope, key, existing.body, response.etag ?: existing.etag)
            return decoded
        }
        // An unexpected 304 without a local body cannot be used as an empty timeline.
        val body = response.body ?: api.get(path).also { checkScope(scope) }
        val result = json.decodeFromString<T>(body)
        store(scope, key, body, response.etag)
        return result
    }

    private suspend fun store(scope: String, key: String, body: String, etag: String?, checkedAt: Long = System.currentTimeMillis()) {
        checkScope(scope)
        // Disk exhaustion must not turn a successful API operation into a visible failure.
        try { withContext(Dispatchers.IO) { cache.write(scope, key, body, etag, checkedAt) } }
        catch (_: IOException) { }
        catch (_: SecurityException) { }
        checkScope(scope)
    }

    private fun currentScope() = NativeCacheStore.scope(api.baseUrl, api.workspaceId, api.accountId, api.token)
    private fun requireScope() = currentScope() ?: throw ApiException("请先登录并选择工作区", 401)
    private fun checkScope(scope: String) {
        if (scope != currentScope()) throw CancellationException("Account or server changed")
    }
    private fun messageKey(sessionId: String) = "messages:$sessionId"
    private fun messagePath(sessionId: String, beforeId: String? = null): String {
        // Use HttpUrl encoding rather than interpolating untrusted query values.
        val url = "https://cache.invalid".toHttpUrl().newBuilder()
            .addPathSegment("sessions").addPathSegment(sessionId).addPathSegment("messages")
            .addQueryParameter("limit", "50").addQueryParameter("compact", "false")
            .apply { beforeId?.let { addQueryParameter("before_id", it) } }.build()
        return url.encodedPath + "?" + url.encodedQuery
    }
}
