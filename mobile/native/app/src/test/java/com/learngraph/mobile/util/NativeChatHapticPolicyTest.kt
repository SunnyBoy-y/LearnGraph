package com.learngraph.mobile.util

import org.junit.Assert.*
import org.junit.Test

class NativeChatHapticPolicyTest {
    private fun foregroundPolicy(throttle: NativeStepHapticThrottle = NativeStepHapticThrottle()) =
        NativeChatHapticPolicy(throttle).apply { setForeground(true); beginStream() }

    @Test fun replyBeginsOnlyAtLiveAnswerBoundaryAndDeduplicatesReplay() {
        val policy = foregroundPolicy()
        assertTrue(policy.event("message.started", "1", null, null, 0).isEmpty())
        assertTrue(policy.event("part.delta", "2", "text", "streaming", 1).isEmpty())
        assertEquals(listOf(NativeHapticAction.START_REPLY), policy.event("answer.started", "3", null, null, 2))
        assertTrue(policy.event("answer.started", "3", null, null, 3).isEmpty())
        assertTrue(policy.event("answer.started", "duplicate-boundary", null, null, 4).isEmpty())
        assertEquals(listOf(NativeHapticAction.STOP_REPLY), policy.event("message.completed", "4", null, null, 5))
        assertTrue(policy.event("answer.started", "old-history", null, null, 6).isEmpty())
    }

    @Test fun everyTerminalAndTransportExitStopsWithoutCelebratingFailure() {
        for (terminal in listOf("message.completed", "message.failed", "message.cancelled", "message.interrupted")) {
            val policy = foregroundPolicy()
            policy.event("answer.started", "answer", null, null, 0)
            assertEquals(listOf(NativeHapticAction.STOP_REPLY), policy.event(terminal, "terminal", null, null, 1))
            assertTrue(policy.endStream().isEmpty())
        }
        val policy = foregroundPolicy()
        policy.event("answer.started", "answer", null, null, 0)
        assertEquals(listOf(NativeHapticAction.STOP_REPLY), policy.endStream())
    }

    @Test fun foregroundAndPreferenceChangesStopImmediatelyWithoutResumingHistoricalEffects() {
        val policy = foregroundPolicy()
        policy.event("answer.started", "answer", null, null, 0)
        assertEquals(listOf(NativeHapticAction.STOP_REPLY), policy.setForeground(false))
        assertTrue(policy.event("part.completed", "background-tool", "tool_call", "completed", 1000).isEmpty())
        assertTrue(policy.setForeground(true).isEmpty())
        assertTrue(policy.event("answer.started", "same-answer", null, null, 2000).isEmpty())
        assertTrue(policy.event("message.completed", "completed", null, null, 2100).isEmpty())
        policy.beginStream()
        policy.event("answer.started", "new-answer", null, null, 3000)
        assertEquals(listOf(NativeHapticAction.STOP_REPLY), policy.setEnabled(false))
        assertTrue(policy.setEnabled(true).isEmpty())
        assertTrue(policy.event("answer.started", "new-duplicate", null, null, 3100).isEmpty())
    }

    @Test fun stepsUseStrict900msSharedThrottleAndOnlySuccessfulActiveRoundCelebrates() {
        val throttle = NativeStepHapticThrottle()
        val policy = foregroundPolicy(throttle)
        assertEquals(listOf(NativeHapticAction.STEP), policy.event("part.completed", "a", "tool_call", "completed", 1000))
        assertTrue(policy.event("part.completed", "b", "agent_step", "completed", 1900).isEmpty())
        assertEquals(listOf(NativeHapticAction.STEP), policy.event("part.completed", "c", "agent_step", "completed", 1901))
        assertTrue(policy.event("part.completed", "c", "agent_step", "completed", 2900).isEmpty())
        policy.event("answer.started", "answer", null, null, 3000)
        assertEquals(listOf(NativeHapticAction.STOP_REPLY, NativeHapticAction.CELEBRATE), policy.event("message.completed", "done", null, null, 3100))
        assertTrue(policy.event("message.completed", "done-again", null, null, 3200).isEmpty())
        val other = foregroundPolicy(throttle)
        assertTrue(other.event("part.completed", "other", "tool_call", "completed", 2500).isEmpty())
        assertEquals(listOf(NativeHapticAction.STEP), other.event("part.completed", "other2", "tool_call", "completed", 2802))
        assertTrue(other.event("message.failed", "failed", null, null, 3000).isEmpty())
        other.beginStream()
        assertTrue(other.event("message.completed", "no-stale-celebration", null, null, 4000).isEmpty())
    }

    @Test fun oldNativeOrWebOwnersCannotStopEachOtherOrCancelCompletionPulse() {
        val lease = HapticOwnership()
        lease.claim("native-old", repeating = true)
        lease.claim("web-new", repeating = true)
        assertFalse(lease.release("native-old", repeatingOnly = true))
        assertEquals("web-new", lease.owner)
        assertTrue(lease.release("web-new", repeatingOnly = true))
        lease.claim("native-new", repeating = true)
        assertFalse(lease.release("web-new", repeatingOnly = true))
        assertTrue(lease.release("native-new", repeatingOnly = true))
        lease.claim("native-new", repeating = false)
        assertFalse(lease.release("native-new", repeatingOnly = true))
        assertTrue(lease.release("native-new"))
    }
}
