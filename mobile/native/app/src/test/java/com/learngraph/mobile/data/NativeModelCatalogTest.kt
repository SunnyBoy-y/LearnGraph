package com.learngraph.mobile.data

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class NativeModelCatalogTest {
    private fun provider(
        id: String = "p", type: String = "openai_responses", enabled: Boolean = true,
        remote: Boolean = true, capabilities: String = "{}",
    ) = """{"id":"$id","display_name":"Provider $id","provider_type":"$type","enabled":$enabled,"remote_capability":$remote,"capabilities":$capabilities}"""

    @Test fun onlyEnabledRemoteTextProvidersAreDiscovered() = runBlocking {
        val requests = mutableListOf<String>()
        val result = loadNativeModelChoices { path ->
            requests += path
            if (path == "/providers") "[${provider()},${provider("disabled", enabled = false)},${provider("local", remote = false)},${provider("images", type = "openai_images")},${provider("mock", type = "local_mock")}]"
            else """{"provider_id":"p","models":[{"id":"chat","remote":true}]}"""
        }
        assertEquals(listOf("/providers", "/providers/p/models"), requests)
        assertEquals(NativeModelChoice("p", "Provider p", "chat", "chat", true, providerType = "openai_responses"), result.single())
    }

    @Test fun discoveredManualAndDefaultModelsRespectSavedStatesAndOutputCapabilities() = runBlocking {
        val capabilities = """{"discovered_model_ids":["chat","disabled","hidden","image"],"models":{"manual":{"supports_agent_tools":false},"manual-image":{"supports_text_output":false}},"model_states":{"hidden":false},"default_model":"configured"}"""
        val result = loadNativeModelChoices { path ->
            if (path == "/providers") "[${provider(capabilities = capabilities)}]"
            else """{"provider_id":"p","models":[{"id":"chat","remote":true},{"id":"disabled","remote":true,"enabled":false},{"id":"hidden","remote":true},{"id":"image","remote":true,"capabilities":{"supports_text_output":false}},{"id":"nonremote","remote":false},{"id":"no-tools","remote":true,"capabilities":{"supports_agent_tools":false}}]}"""
        }
        assertEquals(listOf("chat", "nonremote", "no-tools", "manual", "configured"), result.map { it.modelId })
        assertEquals(listOf(true, false, false, false, true), result.map { it.supportsAgent })
    }

    @Test fun providerToolGateAppliesAndSameModelIdsRemainSeparatePerProvider() = runBlocking {
        val result = loadNativeModelChoices { path ->
            if (path == "/providers") "[${provider(capabilities = """{"supports_agent_tools":false}""")},${provider("q", type = "qwen")}]"
            else """{"models":[{"id":"same-model","remote":true}]}"""
        }
        assertEquals(listOf("p", "q"), result.map { it.providerId })
        assertEquals(listOf(false, true), result.map { it.supportsAgent })
    }

    @Test fun unavailableDiscoveryUsesPersistedAndManualModelsWithoutResurrectingDisabledOnes() = runBlocking {
        val capabilities = """{"discovered_model_ids":["saved","disabled"],"models":{"saved":{"supports_agent_tools":false},"manual":{}},"model_states":{"disabled":false},"default_model":"configured"}"""
        val result = loadNativeModelChoices { path ->
            if (path == "/providers") "[${provider(capabilities = capabilities)}]"
            else throw ApiException("upstream discovery unavailable", 502)
        }
        assertEquals(listOf("saved", "manual", "configured"), result.map { it.modelId })
        assertFalse(result.first().supportsAgent)
    }

    @Test fun emptySuccessfulDiscoveryDoesNotReuseRemovedPersistedIds() = runBlocking {
        val result = loadNativeModelChoices { path ->
            if (path == "/providers") "[${provider(capabilities = """{"discovered_model_ids":["removed"]}""")}]"
            else """{"models":[]}"""
        }
        assertTrue(result.isEmpty())
    }

    @Test fun authenticationAndCancellationNeverFallBackToSavedCatalog() = runBlocking {
        for (code in listOf(401, 403)) {
            val failure = ApiException("auth rejected", code)
            try {
                loadNativeModelChoices { path ->
                    if (path == "/providers") "[${provider(capabilities = """{"default_model":"saved"}""")}]"
                    else throw failure
                }
                fail("Expected auth failure")
            } catch (actual: ApiException) { assertSame(failure, actual) }
        }
        val cancelled = CancellationException("menu closed")
        try {
            loadNativeModelChoices { path -> if (path == "/providers") "[${provider()}]" else throw cancelled }
            fail("Expected cancellation")
        } catch (actual: CancellationException) { assertSame(cancelled, actual) }
    }

    @Test fun missingFallbackPreservesFailureReasonAndHttpCode() = runBlocking {
        try {
            loadNativeModelChoices { path ->
                if (path == "/providers") "[${provider()}]" else throw ApiException("vendor timeout", 504)
            }
            fail("Expected discovery failure")
        } catch (actual: ApiException) {
            assertEquals(504, actual.code)
            assertTrue(actual.message.orEmpty().contains("vendor timeout"))
        }
    }

    @Test fun menuMetadataUsesPerModelImageCapabilitiesAndActualProviderProtocol() = runBlocking {
        val result = loadNativeModelChoices { path ->
            if (path == "/providers") "[${provider(type = "anthropic_messages", capabilities = """{"supports_image_input":true,"models":{"manual":{"supports_image_input":true}}}""")}]"
            else """{"models":[{"id":"vision","remote":true,"capabilities":{"supports_image_input":true}},{"id":"text","remote":true},{"id":"string-flag","remote":true,"capabilities":{"supports_image_input":"true"}}]}"""
        }
        assertEquals(listOf(true, false, false, true), result.map { it.supportsImageInput })
        assertTrue(result.all { it.providerType == "anthropic_messages" && it.protocolLabel == "Anthropic Messages" })
    }

    @Test fun menuSearchMatchesProviderProtocolAndSubsequencesWithoutChangingChoices() {
        val choice = NativeModelChoice("p", "DeepSeek1", "deepseek-flash", "deepseek-flash", true, "openai_compatible_chat")
        assertTrue(nativeModelMatchesQuery(choice, " DSFL "))
        assertTrue(nativeModelMatchesQuery(choice, "compatible chat"))
        assertTrue(nativeModelMatchesQuery(choice, "deepseek1"))
        assertFalse(nativeModelMatchesQuery(choice, "gpt-99"))
        assertTrue(nativeModelMatchesQuery(choice, ""))
        assertEquals("Compatible Chat", choice.protocolLabel)
        assertEquals("Responses", choice.copy(providerType = "openai_responses").protocolLabel)
        assertEquals("Ollama", choice.copy(providerType = "ollama").protocolLabel)
    }

    @Test fun defaultResolutionPreservesServerProviderOrderIncludingEmptyProviders() = runBlocking {
        val catalog = loadNativeModelCatalog { path -> when (path) {
            "/providers" -> "[${provider("empty")},${provider("later")}]"
            "/providers/empty/models" -> """{"models":[]}"""
            else -> """{"models":[{"id":"later-model","remote":true}]}"""
        } }
        assertEquals(listOf("empty", "later"), catalog.providerIds)
        assertEquals("later", catalog.choices.single().providerId)
        val resolved = resolveNativeTextModelOptions(ComposerOptions(), null, catalog)
        assertEquals("empty", resolved.providerId)
        assertNull(resolved.modelId) // Web does not jump to a different provider's model.
    }

    @Test fun defaultsFollowOriginalModelOrderAndLocalIdsWinBeforeSnapshotGaps() = runBlocking {
        val catalog = loadNativeModelCatalog { path ->
            if (path == "/providers") "[${provider(capabilities = """{"default_model":"configured"}""")}]"
            else """{"models":[{"id":"server-first","remote":true},{"id":"configured","remote":true}]}"""
        }
        val snapshot = Json.parseToJsonElement("""{"provider_id":"p","model_id":"configured"}""").jsonObject
        assertEquals("server-first", resolveNativeTextModelOptions(ComposerOptions(), null, catalog).modelId)
        assertEquals("configured", resolveNativeTextModelOptions(ComposerOptions(), snapshot, catalog).modelId)
        val explicit = ComposerOptions(responseMode = "fast", thinkingMode = "off", searchRoute = "disabled", providerId = "p", modelId = "server-first")
        assertEquals(explicit, resolveNativeTextModelOptions(explicit, snapshot, catalog))
        assertEquals("configured", resolveNativeTextModelOptions(explicit.copy(modelId = null), snapshot, catalog).modelId)
    }

    @Test fun removedIdsDowngradeInsideChosenProviderWithoutInventingACatalog() {
        val first = NativeModelChoice("p", "P", "shared", "shared", true)
        val second = NativeModelChoice("q", "Q", "q-first", "q-first", true)
        val catalog = NativeModelCatalog(listOf("p", "q"), listOf(first, second, second.copy(modelId = "shared")))
        val stored = ComposerOptions(providerId = "q", modelId = "removed", thinkingMode = "xhigh", searchRoute = "local")
        assertEquals(stored.copy(modelId = "q-first"), resolveNativeTextModelOptions(stored, null, catalog))
        assertEquals(stored.copy(providerId = "p", modelId = "shared"), resolveNativeTextModelOptions(stored.copy(providerId = "removed"), null, catalog))
        assertEquals(stored, resolveNativeTextModelOptions(stored, null, null))
        assertEquals(stored.copy(providerId = null, modelId = null), resolveNativeTextModelOptions(stored, null, NativeModelCatalog(emptyList(), emptyList())))
    }

    @Test fun sessionSnapshotAndResolvedRequestUseTheSameExplicitIdsWithoutChangingModes() {
        val session = Json.decodeFromString<Session>("""{"id":"s","workspace_id":"w","title":"Chat","model_snapshot":{"provider_id":"p","model_id":"saved"}}""")
        val saved = NativeModelChoice("p", "P", "saved", "saved", true)
        val original = ComposerOptions(responseMode = "fast", thinkingMode = "off", searchRoute = "disabled", imageProviderId = "image", imageModelId = "picture")
        val resolved = resolveNativeTextModelOptions(original, session.modelSnapshot, NativeModelCatalog(listOf("p"), listOf(saved.copy(modelId = "first"), saved)))
        assertEquals(original.copy(providerId = "p", modelId = "saved"), resolved)
        val body = Json.parseToJsonElement(resolved.toBody("hello")).jsonObject
        assertEquals("p", body["provider_id"]?.jsonPrimitive?.content)
        assertEquals("saved", body["model_id"]?.jsonPrimitive?.content)
        assertEquals("false", body["agent_mode"]?.jsonPrimitive?.content)
        assertEquals("disabled", body["search_route"]?.jsonPrimitive?.content)
    }
}
