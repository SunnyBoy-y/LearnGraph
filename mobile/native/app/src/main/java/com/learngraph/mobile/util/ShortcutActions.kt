package com.learngraph.mobile.util

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences

/**
 * 长按图标快捷方式（App Shortcuts）。
 *
 * MainActivity.onCreate 注册 4 个动态快捷方式：
 *  - new-chat：新对话
 *  - note：记笔记（快捷动作，网页版引导到记忆页）
 *  - tasks：投递后台任务（网页版引导到任务页）
 *  - connect：切换服务器（回连接页）
 *
 * 点击快捷方式 → MainActivity 收到 intent（extra: lg.shortcut）→ 把动作写入
 * SharedPreferences（待消费）→ 正常进入网页版 → 网页版通过
 * LearnGraphNative.consumeShortcutAction() 读取并执行导航。
 * 消费后清除，避免重复触发。
 */
object ShortcutActions {

    private const val PREFS = "lg_shortcuts"
    private const val KEY_ACTION = "pending_action"

    const val ACTION_NEW_CHAT = "new-chat"
    const val ACTION_NOTE = "note"
    const val ACTION_TASKS = "tasks"
    const val ACTION_CONNECT = "connect"

    /** 注册动态快捷方式（MainActivity.onCreate 调用，幂等） */
    fun register(context: Context) {
        val manager = context.getSystemService(Context.SHORTCUT_SERVICE) as? android.content.pm.ShortcutManager
            ?: return
        if (manager.isRateLimitingActive) return
        val icon = android.graphics.drawable.Icon.createWithResource(
            context,
            com.learngraph.mobile.R.mipmap.ic_launcher,
        )
        fun shortcut(id: String, label: String, description: String): android.content.pm.ShortcutInfo =
            android.content.pm.ShortcutInfo.Builder(context, id)
                .setShortLabel(label)
                .setLongLabel(description)
                .setIcon(icon)
                .setIntent(
                    Intent(context, com.learngraph.mobile.MainActivity::class.java)
                        .setAction(Intent.ACTION_VIEW)
                        .putExtra("lg.shortcut", id)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
                )
                .build()

        runCatching {
            manager.dynamicShortcuts = listOf(
                shortcut(ACTION_NEW_CHAT, "新对话", "开始新的对话"),
                shortcut(ACTION_NOTE, "记笔记", "快速记录一条记忆"),
                shortcut(ACTION_TASKS, "投递任务", "后台跑一个长任务"),
                shortcut(ACTION_CONNECT, "切换服务器", "连接其他 LearnGraph 服务器"),
            )
        }
    }

    /** 从启动 Intent 提取快捷动作并写入待消费队列 */
    fun ingestFromIntent(context: Context, intent: Intent?) {
        val action = intent?.getStringExtra("lg.shortcut") ?: return
        if (action.isBlank()) return
        if (action == ACTION_CONNECT) {
            // 切换服务器：直接回连接页，无需网页版消费
            prefs(context).edit().putString(KEY_ACTION, action).apply()
            return
        }
        prefs(context).edit().putString(KEY_ACTION, action).apply()
    }

    /** 通知「打开会话」→ 记录待打开会话（网页版消费） */
    fun ingestOpenSession(context: Context, sessionId: String) {
        if (sessionId.isBlank()) return
        prefs(context).edit().putString(KEY_ACTION, "open-session:$sessionId").apply()
    }

    /** 读取并清除待消费动作（JS bridge 调用） */
    fun consume(context: Context): String {
        val prefs = prefs(context)
        val action = prefs.getString(KEY_ACTION, null) ?: return ""
        prefs.edit().remove(KEY_ACTION).apply()
        return action
    }

    private fun prefs(context: Context): SharedPreferences =
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}
