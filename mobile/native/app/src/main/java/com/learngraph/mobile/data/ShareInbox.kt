package com.learngraph.mobile.data

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import android.util.Base64
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.Json
import java.io.File
import java.util.UUID

/**
 * 分享收件箱（Share Target 本地暂存）。
 *
 * 任意 App 通过系统分享（ACTION_SEND）投递的文本/图片先落在本地收件箱，
 * 避免「后台鉴权」问题——用户下次打开 App 时，网页版通过
 * LearnGraphNative.getInboxItems() 拉取并在登录态下执行
 * 「存记忆 / 发起对话 / 挂目标」。
 *
 * 存储：
 *  - 元数据：SharedPreferences("lg_share_inbox") 一条 JSON 数组
 *  - 图片：filesDir/inbox/<id>.<ext>（应用私有，跨重启保留）
 */
object ShareInbox {

    private const val PREFS = "lg_share_inbox"
    private const val KEY_ITEMS = "items"
    private const val MAX_IMAGE_BYTES = 15 * 1024 * 1024 // 15 MiB 上限，防超大分享

    private val json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
    }

    /** 供 JS bridge 使用的 JSON 编码器（与内部存储同配置） */
    fun jsonOf(): Json = json

    /** 序列化条目列表为 JSON 数组字符串 */
    fun encode(items: List<Item>): String = json.encodeToString(items)

    /** 解析 JSON 数组字符串为条目列表（桥接/测试用） */
    fun decode(raw: String): List<Item> = try {
        json.decodeFromString<List<Item>>(raw)
    } catch (_: Exception) {
        emptyList()
    }

    @Serializable
    data class Item(
        val id: String,
        val kind: String,            // "text" | "image"
        val text: String = "",       // 文本内容 / 图片说明（可空）
        val imagePath: String = "",  // kind=image 时缓存文件绝对路径
        val mime: String = "text/plain",
        val source: String = "",     // 来源包名，如 com.android.chrome
        @SerialName("created_at") val createdAt: Long = System.currentTimeMillis(),
    )

    // ------------------------------------------------------------------ //
    // 写入
    // ------------------------------------------------------------------ //

    /** 从分享 Intent 读取 EXTRA_TEXT 存入收件箱（自动截断超长文本）。 */
    fun addText(context: Context, text: String, source: String = ""): Item {
        val trimmed = text.trim()
        val item = Item(
            id = UUID.randomUUID().toString(),
            kind = "text",
            text = trimmed.take(100_000),
            source = source,
        )
        addItem(context, item)
        return item
    }

    /**
     * 从 content:// 或 file:// URI 复制图片到应用私有缓存并登记。
     * 失败（URI 无权限/损坏/超限）返回 null。
     */
    fun addImage(context: Context, uri: Uri, source: String = ""): Item? {
        return try {
            val resolver = context.contentResolver
            val ext = guessExtension(resolver, uri)
            val dir = File(context.filesDir, "inbox").apply { mkdirs() }
            val target = File(dir, "${UUID.randomUUID()}.$ext")

            resolver.openInputStream(uri)?.use { input ->
                target.outputStream().use { output -> input.copyTo(output) }
            } ?: return null

            if (target.length() > MAX_IMAGE_BYTES) {
                target.delete()
                return null
            }

            val item = Item(
                id = UUID.randomUUID().toString(),
                kind = "image",
                text = "",
                imagePath = target.absolutePath,
                mime = resolver.getType(uri) ?: "image/jpeg",
                source = source,
            )
            addItem(context, item)
            item
        } catch (_: Exception) {
            null
        }
    }

    // ------------------------------------------------------------------ //
    // 读取 / 删除
    // ------------------------------------------------------------------ //

    fun list(context: Context): List<Item> {
        val raw = prefs(context).getString(KEY_ITEMS, null) ?: return emptyList()
        return try {
            json.decodeFromString<List<Item>>(raw)
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun count(context: Context): Int = list(context).size

    fun remove(context: Context, id: String) {
        val updated = list(context).filterNot { it.id == id }
        save(context, updated)
    }

    fun clear(context: Context) {
        save(context, emptyList())
        File(context.filesDir, "inbox").listFiles()?.forEach { it.delete() }
    }

    /** 图片转 base64 data URL 供网页版直接上传（限制大小防撑爆 bridge）。 */
    fun imageDataUrl(context: Context, id: String): String? {
        val item = list(context).firstOrNull { it.id == id } ?: return null
        if (item.kind != "image" || item.imagePath.isBlank()) return null
        val file = File(item.imagePath)
        if (!file.exists() || file.length() > MAX_IMAGE_BYTES) return null
        return try {
            val bytes = file.readBytes()
            val mime = if (item.mime.isNotBlank()) item.mime else "image/jpeg"
            "data:$mime;base64," + Base64.encodeToString(bytes, Base64.NO_WRAP)
        } catch (_: Exception) {
            null
        }
    }

    // ------------------------------------------------------------------ //
    // 内部
    // ------------------------------------------------------------------ //

    private fun addItem(context: Context, item: Item) {
        val updated = list(context) + item
        // 上限 50 条：超出丢最旧
        save(context, updated.takeLast(50))
    }

    private fun save(context: Context, items: List<Item>) {
        prefs(context).edit().putString(KEY_ITEMS, json.encodeToString(items)).apply()
        // 清理已被删除条目对应的孤儿图片
        val keepIds = items.mapTo(HashSet()) { it.id }
        File(context.filesDir, "inbox").listFiles()?.forEach { file ->
            val id = file.name.substringBeforeLast('.').takeIf { keepIds.contains(it) }
            if (id == null) file.delete()
        }
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun guessExtension(resolver: ContentResolver, uri: Uri): String {
        val mime = resolver.getType(uri) ?: return "jpg"
        return when (mime.lowercase()) {
            "image/png" -> "png"
            "image/webp" -> "webp"
            "image/gif" -> "gif"
            "image/bmp" -> "bmp"
            "image/heic", "image/heif" -> "heic"
            else -> "jpg"
        }
    }
}
