package com.learngraph.mobile.data

import kotlinx.serialization.json.Json
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

class NativeCacheStoreTest {
    @get:Rule val temporary = TemporaryFolder()
    private val json = Json { ignoreUnknownKeys = true }

    @Test fun scopeSeparatesServersAccountsWorkspacesAndLegacyTokens() {
        fun scope(server: String = "https://example.com", workspace: String = "w", user: String? = "u", token: String = "t") =
            NativeCacheStore.scope(server, workspace, user, token)
        assertEquals(scope(), scope("https://EXAMPLE.com/"))
        assertEquals(scope(token = "renewed"), scope())
        assertNotEquals(scope(), scope("https://other.example.com"))
        assertNotEquals(scope(), scope(workspace = "other"))
        assertNotEquals(scope(), scope(user = "other"))
        assertNotEquals(scope(user = null), scope(user = null, token = "other"))
        assertNull(NativeCacheStore.scope("https://example.com", "w", "u", null))
    }

    @Test fun persistedBodyAndValidatorSurviveNewStoreInstance() {
        val directory = temporary.newFolder()
        NativeCacheStore(directory, json) { 1234 }.write("scope", "sessions", "[]", "\"v1\"")
        val restored = NativeCacheStore(directory, json).read("scope", "sessions")!!
        assertEquals("[]", restored.body)
        assertEquals("\"v1\"", restored.etag)
        assertTrue(restored.isFresh(1235, 30_000))
        assertFalse(restored.isFresh(31_234, 30_000))
        assertFalse(restored.isFresh(1233, 30_000))
        assertNull(NativeCacheStore(directory, json).read("another-scope", "sessions"))
    }

    @Test fun deletionRemovesOldMessagePageButKeepsOtherSessionAndAccount() {
        val directory = temporary.newFolder()
        val cache = NativeCacheStore(directory, json)
        cache.write("account-a", "sessions", "[]", null)
        cache.write("account-a", "messages:deleted", "old", null)
        cache.write("account-a", "messages:kept", "current", null)
        cache.write("account-b", "messages:deleted", "separate", null)
        cache.pruneMessagePages("account-a", setOf("kept"))
        assertNull(cache.read("account-a", "messages:deleted"))
        assertEquals("current", cache.read("account-a", "messages:kept")!!.body)
        assertNotNull(cache.read("account-a", "sessions"))
        assertEquals("separate", cache.read("account-b", "messages:deleted")!!.body)
    }

    @Test fun corruptEntryIsDiscardedAndAccountClearIsIsolated() {
        val directory = temporary.newFolder()
        val cache = NativeCacheStore(directory, json)
        cache.write("account-a", "sessions", "[]", null)
        cache.write("account-b", "sessions", "[]", null)
        File(directory, "account-a").listFiles()!!.single().writeText("partial JSON")
        assertNull(cache.read("account-a", "sessions"))
        cache.write("account-a", "sessions", "[]", null)
        cache.clear("account-a")
        assertNull(cache.read("account-a", "sessions"))
        assertNotNull(cache.read("account-b", "sessions"))
    }

    @Test fun replacementCommitsUpdatedBodyValidatorAndTimestamp() {
        val directory = temporary.newFolder()
        val cache = NativeCacheStore(directory, json)
        cache.write("scope", "sessions", "Original", "\"v1\"", 100)
        cache.write("scope", "sessions", "Renamed", "\"v2\"", 200)
        val restored = NativeCacheStore(directory, json).read("scope", "sessions")!!
        assertEquals("Renamed", restored.body)
        assertEquals("\"v2\"", restored.etag)
        assertEquals(200L, restored.checkedAt)
        assertEquals(1, File(directory, "scope").listFiles()!!.size)
    }

    @Test fun interruptedReplacementRecoversCommittedBackupAndDiscardsPendingWrite() {
        val directory = temporary.newFolder()
        val cache = NativeCacheStore(directory, json)
        cache.write("scope", "sessions", "Committed", "\"v1\"", 100)
        val target = File(directory, "scope").listFiles()!!.single()
        val backup = File(target.parentFile, target.name + ".backup")
        val pending = File(target.parentFile, target.name + ".pending")
        assertTrue(target.renameTo(backup))
        pending.writeText(json.encodeToString(NativeCacheStore.Entry.serializer(), NativeCacheStore.Entry("Uncommitted", "\"v2\"", 200)))
        val restored = NativeCacheStore(directory, json).read("scope", "sessions")!!
        assertEquals("Committed", restored.body)
        assertEquals("\"v1\"", restored.etag)
        assertTrue(target.isFile)
        assertFalse(backup.exists())
        assertFalse(pending.exists())
    }

    @Test fun recoveryPrefersValidCommittedTargetAndCanRestoreFirstPendingWrite() {
        val directory = temporary.newFolder()
        val cache = NativeCacheStore(directory, json)
        cache.write("scope", "sessions", "Original", "\"v1\"")
        val target = File(directory, "scope").listFiles()!!.single()
        val backup = File(target.parentFile, target.name + ".backup")
        target.copyTo(backup)
        target.writeText(json.encodeToString(NativeCacheStore.Entry.serializer(), NativeCacheStore.Entry("New committed", "\"v2\"", 200)))
        assertEquals("New committed", NativeCacheStore(directory, json).read("scope", "sessions")!!.body)
        assertFalse(backup.exists())
        val pending = File(target.parentFile, target.name + ".pending")
        assertTrue(target.renameTo(pending))
        assertEquals("New committed", NativeCacheStore(directory, json).read("scope", "sessions")!!.body)
        assertTrue(target.isFile)
        assertFalse(pending.exists())
    }

    @Test fun corruptTargetRecoversBackupAndRemovalCannotResurrectBackup() {
        val directory = temporary.newFolder()
        val cache = NativeCacheStore(directory, json)
        cache.write("scope", "sessions", "Durable", "\"v1\"")
        val target = File(directory, "scope").listFiles()!!.single()
        val backup = File(target.parentFile, target.name + ".backup")
        target.copyTo(backup)
        target.writeText("partial JSON")
        assertEquals("Durable", NativeCacheStore(directory, json).read("scope", "sessions")!!.body)
        target.copyTo(backup)
        target.copyTo(File(target.parentFile, target.name + ".pending"))
        cache.remove("scope", "sessions")
        assertNull(NativeCacheStore(directory, json).read("scope", "sessions"))
        assertTrue(File(directory, "scope").listFiles()!!.isEmpty())
    }
}
