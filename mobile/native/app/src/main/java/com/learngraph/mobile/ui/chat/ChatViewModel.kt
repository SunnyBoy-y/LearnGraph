package com.learngraph.mobile.ui.chat

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.learngraph.mobile.data.*
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import java.util.UUID
import com.learngraph.mobile.util.NativeChatHapticFeedback

data class QueuedNativeMessage(
    val id: String,
    val content: String,
    val options: ComposerOptions,
    val requestExtras: JsonObject = JsonObject(emptyMap()),
)

/** Durable local reads, conditional page refreshes and the shared SSE protocol. */
class ChatViewModel(
    private val api: ApiClient,
    val sessionId: String,
    private val repository: NativeRepository? = null,
    private val onAuthExpired: () -> Unit = {},
    private val feedback: NativeChatHapticFeedback = NativeChatHapticFeedback.None,
    private val activityStore: NativeSidebarActivityStore? = null,
) : ViewModel() {
    private val activityScope = NativeSidebarActivityStore.scope(api)
    private var activeActivityOwner: String? = null
    private val _messages = MutableStateFlow<List<Message>>(emptyList())
    val messages: StateFlow<List<Message>> = _messages
    private val _loading = MutableStateFlow(true)
    val loading: StateFlow<Boolean> = _loading
    private val _refreshing = MutableStateFlow(false)
    val refreshing: StateFlow<Boolean> = _refreshing
    private val _loadingOlder = MutableStateFlow(false)
    val loadingOlder: StateFlow<Boolean> = _loadingOlder
    private val _hasMoreBefore = MutableStateFlow(false)
    val hasMoreBefore: StateFlow<Boolean> = _hasMoreBefore
    private val _isOffline = MutableStateFlow(false)
    val isOffline: StateFlow<Boolean> = _isOffline
    private val _sending = MutableStateFlow(false)
    val sending: StateFlow<Boolean> = _sending
    private val _error = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = _error
    private val _failedDraft = MutableStateFlow<String?>(null)
    val failedDraft: StateFlow<String?> = _failedDraft
    private val _requestedComposerDraft = MutableStateFlow<String?>(null)
    val requestedComposerDraft: StateFlow<String?> = _requestedComposerDraft
    private val _presentations = MutableStateFlow<Map<String, NativeMessagePresentation>>(emptyMap())
    val presentations: StateFlow<Map<String, NativeMessagePresentation>> = _presentations
    private val _composerOptions = MutableStateFlow(ComposerOptions())
    val composerOptions: StateFlow<ComposerOptions> = _composerOptions
    private val _composerOptionsReady = MutableStateFlow(false)
    val composerOptionsReady: StateFlow<Boolean> = _composerOptionsReady
    private var composerOptionsRevision = 0L
    private val composerPersistenceMutex = Mutex()
    private val _queuedMessages = MutableStateFlow<List<QueuedNativeMessage>>(emptyList())
    val queuedMessages: StateFlow<List<QueuedNativeMessage>> = _queuedMessages
    private val _messageVersions = MutableStateFlow<Map<String, NativeMessageVersionState>>(emptyMap())
    val messageVersions: StateFlow<Map<String, NativeMessageVersionState>> = _messageVersions
    private val _conversationVersions = MutableStateFlow<Map<String, NativeConversationVersions>>(emptyMap())
    val conversationVersions: StateFlow<Map<String, NativeConversationVersions>> = _conversationVersions
    private val _messageOperation = MutableStateFlow<String?>(null)
    val messageOperation: StateFlow<String?> = _messageOperation
    private val _openSession = MutableStateFlow<NativeOpenSession?>(null)
    val openSession: StateFlow<NativeOpenSession?> = _openSession
    private val _sessionClosed = MutableStateFlow(false)
    val sessionClosed: StateFlow<Boolean> = _sessionClosed
    private val json = Json { ignoreUnknownKeys = true }
    private var streamJob: Job? = null
    private var loadJob: Job? = null
    private var activeAssistantId: String? = null
    private var stopping = false
    private var streamEpoch = 0
    private var queuePausedByStop = false
    private var activeStreamSessionId = sessionId
    private val versionJobs = mutableMapOf<String, Job>()
    private var sessionNavigationJob: Job? = null
    private var navigationSessions = emptyList<Session>()
    private var currentSession: Session? = null
    private val createdIdentity = identity()
    private var synchronizationForeground = false
    private var synchronizationJob: Job? = null
    private var remoteFollowing = false
    private var remoteRetryAt = 0L
    private var lastSyncError: String? = null
    private var timelineRevision = 0L
    private val timelineWindows = NativeTimelineWindowReader { path, etag ->
        checkIdentity(createdIdentity)
        api.getConditional(path, etag).also { checkIdentity(createdIdentity) }
    }

    init {
        val restoreRevision = composerOptionsRevision
        viewModelScope.launch {
            try {
                repository?.cachedComposerOptions(sessionId)?.let {
                    if (composerOptionsRevision == restoreRevision) _composerOptions.value = it
                }
            } finally { _composerOptionsReady.value = true }
            loadMessages()
        }
    }

    fun loadMessages(force: Boolean = false) {
        if (_sending.value) return
        loadJob?.cancel()
        loadJob = viewModelScope.launch {
            _error.value = null
            if (_messages.value.isEmpty()) repository?.cachedMessages(sessionId)?.let(::applyPage)
            _loading.value = _messages.value.isEmpty()
            _refreshing.value = true
            var synchronized = false
            try { synchronize(force); synchronized = true }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                reportError(e, "加载失败")
                if (e is ApiException && e.code in listOf(403, 404)) {
                    _messages.value = emptyList()
                    _hasMoreBefore.value = false
                }
            } finally {
                _loading.value = false
                _refreshing.value = false
            }
            if (synchronized) {
                clearSyncError()
                followRemoteIfNeeded()
                resumeQueuedAfterSync()
            }
        }
    }

    private suspend fun synchronize(force: Boolean, expectedEpoch: Int? = null) {
        checkIdentity(createdIdentity)
        val latest = repository?.refreshMessages(sessionId, force)
            ?: json.decodeFromString<MessageListPage>(api.get("/sessions/$sessionId/messages?limit=50&compact=false"))
        val page = timelineWindows.readOlder(sessionId, latest, _messages.value)
        checkIdentity(createdIdentity)
        if (expectedEpoch != null && expectedEpoch != streamEpoch) return
        applyPage(page)
        _isOffline.value = false
    }

    private fun applyPage(page: MessageListPage) {
        if (page.items == _messages.value && page.hasMoreBefore == _hasMoreBefore.value) {
            followRemoteIfNeeded()
            return
        }
        val oldMessages = _messages.value.associateBy { it.id }
        val stableIds = page.items.filter { it.version == oldMessages[it.id]?.version }.map { it.id }.toSet()
        (versionJobs.keys - stableIds).forEach { versionJobs.remove(it)?.cancel() }
        _presentations.value = _presentations.value.filterKeys { it in stableIds }
        _messages.value = page.items
        timelineRevision++
        _hasMoreBefore.value = page.hasMoreBefore
        // A new retry version returns the row to the current answer, as on the Web.
        _messageVersions.value = _messageVersions.value.filter { (id, _) ->
            page.items.any { it.id == id && it.version == oldMessages[id]?.version }
        }
        currentSession?.let { _conversationVersions.value = conversationVersions(page.items, it, navigationSessions, page.hasMoreBefore) }
        loadSessionNavigation()
        followRemoteIfNeeded()
    }

    /** Visible current conversation only; local submissions keep their existing SSE in the background. */
    fun setSynchronizationForeground(foreground: Boolean) {
        if (synchronizationForeground == foreground) return
        synchronizationForeground = foreground
        synchronizationJob?.cancel()
        if (!foreground) {
            if (remoteFollowing) {
                ++streamEpoch
                streamJob?.cancel(); remoteFollowing = false
                _sending.value = false
                feedback.endStream()
                activityScope?.let { key -> activeActivityOwner?.let { activityStore?.releaseOwner(key, it) } }
            }
            return
        }
        synchronizationJob = viewModelScope.launch {
            var wait = 2_000L
            while (isActive && synchronizationForeground) {
                if (!_sending.value && streamJob?.isActive != true && loadJob?.isActive != true && !_loadingOlder.value && _messageOperation.value == null) {
                    val epoch = streamEpoch
                    val revision = timelineRevision
                    try {
                        checkIdentity(createdIdentity)
                        val latest = repository?.refreshMessages(sessionId, true)
                            ?: json.decodeFromString<MessageListPage>(api.get("/sessions/$sessionId/messages?limit=50&compact=false"))
                        val page = timelineWindows.readOlder(sessionId, latest, _messages.value)
                        checkIdentity(createdIdentity)
                        if (synchronizationForeground && epoch == streamEpoch && !_sending.value && !_loadingOlder.value && revision == timelineRevision) {
                            applyPage(page); _isOffline.value = false
                            clearSyncError()
                            resumeQueuedAfterSync()
                        }
                        wait = 2_000L
                    } catch (e: CancellationException) { throw e }
                    catch (e: Exception) {
                        if (e is ApiException && e.code in listOf(401, 403, 404)) {
                            _messages.value = emptyList(); _hasMoreBefore.value = false
                            reportError(e, "会话已删除或无法访问")
                            break
                        }
                        _isOffline.value = e is ApiException && e.code == null
                        wait = (wait * 2).coerceAtMost(10_000)
                    }
                }
                delay(wait)
            }
        }
        followRemoteIfNeeded()
    }

    private fun followRemoteIfNeeded() {
        if (!synchronizationForeground || _sending.value || _loading.value || _refreshing.value || _messageOperation.value != null || stopping || System.currentTimeMillis() < remoteRetryAt) return
        val message = _messages.value.lastOrNull { it.role == "assistant" && it.status in NativeRemoteEventCursor.inFlight } ?: return
        val sourceSessionId = message.sessionId
        val epoch = ++streamEpoch
        val owner = "native-replay:${UUID.randomUUID()}"
        remoteFollowing = true; _sending.value = true; activeAssistantId = message.id
        activeStreamSessionId = sourceSessionId; activeActivityOwner = owner
        activityScope?.let { activityStore?.running(it, owner, sessionId, true) }
        feedback.beginStream()
        streamJob = viewModelScope.launch {
            var terminal = false
            try {
                checkIdentity(createdIdentity)
                val snapshot = json.parseToJsonElement(api.get("/sessions/$sourceSessionId/messages/${message.id}")).jsonObject
                checkIdentity(createdIdentity)
                val cursor = NativeRemoteEventCursor(snapshot)
                val initial = cursor.message(snapshot)
                if (epoch != streamEpoch || !synchronizationForeground) return@launch
                clearSyncError()
                _isOffline.value = false
                _messages.value = _messages.value.map { if (it.id == message.id) initial else it }
                terminal = initial.status !in NativeRemoteEventCursor.inFlight
                val seen = mutableSetOf<String>()
                var wait = 400L
                var snapshotAt = System.currentTimeMillis()
                while (!terminal && isActive && synchronizationForeground && epoch == streamEpoch) {
                    val previousCursor = cursor.afterEventId
                    val events = json.parseToJsonElement(api.get(cursor.eventsPath(sourceSessionId, message.id))).jsonArray
                    checkIdentity(createdIdentity)
                    if (epoch != streamEpoch || !synchronizationForeground) break
                    clearSyncError()
                    _isOffline.value = false
                    for (raw in events) {
                        val envelope = raw as? JsonObject ?: continue
                        val event = cursor.accept(envelope) ?: continue
                        val update = NativeStreamReducer.reduce(sourceSessionId, _messages.value, event, seen)
                        _messages.value = update.messages
                        update.error?.let { _error.value = it }
                        if (update.presentation != null && update.assistantId != null) _presentations.value += update.assistantId to update.presentation
                        val part = envelope["part"] as? JsonObject
                        feedback.onEvent(event.event, event.id, part?.get("type")?.jsonPrimitive?.contentOrNull, part?.get("status")?.jsonPrimitive?.contentOrNull)
                        terminal = _messages.value.firstOrNull { it.id == message.id }?.status !in NativeRemoteEventCursor.inFlight
                    }
                    // Detect cancellation, a newer retry version or producer restart even when no new event arrives.
                    if (!terminal && System.currentTimeMillis() - snapshotAt >= 2_000) {
                        val current = json.parseToJsonElement(api.get("/sessions/$sourceSessionId/messages/${message.id}")).jsonObject
                        checkIdentity(createdIdentity)
                        if (epoch != streamEpoch || !synchronizationForeground) break
                        if (current["message_version_id"]?.jsonPrimitive?.contentOrNull != cursor.versionId) break
                        val persisted = cursor.message(current)
                        if (persisted.status !in NativeRemoteEventCursor.inFlight) {
                            _messages.value = _messages.value.map { if (it.id == message.id) persisted else it }; terminal = true
                        }
                        snapshotAt = System.currentTimeMillis()
                    }
                    if (terminal) break
                    val advanced = previousCursor != cursor.afterEventId
                    wait = NativeRemoteEventCursor.nextDelay(wait, advanced)
                    delay(if (events.size >= 500 && advanced) 0 else wait)
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                if (epoch == streamEpoch) {
                    remoteRetryAt = System.currentTimeMillis() + 2_000
                    if (e is ApiException && e.code in listOf(401, 403, 404)) { _messages.value = emptyList(); _hasMoreBefore.value = false }
                    reportError(e, "同步中断，将自动重连")
                    lastSyncError = _error.value
                }
            } finally {
                activityScope?.let { key ->
                    if (terminal && !stopping && epoch == streamEpoch) activityStore?.finished(key, owner, sessionId)
                    activityStore?.releaseOwner(key, owner)
                }
                if (epoch == streamEpoch) {
                    remoteFollowing = false; _sending.value = false; feedback.endStream()
                    if (synchronizationForeground && isActive && !stopping) {
                        try { synchronize(true, expectedEpoch = epoch) }
                        catch (e: CancellationException) { throw e }
                        catch (_: Exception) { /* The quiet foreground detector retries. */ }
                        if (epoch == streamEpoch && terminal && _error.value == null && !queuePausedByStop) dispatchNextQueued()
                    }
                }
            }
        }
    }

    fun loadOlderMessages() {
        if (_loadingOlder.value || _refreshing.value || _sending.value || !_hasMoreBefore.value) return
        val oldestId = _messages.value.firstOrNull()?.id ?: return
        _loadingOlder.value = true
        viewModelScope.launch {
            try {
                val page = repository?.loadOlderMessages(sessionId, oldestId)
                    ?: json.decodeFromString<MessageListPage>(api.get("/sessions/$sessionId/messages?limit=50&compact=false&before_id=$oldestId"))
                if (_messages.value.firstOrNull()?.id == oldestId) {
                    _messages.value = (page.items + _messages.value).distinctBy { it.id }
                    timelineRevision++
                    _hasMoreBefore.value = page.hasMoreBefore
                    currentSession?.let { _conversationVersions.value = conversationVersions(_messages.value, it, navigationSessions, page.hasMoreBefore) }
                }
                _isOffline.value = false
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { reportError(e, "加载较早消息失败") }
            finally { _loadingOlder.value = false }
        }
    }

    fun send(content: String) {
        sendWithExtras(content)
    }

    fun sendRenderedAction(raw: String) {
        val submission = try { NativeRendererSubmission.parse(raw) }
        catch (e: IllegalArgumentException) {
            _error.value = "无效的交互动作：${e.message ?: "格式错误"}"
            return
        }
        if (!submission.autoSend) {
            _requestedComposerDraft.value = submission.action.content
            return
        }
        sendWithExtras(submission.action.content, submission.action.requestExtras)
    }

    private fun sendWithExtras(content: String, requestExtras: JsonObject = JsonObject(emptyMap()), options: ComposerOptions = _composerOptions.value) {
        if (content.isBlank()) return
        if (_messageOperation.value != null) { _error.value = "请等待当前消息操作完成"; _failedDraft.value = content; return }
        if (_sessionClosed.value) { _error.value = "会话已结束，请创建分支或新会话继续"; _failedDraft.value = content; return }
        val body = try { nativeMessageRequestBody(options, content, requestExtras) }
        catch (e: IllegalArgumentException) {
            _error.value = e.message
            _failedDraft.value = content
            return
        }
        if (_sending.value) {
            _queuedMessages.value += QueuedNativeMessage(UUID.randomUUID().toString(), content, options, requestExtras)
            return
        }
        val localId = "local-${UUID.randomUUID()}"
        _messages.value += Message(
            id = localId, sessionId = sessionId, role = "user", status = "pending", content = content,
            parts = listOf(MessagePart(id = "$localId-p", type = "text", status = "completed", content = content)),
        )
        _messages.value += Message(
            id = "local-assistant-$localId", sessionId = sessionId, parentMessageId = localId,
            role = "assistant", status = "streaming", parts = listOf(
                MessagePart(id = "local-ack-$localId", type = "acknowledgement", status = "pending", content = ""),
            ),
        )
        startStream("/sessions/$sessionId/messages/stream", body, content, localId)
    }

    fun retry(messageId: String, options: ComposerOptions = _composerOptions.value, allowedDomains: String = "") {
        val message = _messages.value.firstOrNull { it.id == messageId && it.role == "assistant" } ?: return
        if (_sending.value || _messageOperation.value != null || !message.isPersistedMessage()) return
        if (_sessionClosed.value) { _error.value = "会话已结束，请创建分支或新会话继续"; return }
        val body = try { nativeRetryBody(options, allowedDomains) }
        catch (e: IllegalArgumentException) { _error.value = e.message; return }
        versionJobs.remove(messageId)?.cancel()
        _messageVersions.value -= messageId
        _presentations.value -= messageId
        _messages.value = _messages.value.map { if (it.id == messageId) it.copy(
            status = "streaming", version = (it.version ?: 0) + 1, content = "", providerTrace = null,
            parts = listOf(MessagePart("local-retry-ack-$messageId", "acknowledgement", "pending", "")),
        ) else it }
        // A message inherited from a branch still belongs to its original session.
        startStream("/sessions/${message.sessionId}/messages/$messageId/retry", body, sourceSessionId = message.sessionId, retryOriginal = message)
    }

    /** Fetch only on explicit interest in versions; never issue N snapshot calls on mount. */
    fun requestMessageVersions(message: Message, force: Boolean = false) {
        if (!message.isPersistedMessage() || message.role != "assistant" || !sameCurrentMessageVersion(message)) return
        val old = _messageVersions.value[message.id] ?: NativeMessageVersionState()
        if (old.loading || old.loaded && !force) return
        val identity = identity()
        _messageVersions.value += (message.id to old.copy(loading = true, error = null))
        versionJobs[message.id] = viewModelScope.launch {
            try {
                val versions = json.decodeFromString<List<NativeMessageVersion>>(api.get("/sessions/${message.sessionId}/messages/${message.id}/versions"))
                checkIdentity(identity)
                if (!sameCurrentMessageVersion(message)) return@launch
                val state = _messageVersions.value[message.id] ?: old
                _messageVersions.value += (message.id to state.copy(versions = versions.sortedBy { it.version }, loading = false, loaded = true))
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                checkIdentity(identity)
                if (!sameCurrentMessageVersion(message)) return@launch
                _messageVersions.value += (message.id to old.copy(error = e.message ?: "读取版本失败"))
                reportError(e, "读取版本失败")
            }
        }
    }

    fun selectMessageVersion(message: Message, versionId: String?) {
        if (!sameCurrentMessageVersion(message)) return
        versionJobs.remove(message.id)?.cancel()
        val old = _messageVersions.value[message.id] ?: return
        val version = old.versions.firstOrNull { it.id == versionId }
        if (versionId == null || version?.version == message.version) {
            _messageVersions.value += (message.id to old.copy(selectedVersionId = null, snapshot = null, loading = false, error = null))
            return
        }
        if (version == null) return
        val identity = identity()
        _messageVersions.value += (message.id to old.copy(loading = true, error = null))
        versionJobs[message.id] = viewModelScope.launch {
            try {
                val snapshot = json.decodeFromString<Message>(api.get("/sessions/${message.sessionId}/messages/${message.id}?message_version_id=$versionId"))
                checkIdentity(identity)
                if (!sameCurrentMessageVersion(message)) return@launch
                _messageVersions.value += (message.id to old.copy(selectedVersionId = versionId, snapshot = snapshot, loading = false, error = null))
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                checkIdentity(identity)
                if (!sameCurrentMessageVersion(message)) return@launch
                _messageVersions.value += (message.id to old.copy(loading = false, error = e.message ?: "读取历史回答失败"))
                reportError(e, "读取历史回答失败")
            }
        }
    }

    private fun sameCurrentMessageVersion(message: Message): Boolean = _messages.value.any {
        it.id == message.id && it.sessionId == message.sessionId && it.version == message.version
    }

    private fun clearSyncError() {
        if (lastSyncError != null && _error.value == lastSyncError) _error.value = null
        lastSyncError = null
    }

    private fun resumeQueuedAfterSync() {
        if (_queuedMessages.value.isEmpty()) return
        if (canResumeQueuedAfterSync(synchronizationForeground, _sending.value, queuePausedByStop,
                _error.value != null, _sessionClosed.value, _messageOperation.value != null, _messages.value)) dispatchNextQueued()
    }

    /** Editing is a new branch plus pending prompt; never mutate the original user turn. */
    fun branchFrom(message: Message, editedContent: String? = null) {
        if (_sending.value || _messageOperation.value != null || !message.isPersistedMessage()) return
        if (editedContent != null && (editedContent.isBlank() || editedContent.length > 50_000)) {
            _error.value = "消息长度应在 1 到 50000 字符之间"; return
        }
        if (editedContent == null && message.sessionId != sessionId) {
            _error.value = "请先切换到该回答所属的会话版本"; return
        }
        val identity = identity()
        val inheritedOptions = _composerOptions.value
        _messageOperation.value = message.id
        _error.value = null
        viewModelScope.launch {
            try {
                val created = json.decodeFromString<Session>(api.post("/sessions/${message.sessionId}/messages/${message.id}/branch", "{}"))
                checkIdentity(identity)
                require(created.workspaceId == api.workspaceId) { "分支工作区发生变化，请刷新会话" }
                // The server has committed the branch. A local cache write must not duplicate it on retry.
                try {
                    repository?.rememberSession(created)
                    repository?.saveComposerOptions(created.id, inheritedOptions)
                } catch (e: CancellationException) { throw e }
                catch (_: Exception) { /* The next sidebar refresh reconciles a failed local write. */ }
                checkIdentity(identity)
                _openSession.value = NativeOpenSession(created.id, editedContent?.trim(), inheritedOptions)
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { reportError(e, "创建分支失败") }
            finally { _messageOperation.value = null }
        }
    }

    fun openConversationVersion(targetSessionId: String) {
        if (_sending.value || _messageOperation.value != null) return
        if (_conversationVersions.value.values.none { targetSessionId in it.sessionIds }) return
        _openSession.value = NativeOpenSession(targetSessionId, null, _composerOptions.value)
    }
    fun consumeOpenSession() { _openSession.value = null }

    private fun loadSessionNavigation() {
        if (sessionNavigationJob?.isActive == true) return
        val identity = identity()
        sessionNavigationJob = viewModelScope.launch {
            try {
                var sessions = repository?.refreshSessions() ?: json.decodeFromString<List<Session>>(api.get("/sessions"))
                checkIdentity(identity)
                if (sessions.none { it.id == sessionId }) {
                    sessions = repository?.refreshSessions(force = true) ?: json.decodeFromString<List<Session>>(api.get("/sessions"))
                    checkIdentity(identity)
                }
                val current = sessions.firstOrNull { it.id == sessionId } ?: return@launch
                checkIdentity(identity)
                navigationSessions = sessions
                currentSession = current
                _sessionClosed.value = current.status == "closed"
                _conversationVersions.value = conversationVersions(_messages.value, current, sessions, _hasMoreBefore.value)
            } catch (e: CancellationException) { throw e }
            catch (_: Exception) { /* Message history remains usable when sidebar metadata is offline. */ }
        }
    }

    private fun identity(): List<String?> = listOf(api.baseUrl, api.token, api.workspaceId, api.accountId)
    private fun checkIdentity(expected: List<String?>) { if (identity() != expected) throw CancellationException("账户或工作区已变化") }

    fun consumeFailedDraft() { _failedDraft.value = null }
    fun consumeRequestedComposerDraft() { _requestedComposerDraft.value = null }
    /** Screen visibility and RESUMED lifecycle must both be true; streams may continue in the background. */
    fun setHapticsForeground(foreground: Boolean) { feedback.setForeground(foreground) }
    fun setComposerOptions(options: ComposerOptions) {
        val revision = ++composerOptionsRevision
        val scope = identity()
        _composerOptions.value = options
        viewModelScope.launch {
            try { composerPersistenceMutex.withLock {
                checkIdentity(scope)
                if (revision == composerOptionsRevision) repository?.saveComposerOptions(sessionId, options)
            } }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { /* Preference persistence must not interrupt input. */ }
        }
    }
    fun removeQueuedMessage(id: String) { _queuedMessages.value = _queuedMessages.value.filterNot { it.id == id } }

    private fun startStream(path: String, body: String, draft: String? = null, localId: String? = null, sourceSessionId: String = sessionId, retryOriginal: Message? = null) {
        val epoch = ++streamEpoch
        val activityOwner = "native-stream:${UUID.randomUUID()}"
        activeActivityOwner = activityOwner
        activityScope?.let {
            activityStore?.touched(it, sourceSessionId)
            activityStore?.running(it, activityOwner, sourceSessionId, true)
        }
        feedback.beginStream()
        loadJob?.cancel()
        _loading.value = false
        _refreshing.value = false
        _sending.value = true
        _error.value = null
        activeAssistantId = null
        activeStreamSessionId = sourceSessionId
        stopping = false
        queuePausedByStop = false
        streamJob = viewModelScope.launch {
            val seen = mutableSetOf<String>()
            var workingMessages = _messages.value
            var lastPublished = System.nanoTime() / 1_000_000
            var pendingPublish: Job? = null
            try {
                api.streamSse(path, body).collect { event ->
                    val update = NativeStreamReducer.reduce(sourceSessionId, workingMessages, event, seen)
                    workingMessages = update.messages
                    val envelope = runCatching { json.parseToJsonElement(event.rawData) as? JsonObject }.getOrNull()
                    val type = (envelope?.get("type") as? JsonPrimitive)?.content ?: event.event
                    val part = envelope?.get("part") as? JsonObject
                    val partType = (part?.get("type") as? JsonPrimitive)?.content
                    val partStatus = (part?.get("status") as? JsonPrimitive)?.content
                    val eventId = (envelope?.get("event_id") as? JsonPrimitive)?.content ?: event.id
                    if (epoch == streamEpoch) feedback.onEvent(type, eventId, partType, partStatus)
                    val delta = type == "part.delta" || type == "message.part.delta"
                    val frameMillis = if (partType in listOf("reasoning_content", "reasoning_summary")) 16 else 50
                    val now = System.nanoTime() / 1_000_000
                    if (!delta || now - lastPublished >= frameMillis) {
                        pendingPublish?.cancel()
                        pendingPublish = null
                        _messages.value = workingMessages
                        lastPublished = now
                    } else if (pendingPublish == null) {
                        pendingPublish = launch {
                            delay(frameMillis - (now - lastPublished))
                            if (epoch == streamEpoch) _messages.value = workingMessages
                            lastPublished = System.nanoTime() / 1_000_000
                            pendingPublish = null
                        }
                    }
                    update.assistantId?.let { activeAssistantId = it }
                    update.error?.let { _error.value = it }
                    if (update.presentation != null && update.assistantId != null) {
                        _presentations.value += (update.assistantId to update.presentation)
                    }
                }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { reportError(e, "发送失败") }
            finally {
                pendingPublish?.cancel()
                activityScope?.let {
                    if (!stopping && isActive && epoch == streamEpoch) activityStore?.finished(it, activityOwner, sourceSessionId)
                    activityStore?.releaseOwner(it, activityOwner)
                }
                if (epoch == streamEpoch) feedback.endStream()
                if (!stopping && epoch == streamEpoch) {
                    _messages.value = workingMessages
                    if (activeAssistantId == null && draft != null) {
                        _failedDraft.value = draft
                        _messages.value = _messages.value.filterNot { it.id == localId || it.id == "local-assistant-$localId" }
                        if (_error.value == null) _error.value = "服务器未确认接收，请检查后重新发送"
                    }
                    if (activeAssistantId == null && retryOriginal != null) {
                        _messages.value = _messages.value.map { if (it.id == retryOriginal.id) retryOriginal else it }
                    }
                    _sending.value = false
                    // Reconcile once after the stream, without fetching the whole history.
                    if (isActive) {
                        _refreshing.value = true
                        try { synchronize(true, expectedEpoch = epoch) }
                        catch (e: CancellationException) { throw e }
                        catch (e: Exception) { if (epoch == streamEpoch) reportError(e, "同步失败，已保留当前回复") }
                        finally { if (epoch == streamEpoch) _refreshing.value = false }
                    }
                    followRemoteIfNeeded()
                    if (epoch == streamEpoch && _error.value == null) dispatchNextQueued()
                }
            }
        }
    }

    fun stop() {
        if (!_sending.value || stopping) return
        feedback.endStream()
        stopping = true
        activityScope?.let { current -> activeActivityOwner?.let { activityStore?.releaseOwner(current, it) } }
        queuePausedByStop = true
        streamJob?.cancel()
        viewModelScope.launch {
            try {
                activeAssistantId?.let { api.post("/sessions/$activeStreamSessionId/messages/$it/cancel", "{}") }
                    ?: run { _error.value = "请求仍在提交，服务器可能继续生成；请刷新查看结果" }
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) { reportError(e, "停止失败，请刷新确认生成状态") }
            finally {
                _sending.value = false
                streamJob = null
                stopping = false
                loadMessages(force = true)
            }
        }
    }

    private fun dispatchNextQueued() {
        val next = _queuedMessages.value.firstOrNull() ?: return
        _queuedMessages.value = _queuedMessages.value.drop(1)
        sendWithExtras(next.content, next.requestExtras, next.options)
    }

    private fun reportError(e: Exception, fallback: String) {
        checkIdentity(createdIdentity)
        _isOffline.value = e is ApiException && e.code == null
        _error.value = e.message ?: fallback
        if (e is ApiException && e.code == 401) onAuthExpired()
    }

    override fun onCleared() {
        activityScope?.let { current -> activeActivityOwner?.let { activityStore?.releaseOwner(current, it) } }
        feedback.close()
        super.onCleared()
    }
}
