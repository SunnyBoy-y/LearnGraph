package com.learngraph.mobile.notify

import android.Manifest
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.os.Build
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.learngraph.mobile.data.AuthStore
import com.learngraph.mobile.util.Haptics
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import okhttp3.OkHttpClient
import okhttp3.Request
import java.util.concurrent.TimeUnit

/**
 * 回复通知器（非常驻，v0.9.0）：
 *  - AlarmManager 周期轮询 /api/v1/sessions（默认 60s），无常驻服务、无常驻通知
 *  - 基线对比：seen（是否已见）/ notified（是否已提醒），避免重复打扰
 *  - 仅当 App 不在前台且有会话 updated_at 变化时才弹通知（InboxStyle 聚合多条）
 *  - 登录态直接从 AuthStore(DataStore) 读取，无需 WebView
 */
object ReplyNotifier {

    private const val PREFS = "lg_reply_notify"
    private const val CH_REPLIES = "lg_replies"
    private const val CH_TASKS = "lg_tasks"
    private const val NOTIF_REPLY = 9002
    private const val NOTIF_TASK = 9003
    private const val ALARM_REQUEST = 4242

    /** 轮询间隔（毫秒）。60s 平衡及时性与电量；Doze 下 AlarmManager 会放宽。 */
    const val POLL_INTERVAL_MS = 60_000L

    @Volatile
    var appInForeground = true
        private set

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private val http = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(10, TimeUnit.SECONDS)
        .build()
    private val json = Json { ignoreUnknownKeys = true }

    @Serializable
    private data class SessionUpdate(val id: String, val title: String? = null, val updated_at: String? = null)

    fun setForeground(fg: Boolean, context: Context? = null) {
        appInForeground = fg
        if (!fg && context != null) {
            // 后台化立即轮询一次：让刚投递的后台任务完成后尽快通知
            scope.launch { poll(context.applicationContext, null) }
        }
    }

    /**
     * 网页版投递后台任务成功 → 标记该会话「生成完成后推送通知」。
     *
     * 立即抓一次该会话当前 updated_at 作为基线（投递时刻），之后轮询里
     * updated_at 相对基线变化即视为任务完成。这样即使任务在第一次轮询
     * 之前就跑完（首轮轮询见到的已是最终 updated_at），也不会漏通知。
     * 标记持久化在 prefs，App 进程被杀后由 AlarmManager 拉起轮询仍有效。
     */
    fun markTaskSession(context: Context, sessionId: String) {
        if (sessionId.isBlank()) return
        val ctx = context.applicationContext
        val prefs = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        scope.launch {
            try {
                val auth = withContext(Dispatchers.IO) { readAuth(ctx) }
                val baseline = if (auth.token.isNullOrBlank() || auth.baseUrl.isBlank()) {
                    ""
                } else {
                    withContext(Dispatchers.IO) {
                        fetchSessions(auth.baseUrl, auth.token, auth.workspaceId, auth.deviceId)
                            ?.firstOrNull { it.id == sessionId }
                            ?.updated_at
                            .orEmpty()
                    }
                }
                prefs.edit()
                    .putString("task-pending:$sessionId", "1")
                    .putString("task-baseline:$sessionId", baseline)
                    .apply()
            } catch (_: Exception) {
                // 网络异常时仅保留标记，走 seen 基线兜底
                prefs.edit().putString("task-pending:$sessionId", "1").apply()
            }
        }
    }

    // ------------------------------------------------------------------ //
    // 调度
    // ------------------------------------------------------------------ //

    fun start(context: Context) {
        val ctx = context.applicationContext
        ensureChannel(ctx)
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val pi = alarmIntent(ctx)
        am.setInexactRepeating(
            AlarmManager.ELAPSED_REALTIME,
            SystemClock.elapsedRealtime() + 10_000,
            POLL_INTERVAL_MS,
            pi,
        )
    }

    fun stop(context: Context) {
        val ctx = context.applicationContext
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        am.cancel(alarmIntent(ctx))
    }

    private fun alarmIntent(ctx: Context): PendingIntent =
        PendingIntent.getBroadcast(
            ctx,
            ALARM_REQUEST,
            Intent(ctx, ReplyAlarmReceiver::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    // ------------------------------------------------------------------ //
    // 轮询
    // ------------------------------------------------------------------ //

    /** 由 BroadcastReceiver 调用；pendingResult 用于 goAsync 收尾 */
    fun poll(context: Context, pendingResult: android.content.BroadcastReceiver.PendingResult?) {
        scope.launch {
            try {
                val auth = withContext(Dispatchers.IO) { readAuth(context) }
                if (auth.token.isNullOrBlank() || auth.baseUrl.isBlank()) return@launch

                // 会话新回复（现有逻辑）
                val sessions = withContext(Dispatchers.IO) {
                    fetchSessions(auth.baseUrl, auth.token, auth.workspaceId, auth.deviceId)
                }
                if (sessions != null) process(context, sessions)

                // B1：后台 agent 任务完成 → 摘要通知
                val agentTasks = withContext(Dispatchers.IO) {
                    fetchAgentTasks(auth.baseUrl, auth.token, auth.workspaceId, auth.deviceId)
                }
                if (agentTasks != null) processAgentTasks(context, agentTasks)

                // B2：待复习知识点 → 周期提醒
                val reviewCount = withContext(Dispatchers.IO) {
                    fetchDueReviewCount(auth.baseUrl, auth.token, auth.workspaceId, auth.deviceId)
                }
                if (reviewCount != null) processReviewDue(context, reviewCount)
            } catch (_: Exception) {
                // 任何异常静默，不打扰用户
            } finally {
                pendingResult?.finish()
            }
        }
    }

    private suspend fun readAuth(context: Context): AuthStore.AuthState =
        AuthStore(context).state.first()

    private fun fetchSessions(baseUrl: String, token: String, workspaceId: String?, deviceId: String): List<SessionUpdate>? {
        val url = "${baseUrl.trimEnd('/')}/api/v1/sessions"
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer $token")
            .apply {
                workspaceId?.let { header("X-Workspace-ID", it) }
                if (deviceId.isNotBlank()) header("X-Device-ID", deviceId)
            }
            .build()
        return try {
            http.newCall(request).execute().use { resp ->
                if (resp.code != 200) return null
                val body = resp.body?.string() ?: return null
                parseSessions(body)
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun parseSessions(body: String): List<SessionUpdate>? = try {
        val arr = json.parseToJsonElement(body) as? JsonArray ?: return null
        arr.mapNotNull { el ->
            val obj = el as? JsonObject ?: return@mapNotNull null
            SessionUpdate(
                id = obj["id"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null,
                title = obj["title"]?.jsonPrimitive?.contentOrNull,
                updated_at = obj["updated_at"]?.jsonPrimitive?.contentOrNull,
            )
        }
    } catch (_: Exception) {
        null
    }

    // ------------------------------------------------------------------ //
    // B1：后台 agent 任务完成通知
    // ------------------------------------------------------------------ //

    private data class AgentTaskInfo(
        val id: String,
        val title: String?,
        val status: String?,
        val finished: String?,
        val summary: String?,
    )

    private fun fetchAgentTasks(
        baseUrl: String, token: String, workspaceId: String?, deviceId: String,
    ): List<AgentTaskInfo>? {
        val url = baseUrl.trimEnd('/') + "/api/v1/sandbox/agent-tasks?limit=20"
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer " + token)
            .apply {
                workspaceId?.let { header("X-Workspace-ID", it) }
                if (deviceId.isNotBlank()) header("X-Device-ID", deviceId)
            }
            .build()
        return try {
            http.newCall(request).execute().use { resp ->
                if (resp.code != 200) return null
                parseAgentTasks(resp.body?.string() ?: return null)
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun parseAgentTasks(body: String): List<AgentTaskInfo>? = try {
        val root = json.parseToJsonElement(body) as? JsonObject ?: return null
        val tasks = root["tasks"] as? JsonArray ?: return null
        tasks.mapNotNull { el ->
            val obj = el as? JsonObject ?: return@mapNotNull null
            AgentTaskInfo(
                id = obj["id"]?.jsonPrimitive?.contentOrNull ?: return@mapNotNull null,
                title = obj["title"]?.jsonPrimitive?.contentOrNull,
                status = obj["status"]?.jsonPrimitive?.contentOrNull,
                finished = obj["finished_at"]?.jsonPrimitive?.contentOrNull,
                summary = obj["deliverables"]?.let { dl ->
                    val dlObj = dl as? JsonObject ?: return@let null
                    dlObj["summary"]?.jsonPrimitive?.contentOrNull
                },
            )
        }
    } catch (_: Exception) {
        null
    }

    private fun processAgentTasks(context: Context, tasks: List<AgentTaskInfo>) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val terminalStatuses = setOf("SUCCEEDED", "PARTIAL", "FAILED", "TIMED_OUT")
        val done = mutableListOf<AgentTaskInfo>()

        for (t in tasks) {
            val status = t.status ?: continue
            if (!terminalStatuses.contains(status)) continue
            val finished = t.finished ?: continue
            val key = "agent-seen:" + t.id
            if (prefs.getString(key, null) == finished) continue
            // 首次见到终态：记录，App 前台时不打扰
            prefs.edit().putString(key, finished).apply()
            if (!appInForeground) done.add(t)
        }

        if (done.isNotEmpty()) showAgentNotification(context, done)
    }

    private fun showAgentNotification(context: Context, tasks: List<AgentTaskInfo>) {
        if (!canNotify(context)) return
        val launchIntent = Intent(context, com.learngraph.mobile.MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val pi = PendingIntent.getActivity(
            context,
            9003,
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val first = tasks.first()
        val title = first.title ?: "后台任务"
        val summary = first.summary?.trim()?.take(80)
        val body = summary ?: taskStatusText(first.status)
        val builder = NotificationCompat.Builder(context, CH_TASKS)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setContentTitle("任务完成：" + title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
        // B4：🔊 朗读结果（耳机场景友好；把 body 带过去）
        if (summary != null) {
            val ttsIntent = Intent(context, TtsReadbackReceiver::class.java)
                .putExtra("tts_text", summary)
            val ttsPi = PendingIntent.getBroadcast(
                context,
                first.id.hashCode() xor 0x77,
                ttsIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            builder.addAction(
                NotificationCompat.Action.Builder(
                    android.R.drawable.ic_btn_speak_now,
                    "🔊 朗读结果",
                    ttsPi,
                ).build(),
            )
        }
        try {
            NotificationManagerCompat.from(context).notify(NOTIF_TASK, builder.build())
            // 任务完成 → 轻震庆祝（A3，受「回复震动」开关控制）
            val hapticsOn = context.getSharedPreferences("lg_mobile", Context.MODE_PRIVATE)
                .getBoolean(Haptics.KEY_VIBRATE, true)
            if (hapticsOn) Haptics.celebration(context)
        } catch (_: SecurityException) {
            // 无通知权限
        }
    }

    private fun taskStatusText(status: String?): String = when (status) {
        "SUCCEEDED" -> "已完成，可查看交付内容"
        "PARTIAL" -> "部分完成，请查看详情"
        "FAILED" -> "执行失败，请重试"
        "TIMED_OUT" -> "任务超时"
        else -> "任务已结束"
    }

    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    // ------------------------------------------------------------------ //
    // 基线对比 + 通知
    // ------------------------------------------------------------------ //

    private fun process(context: Context, sessions: List<SessionUpdate>) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val changes = mutableListOf<SessionUpdate>() // 有更新的会话

        // 待通知的后台任务会话（网页版 / 快捷回复投递 async 后标记）
        val taskSessions = prefs.all.keys
            .filter { it.startsWith("task-pending:") }
            .mapNotNull { it.removePrefix("task-pending:").takeIf { id -> id.isNotBlank() } }
            .toSet()

        for (s in sessions) {
            val updated = s.updated_at ?: continue
            val key = "seen:${s.id}"

            // 后台任务会话：用「投递时刻基线」判定完成，前台绝不吞变化
            if (s.id in taskSessions) {
                val baseline = prefs.getString("task-baseline:${s.id}", null)
                if (!baseline.isNullOrEmpty()) {
                    if (updated == baseline) continue // 尚无变化，继续等
                    if (appInForeground) continue // 任务在前台完成：不更新基线，切后台后首轮通知
                    // 后台且已变化 → 任务完成，通知并清标记（写 seen/notified 防重复）
                    prefs.edit()
                        .putString(key, updated)
                        .putString("notified:${s.id}", updated)
                        .remove("task-pending:${s.id}")
                        .remove("task-baseline:${s.id}")
                        .apply()
                    changes.add(s)
                    continue
                }
                // 基线缺失（标记瞬间网络失败）：回退 seen 逻辑但前台不吞变化
                val seen = prefs.getString(key, null)
                if (seen == null) {
                    prefs.edit().putString(key, updated).apply()
                    continue
                }
                if (seen == updated) continue
                if (appInForeground) continue
                prefs.edit()
                    .putString(key, updated)
                    .putString("notified:${s.id}", updated)
                    .remove("task-pending:${s.id}")
                    .apply()
                changes.add(s)
                continue
            }

            val seen = prefs.getString(key, null)
            if (seen == null) {
                // 首次见到：建立基线，不通知
                prefs.edit().putString(key, updated).apply()
                continue
            }
            if (seen == updated) continue
            // 有新动态：更新基线
            prefs.edit().putString(key, updated).apply()
            if (!appInForeground) {
                val notified = prefs.getString("notified:${s.id}", null)
                if (notified != updated) {
                    prefs.edit().putString("notified:${s.id}", updated).apply()
                    changes.add(s)
                }
            }
        }

        if (changes.isNotEmpty() && !appInForeground) {
            showNotification(context, changes)
        }
    }

    private fun showNotification(context: Context, changes: List<SessionUpdate>) {
        if (!canNotify(context)) return

        // 点击通知 → 打开 App（网页版）
        val launchIntent = Intent(context, com.learngraph.mobile.MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val pi = PendingIntent.getActivity(
            context,
            0,
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

        val builder = NotificationCompat.Builder(context, CH_REPLIES)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setContentTitle(if (changes.size == 1) "有新回复" else "${changes.size} 个会话有新回复")
            .setContentText(changes.first().title ?: "会话")
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        if (changes.size > 1) {
            val inbox = NotificationCompat.InboxStyle()
            changes.forEach { s -> inbox.addLine(s.title ?: "会话") }
            inbox.setBigContentTitle("${changes.size} 个会话有新回复")
            builder.setStyle(inbox)
        }

        // 快捷操作：打开会话 / 快速回复
        val single = changes.firstOrNull()
        if (single != null) {
            val openIntent = Intent(context, com.learngraph.mobile.MainActivity::class.java)
                .setAction("com.learngraph.mobile.OPEN_SESSION")
                .putExtra("session_id", single.id)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
            val openPi = PendingIntent.getActivity(
                context,
                single.id.hashCode(),
                openIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            builder.addAction(
                NotificationCompat.Action.Builder(
                    android.R.drawable.ic_menu_agenda,
                    "打开会话",
                    openPi,
                ).build(),
            )

            // 快速回复（RemoteInput → QuickReplyReceiver → 异步发消息）
            val replyKey = "quick_reply"
            val remoteInput = androidx.core.app.RemoteInput.Builder(replyKey)
                .setLabel("回复")
                .build()
            val replyIntent = Intent(context, com.learngraph.mobile.notify.QuickReplyReceiver::class.java)
                .putExtra("session_id", single.id)
            val replyPi = PendingIntent.getBroadcast(
                context,
                single.id.hashCode() xor 0x5F,
                replyIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            builder.addAction(
                NotificationCompat.Action.Builder(
                    android.R.drawable.ic_menu_send,
                    "回复",
                    replyPi,
                )
                    .addRemoteInput(remoteInput)
                    .build(),
            )
        }

        try {
            NotificationManagerCompat.from(context).notify(NOTIF_REPLY, builder.build())
        } catch (_: SecurityException) {
            // 无通知权限
        }
    }

    // ------------------------------------------------------------------ //
    // B2：待复习知识点提醒
    // ------------------------------------------------------------------ //

    /** 统计已到复习时间的知识点数量；网络/认证失败返回 null */
    private fun fetchDueReviewCount(
        baseUrl: String, token: String, workspaceId: String?, deviceId: String,
    ): Int? {
        val url = baseUrl.trimEnd('/') + "/api/v1/mastery/schedules"
        val request = Request.Builder().url(url)
            .header("Authorization", "Bearer " + token)
            .apply {
                workspaceId?.let { header("X-Workspace-ID", it) }
                if (deviceId.isNotBlank()) header("X-Device-ID", deviceId)
            }
            .build()
        return try {
            http.newCall(request).execute().use { resp ->
                if (resp.code != 200) return null
                val body = resp.body?.string() ?: return null
                val arr = json.parseToJsonElement(body) as? JsonArray ?: return null
                val now = java.time.Instant.now().toString()
                arr.count { el ->
                    val obj = el as? JsonObject ?: return@count false
                    val next = obj["next_review_at"]?.jsonPrimitive?.contentOrNull ?: return@count false
                    // ISO 字符串可直接按字典序比较（同格式）
                    next <= now
                }
            }
        } catch (_: Exception) {
            null
        }
    }

    private fun processReviewDue(context: Context, dueCount: Int) {
        if (dueCount <= 0) return
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        // 每 6 小时最多提醒一次，避免打扰
        val lastReviewAt = prefs.getLong("review-last-notify", 0L)
        val now = System.currentTimeMillis()
        if (now - lastReviewAt < 6 * 60 * 60 * 1000L) return
        if (appInForeground) return
        if (!canNotify(context)) return

        val launchIntent = Intent(context, com.learngraph.mobile.MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        val pi = PendingIntent.getActivity(
            context,
            9004,
            launchIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val builder = NotificationCompat.Builder(context, CH_REPLIES)
            .setSmallIcon(android.R.drawable.stat_notify_chat)
            .setContentTitle("该复习了")
            .setContentText("有 $dueCount 个知识点已到复习时间")
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
        try {
            NotificationManagerCompat.from(context).notify(9004, builder.build())
            prefs.edit().putLong("review-last-notify", now).apply()
        } catch (_: SecurityException) {
            // 无通知权限
        }
    }

    // ------------------------------------------------------------------ //
    // 通道 / 权限
    // ------------------------------------------------------------------ //

    private fun ensureChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(CH_REPLIES) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CH_REPLIES, "新回复", NotificationManager.IMPORTANCE_HIGH).apply {
                    enableVibration(true)
                },
            )
        }
        if (nm.getNotificationChannel(CH_TASKS) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CH_TASKS, "后台任务", NotificationManager.IMPORTANCE_DEFAULT).apply {
                    enableVibration(true)
                },
            )
        }
    }

    private fun canNotify(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
}
