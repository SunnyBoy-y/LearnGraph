package com.learngraph.mobile.ui.components

import org.junit.Assert.*
import org.junit.Test

class WebUserAvatarModelTest {
    @Test fun matchesWebPaletteAndDoesNotReplaceNamedColorWithMonochrome() {
        val avatar = defaultWebUserAvatar("helloworld")
        assertEquals("H", avatar.initial)
        assertEquals(0xFFDDF7F4L, avatar.backgroundArgb)
        assertEquals(0xFF0F766EL, avatar.foregroundArgb)
    }

    @Test fun supplementaryCharactersCountOnceRatherThanAsUtf16Surrogates() {
        val avatar = defaultWebUserAvatar("😀")
        assertEquals("😀", avatar.initial)
        assertEquals(0xFFFBEFFFL, avatar.backgroundArgb)
        assertEquals(defaultWebUserAvatar("😀A").backgroundArgb, avatar.backgroundArgb)
    }

    @Test fun emptyNameAndUnicodeUppercaseHaveTheSameWebSemantics() {
        assertEquals("U", defaultWebUserAvatar("").initial)
        assertEquals("SS", defaultWebUserAvatar("ß").initial)
        assertEquals("Σ", defaultWebUserAvatar("σ").initial)
        assertEquals(" ", defaultWebUserAvatar(" a").initial)
    }
}
