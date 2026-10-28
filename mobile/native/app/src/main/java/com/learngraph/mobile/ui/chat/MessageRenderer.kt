package com.learngraph.mobile.ui.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import com.learngraph.mobile.ui.theme.WebText as Text
import androidx.compose.material3.Button
import androidx.compose.material3.TextButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.IconButton
import com.learngraph.mobile.ui.components.WebDropdownMenu as DropdownMenu
import com.learngraph.mobile.ui.components.WebDropdownMenuItem as DropdownMenuItem
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import com.learngraph.mobile.ui.components.WebGlyph
import com.learngraph.mobile.ui.components.WebIcon
import com.learngraph.mobile.ui.components.webDrawerHorizontalScrollable
import com.learngraph.mobile.ui.components.webDrawerSwipeExcluded
import com.learngraph.mobile.ui.components.webClickFeedback
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.learngraph.mobile.data.Message
import com.learngraph.mobile.data.MessagePart
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.JsonPrimitive

/**
 * 原生消息容器与滚动；正文、过程内容共享 Web 的真实 renderer 和组件注册表。
 */
@Composable
fun MessageBubble(
    message: Message,
    baseUrl: String,
    onLinkClick: (String) -> Unit,
    onOpenWeb: () -> Unit,
    onRetry: (() -> Unit)?,
    modifier: Modifier = Modifier,
    presentation: NativeMessagePresentation? = null,
    onFinalAnswerTop: (Float) -> Unit = {},
    onSendAction: (String) -> Unit = {},
    onRefresh: () -> Unit = {},
    onCopy: (() -> Unit)? = null,
    onCopyId: (() -> Unit)? = null,
    onEdit: ((String) -> Unit)? = null,
    onBranch: (() -> Unit)? = null,
    actionsEnabled: Boolean = true,
    versionState: NativeMessageVersionState? = null,
    currentVersion: Int? = message.version,
    onRequestVersions: () -> Unit = {},
    onSelectVersion: (String?) -> Unit = {},
    conversationVersions: NativeConversationVersions? = null,
    onSelectConversation: (String) -> Unit = {},
) {
    val isUser = message.role == "user"
    val streaming = message.status in listOf("pending", "submitted", "streaming")
    val groups = remember(message.parts, presentation, streaming) { groupMessageParts(message.parts, presentation, streaming) }
    var editing by rememberSaveable(message.id) { mutableStateOf(false) }
    var editValue by rememberSaveable(message.id) { mutableStateOf(messageCopyText(message)) }
    var moreOpen by remember(message.id) { mutableStateOf(false) }
    Column(modifier.fillMaxWidth()) {
      if (!isUser && versionState != null && (versionState.versions.size > 1 || versionState.loading || versionState.error != null)) {
        val versionScroll = rememberScrollState()
        Row(Modifier.fillMaxWidth().horizontalScroll(versionScroll).webDrawerHorizontalScrollable(versionScroll), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            Text(if (versionState.loading) "正在读取版本…" else "版本：", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            versionState.versions.forEach { version ->
                val selected = version.id == versionState.selectedVersionId || versionState.selectedVersionId == null && version.version == currentVersion
                TextButton(onClick = { onSelectVersion(version.id) }, enabled = !versionState.loading && !streaming,
                    contentPadding = PaddingValues(horizontal = 8.dp), modifier = Modifier.height(28.dp)) {
                    Text("v${version.version}", fontSize = 11.sp, fontWeight = if (selected) FontWeight.Bold else FontWeight.Normal,
                        color = if (selected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
                }
            }
            if (versionState.error != null) TextButton(onClick = onRequestVersions) { Text("重试读取", fontSize = 11.sp) }
        }
      }
      BoxWithConstraints(Modifier.fillMaxWidth()) {
        val availableWidth = maxWidth
        val bubbleWidth = maxWidth * .8f
        Row(Modifier.fillMaxWidth(), horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start) {
            val bubble = if (isUser) Modifier.widthIn(max = if (editing) availableWidth else bubbleWidth).clip(RoundedCornerShape(22.dp))
                .background(MaterialTheme.colorScheme.secondaryContainer).padding(horizontal = 16.dp, vertical = 12.dp)
            else Modifier.fillMaxWidth()
            SelectionContainer {
                Column(bubble) {
                    if (isUser) {
                      if (editing) {
                        OutlinedTextField(editValue, { editValue = it }, enabled = actionsEnabled, label = { Text("编辑消息") },
                            minLines = 3, maxLines = 10, modifier = Modifier.fillMaxWidth().webDrawerSwipeExcluded(), textStyle = androidx.compose.ui.text.TextStyle(fontSize = 16.sp, lineHeight = 28.8.sp))
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                            TextButton(onClick = { editing = false }, enabled = actionsEnabled) { Text("取消") }
                            Button(onClick = { onEdit?.invoke(editValue.trim()) }, enabled = actionsEnabled && editValue.isNotBlank() && editValue.length <= 50_000) { Text("保存并重新回答", fontSize = 12.sp) }
                        }
                      } else {
                        if (message.parts.isEmpty()) Text(message.content, fontSize = 16.sp, lineHeight = 28.8.sp, color = MaterialTheme.colorScheme.onSecondaryContainer)
                        message.parts.forEach { part -> key(part.id) {
                            if (part.type == "text") Text(part.content.orEmpty(), fontSize = 16.sp, lineHeight = 28.8.sp, color = MaterialTheme.colorScheme.onSecondaryContainer)
                            else SharedMessageBody("${message.id}-${part.id}", message.sessionId, listOf(part), message.parts, false,
                                onSendAction, onRefresh, onLinkClick, onOpenWeb)
                        } }
                      }
                    } else {
                        if (groups.chain.isNotEmpty()) key(message.version ?: 1) {
                            SharedMessageBody(message.id + "-process", message.sessionId, groups.chain, message.parts, streaming,
                                onSendAction, onRefresh, onLinkClick, onOpenWeb, providerTrace = message.providerTrace,
                                thinkingChain = NativeThinkingChainInfo(
                                    messageStatus = if (streaming) "streaming" else message.status ?: "completed",
                                    startedAt = message.createdAt,
                                    finalAnswerStarted = presentation?.answerStarted == true || message.parts.any { it.data.string("kind") == "final_answer" },
                                    thinkingDurationMs = presentation?.thinkingDurationMs,
                                ))
                        }
                        if (streaming || groups.answer.isNotEmpty() || message.parts.isEmpty() && message.content.isNotBlank()) {
                            Column(Modifier.fillMaxWidth().onGloballyPositioned { onFinalAnswerTop(it.positionInRoot().y) }, verticalArrangement = Arrangement.spacedBy(14.4.dp)) {
                                val answer = if (message.parts.isEmpty()) listOf(MessagePart("${message.id}-body", "text", if (streaming) "streaming" else "completed", message.content)) else groups.answer
                                SharedMessageBody(message.id, message.sessionId, answer, message.parts.ifEmpty { answer }, streaming,
                                    onSendAction, onRefresh, onLinkClick, onOpenWeb, showStreamIndicator = true, providerTrace = message.providerTrace)
                            }
                        }
                    }
                }
            }
        }
      }
      if (!editing && (onCopy != null || onRetry != null || onEdit != null)) {
        // AiMessage's flex-col gap-2 separates the body and its action row.
        Row(Modifier.fillMaxWidth().padding(top = 8.dp), horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start, verticalAlignment = Alignment.CenterVertically) {
            if (onCopy != null) MessageActionButton(if (isUser) "复制消息" else "复制全文", WebIcon.Copy, onCopy)
            if (isUser && onEdit != null) MessageActionButton("编辑消息", WebIcon.Edit, { editValue = messageCopyText(message); editing = true }, actionsEnabled)
            if (!isUser) {
                if (onRetry != null) MessageActionButton("重试并保留版本", WebIcon.RefreshCcw, onRetry, actionsEnabled)
                Box {
                    MessageActionButton("更多操作", WebIcon.More, { moreOpen = true; onRequestVersions() })
                    DropdownMenu(moreOpen, { moreOpen = false }) {
                        DropdownMenuItem(text = { Text("从此创建分支", fontSize = 13.sp) }, enabled = actionsEnabled && onBranch != null,
                            leadingIcon = { WebGlyph(WebIcon.GitBranch, modifier = Modifier.size(15.dp)) }, onClick = { moreOpen = false; onBranch?.invoke() })
                        DropdownMenuItem(text = { Text("查看回答版本", fontSize = 13.sp) }, enabled = !streaming,
                            onClick = { moreOpen = false; onRequestVersions() })
                        if (onCopyId != null) DropdownMenuItem(text = { Column { Text("复制消息 ID", fontSize = 13.sp); Text(message.id + " · v" + (message.version ?: 1), fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant) } },
                            leadingIcon = { WebGlyph(WebIcon.Copy, modifier = Modifier.size(15.dp)) }, onClick = { moreOpen = false; onCopyId() })
                    }
                }
                if (versionState?.loaded == true && versionState.versions.size <= 1) Text("仅当前版本", fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
      }
      if (isUser && conversationVersions != null) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
          MessageActionButton("上一版本", WebIcon.ChevronLeft, { onSelectConversation(conversationVersions.sessionIds[conversationVersions.currentIndex - 1]) }, actionsEnabled && conversationVersions.currentIndex > 0)
          Text("${conversationVersions.currentIndex + 1}/${conversationVersions.sessionIds.size}", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
          MessageActionButton("下一版本", WebIcon.ChevronRight, { onSelectConversation(conversationVersions.sessionIds[conversationVersions.currentIndex + 1]) }, actionsEnabled && conversationVersions.currentIndex < conversationVersions.sessionIds.lastIndex)
      }
    }
}

@Composable
private fun MessageActionButton(label: String, icon: WebIcon, onClick: () -> Unit, enabled: Boolean = true) {
    Box(contentAlignment = Alignment.Center, modifier = Modifier.size(30.dp).clip(CircleShape).webDrawerSwipeExcluded().webClickFeedback(enabled = enabled, hasPopup = label == "更多操作", onClick = onClick).semantics { contentDescription = label }) {
        WebGlyph(icon, modifier = Modifier.size(14.dp), color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = if (enabled) .75f else .3f))
    }
}

private val chainTypes = setOf("reasoning_summary", "reasoning_content", "tool_call", "agent_step", "graph_context", "sandbox_status", "subagent_task", "skill_trigger", "graph_progress")
private val artifactTypes = setOf("image", "sticker", "sandbox", "sandbox_artifact", "subapp_artifact", "magic_card", "component", "chart", "ui")
private fun JsonObject?.string(key: String): String? = (this?.get(key) as? JsonPrimitive)?.contentOrNull
private data class MessageGroups(val chain: List<MessagePart>, val answer: List<MessagePart>)

/** Same boundary-first partition as the web groupPartsForDisplay; unmarked live text stays visible. */
private fun groupMessageParts(parts: List<MessagePart>, presentation: NativeMessagePresentation?, live: Boolean): MessageGroups {
    val ordered = parts.withIndex().sortedWith(compareBy({ it.value.sequence ?: it.index }, { it.index })).map { it.value }
    val boundary = presentation?.finalPartId?.let { id -> ordered.indexOfFirst { it.id == id }.takeIf { it >= 0 } }
        ?: presentation?.boundarySequence?.let { sequence -> ordered.indexOfFirst { it.sequence == sequence }.takeIf { it >= 0 } }
        ?: ordered.indexOfFirst { it.type == "text" && it.data.string("kind") == "final_answer" }
    val protocol = live || presentation != null || ordered.any { it.type == "text" && it.data.string("kind") != null }
    val chain = mutableListOf<MessagePart>()
    val answer = mutableListOf<MessagePart>()
    val proposals = mutableListOf<MessagePart>()
    fun anchorsArtifact(index: Int): Boolean {
        fun relevant(part: MessagePart) = part.type in artifactTypes || part.type == "text" && !part.content.isNullOrBlank()
        return ordered.take(index).lastOrNull(::relevant)?.type in artifactTypes || ordered.drop(index + 1).firstOrNull(::relevant)?.type in artifactTypes
    }
    ordered.forEachIndexed { index, part ->
        val text = part.content.orEmpty().trim()
        if (part.type == "acknowledgement" && (text.isEmpty() || text in listOf("正在思考", "Thinking...", "Thinking"))) return@forEachIndexed
        if (part.type == "agent_step" && (text.isEmpty() || text in listOf("智能体正在执行已授权的工具调用。", "智能体步骤已完成", "正在执行智能体步骤"))) return@forEachIndexed
        when {
            part.type == "component" && part.data.string("component_type") == "graph_update_proposal" -> proposals += part
            part.type in chainTypes || part.type in artifactTypes && part.status == "failed" -> chain += part
            part.type == "text" && text.isBlank() -> Unit
            part.type == "text" && protocol && part.data.string("kind") != "final_answer" && (boundary < 0 || index < boundary)
                && !(live && part.data.string("kind") == null) && !anchorsArtifact(index) -> chain += part
            else -> answer += part
        }
    }
    return MessageGroups(chain, answer + proposals)
}
