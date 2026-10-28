package com.learngraph.mobile.data

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.serialization.Serializable
import java.text.SimpleDateFormat
import java.util.Locale

@Serializable
data class NativeSessionActivity(
    val running: Boolean = false,
    val unreadCompleted: Boolean = false,
    val completedAt: Long? = null,
    val lastTouchedAt: Long = 0,
)

@Serializable
data class NativeSidebarActivitySnapshot(
    val revision: Long = 0,
    val activities: Map<String, NativeSessionActivity> = emptyMap(),
    val voiceActiveSessionIds: Set<String> = emptySet(),
    val acknowledged: Map<String, Long> = emptyMap(),
)

/** Same tiers and timestamps as frontend/lib/session-activity; navigation never touches recency. */
object NativeSidebarActivityPolicy {
    fun tier(activity: NativeSessionActivity?): Int = when {
        activity?.unreadCompleted == true -> 1
        (activity?.lastTouchedAt ?: 0) > 0 -> 0
        else -> 2
    }

    fun comparator(sessions: List<Session>, activity: Map<String, NativeSessionActivity>): Comparator<Session> {
        val times = sessions.associate { it.id to epoch(it.updatedAt) }
        return Comparator { left, right ->
            when {
                (left.pinned == true) != (right.pinned == true) -> if (left.pinned == true) -1 else 1
                tier(activity[left.id]) != tier(activity[right.id]) -> tier(activity[left.id]) - tier(activity[right.id])
                else -> {
                    val primary = when (tier(activity[left.id])) {
                        0 -> (activity[right.id]?.lastTouchedAt ?: 0).compareTo(activity[left.id]?.lastTouchedAt ?: 0)
                        1 -> (activity[right.id]?.completedAt ?: 0).compareTo(activity[left.id]?.completedAt ?: 0)
                        else -> 0
                    }
                    if (primary != 0) primary else {
                        val updated = (times[right.id] ?: 0).compareTo(times[left.id] ?: 0)
                        if (updated != 0) updated else left.id.compareTo(right.id)
                    }
                }
            }
        }
    }

    private fun epoch(value: String?): Long {
        if (value.isNullOrBlank()) return 0
        val normalized = value.replace(Regex("(\\.\\d{3})\\d+"), "$1").replace("Z", "+0000")
            .replace(Regex("([+-]\\d{2}):(\\d{2})$"), "$1$2")
        return listOf("yyyy-MM-dd'T'HH:mm:ss.SSSZ", "yyyy-MM-dd'T'HH:mm:ssZ", "yyyy-MM-dd'T'HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm:ss").firstNotNullOfOrNull { pattern ->
            runCatching { SimpleDateFormat(pattern, Locale.US).apply { isLenient = false }.parse(normalized)?.time }.getOrNull()
        } ?: 0
    }
}

/** Real stream/page owners keep transient running/voice state out of persistent storage. */
internal class NativeSidebarActivityLedger(
    initial: Map<String, NativeSessionActivity> = emptyMap(),
    private val save: (Map<String, NativeSessionActivity>) -> Unit = {},
) {
    private data class Owner(var viewed: String? = null, val running: MutableSet<String> = mutableSetOf(),
        val voice: MutableSet<String> = mutableSetOf(), var sequence: Long = 0)
    private var durable = initial.mapValues { (_, value) -> value.copy(running = false) }
    private val owners = mutableMapOf<String, Owner>()
    private var revision = 0L
    private val mutable = MutableStateFlow(NativeSidebarActivitySnapshot(activities = durable))
    val state: StateFlow<NativeSidebarActivitySnapshot> = mutable

    @Synchronized fun touched(sessionId: String, at: Long) {
        if (!validSession(sessionId)) return
        val old = durable[sessionId] ?: NativeSessionActivity()
        durable = durable + (sessionId to old.copy(lastTouchedAt = maxOf(old.lastTouchedAt, at)))
        publish(true)
    }
    @Synchronized fun viewed(owner: String, sessionId: String?) {
        owners.getOrPut(owner) { Owner() }.viewed = sessionId?.takeIf(::validSession)
        if (sessionId != null) viewed(sessionId) else publish()
    }
    @Synchronized fun viewed(sessionId: String) {
        if (!validSession(sessionId)) return
        val old = durable[sessionId] ?: return
        durable = durable + (sessionId to old.copy(unreadCompleted = false, completedAt = null))
        publish(true)
    }
    @Synchronized fun running(owner: String, sessionId: String, running: Boolean) {
        if (!validSession(sessionId)) return
        val current = owners.getOrPut(owner) { Owner() }
        if (running) current.running += sessionId else current.running -= sessionId
        publish()
    }
    @Synchronized fun finished(owner: String, sessionId: String, viewing: Boolean = false, at: Long) {
        if (!validSession(sessionId)) return
        owners[owner]?.running?.remove(sessionId)
        val old = durable[sessionId] ?: NativeSessionActivity()
        val visible = viewing || owners.values.any { it.viewed == sessionId }
        durable = durable + (sessionId to if (visible) old.copy(unreadCompleted = false, completedAt = null,
            lastTouchedAt = maxOf(old.lastTouchedAt, at)) else old.copy(unreadCompleted = true, completedAt = at))
        publish(true)
    }
    @Synchronized fun voice(owner: String, sessionId: String, active: Boolean) {
        if (!validSession(sessionId)) return
        val current = owners.getOrPut(owner) { Owner() }
        if (active) current.voice += sessionId else current.voice -= sessionId
        publish()
    }
    @Synchronized fun acknowledge(owner: String, sequence: Long): Boolean {
        val current = owners.getOrPut(owner) { Owner() }
        if (sequence <= current.sequence) return false
        current.sequence = sequence
        return true
    }
    @Synchronized fun acknowledged() = publish()
    @Synchronized fun release(owner: String) { if (owners.remove(owner) != null) publish() }
    @Synchronized fun remove(sessionIds: Set<String>) {
        durable = durable.filterKeys { it !in sessionIds }
        owners.values.forEach { it.running -= sessionIds; it.voice -= sessionIds; if (it.viewed in sessionIds) it.viewed = null }
        publish(true)
    }
    private fun publish(persist: Boolean = false) {
        if (persist) {
            durable = durable.filterValues { it.unreadCompleted || it.completedAt != null || it.lastTouchedAt > 0 }
            save(durable)
        }
        val running = owners.values.flatMap { it.running }.toSet()
        val all = (durable.keys + running).associateWith { (durable[it] ?: NativeSessionActivity()).copy(running = it in running) }
        mutable.value = NativeSidebarActivitySnapshot(++revision, all, owners.values.flatMap { it.voice }.toSet(),
            owners.filterValues { it.sequence > 0 }.mapValues { it.value.sequence })
    }
    companion object { fun validSession(id: String) = id != "new" && id.matches(Regex("[A-Za-z0-9_-]{1,100}")) }
}
