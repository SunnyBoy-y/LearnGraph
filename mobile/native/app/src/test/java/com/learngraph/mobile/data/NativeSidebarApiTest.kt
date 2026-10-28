package com.learngraph.mobile.data

import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.*
import org.junit.Test
import java.net.InetAddress
import java.net.ServerSocket
import java.util.Locale
import java.util.concurrent.CopyOnWriteArrayList

class NativeSidebarApiTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun newSessionsUseOnlyTheChosenProjectGoalAndGraph() {
        val project = NativeProject("p1", "Project", primaryGoalId = "goal1", primaryGraphId = "graph1")
        val body = json.parseToJsonElement(NativeSidebarRequests.createSession(project)).jsonObject
        assertEquals("p1", body.getValue("project_id").jsonPrimitive.content)
        assertEquals("goal1", body.getValue("goal_id").jsonPrimitive.content)
        assertEquals("graph1", body.getValue("graph_id").jsonPrimitive.content)
        assertTrue(body.getValue("memory_enabled").jsonPrimitive.boolean)
        val ungrouped = json.parseToJsonElement(NativeSidebarRequests.createSession(null)).jsonObject
        for (key in listOf("project_id", "goal_id", "graph_id")) assertEquals(JsonNull, ungrouped.getValue(key))
    }

    @Test fun sessionAndProjectSnapshotsDecodeTheActualSidebarFields() {
        val session = json.decodeFromString<Session>("""{"id":"s","workspace_id":"w","title":"Side","project_id":"p","parent_session_id":"parent","source_message_id":"source","status":"archived","pinned":true}""")
        assertEquals("p", session.projectId)
        assertEquals("source", session.sourceMessageId)
        assertEquals("parent", session.parentSessionId)
        val project = json.decodeFromString<NativeProject>("""{"id":"p","workspace_id":"w","title":"Project","primary_goal_id":"g","primary_graph_id":"graph","position":2,"status":"archived"}""")
        assertEquals("g", project.primaryGoalId)
        assertEquals("graph", project.primaryGraphId)
        assertEquals(2, project.position)
    }

    @Test fun movingOutOfAProjectExplicitlyClearsTheAssignmentAndPathsEncodeIds() {
        assertEquals(JsonNull, json.parseToJsonElement(NativeSidebarRequests.move(null)).jsonObject.getValue("project_id"))
        assertEquals("/sessions/a%2Fb%3Fquery/project", NativeSidebarRequests.path("sessions", "a/b?query", "project"))
        assertEquals("https://host.example/share/a%2Fb%3Ftoken", NativeSidebarRequests.shareUrl("https://host.example/ignored", "a/b?token"))
    }

    @Test fun batchDeletionNormalizesTheExactSelectionAndKeepsServerConfirmationOpaque() {
        val body = json.parseToJsonElement(NativeSidebarRequests.deleteSessions(listOf(" s1 ", "s2", "s1"), "删除 \"2\" 个会话")).jsonObject
        assertEquals(listOf("s1", "s2"), body.getValue("session_ids").jsonArray.map { it.jsonPrimitive.content })
        assertEquals("删除 \"2\" 个会话", body.getValue("confirmation_text").jsonPrimitive.content)
        for (ids in listOf(emptyList(), listOf(" "), listOf("x".repeat(37)), List(101) { "s$it" })) {
            invalid { NativeSidebarRequests.deleteSessions(ids) }
        }
        val result = json.decodeFromString<NativeSessionDeleteResult>("""{"status":"deleted","deleted_session_ids":["s1"],"deleted_count":1,"impacts":[]}""")
        assertEquals(listOf("s1"), result.deletedSessionIds)
    }

    @Test fun sharingMatchesFullRangeAndAnswerOnlyScopesWithoutLeakingOtherMessages() {
        val full = json.parseToJsonElement(NativeSidebarRequests.share("full", "", null, "ignored", "ignored")).jsonObject
        assertEquals(setOf("scope"), full.keys)
        val answers = json.parseToJsonElement(NativeSidebarRequests.share("answers", " label ", 5, null, null)).jsonObject
        assertTrue(answers.getValue("answers_only").jsonPrimitive.boolean)
        assertEquals("label", answers.getValue("label").jsonPrimitive.content)
        assertEquals("5", answers.getValue("max_views").jsonPrimitive.content)
        val range = json.parseToJsonElement(NativeSidebarRequests.share("range", "", null, "m1", "m2")).jsonObject
        assertEquals("m1", range.getValue("from_message_id").jsonPrimitive.content)
        assertEquals("m2", range.getValue("to_message_id").jsonPrimitive.content)
        invalid { NativeSidebarRequests.share("range", "", null, null, "m2") }
        invalid { NativeSidebarRequests.share("full", "", 0, null, null) }
        invalid { NativeSidebarRequests.share("unknown", "", null, null, null) }
        invalid { NativeSidebarRequests.share("full", "x".repeat(121), null, null, null) }
        assertEquals("😀".repeat(240), NativeSidebarRequests.title("😀".repeat(240)))
    }

    @Test fun shareManagementDoesNotRequireTheOneTimeRawToken() {
        val share = json.decodeFromString<NativeSessionShare>("""{"id":"share","title":"Snapshot","scope":"answers","message_count":2,"status":"active","tokens":[{"id":"token","token_prefix":"lg_","label":"test","view_count":3}]}""")
        assertEquals(2, share.messageCount)
        assertEquals(3, share.tokens.single().viewCount)
        assertNull(share.tokens.single().token)
        val created = json.decodeFromString<NativeSessionShareToken>("""{"id":"token","token":"one-time-token"}""")
        assertEquals("one-time-token", created.token)
    }

    @Test fun workspaceSearchEncodesEveryQueryValueAndPreservesHitTargetsAndPagination() {
        val query = "中文 + & ? # %"
        val path = NativeSidebarRequests.searchWorkspace(query, "message", 20)
        val url = ("https://host.example" + path).toHttpUrl()
        assertEquals("/workspace/search", url.encodedPath)
        assertEquals(query, url.queryParameter("q"))
        assertEquals("message", url.queryParameter("kind"))
        assertEquals("20", url.queryParameter("offset"))
        assertEquals("20", url.queryParameter("limit"))
        val page = json.decodeFromString<NativeWorkspaceSearchPage>("""{"items":[{"kind":"message","id":"m","title":"Matched session","snippet":"matched text","session_id":"s","updated_at":"2026-10-18T00:00:00Z","match_field":"content"},{"kind":"file","id":"f","title":"File","snippet":"","session_id":null,"updated_at":"2026-10-18T00:00:00Z","match_field":"title"}],"has_more":true,"next_offset":40}""")
        assertTrue(page.hasMore)
        assertEquals(40, page.nextOffset)
        assertEquals("s", page.items.first().sessionId)
        assertEquals("m", page.items.first().id)
        assertEquals("content", page.items.first().matchField)
        assertNull(page.items.last().sessionId)
        invalid { NativeSidebarRequests.searchWorkspace("x".repeat(241), "all", 0) }
        invalid { NativeSidebarRequests.searchWorkspace("query", "unknown", 0) }
        invalid { NativeSidebarRequests.searchWorkspace("query", "all", -1) }
        invalid { NativeSidebarRequests.searchWorkspace("query", "all", 10_001) }
    }

    @Test fun patchAndPutSendTheActualMethodsAuthWorkspaceAndJsonBody() = runBlocking {
        SidebarHttpFixture(2).use { fixture ->
            val api = ApiClient(json).apply { baseUrl = fixture.baseUrl; token = "private-bearer"; workspaceId = "workspace"; deviceId = "device" }
            api.patch(NativeSidebarRequests.path("sessions", "s1"), NativeSidebarRequests.rename("Renamed"))
            api.put(NativeSidebarRequests.path("sessions", "s1", "project"), NativeSidebarRequests.move(null))
            val requests = fixture.requests.toList()
            assertEquals(listOf("PATCH /api/v1/sessions/s1 HTTP/1.1", "PUT /api/v1/sessions/s1/project HTTP/1.1"), requests.map { it.first })
            requests.forEach { (_, headers, _) ->
                assertEquals("Bearer private-bearer", headers["authorization"])
                assertEquals("workspace", headers["x-workspace-id"])
                assertEquals("device", headers["x-device-id"])
            }
            assertEquals("Renamed", json.parseToJsonElement(requests[0].third).jsonObject.getValue("title").jsonPrimitive.content)
            assertEquals(JsonNull, json.parseToJsonElement(requests[1].third).jsonObject.getValue("project_id"))
        }
    }

    private fun invalid(block: () -> Unit) {
        try { block(); fail("Expected a request outside the backend contract to be rejected") }
        catch (_: IllegalArgumentException) { }
    }
}

/** Loopback fixture, matching the repository tests without another runtime dependency. */
private class SidebarHttpFixture(count: Int) : AutoCloseable {
    private val server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    val baseUrl = "http://127.0.0.1:${server.localPort}"
    val requests = CopyOnWriteArrayList<Triple<String, Map<String, String>, String>>()
    private val worker = Thread({
        repeat(count) {
            server.accept().use { socket ->
                socket.soTimeout = 5_000
                val reader = socket.getInputStream().bufferedReader(Charsets.ISO_8859_1)
                val request = reader.readLine()
                val headers = mutableMapOf<String, String>()
                while (true) {
                    val line = reader.readLine() ?: break
                    if (line.isEmpty()) break
                    val colon = line.indexOf(':')
                    if (colon > 0) headers[line.substring(0, colon).lowercase(Locale.ROOT)] = line.substring(colon + 1).trim()
                }
                val body = CharArray(headers["content-length"]?.toInt() ?: 0)
                var read = 0
                while (read < body.size) { val received = reader.read(body, read, body.size - read); if (received < 0) break; read += received }
                requests += Triple(request, headers, String(body))
                socket.getOutputStream().apply {
                    write("HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}".toByteArray(Charsets.US_ASCII)); flush()
                }
            }
        }
    }, "native-sidebar-http-fixture").apply { isDaemon = true; start() }

    override fun close() { server.close(); worker.join(2_000) }
}
