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
 * 任意 App 通过系统分享（ACTION_SEND / ACTION_SEND_MULTIPLE）投递的文本/文件
 * 先落在本地收件箱，网页版通过 LearnGraphNative.getInboxItems() 拉取并自动
 * 消费（文本→填对话框；文件→上传为新会话附件），无需中转箱手动点击。
 *
 * 存储：
 *  - 元数据：SharedPreferences("lg_share_inbox") 一条 JSON 数组
 *  - 文件：filesDir/inbox/<id>.<ext>（应用私有，跨重启保留）
 */
object ShareInbox {

    private const val PREFS = "lg_share_inbox"
    private const val KEY_ITEMS = "items"
    // 单个文件 15 MiB 上限：桥接走 base64，过大会撑爆 WebView 桥接消息。
    private const val MAX_FILE_BYTES = 15 * 1024 * 1024

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
        val kind: String,            // "text" | "image" | "file"
        val text: String = "",       // 文本内容（kind=text 时）
        val imagePath: String = "",  // 缓存文件绝对路径（image/file）
        val mime: String = "text/plain",
        val name: String = "",       // 原始文件名（image/file）
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
     * 从 content:// 或 file:// URI 复制任意文件到应用私有缓存并登记（通用）。
     * 失败（URI 无权限/损坏/超限）返回 null。
     */
    fun addFile(context: Context, uri: Uri, source: String = ""): Item? {
        return try {
            val resolver = context.contentResolver
            val mime = resolver.getType(uri) ?: "application/octet-stream"
            val name = queryDisplayName(resolver, uri)
                ?: "share-${System.currentTimeMillis()}"
            val ext = guessFileExtension(name, mime)
            val dir = File(context.filesDir, "inbox").apply { mkdirs() }
            val target = File(dir, "${UUID.randomUUID()}.$ext")

            resolver.openInputStream(uri)?.use { input ->
                target.outputStream().use { output -> input.copyTo(output) }
            } ?: return null

            if (target.length() > MAX_FILE_BYTES) {
                target.delete()
                return null
            }

            val item = Item(
                id = UUID.randomUUID().toString(),
                kind = "file",
                text = "",
                imagePath = target.absolutePath,
                mime = mime,
                name = name,
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

    /** 文件转 base64 data URL 供网页版直接上传（限制大小防撑爆 bridge）。 */
    fun fileDataUrl(context: Context, id: String): String? {
        val item = list(context).firstOrNull { it.id == id } ?: return null
        if (item.kind != "file" && item.kind != "image") return null
        val file = File(item.imagePath)
        if (!file.exists() || file.length() > MAX_FILE_BYTES) return null
        return try {
            val bytes = file.readBytes()
            val mime = if (item.mime.isNotBlank()) item.mime else "application/octet-stream"
            "data:$mime;base64," + Base64.encodeToString(bytes, Base64.NO_WRAP)
        } catch (_: Exception) {
            null
        }
    }

    /** 兼容旧桥接：等价于 fileDataUrl。 */
    fun imageDataUrl(context: Context, id: String): String? = fileDataUrl(context, id)

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
        // 清理已被删除条目对应的孤儿文件
        val keepIds = items.mapTo(HashSet()) { it.id }
        File(context.filesDir, "inbox").listFiles()?.forEach { file ->
            val id = file.name.substringBeforeLast('.').takeIf { keepIds.contains(it) }
            if (id == null) file.delete()
        }
    }

    private fun prefs(context: Context) =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun queryDisplayName(resolver: ContentResolver, uri: Uri): String? {
        return try {
            resolver.query(uri, null, null, null, null)?.use { cursor ->
                val idx = cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
                if (idx >= 0 && cursor.moveToFirst()) cursor.getString(idx) else null
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun guessFileExtension(name: String, mime: String): String {
        val dot = name.lastIndexOf('.')
        if (dot > 0 && dot < name.length - 1) {
            val ext = name.substring(dot + 1).lowercase()
            if (ext.length in 1..10 && ext.all { it.isLetterOrDigit() }) return ext
        }
        return when (mime.lowercase()) {
            "application/pdf" -> "pdf"
            "text/plain" -> "txt"
            "application/json" -> "json"
            else -> "bin"
        }
    }
}
