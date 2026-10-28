package com.learngraph.mobile.util

import android.app.Activity
import android.app.Application
import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleOwner
import java.lang.ref.WeakReference
import java.util.UUID

/** The compatibility bridge and Compose share preferences, waveforms and motor ownership. */
object Haptics {
    const val PREFS = "lg_mobile"
    const val KEY_VIBRATE = "haptics_enabled"
    const val KEY_STRENGTH = "haptics_strength"
    private const val REPLY_ON_MS = 14L
    private const val REPLY_OFF_MS = 150L
    private const val REPLY_AMPLITUDE = 200
    private val ownership = HapticOwnership()
    private val stepThrottle = NativeStepHapticThrottle()
    private var activeHost: WeakReference<Activity>? = null
    private var application: Application? = null
    private var preferenceListener: SharedPreferences.OnSharedPreferenceChangeListener? = null

    fun newOwnerToken(source: String): String = "$source:${UUID.randomUUID()}"
    fun nativeFeedback(context: Context): NativeChatHapticFeedback = AndroidNativeFeedback(context)

    @JvmStatic @JvmOverloads
    fun haptic(context: Context, intensity: Int = 1, owner: String = legacyOwner(context)) {
        if (!permitted(context)) return
        val strength = if (intensity >= 0) intensity else preferences(context).getInt(KEY_STRENGTH, 1)
        oneShot(context, strength, owner)
    }

    @JvmStatic @JvmOverloads
    fun replyHaptic(context: Context, owner: String = legacyOwner(context)) {
        if (permitted(context)) oneShot(context, preferences(context).getInt(KEY_STRENGTH, 1), owner)
    }

    /** Exactly the Web final-answer waveform: 14ms on, 150ms off, amplitude 200. */
    @JvmStatic @JvmOverloads @Synchronized
    fun startReplyVibration(context: Context, owner: String = legacyOwner(context)) {
        if (!permitted(context)) return
        val vibrator = vibrator(context) ?: return
        ownership.claim(owner, repeating = true)
        activeHost = activity(context)?.let(::WeakReference)
        try {
            vibrator.cancel()
            if (Build.VERSION.SDK_INT >= 26) vibrator.vibrate(VibrationEffect.createWaveform(
                longArrayOf(0, REPLY_ON_MS, REPLY_OFF_MS), intArrayOf(0, REPLY_AMPLITUDE, 0), 0,
            )) else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(longArrayOf(0, REPLY_ON_MS, REPLY_OFF_MS), 0)
            }
        } catch (_: Exception) { ownership.release(owner); activeHost = null }
    }

    /** A stale page cannot cancel another stream, slider pulse or completion celebration. */
    @JvmStatic @JvmOverloads @Synchronized
    fun stopReplyVibration(context: Context, owner: String = legacyOwner(context)) {
        if (ownership.release(owner, repeatingOnly = true)) {
            runCatching { vibrator(context)?.cancel() }
            activeHost = null
        }
    }

    /** Release all effects belonging to a disposed/hidden page, including finite pulses. */
    @JvmStatic @Synchronized
    fun releaseOwner(context: Context, owner: String) {
        if (ownership.release(owner)) {
            runCatching { vibrator(context)?.cancel() }
            activeHost = null
        }
    }

    @JvmStatic @JvmOverloads
    fun stepHaptic(context: Context, owner: String = legacyOwner(context)) {
        if (permitted(context) && stepThrottle.acquire(SystemClock.elapsedRealtime())) oneShot(context, 0, owner)
    }

    @JvmStatic @JvmOverloads @Synchronized
    fun celebration(context: Context, owner: String = legacyOwner(context)) {
        // ReplyNotifier also uses this finite pattern from a receiver/application
        // context. Preserve that notification feedback; live chat always passes
        // its Activity and must satisfy the foreground gate.
        if (activity(context) == null) { install(context); if (!enabled(context)) return }
        else if (!permitted(context)) return
        val vibrator = vibrator(context) ?: return
        ownership.claim(owner, repeating = false)
        activeHost = activity(context)?.let(::WeakReference)
        try {
            if (Build.VERSION.SDK_INT >= 26) vibrator.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 40, 60, 40, 60, 80), -1))
            else {
                @Suppress("DEPRECATION")
                vibrator.vibrate(longArrayOf(0, 40, 60, 40, 60, 80), -1)
            }
        } catch (_: Exception) { ownership.release(owner); activeHost = null }
    }

    @JvmStatic fun chime() {
        try {
            val tone = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 60)
            tone.startTone(ToneGenerator.TONE_PROP_BEEP, 120)
            tone.release()
        } catch (_: Exception) { }
    }

    @Synchronized private fun oneShot(context: Context, strength: Int, owner: String) {
        if (!permitted(context)) return
        val vibrator = vibrator(context) ?: return
        ownership.claim(owner, repeating = false)
        activeHost = activity(context)?.let(::WeakReference)
        val duration = when (strength) { 2 -> 40L; 0 -> 12L; else -> 24L }
        try {
            if (Build.VERSION.SDK_INT >= 26) vibrator.vibrate(VibrationEffect.createOneShot(duration, VibrationEffect.DEFAULT_AMPLITUDE))
            else { @Suppress("DEPRECATION") vibrator.vibrate(duration) }
        } catch (_: Exception) { ownership.release(owner); activeHost = null }
    }

    private fun preferences(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    private fun enabled(context: Context) = preferences(context).getBoolean(KEY_VIBRATE, true)
    private fun legacyOwner(context: Context) = "legacy-web:${System.identityHashCode(activity(context) ?: context)}"
    private fun activity(context: Context): Activity? {
        var current = context
        while (current is ContextWrapper) {
            if (current is Activity) return current
            val base = current.baseContext
            if (base === current) break
            current = base
        }
        return current as? Activity
    }

    private fun permitted(context: Context): Boolean {
        install(context)
        val host = activity(context)
        return enabled(context) && host != null && !host.isFinishing && !host.isDestroyed &&
            (host as? LifecycleOwner)?.lifecycle?.currentState?.isAtLeast(Lifecycle.State.RESUMED) == true
    }

    @Synchronized private fun install(context: Context) {
        if (application != null) return
        val app = context.applicationContext as? Application ?: return
        application = app
        app.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityPaused(activity: Activity) {
                synchronized(Haptics) {
                    if (activeHost?.get() === activity) ownership.owner?.let { releaseOwner(activity, it) }
                }
            }
            override fun onActivityDestroyed(activity: Activity) = onActivityPaused(activity)
            override fun onActivityCreated(activity: Activity, state: Bundle?) = Unit
            override fun onActivityStarted(activity: Activity) = Unit
            override fun onActivityResumed(activity: Activity) = Unit
            override fun onActivityStopped(activity: Activity) = Unit
            override fun onActivitySaveInstanceState(activity: Activity, state: Bundle) = Unit
        })
        preferenceListener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
            if (key == KEY_VIBRATE && !enabled(app)) synchronized(Haptics) {
                ownership.owner?.let { releaseOwner(app, it) }
            }
        }.also { preferences(app).registerOnSharedPreferenceChangeListener(it) }
    }

    private class AndroidNativeFeedback(context: Context) : NativeChatHapticFeedback {
        private val app = context.applicationContext
        private val host = WeakReference(activity(context))
        private val owner = newOwnerToken("native")
        private val policy = NativeChatHapticPolicy(stepThrottle)
        private var closed = false
        private val preferenceListener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
            if (key == KEY_VIBRATE) synchronized(this) { apply(policy.setEnabled(enabled(app))) }
        }
        init { install(context); preferences(app).registerOnSharedPreferenceChangeListener(preferenceListener) }
        @Synchronized override fun setForeground(foreground: Boolean) {
            if (closed) return
            apply(policy.setEnabled(enabled(app)))
            apply(policy.setForeground(foreground))
            if (!foreground) releaseOwner(app, owner)
        }
        @Synchronized override fun beginStream() { if (!closed) apply(policy.beginStream()) }
        @Synchronized override fun onEvent(type: String, eventId: String?, partType: String?, partStatus: String?) {
            if (closed) return
            apply(policy.setEnabled(enabled(app)))
            if (host.get()?.let { permitted(it) } != true) apply(policy.setForeground(false))
            apply(policy.event(type, eventId, partType, partStatus, SystemClock.elapsedRealtime()))
        }
        @Synchronized override fun endStream() { if (!closed) apply(policy.endStream()) }
        @Synchronized override fun close() {
            if (closed) return
            apply(policy.endStream())
            releaseOwner(app, owner)
            preferences(app).unregisterOnSharedPreferenceChangeListener(preferenceListener)
            closed = true
        }
        private fun apply(actions: List<NativeHapticAction>) {
            val context = host.get() ?: app
            for (action in actions) when (action) {
                NativeHapticAction.START_REPLY -> startReplyVibration(context, owner)
                NativeHapticAction.STOP_REPLY -> stopReplyVibration(context, owner)
                NativeHapticAction.STEP -> if (permitted(context)) oneShot(context, 0, owner)
                NativeHapticAction.CELEBRATE -> celebration(context, owner)
            }
        }
    }

    private fun vibrator(context: Context): Vibrator? = if (Build.VERSION.SDK_INT >= 31) {
        (context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)?.defaultVibrator
    } else {
        @Suppress("DEPRECATION")
        context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
    }
}
