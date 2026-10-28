package com.learngraph.mobile.data

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.net.InetAddress
import java.net.ServerSocket
import java.net.SocketException
import java.net.URI
import java.io.File
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class NativeRepositoryTest {
    @get:Rule val temporary = TemporaryFolder()
    private lateinit var server: TestHttpServer
    private lateinit var api: ApiClient
    private lateinit var repository: NativeRepository
    private lateinit var cacheDirectory: File
    private var sessionsBody = """[{"id":"s1","workspace_id":"w1","title":"Original"}]"""
    private var sessionsEtag = "\"sessions-v1\""
    private var messageBody = """{"items":[{"id":"m1","session_id":"s1","role":"assistant","content":"Cached answer"}],"has_more_before":true,"oldest_id":"m1","newest_id":"m1","total_count":90}"""
    private var messageEtag = "\"messages-v1\""
    private var projectsBody = """[{"id":"p1","workspace_id":"w1","title":"Original project","status":"active","primary_goal_id":"goal1","primary_graph_id":"graph1","position":0}]"""
    private var projectsEtag = "\"projects-v1\""
    private var projectRequestStarted: CountDownLatch? = null
    private var projectResponseGate: CountDownLatch? = null
    private val requests = AtomicInteger()
    private val notModified = AtomicInteger()
    private var lastQuery: String? = null
    private var mutateIdentity = false

    @Before fun setup() {
        server = TestHttpServer { request ->
            if (request.uri.path == "/api/v1/events") {
                return@TestHttpServer TestHttpResponse(200,
                    (0 until 512).joinToString("") { "event: token\ndata: $it\n\n" },
                    contentType = "text/event-stream")
            }
            if (request.uri.path == "/api/v1/rejected-events") {
                return@TestHttpServer TestHttpResponse(403, """{"detail":"Generation denied"}""")
            }
            requests.incrementAndGet()
            lastQuery = request.uri.rawQuery
            val isMessages = request.uri.path.endsWith("/messages")
            val isProjects = request.uri.path == "/api/v1/projects"
            val etag = if (isProjects) projectsEtag else if (isMessages) messageEtag else sessionsEtag
            val body = if (isProjects) projectsBody else if (isMessages) messageBody else sessionsBody
            if (isProjects) {
                projectRequestStarted?.countDown()
                projectResponseGate?.await(5, TimeUnit.SECONDS)
            }
            if (mutateIdentity) api.accountId = "another-user"
            if (request.headers["if-none-match"] == etag) {
                notModified.incrementAndGet()
                TestHttpResponse(304, "", etag)
            } else {
                TestHttpResponse(200, body, etag)
            }
        }
        api = ApiClient(Json { ignoreUnknownKeys = true }).apply {
            baseUrl = "http://127.0.0.1:${server.port}"
            token = "token1"
            accountId = "user1"
            workspaceId = "w1"
        }
        cacheDirectory = temporary.newFolder()
        repository = NativeRepository(NativeCacheStore(cacheDirectory, Json { ignoreUnknownKeys = true }), api)
    }

    @After fun shutdown() { server.stop() }

    @Test fun freshCacheAvoidsRequestAndForcedRefreshUses304() = runBlocking {
        assertEquals("Original", repository.refreshSessions().single().title)
        repository.refreshSessions()
        assertEquals(1, requests.get())
        assertEquals("Original", repository.refreshSessions(force = true).single().title)
        assertEquals(1, notModified.get())
        assertEquals("Original", repository.cachedSessions()!!.single().title)
        sessionsBody = """[{"id":"s1","workspace_id":"w1","title":"Renamed"}]"""
        sessionsEtag = "\"sessions-v2\""
        assertEquals("Renamed", repository.refreshSessions(force = true).single().title)
        assertEquals("Renamed", repository.cachedSessions()!!.single().title)
    }

    @Test fun projectCacheUsesThirtySecondTtlAndSurvivesAnOfflineRestart() = runBlocking {
        val original = repository.refreshProjects().single()
        assertEquals("Original project", original.title)
        assertEquals("include_archived=true", lastQuery)
        repository.refreshProjects()
        assertEquals(1, requests.get())
        val restarted = NativeRepository(NativeCacheStore(cacheDirectory, Json { ignoreUnknownKeys = true }), api)
        assertEquals(original, restarted.cachedProjects()!!.single())
        assertEquals(original, restarted.refreshProjects().single())
        assertEquals(1, requests.get())
        server.stop()
        try { restarted.refreshProjects(force = true); fail("Expected offline error") }
        catch (_: ApiException) { }
        assertEquals(original, restarted.cachedProjects()!!.single())
    }

    @Test fun confirmedProjectCreateRenameArchiveAndDeleteUpdateThePrivateDiskList() = runBlocking {
        val original = repository.refreshProjects().single()
        val renamed = original.copy(title = "Renamed project", status = "archived")
        repository.rememberProject(renamed)
        assertEquals(renamed, repository.cachedProjects()!!.single())
        repository.rememberProject(NativeProject("p2", "New project", position = 1))
        assertEquals(listOf("p1", "p2"), repository.cachedProjects()!!.map { it.id })
        repository.removeProjects(listOf("p1"))
        val restarted = NativeRepository(NativeCacheStore(cacheDirectory, Json { ignoreUnknownKeys = true }), api)
        assertEquals(listOf("p2"), restarted.cachedProjects()!!.map { it.id })
    }

    @Test fun projectCachesRemainIsolatedAndAnAccountChangeCannotOverwriteSavedProjects() = runBlocking {
        repository.refreshProjects()
        api.workspaceId = "w2"
        assertNull(repository.cachedProjects())
        api.workspaceId = "w1"
        api.accountId = "user2"
        assertNull(repository.cachedProjects())
        api.accountId = "user1"
        mutateIdentity = true
        try { repository.refreshProjects(force = true); fail("Expected scope-change cancellation") }
        catch (_: CancellationException) { }
        assertNull(repository.cachedProjects())
        api.accountId = "user1"
        assertEquals("Original project", repository.cachedProjects()!!.single().title)
    }

    @Test fun aConfirmedDeletionWinsOverAnOlderInflightProjectListResponse() = runBlocking {
        repository.refreshProjects()
        projectRequestStarted = CountDownLatch(1)
        projectResponseGate = CountDownLatch(1)
        val oldRefresh = async(Dispatchers.Default) { repository.refreshProjects(force = true) }
        assertTrue(projectRequestStarted!!.await(5, TimeUnit.SECONDS))
        val deletion = async(Dispatchers.Default) { repository.removeProjects(listOf("p1")) }
        projectResponseGate!!.countDown()
        oldRefresh.await()
        deletion.await()
        assertTrue(repository.cachedProjects()!!.isEmpty())
        val restarted = NativeRepository(NativeCacheStore(cacheDirectory, Json { ignoreUnknownKeys = true }), api)
        assertTrue(restarted.cachedProjects()!!.isEmpty())
    }

    @Test fun messageWindowIsBoundedAndOlderMessagesUseEncodedCursor() = runBlocking {
        val page = repository.refreshMessages("s1")
        assertTrue(page.hasMoreBefore)
        assertEquals(90, page.totalCount)
        assertEquals("limit=50&compact=false", lastQuery)
        repository.refreshMessages("s1", force = true)
        assertEquals(1, notModified.get())
        repository.loadOlderMessages("s1", "old & cursor")
        assertEquals("limit=50&compact=false&before_id=old%20%26%20cursor", lastQuery)
        assertEquals(page, repository.cachedMessages("s1"))
    }

    @Test fun remoteSessionDeletionPrunesMessageCacheAndNeverMergesStaleRows() = runBlocking {
        repository.refreshSessions()
        repository.refreshMessages("s1")
        assertNotNull(repository.cachedMessages("s1"))
        sessionsBody = "[]"
        sessionsEtag = "\"sessions-empty\""
        assertTrue(repository.refreshSessions(force = true).isEmpty())
        assertNull(repository.cachedMessages("s1"))
    }

    @Test fun accountChangesCannotReadOrOverwriteAnotherAccountCache() = runBlocking {
        repository.refreshSessions()
        api.accountId = "user2"
        assertNull(repository.cachedSessions())
        api.accountId = "user1"
        assertEquals("Original", repository.cachedSessions()!!.single().title)
        mutateIdentity = true
        try {
            repository.refreshSessions(force = true)
            fail("Expected scope-change cancellation")
        } catch (_: CancellationException) { }
        assertNull(repository.cachedSessions())
        api.accountId = "user1"
        assertEquals("Original", repository.cachedSessions()!!.single().title)
    }

    @Test fun changedBearerCannotInheritPreviousAccountIdentity() = runBlocking {
        repository.refreshSessions()
        api.token = "new-bearer"
        assertNull(api.accountId)
        assertNull(repository.cachedSessions())
        // Only a verified login identity can reconnect the same user's stable cache.
        api.accountId = "user1"
        assertEquals("Original", repository.cachedSessions()!!.single().title)
    }

    @Test fun offlineRefreshKeepsPreviouslySavedData() = runBlocking {
        repository.refreshMessages("s1")
        server.stop()
        try {
            repository.refreshMessages("s1", force = true)
            fail("Expected offline error")
        } catch (_: ApiException) { }
        assertEquals("Cached answer", repository.cachedMessages("s1")!!.items.single().content)
    }

    @Test fun burstSseDoesNotLoseEventsWhileCollectorIsSlow() = runBlocking {
        val received = mutableListOf<Int>()
        api.streamSse("/events", "{}").collect {
            delay(1)
            received.add(it.rawData.toInt())
        }
        assertEquals((0 until 512).toList(), received)
    }

    @Test fun sseHttpRejectionPreservesStatusAndServerReason() = runBlocking {
        try {
            api.streamSse("/rejected-events", "{}").collect { }
            fail("Expected HTTP rejection")
        } catch (e: ApiException) {
            assertEquals(403, e.code)
            assertEquals("Generation denied", e.message)
        }
    }
}

/** Tiny loopback HTTP fixture using only Java APIs also visible to Android's test compiler. */
private data class TestHttpRequest(val uri: URI, val headers: Map<String, String>)
private data class TestHttpResponse(val status: Int, val body: String, val etag: String? = null,
    val contentType: String = "application/json")

private class TestHttpServer(private val respond: (TestHttpRequest) -> TestHttpResponse) {
    private val server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    val port: Int = server.localPort
    @Volatile private var stopped = false
    private val worker = Thread({
        while (!stopped) {
            try {
                server.accept().use { socket ->
                    socket.soTimeout = 5_000
                    val reader = socket.getInputStream().bufferedReader(Charsets.ISO_8859_1)
                    val requestLine = reader.readLine() ?: return@use
                    val headers = mutableMapOf<String, String>()
                    while (true) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) break
                        val colon = line.indexOf(':')
                        if (colon > 0) headers[line.substring(0, colon).lowercase(Locale.ROOT)] = line.substring(colon + 1).trim()
                    }
                    // Requests in this fixture contain only small ASCII JSON objects.
                    repeat(headers["content-length"]?.toIntOrNull() ?: 0) { reader.read() }
                    val response = respond(TestHttpRequest(URI(requestLine.split(' ')[1]), headers))
                    val body = response.body.toByteArray(Charsets.UTF_8)
                    val head = buildString {
                        append("HTTP/1.1 ${response.status} Test\r\nConnection: close\r\n")
                        append("Content-Type: ${response.contentType}\r\nContent-Length: ${body.size}\r\n")
                        response.etag?.let { append("ETag: $it\r\n") }
                        append("\r\n")
                    }.toByteArray(Charsets.ISO_8859_1)
                    socket.getOutputStream().apply { write(head); write(body); flush() }
                }
            } catch (e: SocketException) {
                if (!stopped) throw e
            }
        }
    }, "native-cache-http-fixture").apply { isDaemon = true; start() }

    fun stop() {
        stopped = true
        server.close()
        worker.join(2_000)
    }
}
