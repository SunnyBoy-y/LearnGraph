package com.learngraph.mobile.util

import android.content.Context
import android.speech.tts.TextToSpeech
import java.util.Locale

/**
 * 文本朗读（B4 耳机自动朗读 / 网页版「朗读回复」）。
 *
 * 简单封装系统 TextToSpeech；初始化一次后复用。
 * 仅应朗读「最终回复正文」，避免朗读思维链/工具输出。
 */
object TtsSynth {

    @Volatile
    private var tts: TextToSpeech? = null
    @Volatile
    private var ready = false

    fun speak(context: Context, text: String) {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) return
        ensureInit(context)
        val engine = tts ?: return
        if (!ready) return
        // 打断上一段，避免重叠
        runCatching { engine.stop() }
        engine.speak(trimmed, TextToSpeech.QUEUE_FLUSH, null, "lg-speak")
    }

    fun stop() {
        runCatching { tts?.stop() }
    }

    private fun ensureInit(context: Context) {
        if (tts != null) return
        val appCtx = context.applicationContext
        tts = TextToSpeech(appCtx) { status ->
            ready = status == TextToSpeech.SUCCESS
            if (ready) {
                runCatching { tts?.setLanguage(Locale.SIMPLIFIED_CHINESE) ?: Locale.getDefault() }
            }
        }
    }
}