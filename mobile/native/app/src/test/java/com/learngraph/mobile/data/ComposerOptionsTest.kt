package com.learngraph.mobile.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.long
import org.junit.Assert.*
import org.junit.Test

class ComposerOptionsTest {
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun defaultsUseExistingAgentThinkingAndSearchContractOnly() {
        val body = json.parseToJsonElement(ComposerOptions().toBody("Hello")).jsonObject
        assertEquals(setOf("content", "agent_mode", "thinking_mode", "search_route", "web_search"), body.keys)
        assertTrue(body.getValue("agent_mode").jsonPrimitive.boolean)
        assertEquals("medium", body.getValue("thinking_mode").jsonPrimitive.content)
        assertEquals("auto", body.getValue("search_route").jsonPrimitive.content)
        assertTrue(body.getValue("web_search").jsonPrimitive.boolean)
        assertFalse(body.containsKey("response_mode"))
    }

    @Test fun fastModeForcesThinkingOffAndDisabledSearchStaysDisabled() {
        val body = json.parseToJsonElement(ComposerOptions(
            responseMode = "fast", thinkingMode = "xhigh", searchRoute = "disabled",
            providerId = "provider1", modelId = "model1",
        ).toBody("Hello")).jsonObject
        assertFalse(body.getValue("agent_mode").jsonPrimitive.boolean)
        assertEquals("off", body.getValue("thinking_mode").jsonPrimitive.content)
        assertFalse(body.getValue("web_search").jsonPrimitive.boolean)
        assertEquals("provider1", body.getValue("provider_id").jsonPrimitive.content)
        assertEquals("model1", body.getValue("model_id").jsonPrimitive.content)
    }

    @Test fun thinkingModeAndEverySearchRouteProduceValidJsonWithEscapedContent() {
        val content = "引用 \"hello\"\n第二行 \\ 路径"
        for (route in listOf("disabled", "model_native", "external", "local", "auto")) {
            val body = json.parseToJsonElement(ComposerOptions(
                responseMode = "thinking", thinkingMode = "high", searchRoute = route,
                providerId = "  ", modelId = "",
            ).toBody(content)).jsonObject
            assertEquals(content, body.getValue("content").jsonPrimitive.content)
            assertEquals("high", body.getValue("thinking_mode").jsonPrimitive.content)
            assertTrue(body.getValue("agent_mode").jsonPrimitive.boolean)
            assertEquals(route != "disabled", body.getValue("web_search").jsonPrimitive.boolean)
            assertFalse(body.containsKey("provider_id"))
            assertFalse(body.containsKey("model_id"))
        }
    }

    @Test fun invalidOptionsCannotEmitRequestsOutsideBackendContract() {
        val invalid = listOf(
            ComposerOptions(responseMode = "unknown"), ComposerOptions(thinkingMode = "invalid"),
            ComposerOptions(searchRoute = "invalid"), ComposerOptions(providerId = "x".repeat(37)),
            ComposerOptions(modelId = "x".repeat(161)),
        )
        for (options in invalid) {
            try { options.toBody("Hello"); fail("Expected invalid options to be rejected") }
            catch (_: IllegalArgumentException) { }
        }
        try { ComposerOptions().toBody(""); fail("Expected empty content to be rejected") }
        catch (_: IllegalArgumentException) { }
    }

    @Test fun persistedComposerPreferencesAndMessageTimingKeepTheirValues() {
        val options = ComposerOptions("thinking", "high", "model_native", "p1", "m1")
        assertEquals(options, json.decodeFromString<ComposerOptions>(json.encodeToString(ComposerOptions.serializer(), options)))
        val message = json.decodeFromString<Message>("""{"id":"m1","session_id":"s1","role":"assistant","provider_trace":{"thinking_duration_ms":1234,"generation_started_at":"2026-10-18T03:00:00Z"}}""")
        assertEquals(1234L, message.providerTrace!!.getValue("thinking_duration_ms").jsonPrimitive.long)
    }

    @Test fun webSnapshotsPreserveBothModelFamiliesAndRejectMalformedValues() {
        val options = ComposerOptions("agentic", "xhigh", "disabled", "text-provider", "text-model", "image", "image-provider", "image-model")
        assertEquals(options, ComposerOptions.fromWebPrefs(options.toWebPrefs()))
        assertNull(ComposerOptions.fromWebPrefs(json.parseToJsonElement("""{"responseMode":"fast","thinkingMode":"maximum","searchRoute":"auto"}""").jsonObject))
        assertNull(ComposerOptions.fromWebPrefs(json.parseToJsonElement("""{"responseMode":"fast","thinkingMode":"off","searchRoute":"auto","modelId":12}""").jsonObject))
        assertNull(ComposerOptions.fromWebPrefs(json.parseToJsonElement("""{}""").jsonObject))
    }

    @Test fun nativeImageBodyUsesImageSelectionWithoutReplacingTextPreferences() {
        val options = ComposerOptions(providerId = "text-p", modelId = "text-m", generationMode = "image", imageProviderId = "image-p", imageModelId = "image-m")
        val image = json.parseToJsonElement(options.toBody("画一棵树")).jsonObject
        assertEquals("image", image.getValue("generation_mode").jsonPrimitive.content)
        assertEquals("image-p", image.getValue("provider_id").jsonPrimitive.content)
        assertEquals("image-m", image.getValue("model_id").jsonPrimitive.content)
        assertEquals("off", image.getValue("thinking_mode").jsonPrimitive.content)
        assertFalse(image.getValue("agent_mode").jsonPrimitive.boolean)
        assertFalse(image.getValue("web_search").jsonPrimitive.boolean)
        val text = json.parseToJsonElement(options.copy(generationMode = "text").toBody("解释这幅画")).jsonObject
        assertEquals("text-p", text.getValue("provider_id").jsonPrimitive.content)
        assertEquals("text-m", text.getValue("model_id").jsonPrimitive.content)
        assertEquals("image", options.generationMode)
    }
}
