package com.learngraph.mobile.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val LightColors = lightColorScheme(
    primary = Color(0xFF2F54D0),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE0E6FF),
    onPrimaryContainer = Color(0xFF16255C),
    secondary = Color(0xFF5B6478),
    background = Color(0xFFF7F7F5),
    surface = Color(0xFFFFFFFF),
    surfaceVariant = Color(0xFFF0F1F4),
    onSurfaceVariant = Color(0xFF4A4F58),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF9DB4FF),
    onPrimary = Color(0xFF16255C),
    primaryContainer = Color(0xFF2A3A78),
    onPrimaryContainer = Color(0xFFDDE4FF),
    secondary = Color(0xFFB6BCC9),
    background = Color(0xFF12141A),
    surface = Color(0xFF1C1F27),
    surfaceVariant = Color(0xFF262A33),
    onSurfaceVariant = Color(0xFFB8BDC7),
)

@Composable
fun LearnGraphTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkColors else LightColors,
        content = content,
    )
}
