package com.learngraph.mobile.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import org.junit.Assert.*
import org.junit.Test

class NativeAppearanceStoreTest {
    private fun settings(raw: String) = Json.parseToJsonElement(raw) as JsonArray

    @Test fun thinkingChainDefaultsToCollapsedAndPreservesExplicitOpen() {
        assertFalse(NativeAppearanceStore.thinkingChainDefaultFromSettings(settings("""[{"key":"chat.thinking_chain_default","value":{"default_state":"collapsed"}}]""")))
        assertTrue(NativeAppearanceStore.thinkingChainDefaultFromSettings(settings("""[{"key":"chat.thinking_chain_default","value":{"default_state":"open"}}]""")))
        for (value in listOf("""{"default_state":"unknown"}""", """{"default_state":false}""", "null", "\"collapsed\"", "{}")) {
            assertFalse(NativeAppearanceStore.thinkingChainDefaultFromSettings(settings("""[{"key":"chat.thinking_chain_default","value":$value}]""")))
        }
        assertFalse(NativeAppearanceStore.thinkingChainDefaultFromSettings(settings("[]")))
        assertFalse(NativeAppearanceStore.thinkingChainDefaultFromSettings(settings("""[{"key":"other","value":{"default_state":"collapsed"}},{"key":"ui.preferences","value":{"thinking_chain_default":"collapsed"}}]""")))
    }

    @Test fun readsOnlyTheWorkspaceUiPreferenceAndMatchesWebThemeSemantics() {
        assertEquals("dark", NativeAppearanceStore.themeFromSettings(settings("""[{"key":"other","value":{"theme":"light"}},{"key":"ui.preferences","value":{"theme":"dark","sidebar":{}}}]""")))
        for (value in listOf("\"light\"", "\"system\"", "null", "true", "{}")) {
            assertEquals("light", NativeAppearanceStore.themeFromSettings(settings("""[{"key":"ui.preferences","value":{"theme":$value}}]""")))
        }
    }

    @Test fun absentThemeKeepsTheSavedPreferenceInsteadOfFollowingTheSystem() {
        for (raw in listOf("[]", """[{"key":"ui.preferences","value":{"sidebar":{}}}]""", """[{"key":"ui.preferences","value":null}]""")) {
            assertNull(NativeAppearanceStore.themeFromSettings(settings(raw)))
        }
    }

    @Test fun scopesSeparateServerWorkspaceAndAccountButSurviveBearerRefresh() {
        val auth = AuthStore.AuthState(baseUrl = "https://one.example", workspaceId = "w1", userId = "u1", token = "first")
        val scope = NativeAppearanceStore.scope(auth)
        assertNotNull(scope)
        assertEquals(scope, NativeAppearanceStore.scope(auth.copy(token = "renewed")))
        assertNotEquals(scope, NativeAppearanceStore.scope(auth.copy(baseUrl = "https://two.example")))
        assertNotEquals(scope, NativeAppearanceStore.scope(auth.copy(workspaceId = "w2")))
        assertNotEquals(scope, NativeAppearanceStore.scope(auth.copy(userId = "u2")))
        assertNotEquals(NativeAppearanceStore.scope(auth.copy(userId = null)), NativeAppearanceStore.scope(auth.copy(userId = null, token = "renewed")))
        assertNull(NativeAppearanceStore.scope(auth.copy(token = null)))
    }

    @Test fun recentWebThemeChangesCannotBeReversedByStaleSettingsOrAnInflightSave() {
        assertTrue(NativeAppearanceStore.acceptsSettings(1, 0))
        assertFalse(NativeAppearanceStore.acceptsSettings(99_999, 100_000))
        assertFalse(NativeAppearanceStore.acceptsSettings(129_999, 100_000))
        assertTrue(NativeAppearanceStore.acceptsSettings(130_000, 100_000))
    }
}
