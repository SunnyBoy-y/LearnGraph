package com.learngraph.mobile.ui.components

import java.util.Locale

internal data class WebAvatarFallback(val initial: String, val backgroundArgb: Long, val foregroundArgb: Long)

/** Same Unicode code-point sum and palette as frontend/src/lib/user-avatar.ts. */
internal fun defaultWebUserAvatar(name: String): WebAvatarFallback {
    val tones = arrayOf(
        0xFFDDF4FFL to 0xFF0969DAL, 0xFFDAFBE1L to 0xFF1A7F37L,
        0xFFFBEFFFL to 0xFF8250DFL, 0xFFFFF1E5L to 0xFFBC4C00L,
        0xFFDDF7F4L to 0xFF0F766EL,
    )
    var sum = 0L
    var index = 0
    while (index < name.length) {
        val point = Character.codePointAt(name, index)
        sum += point
        index += Character.charCount(point)
    }
    val tone = tones[(sum % tones.size).toInt()]
    val initial = if (name.isEmpty()) "U" else String(Character.toChars(Character.codePointAt(name, 0))).uppercase(Locale.ROOT)
    return WebAvatarFallback(initial, tone.first, tone.second)
}
