package com.learngraph.mobile.ui.web

import org.junit.Assert.*
import org.junit.Test

class NativeRendererPolicyTest {
    @Test fun preferenceWritesRequireTheCurrentServerBearerAndWorkspace() {
        val server = "https://example.test"
        assertTrue(NativeRendererPolicy.acceptsPreferenceScope(server, "current", "ws", "$server:443", "current", "ws"))
        assertFalse(NativeRendererPolicy.acceptsPreferenceScope(server, "current", "ws", "https://another.test", "current", "ws"))
        assertFalse(NativeRendererPolicy.acceptsPreferenceScope(server, "current", "ws", server, "previous", "ws"))
        assertFalse(NativeRendererPolicy.acceptsPreferenceScope(server, "current", "ws", server, "current", "other"))
        assertFalse(NativeRendererPolicy.acceptsPreferenceScope(server, null, "ws", server, null, "ws"))
        assertFalse(NativeRendererPolicy.acceptsPreferenceScope(server, "current", null, server, "current", null))
    }

    @Test fun assetsRequireExactOriginAndDedicatedPrefix() {
        val server = "https://example.test"
        assertEquals("renderer/assets/a.js", NativeRendererPolicy.assetPath("$server/__lg_renderer_v1/assets/a.js?v=1", server))
        assertTrue(NativeRendererPolicy.sameOrigin(server, "https://example.test:443/x"))
        listOf("http://example.test/__lg_renderer_v1/a.js", "https://other.test/__lg_renderer_v1/a.js", "$server/api/v1/sessions", "$server/__lg_renderer_v1/%2e%2e/token", "$server/__lg_renderer_v1/a%5cb").forEach {
            assertNull(it, NativeRendererPolicy.assetPath(it, server))
        }
    }
    @Test fun navigationStaysWithinActiveWorkspaceAndKeepsQueries() {
        assertEquals("graphs/g?panel=plan#node", NativeRendererPolicy.workspaceDestination("/w/ws/graphs/g?panel=plan#node", "http://localhost:8000", "ws"))
        assertNull(NativeRendererPolicy.workspaceDestination("/w/another/graphs/g", "http://localhost:8000", "ws"))
        assertNull(NativeRendererPolicy.workspaceDestination("https://attacker.test/w/ws/chat/x", "http://localhost:8000", "ws"))
        assertNull(NativeRendererPolicy.workspaceDestination("/w/ws/%2e%2e/admin", "http://localhost:8000", "ws"))
    }
    @Test fun bootstrapCannotBeClosedByMessageContent() {
        val safe = NativeRendererPolicy.scriptJson("{\"text\":\"</script><script>alert(1)</script>\u2028\u2029\"}")
        assertFalse(safe.contains('<'))
        assertFalse(safe.contains('\u2028'))
        assertTrue(safe.contains("\\u003c/script"))
    }
    @Test fun featureLinksSupportAllWorkspacePagesWithoutOriginEscape() {
        assertEquals("https://example.test/w/ws/settings/models?tab=voice#test", NativeRendererPolicy.featureUrl("https://example.test", "ws", "settings/models?tab=voice#test"))
        listOf("../admin", "https://attacker.test", "//attacker.test", "%2e%2e/admin").forEach {
            assertNull(NativeRendererPolicy.featureUrl("https://example.test", "ws", it))
        }
    }

    @Test fun packagedApplicationNeverReplacesApiDownloadsOrOtherIdentities() {
        val server = "https://example.test"
        listOf("/", "/auth/login", "/auth/change-password", "/w/ws", "/w/ws/chat/new?mode=goal", "/w/ws/settings/models").forEach {
            assertTrue(it, NativeRendererPolicy.isApplicationDocument(server + it, server, "ws"))
        }
        listOf("$server/api/v1/files/file/content", "$server/download.apk", "$server/share/id", "$server/w/another/chat/new",
            "$server/w/ws/%2e%2e/admin", "https://attacker.test/w/ws/chat/new", "http://example.test/w/ws/chat/new").forEach {
            assertFalse(it, NativeRendererPolicy.isApplicationDocument(it, server, "ws"))
        }
    }

    @Test fun onlineDocumentsUseTheServerAndOfflineFallbackUsesThePackage() {
        val server = "https://example.test"
        assertTrue(NativeRendererPolicy.canUseOfflineApplication("$server/w/ws/chat/new", server, "ws"))
        assertTrue(NativeRendererPolicy.canUseOfflineApplication("$server/auth/login", server, "ws"))
        assertFalse(NativeRendererPolicy.canUseOfflineApplication("$server/api/v1/files/file/content", server, "ws"))
        assertFalse(NativeRendererPolicy.canUseOfflineApplication("https://other.test/w/ws/chat/new", server, "ws"))
        assertTrue(NativeRendererPolicy.isPackagedApplicationRequest("$server/__lg_renderer_v1/assets/a.js", server))
        assertFalse(NativeRendererPolicy.isPackagedApplicationRequest("$server/assets/index-a1.js", server))
        NativeRendererPolicy.recordServedWebShell("https://example.test/w/ws/chat/new", server)
        assertFalse(NativeRendererPolicy.canUseOfflineApplication("$server/w/ws/chat/new", server, "ws"))
        assertTrue(NativeRendererPolicy.canRetryPackagedApplication("$server/w/ws/chat/new", server, "ws"))
        assertTrue(NativeRendererPolicy.canUseOfflineApplication("$server/w/ws/settings/models", server, "ws"))
    }

    @Test fun resumeOnlyRevalidatesOnlineDocumentsAndNeverReplacesOfflineFallback() {
        val server = "https://example.test"
        assertTrue(NativeRendererPolicy.shouldRevalidateOnResume("$server/w/ws/chat/new", server, false))
        assertFalse(NativeRendererPolicy.shouldRevalidateOnResume("$server/w/ws/chat/new", server, true))
        assertFalse(NativeRendererPolicy.shouldRevalidateOnResume("https://other.test", server, false))
        assertFalse(NativeRendererPolicy.shouldRevalidateOnResume(null, server, false))
    }
}
