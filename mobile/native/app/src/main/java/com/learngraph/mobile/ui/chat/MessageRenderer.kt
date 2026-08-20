package com.learngraph.mobile.ui.chat

import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.learngraph.mobile.data.Message
import com.learngraph.mobile.data.MessagePart
import com.learngraph.mobile.ui.components.MarkdownText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.longOrNull

/**
 * 消息分级渲染（刚需优先）：
 *  原生渲染：text / reasoning_* / tool_call / agent_step / source_list /
 *            attachment / selection_quote / document_selection / image / acknowledgement / error
 *  兜底卡片：其余复杂类型（chart/quiz/sandbox/magic_card 等）→ 结构化卡片 + 「在网页版查看」
 */
@Composable
fun MessageBubble(
    message: Message,
    baseUrl: String,
    onLinkClick: (String) -> Unit,
    onOpenWeb: () -> Unit,
    onRetry: (() -> Unit)?,
    modifier: Modifier = Modifier,
) {
    val isUser = message.role == "user"
    val bubbleColor = if (isUser) {
        MaterialTheme.colorScheme.primaryContainer
    } else {
        MaterialTheme.colorScheme.surfaceVariant
    }
    val shape = RoundedCornerShape(
        topStart = if (isUser) 16.dp else 4.dp,
        topEnd = if (isUser) 4.dp else 16.dp,
        bottomStart = 16.dp,
        bottomEnd = 16.dp,
    )

    Column(
        modifier = modifier.fillMaxWidth(),
        horizontalAlignment = if (isUser) Alignment.End else Alignment.Start,
    ) {
        Box(
            modifier = Modifier
                .fillMaxWidth(if (isUser) 0.85f else 0.95f)
                .clip(shape)
                .background(bubbleColor)
                .padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            Column {
                if (message.parts.isEmpty()) {
                    if (message.content.isNotBlank()) {
                        Text(message.content, style = MaterialTheme.typography.bodyMedium)
                    }
                } else {
                    message.parts.forEach { part ->
                        PartView(
                            part = part,
                            baseUrl = baseUrl,
                            onLinkClick = onLinkClick,
                            onOpenWeb = onOpenWeb,
                        )
                    }
                }
                // 失败消息可重试
                if (message.status == "failed" && !isUser && onRetry != null) {
                    Spacer(Modifier.height(6.dp))
                    Text(
                        text = "重试",
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.Medium,
                        modifier = Modifier
                            .clip(RoundedCornerShape(6.dp))
                            .clickable { onRetry() }
                            .padding(horizontal = 4.dp, vertical = 2.dp),
                    )
                }
            }
        }
        Spacer(Modifier.height(8.dp))
    }
}

@Composable
private fun PartView(
    part: MessagePart,
    baseUrl: String,
    onLinkClick: (String) -> Unit,
    onOpenWeb: () -> Unit,
) {
    when (part.type) {
        "text" -> {
            val content = part.content ?: ""
            if (content.isNotBlank()) {
                MarkdownText(content, onLinkClick = onLinkClick)
            }
        }
        "reasoning_summary", "reasoning_content" -> ReasoningCard(part)
        "tool_call", "agent_step" -> ToolStepRow(part)
        "source_list" -> SourceListCard(part, onLinkClick)
        "attachment", "selection_quote", "document_selection" -> QuoteCard(part)
        "image" -> ImagePart(part, baseUrl)
        "acknowledgement" -> AckRow(part)
        "error" -> ErrorRow(part)
        "graph_progress" -> {
            part.content?.takeIf { it.isNotBlank() }?.let {
                Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        else -> FallbackCard(part, onOpenWeb)
    }
}

// ------------------------------------------------------------------ //
// 刚需渲染
// ------------------------------------------------------------------ //

@Composable
private fun ReasoningCard(part: MessagePart) {
    val content = part.content ?: return
    if (content.isBlank()) return
    var expanded by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(false) }
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.6f))
            .clickable { expanded = !expanded }
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(
                text = if (expanded) "▾ 思考过程" else "▸ 思考过程",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.weight(1f))
            if (!expanded) {
                Text(
                    text = content.take(40) + if (content.length > 40) "…" else "",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                )
            }
        }
        if (expanded) {
            Spacer(Modifier.height(6.dp))
            Text(
                text = content,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
    Spacer(Modifier.height(6.dp))
}

@Composable
private fun ToolStepRow(part: MessagePart) {
    val data = part.data
    val toolName = data?.get("name")?.jsonPrimitive?.contentOrNull
        ?: data?.get("tool_name")?.jsonPrimitive?.contentOrNull
    val status = part.status
    val icon = when (status) {
        "completed" -> "✓"
        "failed" -> "✗"
        "streaming", "pending" -> "…"
        else -> "·"
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            text = icon,
            style = MaterialTheme.typography.labelMedium,
            color = when (status) {
                "failed" -> MaterialTheme.colorScheme.error
                "completed" -> MaterialTheme.colorScheme.primary
                else -> MaterialTheme.colorScheme.onSurfaceVariant
            },
        )
        Spacer(Modifier.width(6.dp))
        Text(
            text = toolName ?: (data?.get("tool")?.jsonPrimitive?.contentOrNull ?: part.type),
            style = MaterialTheme.typography.labelMedium,
            fontFamily = FontFamily.Monospace,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
        )
    }
    Spacer(Modifier.height(4.dp))
}

@Composable
private fun SourceListCard(part: MessagePart, onLinkClick: (String) -> Unit) {
    val items = part.data?.get("sources")
    if (items !is kotlinx.serialization.json.JsonArray) return
    Column {
        Text(
            text = "来源",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        items.forEachIndexed { idx, item ->
            val obj = item as? JsonObject ?: return@forEachIndexed
            val title = obj["title"]?.jsonPrimitive?.contentOrNull
            val url = obj["url"]?.jsonPrimitive?.contentOrNull
                ?: obj["link"]?.jsonPrimitive?.contentOrNull
            if (title != null || url != null) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(6.dp))
                        .clickable(enabled = url != null) { url?.let(onLinkClick) }
                        .padding(vertical = 4.dp, horizontal = 2.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = "${idx + 1}.",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
                    )
                    Spacer(Modifier.width(6.dp))
                    Text(
                        text = title ?: url.orEmpty(),
                        style = MaterialTheme.typography.bodySmall,
                        color = if (url != null) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurface,
                        maxLines = 2,
                    )
                }
            }
        }
    }
    Spacer(Modifier.height(6.dp))
}

@Composable
private fun QuoteCard(part: MessagePart) {
    val content = part.content
    if (content.isNullOrBlank()) return
    Text(
        text = content,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(6.dp))
            .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.5f))
            .padding(horizontal = 10.dp, vertical = 8.dp),
    )
    Spacer(Modifier.height(6.dp))
}

@Composable
private fun AckRow(part: MessagePart) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        Text(
            text = "✓ 已确认",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
    Spacer(Modifier.height(4.dp))
}

@Composable
private fun ErrorRow(part: MessagePart) {
    Text(
        text = part.content ?: "出错了",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.error,
    )
    Spacer(Modifier.height(4.dp))
}

@Composable
private fun ImagePart(part: MessagePart, baseUrl: String) {
    val fileId = part.data?.get("file_id")?.jsonPrimitive?.contentOrNull
        ?: part.data?.get("fileId")?.jsonPrimitive?.contentOrNull
    if (fileId == null) return
    val context = LocalContext.current
    val bitmap by produceState<android.graphics.Bitmap?>(initialValue = null, fileId, baseUrl) {
        value = withContext(Dispatchers.IO) {
            try {
                val url = "$baseUrl/api/v1/files/$fileId/content"
                val conn = java.net.URL(url).openConnection() as java.net.HttpURLConnection
                conn.connectTimeout = 10000
                conn.readTimeout = 15000
                conn.inputStream.use { BitmapFactory.decodeStream(it) }
            } catch (_: Exception) {
                null
            }
        }
    }
    bitmap?.let {
        Image(
            bitmap = it.asImageBitmap(),
            contentDescription = "图片",
            contentScale = ContentScale.FillWidth,
            modifier = Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(8.dp)),
        )
        Spacer(Modifier.height(6.dp))
    }
}

// ------------------------------------------------------------------ //
// 兜底卡片（复杂类型 → 结构化摘要 + 网页版入口）
// ------------------------------------------------------------------ //

private val FALLBACK_LABELS = mapOf(
    "chart" to "图表",
    "quiz" to "测验",
    "sandbox" to "沙箱",
    "sandbox_artifact" to "沙箱产物",
    "subapp_artifact" to "子应用产物",
    "subapp_event" to "子应用事件",
    "sandbox_status" to "沙箱状态",
    "skill_trigger" to "技能调用",
    "component" to "组件",
    "magic_card" to "卡片",
    "user_confirmation" to "待确认",
    "fetch_authorization" to "网页抓取授权",
    "fetch_setup_notice" to "抓取配置提示",
    "egress_authorization" to "出网授权",
    "graph_context" to "图谱上下文",
)

@Composable
private fun FallbackCard(part: MessagePart, onOpenWeb: () -> Unit) {
    val label = FALLBACK_LABELS[part.type] ?: part.type
    val summary = part.content?.take(80) ?: "该内容在手机端以摘要形式展示"
    Column(
        modifier = Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(MaterialTheme.colorScheme.surface.copy(alpha = 0.6f))
            .padding(horizontal = 10.dp, vertical = 8.dp),
    ) {
        Text(
            text = "「$label」",
            style = MaterialTheme.typography.labelMedium,
            fontWeight = FontWeight.Medium,
        )
        Spacer(Modifier.height(2.dp))
        Text(
            text = summary,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(6.dp))
        Text(
            text = "在网页版查看 →",
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier
                .clip(RoundedCornerShape(6.dp))
                .clickable { onOpenWeb() }
                .padding(horizontal = 4.dp, vertical = 2.dp),
        )
    }
    Spacer(Modifier.height(6.dp))
}
