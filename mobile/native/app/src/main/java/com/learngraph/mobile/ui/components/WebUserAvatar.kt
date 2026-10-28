package com.learngraph.mobile.ui.components

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.RectF
import android.util.Base64
import android.util.LruCache
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawWithContent
import androidx.compose.ui.graphics.BlendMode
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.learngraph.mobile.ui.theme.WebText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

/** Original Web avatar; profile data comes from Home's existing scoped /auth/me snapshot. */
@Composable
fun WebUserAvatar(name: String, avatarUrl: String?, cacheScope: String?, baseUrl: String, modifier: Modifier = Modifier) {
    val context = LocalContext.current.applicationContext
    val pixels = with(LocalDensity.current) { 32.dp.roundToPx() }.coerceIn(64, 96)
    val fallback = remember(name) { defaultWebUserAvatar(name) }
    val border = MaterialTheme.colorScheme.outlineVariant
    val borderBlend = if (MaterialTheme.colorScheme.background.luminance() < .5f) BlendMode.Lighten else BlendMode.Darken
    // Reset synchronously on identity/image changes, before the next loader coroutine starts.
    var image by remember(cacheScope, avatarUrl, pixels, baseUrl) { mutableStateOf<Bitmap?>(null) }
    LaunchedEffect(cacheScope, avatarUrl, pixels, baseUrl) {
        if (cacheScope != null && !avatarUrl.isNullOrBlank()) image = AvatarImages.load(context, cacheScope, baseUrl, avatarUrl, pixels)
    }
    Box(modifier.size(32.dp).clip(CircleShape).background(Color(fallback.backgroundArgb))
        .drawWithContent {
            drawContent()
            val stroke = 1.dp.toPx()
            drawCircle(border, radius = size.minDimension / 2f - stroke / 2f,
                style = Stroke(stroke), blendMode = borderBlend)
        }, contentAlignment = Alignment.Center) {
        val bitmap = image
        if (bitmap != null) Image(bitmap.asImageBitmap(), contentDescription = null, contentScale = ContentScale.Crop, modifier = Modifier.fillMaxSize())
        else WebText(fallback.initial, fontSize = 12.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold, color = Color(fallback.foregroundArgb), maxLines = 1)
    }
}

private object AvatarImages {
    private const val MAX_BYTES = 5 * 1024 * 1024
    private val memory = object : LruCache<String, Bitmap>(1024 * 1024) {
        override fun sizeOf(key: String, value: Bitmap): Int = value.allocationByteCount
    }
    // Avatar HTTP requests never receive bearer, workspace, cookies, or device headers.
    // The normal server avatar is an inline, normalized 256px PNG and needs no request.
    private val http = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(15, TimeUnit.SECONDS).callTimeout(20, TimeUnit.SECONDS).build()

    suspend fun load(context: Context, scope: String, baseUrl: String, url: String, pixels: Int): Bitmap? = withContext(Dispatchers.IO) {
        val key = digest("$scope\u0000$baseUrl\u0000$url\u0000$pixels")
        memory.get(key)?.let { return@withContext it }
        val directory = File(context.cacheDir, "native-avatar-images")
        val file = File(directory, "$key.png")
        val cached = runCatching {
            if (file.isFile && file.length() <= MAX_BYTES && (url.startsWith("data:") || System.currentTimeMillis() - file.lastModified() < 60 * 60 * 1000)) decode(file.readBytes(), pixels)
            else null
        }.getOrNull()
        cached?.let { memory.put(key, it); return@withContext it }
        val bitmap = try {
            val bytes = if (url.startsWith("data:image/")) {
                val comma = url.indexOf(',')
                require(comma in 1..100 && url.substring(0, comma).endsWith(";base64"))
                require(url.length - comma - 1 <= (MAX_BYTES * 4L / 3 + 16))
                Base64.decode(url.substring(comma + 1), Base64.DEFAULT)
            } else {
                val resolved = url.toHttpUrlOrNull() ?: (baseUrl.trimEnd('/') + "/").toHttpUrlOrNull()?.resolve(url)
                require(resolved != null && resolved.username.isEmpty() && resolved.password.isEmpty())
                http.newCall(Request.Builder().url(resolved).build()).execute().use { response ->
                    require(response.isSuccessful)
                    val body = requireNotNull(response.body)
                    require(body.contentLength() <= MAX_BYTES)
                    body.byteStream().use { input ->
                        val output = ByteArrayOutputStream()
                        val buffer = ByteArray(8192)
                        while (true) {
                            ensureActive()
                            val count = input.read(buffer)
                            if (count < 0) break
                            require(output.size() + count <= MAX_BYTES)
                            output.write(buffer, 0, count)
                        }
                        output.toByteArray()
                    }
                }
            }
            ensureActive()
            require(bytes.size <= MAX_BYTES)
            decode(bytes, pixels)
        } catch (cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
        catch (_: Exception) { null }
        if (bitmap != null) {
            ensureActive()
            memory.put(key, bitmap)
            runCatching {
                directory.mkdirs()
                file.outputStream().use { bitmap.compress(Bitmap.CompressFormat.PNG, 100, it) }
                val files = directory.listFiles().orEmpty().filter { it.isFile }.sortedBy { it.lastModified() }
                var bytes = files.sumOf { it.length() }
                for (old in files) {
                    if (bytes <= 8L * 1024 * 1024) break
                    bytes -= old.length(); old.delete()
                }
            }
        }
        bitmap
    }

    private fun decode(bytes: ByteArray, pixels: Int): Bitmap? {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0 || bounds.outWidth.toLong() * bounds.outHeight > 16_000_000) return null
        var sample = 1
        while (bounds.outWidth / sample > pixels * 2 || bounds.outHeight / sample > pixels * 2) sample *= 2
        val source = BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample }) ?: return null
        val result = Bitmap.createBitmap(pixels, pixels, Bitmap.Config.ARGB_8888)
        val scale = maxOf(pixels.toFloat() / source.width, pixels.toFloat() / source.height)
        val width = source.width * scale; val height = source.height * scale
        Canvas(result).drawBitmap(source, null, RectF((pixels - width) / 2, (pixels - height) / 2, (pixels + width) / 2, (pixels + height) / 2), Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG))
        source.recycle()
        return result
    }

    private fun digest(value: String): String = MessageDigest.getInstance("SHA-256").digest(value.toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }
}
