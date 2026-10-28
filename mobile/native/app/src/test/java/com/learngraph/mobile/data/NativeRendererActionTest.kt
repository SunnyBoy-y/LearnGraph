package com.learngraph.mobile.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.boolean
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class NativeRendererActionTest {
    private val base = """{"content":"Explain this","graph_action":"none","generation_mode":"text"}"""

    @Test fun textFollowUpUsesOnlyAllowedExtrasAndPreservesComposerSettings() {
        val action = NativeRendererAction.parse(base)
        val body = Json.parseToJsonElement(nativeMessageRequestBody(
            ComposerOptions(responseMode = "thinking", thinkingMode = "high", providerId = "p", modelId = "m"),
            action.content, action.requestExtras,
        )).jsonObject
        assertEquals("Explain this", body.getValue("content").jsonPrimitive.content)
        assertEquals("none", body.getValue("graph_action").jsonPrimitive.content)
        assertEquals("text", body.getValue("generation_mode").jsonPrimitive.content)
        assertEquals("p", body.getValue("provider_id").jsonPrimitive.content)
        assertEquals("m", body.getValue("model_id").jsonPrimitive.content)
        assertEquals("high", body.getValue("thinking_mode").jsonPrimitive.content)
        assertTrue(body.getValue("agent_mode").jsonPrimitive.boolean)
        assertFalse(body.containsKey("analysis_only"))
        assertFalse(body.containsKey("ui_action"))
    }

    @Test fun analysisAndUiActionSurviveIndependentQueueSnapshots() {
        val first = NativeRendererAction.parse("""{"content":"First","graph_action":"none","generation_mode":"text","analysis_only":true,"ui_action":{"part_id":"part-1","revision":2,"action":"graph.node.explain","node_id":"node_1"}}""")
        val second = NativeRendererAction.parse(base)
        val saved = listOf(first.content to first.requestExtras, second.content to second.requestExtras)
        val bodies = saved.map { (content, extras) ->
            Json.parseToJsonElement(nativeMessageRequestBody(ComposerOptions(), content, extras)).jsonObject
        }
        assertTrue(bodies[0].getValue("analysis_only").jsonPrimitive.boolean)
        assertFalse(bodies[0].getValue("agent_mode").jsonPrimitive.boolean)
        assertFalse(bodies[0].getValue("web_search").jsonPrimitive.boolean)
        assertEquals("disabled", bodies[0].getValue("search_route").jsonPrimitive.content)
        assertTrue(bodies[1].getValue("agent_mode").jsonPrimitive.boolean)
        assertEquals(first.requestExtras.getValue("ui_action"), bodies[0].getValue("ui_action"))
        assertFalse(bodies[1].containsKey("analysis_only"))
        assertFalse(bodies[1].containsKey("ui_action"))
        assertEquals(first.requestExtras, saved.first().second)
    }

    @Test fun explainActionAllowsAbsentOptionalNodeAndRoundTripsEscapedContent() {
        val action = NativeRendererAction("引用 \"text\"\n第二行", uiAction = NativeRendererUiAction("part", 1, "explain"))
        val raw = JsonObject(action.requestExtras + ("content" to JsonPrimitive(action.content))).toString()
        assertEquals(action, NativeRendererAction.parse(raw))
        assertEquals(50_000, NativeRendererAction("x".repeat(50_000)).content.length)
        val initial = NativeRendererAction.parse("""{"content":"Explain","graph_action":"none","generation_mode":"text","ui_action":{"part_id":" ","revision":0,"action":"explain"}}""")
        assertEquals(0L, initial.uiAction!!.revision)
        assertEquals(" ", initial.uiAction.partId)
    }

    @Test fun arbitraryRequestOverridesPrefillAndUnknownNestedFieldsAreRejected() {
        for (field in listOf("provider_id", "model_id", "agent_mode", "thinking_mode", "auto_send", "unexpected")) {
            rejected(base.dropLast(1) + ",\"$field\":false}")
        }
        rejected("""{"content":"Explain","graph_action":"none","generation_mode":"text","ui_action":{"part_id":"p","revision":1,"action":"explain","extra":"x"}}""")
        rejected(base.replace("\"none\"", "\"propose_create\""))
        rejected(base.replace("\"text\"", "\"image\""))
        rejected(base.replace(",\"graph_action\":\"none\"", ""))
        rejected(base.replace("\"Explain this\"", "123"))
        rejected("[]")
        rejected("invalid json")
    }

    @Test fun optionalValuesMustHaveTheirExactJsonTypes() {
        for (value in listOf("false", "\"true\"", "null", "1", "{}")) {
            rejected(base.dropLast(1) + ",\"analysis_only\":$value}")
        }
        for (revision in listOf("-1", "1.5", "1.0", "1e2", "\"1\"", "null", "true")) {
            rejected("""{"content":"Explain","graph_action":"none","generation_mode":"text","ui_action":{"part_id":"p","revision":$revision,"action":"explain"}}""")
        }
        rejected(base.dropLast(1) + ",\"ui_action\":null}")
        val largeRevision = NativeRendererAction.parse("""{"content":"Explain","graph_action":"none","generation_mode":"text","ui_action":{"part_id":"p","revision":2147483648,"action":"explain"}}""")
        assertEquals(2_147_483_648L, largeRevision.uiAction!!.revision)
    }

    @Test fun unsafeIdsUnsupportedActionsAndInvalidContentAreRejected() {
        for (node in listOf("", "node.invalid", "__proto__", "constructor", "prototype", "x".repeat(101))) {
            rejected("""{"content":"Explain","graph_action":"none","generation_mode":"text","ui_action":{"part_id":"p","revision":1,"action":"graph.node.explain","node_id":"$node"}}""")
        }
        rejected("""{"content":"Explain","graph_action":"none","generation_mode":"text","ui_action":{"part_id":"","revision":1,"action":"explain"}}""")
        rejected("""{"content":"Explain","graph_action":"none","generation_mode":"text","ui_action":{"part_id":"p","revision":1,"action":"execute"}}""")
        rejected(base.replace("Explain this", " "))
        rejected(base.replace("Explain this", "x".repeat(50_001)))
    }

    @Test fun ordinarySendBodyIsUnchangedAndExtrasCannotOverrideContentOrOptions() {
        val options = ComposerOptions()
        assertEquals(Json.parseToJsonElement(options.toBody("Hello")), Json.parseToJsonElement(nativeMessageRequestBody(options, "Hello")))
        for (extras in listOf(
            JsonObject(mapOf("content" to JsonPrimitive("Override"))),
            JsonObject(NativeRendererAction.parse(base).requestExtras + ("agent_mode" to JsonPrimitive(false))),
        )) {
            try { nativeMessageRequestBody(options, "Hello", extras); fail("Expected invalid extras to be rejected") }
            catch (_: IllegalArgumentException) { }
        }
    }

    @Test fun textRendererActionsNeverUseTheComposersDrawingModel() {
        val options = ComposerOptions(providerId = "text-provider", modelId = "text-model",
            generationMode = "image", imageProviderId = "image-provider", imageModelId = "image-model")
        val action = NativeRendererAction.parse(base)
        val body = Json.parseToJsonElement(nativeMessageRequestBody(options, action.content, action.requestExtras)).jsonObject
        assertEquals("text", body.getValue("generation_mode").jsonPrimitive.content)
        assertEquals("text-provider", body.getValue("provider_id").jsonPrimitive.content)
        assertEquals("text-model", body.getValue("model_id").jsonPrimitive.content)
        val drawing = Json.parseToJsonElement(nativeMessageRequestBody(options, "Draw this")).jsonObject
        assertEquals("image", drawing.getValue("generation_mode").jsonPrimitive.content)
        assertEquals("image-provider", drawing.getValue("provider_id").jsonPrimitive.content)
    }

    @Test fun graphUpdateAndNodeContextStayWithEachQueuedAction() {
        // MessageCreateRequest.node_ids has no per-string regex, length or reserved-name restrictions.
        val nodes = listOf("", "__proto__", "node.with.dots", "节点", "x".repeat(150), "node space", "node/7", "node-8")
        val update = NativeRendererAction("Update graph", graphAction = "propose_update", graphId = "g".repeat(36), nodeIds = nodes)
        val plain = NativeRendererAction.parse(base)
        val saved = listOf(update.content to update.requestExtras, plain.content to plain.requestExtras)
        val first = Json.parseToJsonElement(nativeMessageRequestBody(ComposerOptions(), saved[0].first, saved[0].second)).jsonObject
        val second = Json.parseToJsonElement(nativeMessageRequestBody(ComposerOptions(), saved[1].first, saved[1].second)).jsonObject
        assertEquals("propose_update", first.getValue("graph_action").jsonPrimitive.content)
        assertEquals("g".repeat(36), first.getValue("graph_id").jsonPrimitive.content)
        assertEquals(JsonArray(nodes.map(::JsonPrimitive)), first.getValue("node_ids"))
        assertEquals("none", second.getValue("graph_action").jsonPrimitive.content)
        assertFalse(second.containsKey("graph_id"))
        assertFalse(second.containsKey("node_ids"))
        assertEquals(update, NativeRendererAction.parse(JsonObject(update.requestExtras + ("content" to JsonPrimitive(update.content))).toString()))
        assertEquals("propose_update", NativeRendererAction.parse(base.replace("\"none\"", "\"propose_update\"")).graphAction)
    }

    @Test fun graphTargetsAndNodeListsMatchTheMessageCreateContract() {
        rejected(base.dropLast(1) + ",\"graph_id\":\"g\"}")
        for (graphId in listOf("\"\"", "\"${"x".repeat(37)}\"", "null", "1")) {
            rejected(base.replace("\"none\"", "\"propose_update\"").dropLast(1) + ",\"graph_id\":$graphId}")
        }
        for (nodeIds in listOf("null", "{}", "\"node\"", "[1]", "[true]", "[null]", JsonArray(List(9) { JsonPrimitive("n") }).toString())) {
            rejected(base.dropLast(1) + ",\"node_ids\":$nodeIds}")
        }
        assertEquals(emptyList<String>(), NativeRendererAction.parse(base.dropLast(1) + ",\"node_ids\":[]}").nodeIds)
    }

    @Test fun uiDeliveryFlagNeverReachesTransportAndInvalidPrefillsAreRejected() {
        assertTrue(NativeRendererSubmission.parse(base).autoSend)
        assertTrue(NativeRendererSubmission.parse(base.dropLast(1) + ",\"auto_send\":true}").autoSend)
        val prefill = NativeRendererSubmission.parse(base.dropLast(1) + ",\"auto_send\":false}")
        assertFalse(prefill.autoSend)
        assertEquals("Explain this", prefill.action.content)
        assertFalse(prefill.action.requestExtras.containsKey("auto_send"))
        rejected(base.dropLast(1) + ",\"auto_send\":false}")
        for (flag in listOf("null", "1", "\"false\"", "{}", "[]")) {
            try { NativeRendererSubmission.parse(base.dropLast(1) + ",\"auto_send\":$flag}"); fail("Expected invalid UI delivery flag") }
            catch (_: IllegalArgumentException) { }
        }
        try {
            NativeRendererSubmission.parse(base.dropLast(1) + ",\"auto_send\":false,\"provider_id\":\"override\"}")
            fail("Expected invalid prefill action")
        } catch (_: IllegalArgumentException) { }
    }

    private fun rejected(raw: String) {
        try { NativeRendererAction.parse(raw); fail("Expected invalid action to be rejected: $raw") }
        catch (_: IllegalArgumentException) { }
    }
}
