package com.learngraph.mobile.ui.home

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.learngraph.mobile.data.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive

class HomeViewModel(
    private val api: ApiClient,
    private val authStore: AuthStore,
    private val repository: NativeRepository? = null,
    private val appearanceStore: NativeAppearanceStore? = null,
) : ViewModel() {

    private val _sessions = MutableStateFlow<List<Session>>(emptyList())
    val sessions: StateFlow<List<Session>> = _sessions
    private val _sidebarActivity = MutableStateFlow(NativeSidebarActivitySnapshot())
    val sidebarActivity: StateFlow<NativeSidebarActivitySnapshot> = _sidebarActivity
    private var sidebarActivityStore: NativeSidebarActivityStore? = null
    private var sidebarActivityScope: String? = null
    private var sidebarActivityJob: Job? = null

    fun attachSidebarActivity(store: NativeSidebarActivityStore, scope: String) {
        if (sidebarActivityStore === store && sidebarActivityScope == scope) return
        sidebarActivityJob?.cancel()
        sidebarActivityStore = store; sidebarActivityScope = scope
        _sidebarActivity.value = store.observe(scope).value
        sidebarActivityJob = viewModelScope.launch { store.observe(scope).collect { _sidebarActivity.value = it } }
    }
    private val _projects = MutableStateFlow<List<NativeProject>>(emptyList())
    val projects: StateFlow<List<NativeProject>> = _projects
    private val _loadingProjects = MutableStateFlow(false)
    val loadingProjects: StateFlow<Boolean> = _loadingProjects
    private var sessionMutationVersion = 0
    private var projectMutationVersion = 0
    private val projectLoadMutex = Mutex()
    private var projectsCheckedAt = 0L
    private var projectsCheckedAs: List<String?>? = null

    private val _loading = MutableStateFlow(false)
    val loading: StateFlow<Boolean> = _loading

    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error
    private val _isOffline = MutableStateFlow(false)
    val isOffline: StateFlow<Boolean> = _isOffline
    private var refreshJob: Job? = null
    private val _modelChoices = MutableStateFlow<List<NativeModelChoice>>(emptyList())
    val modelChoices: StateFlow<List<NativeModelChoice>> = _modelChoices
    private val _modelCatalog = MutableStateFlow<NativeModelCatalog?>(null)
    val modelCatalog: StateFlow<NativeModelCatalog?> = _modelCatalog
    private val _modelCatalogError = MutableStateFlow<String?>(null)
    val modelCatalogError: StateFlow<String?> = _modelCatalogError
    private val _loadingModels = MutableStateFlow(false)
    val loadingModels: StateFlow<Boolean> = _loadingModels
    private var modelsCheckedAt = 0L
    private var modelsCheckedAs: List<String?>? = null

    private val json = Json { ignoreUnknownKeys = true }

    init {
        refresh(force = false)
    }

    fun loadModels() {
        if (_loadingModels.value) return
        val identity = requestIdentity()
        val now = System.currentTimeMillis()
        if (_modelCatalog.value != null && modelsCheckedAs == identity && now >= modelsCheckedAt && now - modelsCheckedAt < 30_000L) return
        _loadingModels.value = true
        _modelCatalogError.value = null
        viewModelScope.launch {
            try {
                val catalog = loadModelCatalog(api)
                requireIdentity(identity)
                _modelCatalog.value = catalog
                _modelChoices.value = catalog.choices
                modelsCheckedAt = System.currentTimeMillis()
                modelsCheckedAs = identity.toList()
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { requireIdentity(identity); _modelCatalogError.value = e.message ?: "加载模型失败" }
            finally { if (identity == requestIdentity()) _loadingModels.value = false }
        }
    }

    fun refresh(force: Boolean = true) {
        loadModels()
        refreshJob?.cancel()
        refreshJob = viewModelScope.launch {
            val requestedAs = requestIdentity()
            val sessionVersion = sessionMutationVersion
            _loading.value = true
            _error.value = null
            launch { refreshAppearance(requestedAs) }
            launch { loadProjects(requestedAs) }
            try {
                if (_sessions.value.isEmpty()) repository?.cachedSessions()?.let {
                    requireIdentity(requestedAs)
                    if (sessionVersion == sessionMutationVersion) _sessions.value = it
                }
                val sessions = repository?.refreshSessions(force)
                    ?: json.decodeFromString<List<Session>>(api.get("/sessions"))
                requireIdentity(requestedAs)
                if (sessionVersion == sessionMutationVersion) _sessions.value = sessions
                _isOffline.value = false
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                requireIdentity(requestedAs)
                _isOffline.value = e is ApiException && e.code == null
                _error.value = e.message ?: "加载失败"
                if (e is ApiException && e.code == 401) authStore.clearAuth()
            } finally {
                if (requestedAs == requestIdentity()) _loading.value = false
            }
        }
    }

    fun refreshProjects(force: Boolean = true) { viewModelScope.launch { loadProjects(requestIdentity(), force) } }

    private suspend fun loadProjects(requestedAs: List<String?>, force: Boolean = false) = projectLoadMutex.withLock {
        requireIdentity(requestedAs)
        val now = System.currentTimeMillis()
        if (repository == null && !force && projectsCheckedAs == requestedAs && projectsCheckedAt > 0 &&
            now >= projectsCheckedAt && now - projectsCheckedAt < 30_000L) return@withLock
        val version = projectMutationVersion
        _loadingProjects.value = true
        try {
            if (_projects.value.isEmpty()) repository?.cachedProjects()?.let {
                requireIdentity(requestedAs)
                if (version == projectMutationVersion) _projects.value = it
            }
            val projects = repository?.refreshProjects(force)
                ?: json.decodeFromString<List<NativeProject>>(api.get("/projects?include_archived=true"))
            requireIdentity(requestedAs)
            if (version == projectMutationVersion) {
                _projects.value = projects
                projectsCheckedAt = System.currentTimeMillis()
                projectsCheckedAs = requestedAs.toList()
            }
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { requireIdentity(requestedAs); reportFailure(e, "加载项目失败") }
        finally { if (requestedAs == requestIdentity()) _loadingProjects.value = false }
    }

    private suspend fun refreshAppearance(requestedAs: List<String?>) {
        val store = appearanceStore ?: return
        val scope = NativeCacheStore.scope(api.baseUrl, api.workspaceId, api.accountId, api.token) ?: return
        try {
            repository?.cachedAppearanceSettings()?.let {
                requireIdentity(requestedAs)
                store.applySettings(scope, it)
            }
            val requestedAt = System.currentTimeMillis()
            val settings = repository?.refreshAppearanceSettings()
                ?: NativeAppearanceSnapshot(json.decodeFromString<JsonArray>(api.get("/settings")), requestedAt)
            requireIdentity(requestedAs)
            store.applySettings(scope, settings)
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            requireIdentity(requestedAs)
            // A theme refresh failure keeps the immediately available preference and does not hide sessions.
            if (e is ApiException && e.code == 401) authStore.clearAuth()
        }
    }

    suspend fun createSession(projectId: String? = null): Session? = mutation("新建失败") { identity ->
        val project = if (projectId == null) null else {
            _projects.value.firstOrNull { it.id == projectId } ?: run {
                val projects = repository?.refreshProjects(force = true)
                    ?: json.decodeFromString<List<NativeProject>>(api.get("/projects?include_archived=true"))
                requireIdentity(identity)
                projects.firstOrNull { it.id == projectId } ?: throw ApiException("所选项目不存在，请刷新", 404)
            }
        }
        val raw = api.post("/sessions", NativeSidebarRequests.createSession(project))
        requireIdentity(identity)
        json.decodeFromString<Session>(raw).also { rememberSession(it, identity) }
    }

    /** 删除会话：先查影响（拿确认文案），调用方弹确认后再执行 */
    suspend fun deleteImpact(sessionId: String): String? = mutation("查询失败") { identity ->
        val raw = api.post("/sessions/batch-delete-impact", NativeSidebarRequests.deleteSessions(listOf(sessionId)))
        requireIdentity(identity)
        json.decodeFromString<NativeDeleteImpact>(raw).confirmationText
    }

    suspend fun deleteSession(sessionId: String, confirmationText: String): Boolean = mutation("删除失败") { identity ->
        deleteSessionBatch(listOf(sessionId), confirmationText, identity)
        true
    } ?: false

    suspend fun renameSession(id: String, title: String): Boolean = mutateSession(id, "会话改名失败") {
        api.patch(NativeSidebarRequests.path("sessions", id), NativeSidebarRequests.rename(title))
    }

    suspend fun toggleSessionPin(session: Session): Boolean = mutateSession(session.id, "置顶状态更新失败") {
        api.patch(NativeSidebarRequests.path("sessions", session.id), NativeSidebarRequests.pin(session.pinned != true))
    }

    suspend fun toggleSessionArchive(session: Session): Boolean = mutateSession(session.id, "会话状态更新失败") {
        api.post(NativeSidebarRequests.path("sessions", session.id, if (session.status == "archived") "restore" else "archive"))
    }

    suspend fun moveSession(id: String, projectId: String?): Boolean = mutateSession(id, "移动会话失败") {
        api.put(NativeSidebarRequests.path("sessions", id, "project"), NativeSidebarRequests.move(projectId))
    }

    suspend fun createProject(title: String, primaryGoalId: String? = null, primaryGraphId: String? = null): NativeProject? = mutation("新建项目失败") { identity ->
        val body = kotlinx.serialization.json.buildJsonObject {
            put("title", JsonPrimitive(NativeSidebarRequests.title(title)))
            primaryGoalId?.let { put("primary_goal_id", JsonPrimitive(it)) }
            primaryGraphId?.let { put("primary_graph_id", JsonPrimitive(it)) }
        }.toString()
        val raw = api.post("/projects", body)
        requireIdentity(identity)
        json.decodeFromString<NativeProject>(raw).also { rememberProject(it, identity) }
    }

    suspend fun renameProject(id: String, title: String): Boolean = mutateProject(id, "项目改名失败") {
        api.patch(NativeSidebarRequests.path("projects", id), NativeSidebarRequests.rename(title))
    }

    suspend fun toggleProjectArchive(project: NativeProject): Boolean = mutateProject(project.id, "项目状态更新失败") {
        api.post(NativeSidebarRequests.path("projects", project.id, if (project.status == "archived") "restore" else "archive"))
    }

    suspend fun prepareDeletion(projectIds: List<String>, sessionIds: List<String>): NativeDeletionPlan? = mutation("查询删除影响失败") { identity ->
        val projects = projectIds.distinct().also { ids -> require(ids.all { it.isNotBlank() }) { "Invalid project ID" } }
        val sessions = sessionIds.filterNot { id -> _sessions.value.any { it.id == id && it.projectId in projects } }
            .let { if (it.isEmpty()) emptyList() else NativeSidebarRequests.sessionIds(it) }
        require(projects.isNotEmpty() || sessions.isNotEmpty()) { "请选择要删除的项目或会话" }
        val impacts = mutableListOf<NativeDeleteImpact>()
        for (projectId in projects) {
            val raw = api.get(NativeSidebarRequests.path("projects", projectId, "delete-impact"))
            requireIdentity(identity)
            val impact = json.decodeFromString<NativeDeleteImpact>(raw)
            require(impact.resourceType == "project" && impact.resourceId == projectId) { "删除影响与所选项目不一致" }
            impacts += impact
        }
        if (sessions.isNotEmpty()) {
            val raw = api.post("/sessions/batch-delete-impact", NativeSidebarRequests.deleteSessions(sessions))
            requireIdentity(identity)
            val impact = json.decodeFromString<NativeDeleteImpact>(raw)
            require(impact.sessionIds.toSet() == sessions.toSet()) { "删除影响与所选会话不一致" }
            impacts += impact
        }
        NativeDeletionPlan(projects, sessions, impacts, identity.toList())
    }

    /** Apply each confirmed success immediately; a later failure must not resurrect earlier deletions. */
    suspend fun executeDeletion(plan: NativeDeletionPlan): Boolean {
        requireIdentity(plan.identity)
        val failures = mutableListOf<String>()
        for (projectId in plan.projectIds) {
            val success = mutation("删除项目失败") { identity ->
                requireIdentity(plan.identity)
                val impact = plan.impacts.single { it.resourceType == "project" && it.resourceId == projectId }
                api.post(NativeSidebarRequests.path("projects", projectId, "delete"), NativeSidebarRequests.confirmation(impact.confirmationText))
                requireIdentity(identity)
                val removed = _sessions.value.filter { it.projectId == projectId }.map { it.id }
                _projects.value = _projects.value.filterNot { it.id == projectId }
                projectMutationVersion++
                repository?.removeProjects(listOf(projectId))
                requireIdentity(identity)
                removeSessions(removed, identity)
                true
            } ?: false
            if (!success) failures += _error.value ?: "删除项目失败"
        }
        if (plan.sessionIds.isNotEmpty()) {
            val success = mutation("删除会话失败") { identity ->
                requireIdentity(plan.identity)
                val impact = plan.impacts.single { it.resourceType != "project" }
                deleteSessionBatch(plan.sessionIds, impact.confirmationText, identity)
                true
            } ?: false
            if (!success) failures += _error.value ?: "删除会话失败"
        }
        requireIdentity(plan.identity)
        _error.value = failures.distinct().joinToString("；").takeIf { it.isNotEmpty() }
        return failures.isEmpty()
    }

    suspend fun loadShares(sessionId: String): List<NativeSessionShare>? = mutation("加载分享失败") { identity ->
        val raw = api.get(NativeSidebarRequests.path("sessions", sessionId, "shares"))
        requireIdentity(identity)
        json.decodeFromString<List<NativeSessionShare>>(raw).filter { it.status == "active" }
    }

    suspend fun createShare(
        sessionId: String, scope: String = "full", label: String = "", maxViews: Int? = null,
        fromMessageId: String? = null, toMessageId: String? = null, expiresAt: String? = null,
    ): NativeSessionShareToken? = mutation("创建分享失败") { identity ->
        val body = NativeSidebarRequests.share(scope, label, maxViews, fromMessageId, toMessageId, expiresAt)
        val raw = api.post(NativeSidebarRequests.path("sessions", sessionId, "shares"), body)
        requireIdentity(identity)
        json.decodeFromString<NativeSessionShareToken>(raw).also { require(!it.token.isNullOrBlank()) { "分享令牌缺失" } }
    }

    suspend fun revokeShare(shareId: String): Boolean = mutation("撤销分享失败") { identity ->
        api.delete(NativeSidebarRequests.path("session-shares", shareId)); requireIdentity(identity); true
    } ?: false

    suspend fun revokeToken(shareId: String, tokenId: String): Boolean = mutation("撤销链接失败") { identity ->
        api.delete(NativeSidebarRequests.path("session-shares", shareId, "tokens", tokenId)); requireIdentity(identity); true
    } ?: false

    suspend fun readShareMessages(sessionId: String): List<Message>? = mutation("加载分享区间失败") { identity ->
        val raw = api.get(NativeSidebarRequests.path("sessions", sessionId, "messages") + "?compact=false")
        requireIdentity(identity)
        json.decodeFromString<MessageListPage>(raw).items
    }

    fun shareUrl(token: String): String = NativeSidebarRequests.shareUrl(api.baseUrl, token)

    suspend fun searchWorkspace(q: String, kind: String = "all", offset: Int = 0): NativeWorkspaceSearchPage? = mutation("工作区搜索失败") { identity ->
        val raw = api.get(NativeSidebarRequests.searchWorkspace(q, kind, offset))
        requireIdentity(identity)
        // Search is a separate projection: a file/message hit must never become a session row.
        json.decodeFromString<NativeWorkspaceSearchPage>(raw)
    }

    private suspend fun mutateSession(id: String, fallback: String, request: suspend () -> String): Boolean = mutation(fallback) { identity ->
        val raw = request()
        requireIdentity(identity)
        val session = json.decodeFromString<Session>(raw)
        require(session.id == id) { "会话响应与请求不一致" }
        rememberSession(session, identity)
        true
    } ?: false

    private suspend fun rememberSession(session: Session, identity: List<String?>) {
        requireIdentity(identity)
        repository?.rememberSession(session)
        requireIdentity(identity)
        _sessions.value = if (_sessions.value.any { it.id == session.id }) _sessions.value.map { if (it.id == session.id) session else it }
            else listOf(session) + _sessions.value
        sessionMutationVersion++
    }

    private suspend fun mutateProject(id: String, fallback: String, request: suspend () -> String): Boolean = mutation(fallback) { identity ->
        val raw = request()
        requireIdentity(identity)
        val project = json.decodeFromString<NativeProject>(raw)
        require(project.id == id) { "项目响应与请求不一致" }
        rememberProject(project, identity)
        true
    } ?: false

    private suspend fun rememberProject(project: NativeProject, identity: List<String?>) {
        requireIdentity(identity)
        _projects.value = (_projects.value.filterNot { it.id == project.id } + project).sortedWith(compareBy({ it.position }, { it.createdAt ?: "" }))
        projectMutationVersion++
        repository?.rememberProject(project)
        requireIdentity(identity)
    }

    private suspend fun deleteSessionBatch(ids: List<String>, confirmation: String, identity: List<String?>) {
        val raw = api.post("/sessions/batch-delete", NativeSidebarRequests.deleteSessions(ids, confirmation))
        requireIdentity(identity)
        val result = json.decodeFromString<NativeSessionDeleteResult>(raw)
        require(result.deletedSessionIds.toSet() == ids.toSet()) { "删除结果与确认的会话不一致，请刷新" }
        removeSessions(result.deletedSessionIds, identity)
    }

    private suspend fun removeSessions(ids: List<String>, identity: List<String?>) {
        requireIdentity(identity)
        _sessions.value = _sessions.value.filterNot { it.id in ids }
        sidebarActivityScope?.let { sidebarActivityStore?.removeSessions(it, ids) }
        sessionMutationVersion++
        repository?.removeSessions(ids)
        requireIdentity(identity)
    }

    private suspend fun <T> mutation(fallback: String, operation: suspend (List<String?>) -> T): T? {
        val identity = requestIdentity()
        return try {
            operation(identity).also { requireIdentity(identity); _error.value = null }
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) { requireIdentity(identity); reportFailure(e, fallback); null }
    }

    private suspend fun reportFailure(error: Exception, fallback: String) {
        _error.value = error.message ?: fallback
        if (error is ApiException && error.code == 401) authStore.clearAuth()
    }

    fun logout(onLoggedOut: () -> Unit = {}) {
        viewModelScope.launch {
            val requestedAs = requestIdentity()
            try {
                api.post("/auth/logout", "{}", auth = true, workspace = false)
            } catch (_: Exception) {
            }
            requireIdentity(requestedAs)
            repository?.clearCurrentAccount()
            android.webkit.CookieManager.getInstance().removeAllCookies(null)
            android.webkit.WebStorage.getInstance().deleteAllData()
            authStore.clearAuth()
            onLoggedOut()
        }
    }

    private fun requestIdentity() = listOf(api.baseUrl, api.token, api.workspaceId, api.accountId)
    private fun requireIdentity(requestedAs: List<String?>) {
        if (requestedAs != requestIdentity()) throw CancellationException("Account changed during request")
    }

}
