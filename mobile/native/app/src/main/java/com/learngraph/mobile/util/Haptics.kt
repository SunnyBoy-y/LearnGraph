package com.learngraph.mobile.util

import android.content.Context
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.os.VibrationEffect
import android.os.Vibrator
import android.os.VibratorManager

/**
 * 触觉 / 提示音反馈（A 类体验增强）。
 *
 * 网页版渲染最终回复 / agent 步骤 / 达成时刻时，通过
 * LearnGraphNative.haptic() / sound() 调用本工具，在原生侧触发一次
 * 轻马达或提示音。开关与强度存 SharedPreferences，由网页版或设置读取。
 *
 * 强度档位：
 *  - haptic(intensity): 0=轻(18ms) 1=中(36ms) 2=重(短-长 pattern)
 */
object Haptics {

    const val PREFS = "lg_mobile"
    const val KEY_VIBRATE = "haptics_enabled"   // 总开关，默认 true
    const val KEY_STRENGTH = "haptics_strength"  // 0/1/2，默认 1

    @JvmStatic
    fun haptic(context: Context, intensity: Int = 1) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_VIBRATE, true)) return
        val strength = prefs.getInt(KEY_STRENGTH, 1) // 前端可覆盖
        vibrate(context, if (intensity >= 0) intensity else strength)
    }

    /** 网页版最终回复开始时调用（轻震） */
    @JvmStatic
    fun replyHaptic(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_VIBRATE, true)) return
        vibrate(context, strength = prefs.getInt(KEY_STRENGTH, 1))
    }

    // ------------------------------------------------------------------ //
    // 最终回答渲染期「答答答」持续震动（A1 增强）
    //  - 渲染期间按固定小间隔连续脉冲，力度恒定（不渐变），渲染结束停止。
    //  - 节奏：每 14ms 震 + 150ms 停，循环 ≈ 清脆点按手感。
    // ------------------------------------------------------------------ //

    private const val REPLY_ON_MS = 14L
    private const val REPLY_OFF_MS = 150L
    private const val REPLY_AMPLITUDE = 200 // 恒定振幅(0-255)，力度一致

    /** 开始最终回答渲染期的连续"答答答"震动 */
    @JvmStatic
    fun startReplyVibration(context: Context) {
        // 延续上一轮（连续/切换提问）先停再起，保证从干净状态开始
        stopReplyVibration(context)
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_VIBRATE, true)) return
        val v = vibrator(context) ?: return
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                // timings=[0,on,off] 以 repeat=0 无限循环；amplitudes 固定 → 力度不渐变
                v.vibrate(
                    VibrationEffect.createWaveform(
                        longArrayOf(0, REPLY_ON_MS, REPLY_OFF_MS),
                        intArrayOf(0, REPLY_AMPLITUDE, 0),
                        0,
                    ),
                )
            } else {
                @Suppress("DEPRECATION")
                v.vibrate(longArrayOf(0, REPLY_ON_MS, REPLY_OFF_MS), 0)
            }
        } catch (_: Exception) {
            // ignore
        }
    }

    /** 结束最终回答渲染期的震动（渲染结束调用） */
    @JvmStatic
    fun stopReplyVibration(context: Context) {
        try {
            vibrator(context)?.cancel()
        } catch (_: Exception) {
            // ignore
        }
    }

    /** agent 工具完成时调用（更轻） */
    @JvmStatic
    fun stepHaptic(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_VIBRATE, true)) return
        vibrate(context, strength = 0)
    }

    /** 达成庆祝（目标/掌握度） — 短-长-短 pattern */
    @JvmStatic
    fun celebration(context: Context) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        if (!prefs.getBoolean(KEY_VIBRATE, true)) return
        val v = vibrator(context) ?: return
        try {
            if (Build.VERSION.SDK_INT >= 26) {
                v.vibrate(
                    VibrationEffect.createWaveform(
                        longArrayOf(0, 40, 60, 40, 60, 80),
                        -1,
                    ),
                )
            } else {
                @Suppress("DEPRECATION")
                v.vibrate(longArrayOf(0, 40, 60, 40, 60, 80), -1)
            }
        } catch (_: Exception) {
            // ignore
        }
    }

    /** 提示音（可选，默认关）：轻“叮” */
    @JvmStatic
    fun chime() {
        try {
            val tone = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 60)
            tone.startTone(ToneGenerator.TONE_PROP_BEEP, 120)
            tone.release()
        } catch (_: Exception) {
            // 无声卡/被静音时静默
        }
    }

    private fun vibrate(context: Context, strength: Int): Boolean {
        val v = vibrator(context) ?: return false
        return try {
            if (Build.VERSION.SDK_INT >= 26) {
                v.vibrate(
                    VibrationEffect.createOneShot(when (strength) {
                        2 -> 40L
                        0 -> 12L
                        else -> 24L
                    }, VibrationEffect.DEFAULT_AMPLITUDE),
                )
            } else {
                @Suppress("DEPRECATION")
                v.vibrate(when (strength) { 2 -> 40L; 0 -> 12L; else -> 24L })
            }
            true
        } catch (_: Exception) {
            false
        }
    }

    private fun vibrator(context: Context): Vibrator? {
        return if (Build.VERSION.SDK_INT >= 31) {
            (context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager)
                ?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }
    }
}