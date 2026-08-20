package com.learngraph.mobile.notify

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/**
 * 设备重启后恢复回复通知轮询（AlarmManager 任务在重启后丢失）。
 */
class ReplyBootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == Intent.ACTION_BOOT_COMPLETED) {
            ReplyNotifier.start(context.applicationContext)
        }
    }
}
