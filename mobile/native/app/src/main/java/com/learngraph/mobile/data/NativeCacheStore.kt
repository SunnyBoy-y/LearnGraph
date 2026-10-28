package com.learngraph.mobile.data

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.io.IOException
import java.net.URI
import java.security.MessageDigest
import java.util.Locale

/** App-private cache: no token or account name is written into filenames. */
internal class NativeCacheStore(
    private val directory: File,
    private val json: Json,
    private val now: () -> Long = System::currentTimeMillis,
) {
    @Serializable
    data class Entry(val body: String, val etag: String? = null, val checkedAt: Long = 0) {
        fun isFresh(now: Long, maxAgeMillis: Long): Boolean =
            checkedAt > 0 && now >= checkedAt && now - checkedAt < maxAgeMillis
    }

    @Synchronized
    fun read(scope: String, key: String): Entry? {
        val target = file(scope, key)
        val backup = backup(target)
        val pending = pending(target)
        decode(target)?.let {
            backup.delete()
            pending.delete()
            return it
        }
        // An interrupted replacement keeps the last committed body in .backup.
        // A first-ever write may instead leave only a complete, synced .pending.
        val recovery = listOf(backup, pending).firstNotNullOfOrNull { candidate ->
            decode(candidate)?.let { candidate to it }
        }
        if (recovery != null) {
            val (source, entry) = recovery
            if (!target.exists() || target.delete()) source.renameTo(target)
            if (decode(target) != null) {
                backup.delete()
                pending.delete()
            } else {
                // If the OS temporarily prevents recovery, retain its only good copy.
                if (source != pending) pending.delete()
            }
            return entry
        }
        target.delete()
        backup.delete()
        pending.delete()
        return null
    }

    @Synchronized
    fun write(scope: String, key: String, body: String, etag: String?, checkedAt: Long = now()): Entry {
        val entry = Entry(body, etag, checkedAt)
        val encoded = json.encodeToString(Entry.serializer(), entry).toByteArray(Charsets.UTF_8)
        if (encoded.size > MAX_ENTRY_BYTES) {
            remove(scope, key)
            return entry
        }
        val target = file(scope, key)
        if (!target.parentFile!!.isDirectory && !target.parentFile!!.mkdirs()) {
            throw IOException("Cannot create private cache directory")
        }
        read(scope, key)
        // File.renameTo cannot replace an existing file on Windows. Move the
        // committed body aside first; all operations also work on Android API 23.
        val pending = pending(target)
        val backup = backup(target)
        pending.outputStream().use { stream -> stream.write(encoded); stream.fd.sync() }
        if (target.exists()) {
            if (decode(target) != null) {
                if (backup.exists() && !backup.delete()) throw IOException("Cannot clear old cache backup")
                if (!target.renameTo(backup)) {
                    pending.delete()
                    throw IOException("Cannot preserve previous cache entry")
                }
            } else if (!target.delete()) {
                pending.delete()
                throw IOException("Cannot remove damaged cache entry")
            }
        }
        if (!pending.renameTo(target)) {
            if (!target.exists()) backup.renameTo(target)
            pending.delete()
            throw IOException("Cannot commit private cache entry")
        }
        backup.delete()
        trim()
        return entry
    }

    @Synchronized
    fun remove(scope: String, key: String) {
        val target = file(scope, key)
        target.delete()
        backup(target).delete()
        pending(target).delete()
    }

    @Synchronized
    fun pruneMessagePages(scope: String, sessionIds: Set<String>) {
        val keep = sessionIds.flatMap { listOf(filename("messages:$it"), filename("composer:$it")) }.toSet()
        File(directory, scope).listFiles()?.filter {
            val name = it.name.removeSuffix(".backup").removeSuffix(".pending")
            name != filename(SESSIONS_KEY) && name !in keep
        }
            ?.forEach { it.delete() }
    }

    @Synchronized
    fun clear(scope: String) { File(directory, scope).deleteRecursively() }

    private fun file(scope: String, key: String) = File(File(directory, scope), filename(key))
    private fun filename(key: String) = digest(key) + ".json"
    private fun backup(file: File) = File(file.parentFile, file.name + ".backup")
    private fun pending(file: File) = File(file.parentFile, file.name + ".pending")

    private fun decode(file: File): Entry? = try {
        if (!file.isFile || file.length() > MAX_ENTRY_BYTES) null
        else json.decodeFromString<Entry>(file.readText())
    } catch (_: Exception) { null }

    private fun trim() {
        val entries = directory.listFiles()?.flatMap { it.listFiles()?.toList().orEmpty() }.orEmpty()
            .filter { it.isFile && it.name.removeSuffix(".backup").removeSuffix(".pending").endsWith(".json") }
        var size = entries.sumOf { it.length() }
        for (file in entries.sortedBy { it.lastModified() }) {
            if (size <= MAX_CACHE_BYTES) break
            val length = file.length()
            if (file.delete()) size -= length
        }
    }

    companion object {
        const val SESSIONS_KEY = "sessions"
        private const val MAX_ENTRY_BYTES = 8 * 1024 * 1024
        private const val MAX_CACHE_BYTES = 32L * 1024 * 1024

        fun scope(baseUrl: String, workspaceId: String?, accountId: String?, token: String?): String? {
            if (baseUrl.isBlank() || workspaceId.isNullOrBlank() || token.isNullOrBlank()) return null
            val server = try {
                val uri = URI(baseUrl.trim().trimEnd('/')).normalize()
                URI(uri.scheme?.lowercase(Locale.ROOT), uri.userInfo, uri.host?.lowercase(Locale.ROOT),
                    uri.port, uri.path, uri.query, null).toString()
            } catch (_: Exception) { baseUrl.trim().trimEnd('/') }
            // Old saved logins without a stable ID remain isolated by their bearer fingerprint.
            val account = accountId?.takeIf { it.isNotBlank() } ?: "token:${digest(token)}"
            return digest("$server\u0000$workspaceId\u0000$account")
        }

        private fun digest(value: String): String = MessageDigest.getInstance("SHA-256")
            .digest(value.toByteArray(Charsets.UTF_8)).joinToString("") { "%02x".format(it) }
    }
}
