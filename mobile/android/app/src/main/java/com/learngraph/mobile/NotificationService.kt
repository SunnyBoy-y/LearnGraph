package com.learngraph.mobile

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.VibrationEffect
import android.os.Vibrator
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONArray
import org.json.JSONObject
import org.json.JSONTokener
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * 前台服务：从网页版 WebView 读取登录态（localStorage），轮询 /api/v1/sessions，
 * 检测到会话有新动态（updated_at 变化）且 App 不在前台时，推送系统通知 + 震动。
 *
 * 依赖 MainActivity.webViewRef（应用存活期间有效）；无登录态时自动静默停止。
 * 说明：Android 对 dataSync 前台服务有时间上限（约 6h/天），长时间后台可能被系统
 * 暂停；重新打开 App 即恢复。若要 7×24 可靠推送，后续可接 FCM 通道。
 */
class NotificationService : Service() {

    companion object {
        private const val CH_SERVICE = "lg_service"
        private const val CH_REPLIES = "lg_replies"
        private const val NOTIF_SERVICE = 9001
        private const val NOTIF_REPLY = 9002
        private const val PREFS_LAST_SEEN = "last_seen_sessions"
        private const val PREFS_LAST_NOTIFIED = "last_notified_sessions"
    }

    private val handler = Handler(Looper.getMainLooper())
    private val executor = Executors.newSingleThreadExecutor()
    private var polling = false
    private lateinit var prefs: SharedPreferences

    private val pollTask = object : Runnable {
        override fun run() {
            pollOnce()
            handler.postDelayed(this, prefs.getInt(MainActivity.KEY_INTERVAL, 30) * 1000L)
        }
    }

    override fun onCreate() {
        super.onCreate()
        prefs = getSharedPreferences(MainActivity.PREFS, Context.MODE_PRIVATE)
        createChannels()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        startInForeground()
        if (!polling) {
            polling = true
            handler.post(pollTask)
        }
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        polling = false
        handler.removeCallbacksAndMessages(null)
        executor.shutdownNow()
        super.onDestroy()
    }

    private fun startInForeground() {
        val n = NotificationCompat.Builder(this, CH_SERVICE)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle("LearnGraph")
            .setContentText("正在接收消息提醒")
            .setOngoing(true)
            .build()
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(NOTIF_SERVICE, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
        } else {
            startForeground(NOTIF_SERVICE, n)
        }
    }

    private fun createChannels() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.createNotificationChannel(
            NotificationChannel(CH_SERVICE, "消息提醒服务", NotificationManager.IMPORTANCE_LOW).apply {
                setShowBadge(false)
            },
        )
        nm.createNotificationChannel(
            NotificationChannel(CH_REPLIES, "新回复", NotificationManager.IMPORTANCE_HIGH).apply {
                enableVibration(false)
                setSound(null, null)
            },
        )
    }

    // ------------------------------------------------------------------ //
    // 轮询
    // ------------------------------------------------------------------ //

    /** 主线程从 WebView 页面上下文读取登录态，再交后台线程轮询 API */
    private fun pollOnce() {
        val wv = MainActivity.webViewRef ?: run {
            stopSelf()
            return
        }
        wv.post {
            val url = wv.url
            if (url.isNullOrBlank() || url.startsWith("https://localhost")) {
                return@post // 本机助手页（连接配置中），无网页版登录态
            }
            // WebView 已销毁时 evaluateJavascript 会抛 IllegalStateException，捕获即可
            try {
            wv.evaluateJavascript(
                "(function(){try{return JSON.stringify({t:localStorage.getItem('learngraph.access_token')||'',w:localStorage.getItem('learngraph.workspace_id')||'',u:location.href})}catch(e){return ''}})()",
            ) { result ->
                val raw = result?.trim()
                if (raw.isNullOrEmpty() || raw == "\"\"") return@evaluateJavascript
                try {
                    // evaluateJavascript 回调值本身是 JSON 字符串字面量，先解出内层字符串
                    val payload = JSONTokener(raw).nextValue() as? String ?: return@evaluateJavascript
                    val obj = JSONObject(payload)
                    val token = obj.optString("t")
                    val workspace = obj.optString("w")
                    val pageUrl = obj.optString("u")
                    if (token.isNotEmpty() && workspace.isNotEmpty() && pageUrl.startsWith("http")) {
                        val uri = Uri.parse(pageUrl)
                        val base = "${uri.scheme}://${uri.host}" + if (uri.port > 0) ":${uri.port}" else ""
                        executor.execute { httpPoll(base, token, workspace) }
                    }
                } catch (_: Exception) {
                    // 解析失败静默跳过
                }
            }
            } catch (_: Throwable) {
                // WebView 已销毁等场景：静默，不中断轮询
            }
        }
    }

    private fun httpPoll(base: String, token: String, workspace: String) {
        try {
            val conn = URL("$base/api/v1/sessions").openConnection() as HttpURLConnection
            conn.requestMethod = "GET"
            conn.setRequestProperty("Authorization", "Bearer $token")
            conn.setRequestProperty("X-Workspace-ID", workspace)
            conn.setRequestProperty("X-Device-ID", "learngraph-android-notify")
            conn.connectTimeout = 10_000
            conn.readTimeout = 10_000
            val code = conn.responseCode
            if (code == 200) {
                val body = conn.inputStream.bufferedReader().readText()
                processSessions(JSONArray(body))
            }
            conn.disconnect()
        } catch (_: Exception) {
            // 网络/超时静默
        }
    }

    private fun processSessions(sessions: JSONArray) {
        val lastSeen = parseMap(prefs.getString(PREFS_LAST_SEEN, "{}") ?: "{}")
        val lastNotified = parseMap(prefs.getString(PREFS_LAST_NOTIFIED, "{}") ?: "{}")
        val nextSeen = HashMap(lastSeen)
        val nextNotified = HashMap(lastNotified)
        var shouldNotify = false

        for (i in 0 until sessions.length()) {
            val s = sessions.optJSONObject(i) ?: continue
            val id = s.optString("id")
            val updated = s.optString("updated_at")
            if (id.isEmpty() || updated.isEmpty()) continue
            val seen = lastSeen[id]
            if (seen == null) {
                // 首次见到：建立基线，不通知
                nextSeen[id] = updated
                continue
            }
            if (seen == updated) continue
            nextSeen[id] = updated
            // 前台时只更新基线（用户正看着网页版）；后台且未通知过该版本才提醒
            if (!MainActivity.appInForeground && lastNotified[id] != updated) {
                nextNotified[id] = updated
                shouldNotify = true
                val title = s.optString("title").ifEmpty { "学习会话" }
                notifyReply(title)
            }
        }

        if (shouldNotify) maybeVibrate()
        if (nextSeen != lastSeen) {
            prefs.edit().putString(PREFS_LAST_SEEN, JSONObject(nextSeen).toString()).apply()
        }
        if (nextNotified != lastNotified) {
            prefs.edit().putString(PREFS_LAST_NOTIFIED, JSONObject(nextNotified).toString()).apply()
        }
    }

    // ------------------------------------------------------------------ //
    // 通知 + 震动
    // ------------------------------------------------------------------ //

    private fun notifyReply(title: String) {
        if (Build.VERSION.SDK_INT >= 33 &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val intent = Intent(this, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
        }
        val pi = PendingIntent.getActivity(
            this,
            0,
            intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val n = NotificationCompat.Builder(this, CH_REPLIES)
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentTitle(title)
            .setContentText("有新回复")
            .setAutoCancel(true)
            .setContentIntent(pi)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .build()
        (getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager)
            .notify(NOTIF_REPLY, n)
    }

    private fun maybeVibrate() {
        if (!prefs.getBoolean(MainActivity.KEY_VIBRATE, true)) return
        val vibrator: Vibrator = if (Build.VERSION.SDK_INT >= 31) {
            getSystemService(Vibrator::class.java)
        } else {
            @Suppress("DEPRECATION")
            getSystemService(Context.VIBRATOR_SERVICE) as Vibrator
        }
        if (Build.VERSION.SDK_INT >= 26) {
            vibrator.vibrate(VibrationEffect.createWaveform(longArrayOf(0, 200, 120, 200), -1))
        } else {
            @Suppress("DEPRECATION")
            vibrator.vibrate(500)
        }
    }

    private fun parseMap(s: String): HashMap<String, String> {
        val out = HashMap<String, String>()
        try {
            val obj = JSONObject(s)
            val keys = obj.keys()
            while (keys.hasNext()) {
                val k = keys.next()
                out[k] = obj.optString(k)
            }
        } catch (_: Exception) {
            // 损坏数据按空处理
        }
        return out
    }
}
