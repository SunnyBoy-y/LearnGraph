package com.learngraph.mobile.util

/** Pure timing/state contract for live streams. History snapshots never enter this interface. */
interface NativeChatHapticFeedback {
    fun setForeground(foreground: Boolean)
    fun beginStream()
    fun onEvent(type: String, eventId: String?, partType: String?, partStatus: String?)
    fun endStream()
    fun close()

    object None : NativeChatHapticFeedback {
        override fun setForeground(foreground: Boolean) = Unit
        override fun beginStream() = Unit
        override fun onEvent(type: String, eventId: String?, partType: String?, partStatus: String?) = Unit
        override fun endStream() = Unit
        override fun close() = Unit
    }
}

internal enum class NativeHapticAction { START_REPLY, STOP_REPLY, STEP, CELEBRATE }

/** Shared across native streams and compatibility pages, matching Web's > 900ms gate. */
internal class NativeStepHapticThrottle {
    private var lastAt: Long? = null
    @Synchronized fun acquire(now: Long): Boolean {
        val previous = lastAt
        if (previous != null && now >= previous && now - previous <= 900) return false
        lastAt = now
        return true
    }
}

internal class NativeChatHapticPolicy(private val throttle: NativeStepHapticThrottle = NativeStepHapticThrottle()) {
    private var active = false
    private var foreground = false
    private var enabled = true
    private var replyStarted = false
    private var replyRunning = false
    private var hadStep = false
    private val seen = mutableSetOf<String>()

    fun beginStream(): List<NativeHapticAction> {
        val stop = stopReply()
        active = true
        replyStarted = false
        hadStep = false
        seen.clear()
        return stop
    }

    fun setForeground(value: Boolean): List<NativeHapticAction> {
        foreground = value
        // Returning to the foreground never replays an answer boundary or a past tool step.
        return if (!value) stopReply() else emptyList()
    }

    fun setEnabled(value: Boolean): List<NativeHapticAction> {
        enabled = value
        return if (!value) stopReply() else emptyList()
    }

    fun event(type: String, eventId: String?, partType: String?, partStatus: String?, now: Long): List<NativeHapticAction> {
        if (!active || eventId != null && !seen.add(eventId)) return emptyList()
        if (type in setOf("message.completed", "message.failed", "message.cancelled", "message.interrupted")) {
            active = false
            val result = stopReply().toMutableList()
            if (type == "message.completed" && hadStep && foreground && enabled) result += NativeHapticAction.CELEBRATE
            hadStep = false
            return result
        }
        if (type == "answer.started") {
            if (replyStarted) return emptyList()
            replyStarted = true
            if (foreground && enabled) {
                replyRunning = true
                return listOf(NativeHapticAction.START_REPLY)
            }
        }
        if (foreground && enabled && partType in setOf("tool_call", "agent_step") && partStatus == "completed" && throttle.acquire(now)) {
            hadStep = true
            return listOf(NativeHapticAction.STEP)
        }
        return emptyList()
    }

    fun endStream(): List<NativeHapticAction> {
        active = false
        hadStep = false
        return stopReply()
    }

    private fun stopReply(): List<NativeHapticAction> {
        if (!replyRunning) return emptyList()
        replyRunning = false
        return listOf(NativeHapticAction.STOP_REPLY)
    }
}

/** A stale stream/page can release only its own motor lease, never the current page's. */
internal class HapticOwnership {
    var owner: String? = null
        private set
    var repeating: Boolean = false
        private set
    fun claim(owner: String, repeating: Boolean) { this.owner = owner; this.repeating = repeating }
    fun release(owner: String, repeatingOnly: Boolean = false): Boolean {
        if (this.owner != owner || repeatingOnly && !repeating) return false
        this.owner = null
        repeating = false
        return true
    }
}
