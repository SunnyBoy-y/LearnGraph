package com.learngraph.mobile.notify

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import com.learngraph.mobile.util.TtsSynth

/**
 * 通知「🔊 朗读结果」action：TTS 朗读任务摘要（B4，耳机场景友好）。
 * 从通知文本读取，脱离网页版独立运行，无需 token。
 */
class TtsReadbackReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val text = intent.getStringExtra("tts_text")?.take(200) ?: return
        TtsSynth.speak(context, text)
    }
}