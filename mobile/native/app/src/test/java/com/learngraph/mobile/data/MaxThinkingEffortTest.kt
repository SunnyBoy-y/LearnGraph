package com.learngraph.mobile.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.*
import org.junit.Test

class MaxThinkingEffortTest {
    @Test fun maxRoundTripsThroughWebAndRequestWithoutBecomingXhigh() {
        val options = ComposerOptions(thinkingMode = "max", thinkingMappingVersion = 2)
        val restored = ComposerOptions.fromWebPrefs(options.toWebPrefs())!!
        assertEquals("max", restored.thinkingMode)
        assertEquals("max", Json.parseToJsonElement(restored.toBody("hello")).jsonObject["thinking_mode"]!!.jsonPrimitive.content)
    }

    @Test fun sparseNativeCatalogHidesUnsupportedTiersAndThinkingOnlyHidesOff() {
        val choice = NativeModelChoice("p", "P", "m", "m", true, reasoningEfforts = listOf("max", "high"))
        assertEquals(listOf("off", "high", "max"), choice.thinkingModes)
        assertEquals(listOf("high", "max"), choice.copy(thinkingRequired = true).thinkingModes)
    }

    @Test fun automaticOldXhighCanMigrateOnlyWithRecordedWireEvidence() {
        val mapping = Json.parseToJsonElement("""{"high":"high","xhigh":"xhigh","max":"max"}""").jsonObject
        val legacy = Json.parseToJsonElement("""{"xhigh":"max"}""").jsonObject
        val choice = NativeModelChoice("p", "P", "m", "m", true,
            reasoningEfforts = listOf("high", "xhigh", "max"), thinkingMapping = mapping, legacyThinkingMapping = legacy)
        val old = ComposerOptions(providerId = "p", modelId = "m", thinkingMode = "xhigh")
        val catalog = NativeModelCatalog(listOf("p"), listOf(choice))
        val migrated = resolveNativeTextModelOptions(old, null, catalog)
        assertEquals("max", migrated.thinkingMode)
        assertEquals(2, migrated.thinkingMappingVersion)
        assertEquals("xhigh", resolveNativeTextModelOptions(migrated.copy(thinkingMode = "xhigh"), null, catalog).thinkingMode)
        assertEquals(old, resolveNativeTextModelOptions(old, null, catalog.copy(choices = listOf(choice.copy(legacyThinkingMapping = null)))))
    }
}
