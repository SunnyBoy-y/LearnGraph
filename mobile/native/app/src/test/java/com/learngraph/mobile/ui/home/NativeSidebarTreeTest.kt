package com.learngraph.mobile.ui.home

import com.learngraph.mobile.data.Session
import org.junit.Assert.*
import org.junit.Test

class NativeSidebarTreeTest {
    private fun session(id: String, parent: String? = null, project: String? = null, pinned: Boolean = false, status: String = "active", title: String = id) =
        Session(id = id, workspaceId = "w", title = title, parentSessionId = parent, projectId = project, pinned = pinned, status = status)

    @Test fun projectBoundariesAndMissingParentsNeverHideReachableSessions() {
        val sessions = listOf(session("root", project = "p"), session("branch", "root", "p"), session("orphan", "elsewhere", "p"), session("other", "root"))
        val tree = NativeSidebarTree.forest(sessions, "p")
        assertEquals(setOf("root", "orphan"), tree.map { it.session.id }.toSet())
        assertEquals("branch", tree.first { it.session.id == "root" }.children.single().session.id)
        assertEquals("other", NativeSidebarTree.forest(sessions, null).single().session.id)
    }
    @Test fun cyclesAndSelfReferencesRemainVisibleInsteadOfRecursingForever() {
        val tree = NativeSidebarTree.forest(listOf(session("a", "b"), session("b", "a"), session("c", "c")), null)
        assertEquals(setOf("a", "b", "c"), NativeSidebarTree.flatten(tree).map { it.id }.toSet())
    }
    @Test fun explicitCollapseWinsOverAnActiveNestedBranch() {
        val tree = NativeSidebarTree.forest(listOf(session("root"), session("child", "root")), null)
        assertEquals(2, NativeSidebarTree.rows(tree, emptyMap(), "child").size)
        assertEquals(1, NativeSidebarTree.rows(tree, mapOf("session:root" to false), "child").size)
        assertEquals(2, NativeSidebarTree.rows(tree, mapOf("session:root" to false), null, "child").size)
    }
    @Test fun pinOrderingArchiveFilteringAndLegacyBranchLabelsMatchSidebarMeaning() {
        val tree = NativeSidebarTree.forest(listOf(session("a"), session("b", pinned = true), session("c", status = "archived")), null)
        assertEquals(listOf("b", "a"), tree.map { it.session.id })
        assertEquals("c", NativeSidebarTree.forest(listOf(session("c", status = "archived")), null, archived = true).single().session.id)
        val branches = NativeSidebarTree.forest(listOf(session("root"), session("a", "root", title = "分支.原会话"), session("b", "root", title = "分支.分支.原会话")), null)
        assertEquals(listOf("root", "原会话（1）", "原会话（2）"), NativeSidebarTree.rows(branches, mapOf("session:root" to true), null).map { it.title })
    }
    @Test fun mixedBatchExcludesProjectChildrenAndCapsStandaloneSessionsAtOneHundred() {
        val sessions = listOf(session("covered", project = "p")) + (1..110).map { session("s$it") }
        val selected = sessions.map { it.id }.toSet()
        val ids = NativeSidebarTree.standaloneSelection(sessions, selected, setOf("p"))
        assertEquals(100, ids.size)
        assertFalse("covered" in ids)
    }
}
