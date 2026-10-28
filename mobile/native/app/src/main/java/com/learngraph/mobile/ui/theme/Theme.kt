package com.learngraph.mobile.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.ProvideTextStyle
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import com.learngraph.mobile.LearnGraphApp
import com.learngraph.mobile.data.NativeAppearanceStore
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontWeight
import com.learngraph.mobile.R
import androidx.compose.ui.unit.sp
import androidx.compose.ui.unit.em

// Tokens mirror frontend/src/index.css, including the late dark/monochrome overrides.
val BrandBlue = Color(0xFF171717)
val BrandViolet = Color(0xFF555555)
val BrandGradient = listOf(BrandBlue, BrandViolet)
private val LightColors = lightColorScheme(
    primary = Color(0xFF1B1B1B), onPrimary = Color(0xFFFCFCFC),
    primaryContainer = Color(0xFFEDEDED), onPrimaryContainer = Color(0xFF202020),
    secondary = Color(0xFF656B66), secondaryContainer = Color(0xFFE8F0FE),
    onSecondaryContainer = Color(0xFF292929),
    background = Color.White, onBackground = Color(0xFF0C110F),
    surface = Color.White, onSurface = Color(0xFF0C110F),
    surfaceVariant = Color(0xFFF4F3F0), onSurfaceVariant = Color(0xFF656B66),
    surfaceContainerLow = Color(0xFFF7F7F7),
    surfaceContainerLowest = Color.White, surfaceContainer = Color.White,
    surfaceContainerHigh = Color.White, surfaceContainerHighest = Color(0xFFF4F4F4),
    surfaceDim = Color(0xFFE6E6E6), surfaceBright = Color.White, surfaceTint = Color.Transparent,
    outlineVariant = Color(0xFFDBDBD6),
    outline = Color(0xFFDBDBD6), error = Color(0xFFB3261E),
)
private val DarkColors = darkColorScheme(
    primary = Color(0xFFE8EBEE), onPrimary = Color(0xFF111315),
    primaryContainer = Color(0xFF30343A), onPrimaryContainer = Color(0xFFE8EBEE),
    secondary = Color(0xFFBBC1C9), secondaryContainer = Color(0xFF24272B),
    onSecondaryContainer = Color(0xFFE8EBEE),
    background = Color(0xFF111315), onBackground = Color(0xFFE8EBEE),
    surface = Color(0xFF24272B), onSurface = Color(0xFFE8EBEE),
    surfaceVariant = Color(0xFF222529), onSurfaceVariant = Color(0xFFBBC1C9),
    surfaceContainerLow = Color(0xFF17191C),
    surfaceContainerLowest = Color(0xFF0C0E10), surfaceContainer = Color(0xFF24272B),
    surfaceContainerHigh = Color(0xFF24272B), surfaceContainerHighest = Color(0xFF30343A),
    surfaceDim = Color(0xFF111315), surfaceBright = Color(0xFF353A40), surfaceTint = Color.Transparent,
    outlineVariant = Color(0xFF30343A),
    outline = Color(0xFF30343A), error = Color(0xFFF07167),
)
// Static instances of the same @fontsource-variable/geist Latin outlines used by Web.
// Android supplies CJK fallback glyphs; API 23 does not need variable-font support.
val WebGeistFontFamily = FontFamily(
    Font(R.font.geist_regular, FontWeight.Normal), Font(R.font.geist_medium, FontWeight.Medium),
    Font(R.font.geist_semibold, FontWeight.SemiBold), Font(R.font.geist_650, FontWeight(650)),
    Font(R.font.geist_bold, FontWeight.Bold),
)
private val DefaultTypography = Typography()
private fun TextStyle.webUiRole(): TextStyle = copy(
    fontFamily = WebGeistFontFamily,
    // CSS unitless leading follows a child font-size override; fixed sp does not.
    lineHeight = (lineHeight.value / fontSize.value).em,
    letterSpacing = 0.sp,
).withWebCssLineBox()
private val NativeTypography = Typography(
    displayLarge = DefaultTypography.displayLarge.webUiRole(),
    displayMedium = DefaultTypography.displayMedium.webUiRole(),
    displaySmall = DefaultTypography.displaySmall.webUiRole(),
    headlineLarge = DefaultTypography.headlineLarge.webUiRole(),
    headlineMedium = DefaultTypography.headlineMedium.webUiRole(),
    headlineSmall = DefaultTypography.headlineSmall.webUiRole(),
    titleLarge = DefaultTypography.titleLarge.webUiRole(),
    titleMedium = DefaultTypography.titleMedium.webUiRole(),
    titleSmall = DefaultTypography.titleSmall.webUiRole(),
    bodyLarge = DefaultTypography.bodyLarge.webUiRole(),
    bodyMedium = DefaultTypography.bodyMedium.copy(lineHeight = 21.sp).webUiRole(),
    bodySmall = DefaultTypography.bodySmall.copy(lineHeight = 18.sp).webUiRole(),
    labelLarge = DefaultTypography.labelLarge.webUiRole(),
    labelMedium = DefaultTypography.labelMedium.webUiRole(),
    labelSmall = DefaultTypography.labelSmall.webUiRole(),
)
@Composable
fun LearnGraphTheme(darkTheme: Boolean? = null, content: @Composable () -> Unit) {
    val app = LocalContext.current.applicationContext as? LearnGraphApp
    val dark = darkTheme ?: if (app == null) false else savedDarkTheme(app)
    MaterialTheme(colorScheme = if (dark) DarkColors else LightColors, typography = NativeTypography) {
        ProvideTextStyle(MaterialTheme.typography.bodyLarge) { content() }
    }
}

@Composable
private fun savedDarkTheme(app: LearnGraphApp): Boolean {
    val auth by app.authStore.state.collectAsState(initial = null)
    val scope = remember(auth?.baseUrl, auth?.workspaceId, auth?.userId, auth?.username, auth?.token) {
        auth?.let { NativeAppearanceStore.scope(it) }
    }
    val preference = remember(app, scope) { app.appearanceStore.observe(scope) }
    // Changing accounts reads that account's saved value during composition, before collection resumes.
    var theme by remember(preference) { mutableStateOf(preference.value) }
    LaunchedEffect(preference) { preference.collect { theme = it } }
    return theme == "dark"
}
