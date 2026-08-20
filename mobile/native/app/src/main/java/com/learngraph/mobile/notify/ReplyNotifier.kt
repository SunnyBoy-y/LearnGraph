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
    private const val NOTIF_REPLY = 9002
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

    fun setForeground(fg: Boolean) {
        appInForeground = fg
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

                val sessions = withContext(Dispatchers.IO) {
                    fetchSessions(auth.baseUrl, auth.token, auth.workspaceId, auth.deviceId)
                }
                if (sessions == null) return@launch // 网络/认证失败静默

                process(context, sessions)
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
    // 基线对比 + 通知
    // ------------------------------------------------------------------ //

    private fun process(context: Context, sessions: List<SessionUpdate>) {
        val prefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val changes = mutableListOf<Pair<String, String>>() // title, updated_at

        for (s in sessions) {
            val updated = s.updated_at ?: continue
            val key = "seen:${s.id}"
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
                    changes.add((s.title ?: "会话") to updated)
                }
            }
        }

        if (changes.isNotEmpty() && !appInForeground) {
            showNotification(context, changes)
        }
    }

    private fun showNotification(context: Context, changes: List<Pair<String, String>>) {
        if (!canNotify(context)) return
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
            .setContentText(changes.first().first)
            .setContentIntent(pi)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_HIGH)

        if (changes.size > 1) {
            val inbox = NotificationCompat.InboxStyle()
            changes.forEach { (title, _) -> inbox.addLine(title) }
            inbox.setBigContentTitle("${changes.size} 个会话有新回复")
            builder.setStyle(inbox)
        }

        try {
            NotificationManagerCompat.from(context).notify(NOTIF_REPLY, builder.build())
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
    }

    private fun canNotify(context: Context): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED
}
