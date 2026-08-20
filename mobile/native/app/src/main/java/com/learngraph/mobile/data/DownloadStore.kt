package com.learngraph.mobile.data

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Environment
import android.webkit.MimeTypeMap
import android.webkit.URLUtil
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.util.UUID
import java.util.concurrent.TimeUnit

enum class DownloadStatus { DOWNLOADING, COMPLETED, FAILED, CANCELLED }

data class DownloadTask(
    val id: String,
    val url: String,
    val fileName: String,
    val mimeType: String?,
    val contentDisposition: String? = null,
    val userAgent: String? = null,
    val authToken: String? = null,
    val totalBytes: Long? = null,
    val downloadedBytes: Long = 0,
    val status: DownloadStatus = DownloadStatus.DOWNLOADING,
    val localPath: String? = null,
    val error: String? = null,
    val createdAt: Long = System.currentTimeMillis(),
)

/**
 * 内置下载器（v0.8.0）：
 *  - OkHttp 下载队列，文件存应用专属 Downloads 目录（无需存储权限）
 *  - 进度经 StateFlow 实时发布；取消 / 重试 / 打开（FileProvider）
 *  - 系统通知：进行中进度条 + 完成/失败（点击打开文件）
 *  - 同服务器 API URL 自动附加 Bearer token（网页版文件接口需要认证）
 */
object DownloadStore {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val http = OkHttpClient.Builder()
        .connectTimeout(20, TimeUnit.SECONDS)
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .build()

    private val _tasks = MutableStateFlow<List<DownloadTask>>(emptyList())
    val tasks: StateFlow<List<DownloadTask>> = _tasks

    private val jobs = mutableMapOf<String, Job>()
    private var appContext: Context? = null

    private const val CH_DOWNLOADS = "lg_downloads"

    init {
        // 空：context 由 init/enqueue 注入
    }

    fun init(context: Context) {
        appContext = context.applicationContext
    }

    fun enqueue(
        context: Context,
        url: String,
        contentDisposition: String? = null,
        mimeType: String? = null,
        userAgent: String? = null,
        authToken: String? = null,
    ): String {
        val ctx = context.applicationContext
        appContext = ctx
        val fileName = URLUtil.guessFileName(url, contentDisposition, mimeType)
        val id = UUID.randomUUID().toString()
        _tasks.update { listOf(
            DownloadTask(
                id = id,
                url = url,
                fileName = fileName,
                mimeType = mimeType,
                contentDisposition = contentDisposition,
                userAgent = userAgent,
                authToken = authToken,
            ),
        ) + it }
        ensureChannel(ctx)

        val job = scope.launch {
            try {
                val dir = downloadDir(ctx)
                val target = uniqueFile(dir, fileName)
                val request = Request.Builder().url(url)
                    .header("User-Agent", userAgent ?: "LearnGraph-Android")
                    .apply {
                        if (!authToken.isNullOrBlank()) header("Authorization", "Bearer $authToken")
                    }
                    .build()
                val response = http.newCall(request).execute()
                try {
                    if (!response.isSuccessful) throw IllegalStateException("HTTP ${response.code}")
                    val body = response.body ?: throw IllegalStateException("空响应体")
                    val total = body.contentLength().takeIf { it > 0 }
                    body.byteStream().use { input ->
                        target.outputStream().use { output ->
                            val buf = ByteArray(64 * 1024)
                            var written = 0L
                            var lastNotify = 0L
                            var lastPercent = -1
                            while (true) {
                                val n = input.read(buf)
                                if (n <= 0) break
                                output.write(buf, 0, n)
                                written += n
                                // 节流：StateFlow 每 256KB 或收尾更新；通知每 ≥2% 更新
                                if (written - lastNotify >= 256 * 1024) {
                                    lastNotify = written
                                    updateTask(id) { it.copy(downloadedBytes = written, totalBytes = total) }
                                    val percent = percent(total, written)
                                    if (percent != lastPercent) {
                                        lastPercent = percent
                                        notifyProgress(ctx, task(id), percent)
                                    }
                                }
                            }
                            updateTask(id) {
                                it.copy(
                                    downloadedBytes = written,
                                    totalBytes = total,
                                    status = DownloadStatus.COMPLETED,
                                    localPath = target.absolutePath,
                                )
                            }
                            notifyDone(ctx, task(id), success = true)
                        }
                    }
                } finally {
                    response.close()
                }
            } catch (e: kotlinx.coroutines.CancellationException) {
                updateTask(id) { it.copy(status = DownloadStatus.CANCELLED) }
                notifyDone(ctx, task(id), success = false)
            } catch (e: Exception) {
                updateTask(id) { it.copy(status = DownloadStatus.FAILED, error = e.message) }
                notifyDone(ctx, task(id), success = false)
            } finally {
                jobs.remove(id)
            }
        }
        jobs[id] = job
        return id
    }

    fun cancel(id: String) {
        jobs[id]?.cancel()
    }

    fun retry(id: String) {
        val t = _tasks.value.firstOrNull { it.id == id } ?: return
        _tasks.update { list -> list.filterNot { it.id == id } }
        val ctx = appContext ?: return
        enqueue(ctx, t.url, t.contentDisposition, t.mimeType, t.userAgent, t.authToken)
    }

    fun remove(id: String) {
        jobs[id]?.cancel()
        val t = _tasks.value.firstOrNull { it.id == id }
        t?.localPath?.let { runCatching { File(it).delete() } }
        _tasks.update { list -> list.filterNot { it.id == id } }
    }

    fun removeAllCompleted() {
        _tasks.update { list ->
            list.filter { it.status == DownloadStatus.DOWNLOADING }
        }
    }

    fun open(context: Context, id: String): Boolean {
        val t = _tasks.value.firstOrNull { it.id == id } ?: return false
        val path = t.localPath ?: return false
        val file = File(path)
        if (!file.exists()) return false
        return try {
            val uri = FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
            val intent = Intent(Intent.ACTION_VIEW)
                .setDataAndType(uri, t.mimeType ?: guessMime(file))
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            context.startActivity(intent)
            true
        } catch (_: Exception) {
            false
        }
    }

    fun activeDownloadingCount(): Int =
        _tasks.value.count { it.status == DownloadStatus.DOWNLOADING }

    // ------------------------------------------------------------------ //
    // 内部
    // ------------------------------------------------------------------ //

    private fun downloadDir(ctx: Context): File =
        File(ctx.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS) ?: ctx.filesDir, "downloads").apply { mkdirs() }

    private fun uniqueFile(dir: File, fileName: String): File {
        if (!File(dir, fileName).exists()) return File(dir, fileName)
        val dot = fileName.lastIndexOf('.')
        val base = if (dot > 0) fileName.substring(0, dot) else fileName
        val ext = if (dot > 0) fileName.substring(dot) else ""
        var i = 1
        while (true) {
            val candidate = File(dir, "${base}(${i})$ext")
            if (!candidate.exists()) return candidate
            i++
        }
    }

    private fun updateTask(id: String, transform: (DownloadTask) -> DownloadTask) {
        _tasks.update { list -> list.map { if (it.id == id) transform(it) else it } }
    }

    private fun task(id: String): DownloadTask? = _tasks.value.firstOrNull { it.id == id }

    private fun percent(total: Long?, done: Long): Int {
        if (total == null || total <= 0) return -1
        return ((done * 100) / total).toInt()
    }

    private fun guessMime(file: File): String {
        val ext = file.extension.lowercase()
        return MimeTypeMap.getSingleton().getMimeTypeFromExtension(ext) ?: "*/*"
    }

    // ------------------------------------------------------------------ //
    // 通知
    // ------------------------------------------------------------------ //

    private fun ensureChannel(ctx: Context) {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(CH_DOWNLOADS) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CH_DOWNLOADS, "下载", NotificationManager.IMPORTANCE_LOW).apply {
                    setShowBadge(false)
                },
            )
        }
    }

    private fun canNotify(ctx: Context): Boolean =
        Build.VERSION.SDK_INT < 33 ||
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) ==
            PackageManager.PERMISSION_GRANTED

    private fun notifyProgress(ctx: Context, t: DownloadTask?, percent: Int) {
        if (t == null || !canNotify(ctx)) return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val builder = NotificationCompat.Builder(ctx, CH_DOWNLOADS)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle("正在下载")
            .setContentText(t.fileName)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
        if (percent >= 0) {
            builder.setProgress(100, percent, false)
        } else {
            builder.setProgress(0, 0, true)
        }
        nm.notify(notifId(t.id), builder.build())
    }

    private fun notifyDone(ctx: Context, t: DownloadTask?, success: Boolean) {
        if (t == null || !canNotify(ctx)) return
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val pi = if (success && t.localPath != null) {
            try {
                val file = File(t.localPath)
                val uri = FileProvider.getUriForFile(ctx, "${ctx.packageName}.fileprovider", file)
                val intent = Intent(Intent.ACTION_VIEW)
                    .setDataAndType(uri, t.mimeType ?: guessMime(file))
                    .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                PendingIntent.getActivity(
                    ctx,
                    t.id.hashCode(),
                    intent,
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
                )
            } catch (_: Exception) {
                null
            }
        } else {
            null
        }
        nm.notify(
            notifId(t.id),
            NotificationCompat.Builder(ctx, CH_DOWNLOADS)
                .setSmallIcon(if (success) android.R.drawable.stat_sys_download_done else android.R.drawable.stat_notify_error)
                .setContentTitle(if (success) "下载完成" else if (t.status == DownloadStatus.CANCELLED) "下载已取消" else "下载失败")
                .setContentText(t.fileName)
                .setContentIntent(pi)
                .setAutoCancel(true)
                .build(),
        )
    }

    private fun notifId(id: String): Int = 9100 + (id.hashCode() and 0x3FF)
}
