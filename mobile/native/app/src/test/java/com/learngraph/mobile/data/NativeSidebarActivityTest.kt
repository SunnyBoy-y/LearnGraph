package com.learngraph.mobile.data

import org.junit.Assert.*
import org.junit.Test

class NativeSidebarActivityTest {
    @Test fun openingClearsUnreadWithoutChangingRecency() {
        val ledger = NativeSidebarActivityLedger()
        ledger.touched("a", 10)
        ledger.finished("stream", "a", at = 20)
        assertEquals(NativeSessionActivity(unreadCompleted = true, completedAt = 20, lastTouchedAt = 10), ledger.state.value.activities["a"])
        ledger.viewed("screen", "a")
        assertEquals(NativeSessionActivity(lastTouchedAt = 10), ledger.state.value.activities["a"])
        ledger.viewed("screen", "never-used")
        assertFalse(ledger.state.value.activities.containsKey("never-used"))
    }

    @Test fun realForegroundReplyTouchesWhileBackgroundReplyBecomesUnread() {
        val ledger = NativeSidebarActivityLedger()
        ledger.viewed("screen", "a"); ledger.running("stream", "a", true)
        ledger.finished("stream", "a", at = 30)
        assertEquals(NativeSessionActivity(lastTouchedAt = 30), ledger.state.value.activities["a"])
        ledger.viewed("screen", "b"); ledger.running("stream", "a", true)
        ledger.finished("stream", "a", at = 40)
        assertEquals(NativeSessionActivity(unreadCompleted = true, completedAt = 40, lastTouchedAt = 30), ledger.state.value.activities["a"])
        ledger.touched("a", 5)
        assertEquals(30L, ledger.state.value.activities["a"]!!.lastTouchedAt)
        assertEquals(1, NativeSidebarActivityPolicy.tier(ledger.state.value.activities["a"]))
    }

    @Test fun documentReleaseCannotClearAnotherNativeStreamOrPersistPhantomVoice() {
        var saved = emptyMap<String, NativeSessionActivity>()
        val ledger = NativeSidebarActivityLedger(save = { saved = it })
        ledger.touched("a", 10); ledger.running("native", "a", true)
        ledger.running("web", "a", true); ledger.voice("web", "a", true)
        ledger.release("web")
        assertTrue(ledger.state.value.activities["a"]!!.running)
        assertTrue(ledger.state.value.voiceActiveSessionIds.isEmpty())
        ledger.release("native")
        assertFalse(ledger.state.value.activities["a"]!!.running)
        assertFalse(ledger.state.value.activities["a"]!!.unreadCompleted)
        val restored = NativeSidebarActivityLedger(saved + ("stale" to NativeSessionActivity(running = true, lastTouchedAt = 1)))
        assertTrue(restored.state.value.activities.values.none { it.running })
        assertTrue(restored.state.value.voiceActiveSessionIds.isEmpty())
        assertEquals(10L, restored.state.value.activities["a"]!!.lastTouchedAt)
    }

    @Test fun viewInOtherSurfaceCountsAndDeletingRemovesAllReferences() {
        val ledger = NativeSidebarActivityLedger()
        ledger.viewed("web", "a"); ledger.running("native", "a", true)
        ledger.voice("web", "a", true)
        ledger.finished("native", "a", at = 25)
        assertEquals(25L, ledger.state.value.activities["a"]!!.lastTouchedAt)
        assertFalse(ledger.state.value.activities["a"]!!.unreadCompleted)
        ledger.remove(setOf("a"))
        assertTrue(ledger.state.value.activities.isEmpty())
        assertTrue(ledger.state.value.voiceActiveSessionIds.isEmpty())
    }

    @Test fun duplicateAndOutOfOrderBridgeEventsAreRejectedPerDocument() {
        val ledger = NativeSidebarActivityLedger()
        assertTrue(ledger.acknowledge("web", 2)); ledger.acknowledged()
        assertFalse(ledger.acknowledge("web", 2)); assertFalse(ledger.acknowledge("web", 1))
        assertTrue(ledger.acknowledge("other", 1)); ledger.acknowledged()
        assertEquals(mapOf("web" to 2L, "other" to 1L), ledger.state.value.acknowledged)
        ledger.release("web")
        assertEquals(mapOf("other" to 1L), ledger.state.value.acknowledged)
    }

    @Test fun pinThenWebActivityTiersAndActualTimestampsControlSort() {
        fun session(id: String, pinned: Boolean = false, updated: String? = null, created: String? = null) =
            Session(id = id, workspaceId = "w", title = id, pinned = pinned, updatedAt = updated, createdAt = created)
        val sessions = listOf(session("idle", updated = "2026-10-18T01:00:00+00:00"), session("pin", true),
            session("unread-new"), session("unread-old"), session("touched"), session("a", created = "2099-01-01T00:00:00Z"), session("b"))
        val activity = mapOf("touched" to NativeSessionActivity(lastTouchedAt = 10),
            "unread-new" to NativeSessionActivity(unreadCompleted = true, completedAt = 30, lastTouchedAt = 1),
            "unread-old" to NativeSessionActivity(unreadCompleted = true, completedAt = 20, lastTouchedAt = 999))
        assertEquals(listOf("pin", "touched", "unread-new", "unread-old", "idle", "a", "b"), sessions.sortedWith(NativeSidebarActivityPolicy.comparator(sessions, activity)).map { it.id })
    }
}
