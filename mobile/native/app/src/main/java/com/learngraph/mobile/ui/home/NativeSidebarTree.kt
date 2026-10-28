package com.learngraph.mobile.ui.home

import com.learngraph.mobile.data.Session
import com.learngraph.mobile.data.NativeSessionActivity
import com.learngraph.mobile.data.NativeSidebarActivityPolicy

/** The same project-local parent folders used by workspace-shell's nestSidebarSessions. */
internal data class NativeSidebarNode(val session: Session, val children: List<NativeSidebarNode>)
internal data class NativeSidebarRow(val node: NativeSidebarNode, val title: String, val open: Boolean, val depth: Int)

internal object NativeSidebarTree {
    private val legacyBranch = Regex("^(?:分支\\.)+(.*)$")

    fun forest(sessions: List<Session>, projectId: String?, archived: Boolean = false, activity: Map<String, NativeSessionActivity> = emptyMap()): List<NativeSidebarNode> {
        val group = sessions.filter { it.projectId == projectId && (it.status == "archived") == archived }.distinctBy { it.id }
        val byId = group.associateBy { it.id }
        // A missing parent remains a root. Corrupt/self/cyclic references must never hide a chat.
        fun validParent(session: Session): String? {
            val parent = session.parentSessionId?.takeIf { it != session.id && it in byId } ?: return null
            val visited = mutableSetOf(session.id)
            var cursor: String? = parent
            while (cursor != null && cursor in byId) {
                if (!visited.add(cursor)) return null
                cursor = byId[cursor]?.parentSessionId
            }
            return parent
        }
        val children = group.groupBy(::validParent)
        val activityOrder = NativeSidebarActivityPolicy.comparator(group, activity)
        fun nodes(parent: String?): List<NativeSidebarNode> = children[parent].orEmpty()
            .sortedWith(activityOrder)
            .map { NativeSidebarNode(it, nodes(it.id)) }
        return nodes(null)
    }

    fun flatten(nodes: List<NativeSidebarNode>): List<Session> = nodes.flatMap { listOf(it.session) + flatten(it.children) }

    fun rows(nodes: List<NativeSidebarNode>, folders: Map<String, Boolean>, activeId: String?, query: String = "", depth: Int = 0): List<NativeSidebarRow> {
        var legacyIndex = 0
        return nodes.flatMap { node ->
            val legacy = legacyBranch.matchEntire(node.session.title.trim())
            if (legacy != null) legacyIndex++
            val title = legacy?.let { it.groupValues[1].trim().ifBlank { "未命名会话" } + if (depth > 0) "（$legacyIndex）" else "" } ?: node.session.title
            val descendants = flatten(node.children)
            val matches = query.isBlank() || node.session.title.contains(query, true) || descendants.any { it.title.contains(query, true) }
            if (!matches) emptyList()
            else {
                val open = if (query.isNotBlank()) true else folders["session:${node.session.id}"] ?: descendants.any { it.id == activeId }
                listOf(NativeSidebarRow(node, title, open, depth)) + if (open) rows(node.children, folders, activeId, query, depth + 1) else emptyList()
            }
        }
    }

    /** Project deletion covers its sessions; never submit those IDs twice in a mixed batch. */
    fun standaloneSelection(sessions: List<Session>, sessionIds: Set<String>, projectIds: Set<String>): List<String> =
        sessions.filter { it.id in sessionIds && it.projectId !in projectIds }.map { it.id }.distinct().take(100)
}
