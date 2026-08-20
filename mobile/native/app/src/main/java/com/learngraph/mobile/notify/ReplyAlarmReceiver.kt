package com.learngraph.mobile.notify

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * AlarmManager 周期触发：后台轮询会话更新（goAsync 允许异步网络完成后收尾）。
 */
class ReplyAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val pendingResult = goAsync()
        ReplyNotifier.poll(context.applicationContext, pendingResult)
    }
}
