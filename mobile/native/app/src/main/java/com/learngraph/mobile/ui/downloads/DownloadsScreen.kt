package com.learngraph.mobile.ui.downloads

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBars
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBars
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.windowInsetsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.learngraph.mobile.data.DownloadStatus
import com.learngraph.mobile.data.DownloadStore
import com.learngraph.mobile.data.DownloadTask
import java.util.Locale

/**
 * 内置下载管理页：进度 / 取消 / 打开 / 重试 / 删除。
 */
@Composable
fun DownloadsScreen(onBack: () -> Unit) {
    val context = LocalContext.current
    val tasks by DownloadStore.tasks.collectAsState()

    Column(
        modifier = Modifier
            .fillMaxSize()
            .background(MaterialTheme.colorScheme.background),
    ) {
        // 顶栏（statusBars 安全区）
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .windowInsetsPadding(WindowInsets.statusBars)
                .height(52.dp)
                .padding(horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
            }
            Text(
                text = "下载",
                style = MaterialTheme.typography.titleLarge,
                modifier = Modifier.weight(1f),
            )
            if (tasks.any { it.status == DownloadStatus.COMPLETED }) {
                Text(
                    text = "清除已完成",
                    style = MaterialTheme.typography.labelLarge,
                    color = MaterialTheme.colorScheme.primary,
                    modifier = Modifier
                        .clickable { DownloadStore.removeAllCompleted() }
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                )
            }
        }

        if (tasks.isEmpty()) {
            Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Text("暂无下载", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(6.dp))
                    Text(
                        "在网页里点击下载链接，内容会保存到这里",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
        } else {
            LazyColumn(
                modifier = Modifier
                    .fillMaxSize()
                    .windowInsetsPadding(WindowInsets.navigationBars),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(tasks, key = { it.id }) { task ->
                    DownloadRow(task = task, context = context)
                }
            }
        }
    }
}

@Composable
private fun DownloadRow(task: DownloadTask, context: android.content.Context) {
    Card(
        modifier = Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // 类型图标
            Box(
                modifier = Modifier
                    .size(38.dp)
                    .background(MaterialTheme.colorScheme.surfaceVariant, RoundedCornerShape(11.dp)),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = typeEmoji(task.mimeType, task.fileName),
                    fontSize = MaterialTheme.typography.titleMedium.fontSize,
                )
            }
            Spacer(Modifier.width(10.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = task.fileName,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                Spacer(Modifier.height(2.dp))
                Text(
                    text = statusText(task),
                    style = MaterialTheme.typography.bodySmall,
                    color = statusColor(task),
                )
                if (task.status == DownloadStatus.DOWNLOADING) {
                    Spacer(Modifier.height(6.dp))
                    LinearProgressIndicator(
                        progress = { progressOf(task) },
                        modifier = Modifier.fillMaxWidth().height(4.dp),
                    )
                } else if (task.status == DownloadStatus.FAILED && task.error != null) {
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = task.error,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
            Spacer(Modifier.width(6.dp))
            // 操作按钮
            when (task.status) {
                DownloadStatus.DOWNLOADING -> {
                    IconButton(onClick = { DownloadStore.cancel(task.id) }) {
                        Icon(Icons.Default.Delete, contentDescription = "取消下载", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                DownloadStatus.COMPLETED -> {
                    TextButton(onClick = { DownloadStore.open(context, task.id) }) {
                        Text("打开", color = MaterialTheme.colorScheme.primary)
                    }
                    IconButton(onClick = { DownloadStore.remove(task.id) }) {
                        Icon(Icons.Default.Delete, contentDescription = "删除", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                DownloadStatus.FAILED -> {
                    IconButton(onClick = { DownloadStore.retry(task.id) }) {
                        Icon(Icons.Default.Refresh, contentDescription = "重试", tint = MaterialTheme.colorScheme.primary)
                    }
                    IconButton(onClick = { DownloadStore.remove(task.id) }) {
                        Icon(Icons.Default.Delete, contentDescription = "删除", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                DownloadStatus.CANCELLED -> {
                    IconButton(onClick = { DownloadStore.retry(task.id) }) {
                        Icon(Icons.Default.Refresh, contentDescription = "重试", tint = MaterialTheme.colorScheme.primary)
                    }
                    IconButton(onClick = { DownloadStore.remove(task.id) }) {
                        Icon(Icons.Default.Delete, contentDescription = "删除", tint = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
            }
        }
    }
}

private fun progressOf(task: DownloadTask): Float {
    val total = task.totalBytes ?: return 0f
    if (total <= 0) return 0f
    return (task.downloadedBytes.toFloat() / total).coerceIn(0f, 1f)
}

private fun statusText(task: DownloadTask): String = when (task.status) {
    DownloadStatus.DOWNLOADING -> {
        val total = task.totalBytes
        if (total != null && total > 0) {
            "下载中 ${formatSize(task.downloadedBytes)} / ${formatSize(total)}"
        } else {
            "下载中 ${formatSize(task.downloadedBytes)}"
        }
    }
    DownloadStatus.COMPLETED -> "已完成 · ${formatSize(task.downloadedBytes)}"
    DownloadStatus.FAILED -> "下载失败"
    DownloadStatus.CANCELLED -> "已取消"
}

@Composable
private fun statusColor(task: DownloadTask): Color = when (task.status) {
    DownloadStatus.FAILED -> MaterialTheme.colorScheme.error
    DownloadStatus.COMPLETED -> MaterialTheme.colorScheme.primary
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

private fun formatSize(bytes: Long): String {
    if (bytes < 1024) return "$bytes B"
    val kb = bytes / 1024.0
    if (kb < 1024) return String.format(Locale.US, "%.1f KB", kb)
    val mb = kb / 1024.0
    if (mb < 1024) return String.format(Locale.US, "%.1f MB", mb)
    return String.format(Locale.US, "%.2f GB", mb / 1024.0)
}

private fun typeEmoji(mimeType: String?, fileName: String): String {
    val mime = mimeType?.lowercase() ?: ""
    return when {
        mime.startsWith("image/") -> "🖼️"
        mime.startsWith("video/") -> "🎬"
        mime.startsWith("audio/") -> "🎵"
        mime == "application/pdf" || fileName.endsWith(".pdf", true) -> "📕"
        mime.contains("zip") || mime.contains("compressed") ||
            fileName.endsWith(".zip", true) || fileName.endsWith(".tar", true) ||
            fileName.endsWith(".gz", true) -> "📦"
        mime.contains("word") || fileName.endsWith(".doc", true) || fileName.endsWith(".docx", true) -> "📘"
        mime.contains("sheet") || fileName.endsWith(".xls", true) || fileName.endsWith(".xlsx", true) -> "📊"
        mime.contains("html") || fileName.endsWith(".html", true) || fileName.endsWith(".htm", true) -> "🌐"
        else -> "📄"
    }
}
