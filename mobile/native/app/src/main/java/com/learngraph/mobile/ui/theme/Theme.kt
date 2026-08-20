package com.learngraph.mobile.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// 品牌色：LearnGraph 蓝紫渐变体系
val BrandBlue = Color(0xFF2F54D0)
val BrandViolet = Color(0xFF7B5CFF)
val BrandGradient = listOf(BrandBlue, BrandViolet)

private val LightColors = lightColorScheme(
    primary = BrandBlue,
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE3E9FF),
    onPrimaryContainer = Color(0xFF16255C),
    secondary = Color(0xFF5B6478),
    secondaryContainer = Color(0xFFECEAF6),
    onSecondaryContainer = Color(0xFF262033),
    background = Color(0xFFF7F7F5),
    onBackground = Color(0xFF1B1C20),
    surface = Color(0xFFFFFFFF),
    onSurface = Color(0xFF1B1C20),
    surfaceVariant = Color(0xFFF0F1F4),
    onSurfaceVariant = Color(0xFF4A4F58),
    outline = Color(0xFFD9DCE3),
    error = Color(0xFFD93025),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFF9DB4FF),
    onPrimary = Color(0xFF16255C),
    primaryContainer = Color(0xFF2A3A78),
    onPrimaryContainer = Color(0xFFDDE4FF),
    secondary = Color(0xFFB6BCC9),
    secondaryContainer = Color(0xFF3A3650),
    onSecondaryContainer = Color(0xFFE8E4F8),
    background = Color(0xFF12141A),
    onBackground = Color(0xFFE6E8ED),
    surface = Color(0xFF1C1F27),
    onSurface = Color(0xFFE6E8ED),
    surfaceVariant = Color(0xFF262A33),
    onSurfaceVariant = Color(0xFFB8BDC7),
    outline = Color(0xFF3A3F4A),
    error = Color(0xFFFF8A80),
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
