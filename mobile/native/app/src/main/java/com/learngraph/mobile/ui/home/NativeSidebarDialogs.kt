package com.learngraph.mobile.ui.home

import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.learngraph.mobile.ui.components.WebDialog as Dialog
import com.learngraph.mobile.ui.components.WebAlertDialog as AlertDialog
import com.learngraph.mobile.ui.components.WebDropdownMenu as DropdownMenu
import com.learngraph.mobile.ui.components.WebDropdownMenuItem as DropdownMenuItem
import com.learngraph.mobile.ui.components.rememberWebDialogCloseAction
import com.learngraph.mobile.ui.theme.WebText as Text
import androidx.compose.ui.window.DialogProperties
import com.learngraph.mobile.data.Message
import com.learngraph.mobile.data.NativeDeletionPlan
import com.learngraph.mobile.data.NativeSessionShare
import com.learngraph.mobile.data.NativeSessionShareToken
import com.learngraph.mobile.data.Session
import com.learngraph.mobile.ui.components.WebGlyph
import com.learngraph.mobile.ui.components.WebIcon
import kotlinx.coroutines.launch

@Composable
internal fun NativeRenameDialog(initialTitle: String, heading: String, busy: Boolean, error: String? = null, onDismiss: () -> Unit, onConfirm: (String) -> Unit, expanded: Boolean = true, onClosed: () -> Unit = {}) {
    var title by remember(initialTitle) { mutableStateOf(initialTitle) }
    AlertDialog(expanded = expanded, onClosed = onClosed, properties = DialogProperties(dismissOnBackPress = !busy, dismissOnClickOutside = !busy), onDismissRequest = { if (!busy) onDismiss() }, title = { Text(heading, fontSize = 18.sp) }, text = {
        Column { TextField(title, { title = it }, singleLine = true, label = { Text("名称") }, enabled = !busy); if (error != null) Text(error, fontSize = 12.sp, color = MaterialTheme.colorScheme.error, modifier = Modifier.padding(top = 8.dp)) }
    }, confirmButton = { TextButton(onClick = { onConfirm(title.trim()) }, enabled = title.isNotBlank() && !busy) { Text(if (busy) "保存中…" else "保存") } }, dismissButton = { TextButton(onClick = rememberWebDialogCloseAction(onDismiss), enabled = !busy) { Text("取消") } })
}

private val impactResources = mapOf("project" to "项目", "chat_session" to "会话", "message" to "消息", "memory" to "关联记忆", "memory_draft" to "记忆草稿", "conversation_episode" to "会话记忆摘要", "graph" to "知识图谱", "graph_node" to "知识节点", "graph_edge" to "图谱连线", "goal" to "学习目标", "file" to "文件", "file_reference" to "文件引用", "evidence" to "证据", "source_link" to "资料关联", "exercise" to "练习", "answer_record" to "作答记录", "action_item" to "行动项", "roadmap" to "学习路线", "source_citation" to "来源引用", "suggested_prompt_batch" to "推荐问题")
private val impactActions = mapOf("delete" to "永久删除", "detach" to "解除关联", "unlink" to "解除关联", "preserve" to "保留", "preserve_history" to "保留历史")

@Composable
internal fun NativeSidebarDeleteDialog(plan: NativeDeletionPlan?, loading: Boolean, busy: Boolean, error: String?, onDismiss: () -> Unit, onRetry: () -> Unit, onConfirm: () -> Unit, expanded: Boolean = true, onClosed: () -> Unit = {}) {
    AlertDialog(expanded = expanded, onClosed = onClosed, properties = DialogProperties(dismissOnBackPress = !busy, dismissOnClickOutside = !busy), onDismissRequest = { if (!busy) onDismiss() }, title = { Text("永久删除所选内容？", fontSize = 18.sp) }, text = {
        Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Text("删除后无法恢复。请核对服务端返回的实际影响。", fontSize = 13.sp, lineHeight = 20.sp)
            when {
                loading -> Row { CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp); Spacer(Modifier.width(8.dp)); Text("正在检查关联数据…", fontSize = 12.sp) }
                error != null -> { Text(error, fontSize = 12.sp, color = MaterialTheme.colorScheme.error); TextButton(onClick = onRetry) { Text("重新检查") } }
                plan != null -> plan.impacts.forEach { impact ->
                    Column(Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(8.dp)).padding(10.dp)) {
                        Text(impact.title.ifBlank { "所选内容" }, fontSize = 13.sp, fontWeight = FontWeight.Medium)
                        val visible = impact.impacts.filter { it.count > 0 }
                        if (visible.isEmpty()) Text("无额外关联；当前对象本身仍会永久删除。", fontSize = 11.sp, lineHeight = 18.sp)
                        visible.forEach { item -> Row(Modifier.fillMaxWidth().padding(top = 5.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("${impactResources[item.resourceType] ?: item.resourceType} · ${impactActions[item.action] ?: item.action}", fontSize = 11.sp, modifier = Modifier.weight(1f))
                            Text(item.count.toString(), fontSize = 11.sp, fontWeight = FontWeight.SemiBold)
                        } }
                    }
                }
            }
        }
    }, confirmButton = { TextButton(onClick = onConfirm, enabled = plan != null && !loading && !busy) { Text(if (busy) "正在删除…" else "确认删除", color = MaterialTheme.colorScheme.error) } }, dismissButton = { TextButton(onClick = rememberWebDialogCloseAction(onDismiss), enabled = !busy) { Text("取消，保留") } })
}

/** Immutable snapshots, scope/range selection and link revocation match SessionShareDialog. */
@Composable
internal fun NativeSessionShareDialog(vm: HomeViewModel, session: Session, onDismiss: () -> Unit) {
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    val coroutine = rememberCoroutineScope()
    var shareScope by remember(session.id) { mutableStateOf("full") }
    var label by remember(session.id) { mutableStateOf("") }
    var maxViews by remember(session.id) { mutableStateOf("") }
    var fromId by remember(session.id) { mutableStateOf("") }
    var toId by remember(session.id) { mutableStateOf("") }
    var messages by remember(session.id) { mutableStateOf(emptyList<Message>()) }
    var shares by remember(session.id) { mutableStateOf(emptyList<NativeSessionShare>()) }
    var created by remember(session.id) { mutableStateOf<NativeSessionShareToken?>(null) }
    var busy by remember { mutableStateOf(false) }
    var loading by remember { mutableStateOf(true) }
    var loadingMessages by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var copied by remember { mutableStateOf(false) }
    suspend fun reload() {
        val result = vm.loadShares(session.id)
        if (result != null) shares = result.filter { it.status == "active" }
        else error = vm.error.value ?: "无法加载已有分享"
    }
    fun runAction(action: suspend () -> Unit) {
        if (busy) return
        busy = true; error = null
        coroutine.launch { try { action() } finally { busy = false } }
    }
    LaunchedEffect(session.id) { try { reload() } finally { loading = false } }
    LaunchedEffect(session.id, shareScope) {
        if (shareScope != "range" || messages.isNotEmpty()) return@LaunchedEffect
        loadingMessages = true
        try { val result = vm.readShareMessages(session.id); if (result != null) messages = result else error = vm.error.value ?: "无法加载消息范围" }
        finally { loadingMessages = false }
    }
    val maxViewsValue = maxViews.toIntOrNull()
    val viewsValid = maxViews.isBlank() || maxViewsValue != null && maxViewsValue > 0
    val canCreate = !busy && viewsValid && (shareScope != "range" || fromId.isNotBlank() && toId.isNotBlank())
    val link = created?.token?.let(vm::shareUrl)
    Dialog(onDismissRequest = { if (!busy) onDismiss() }, properties = DialogProperties(dismissOnBackPress = !busy, dismissOnClickOutside = !busy)) {
        Surface(shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxWidth().heightIn(max = (LocalConfiguration.current.screenHeightDp * .85f).dp).padding(20.dp).verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("分享对话 · ${session.title}", fontSize = 18.sp, lineHeight = 25.sp, fontWeight = FontWeight.SemiBold)
                Text("分享会生成不可变的只读快照，不包含记忆、文件或其他会话内容。", fontSize = 12.sp, lineHeight = 19.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text("分享范围", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                listOf(Triple("full", "整段对话", "分享全部消息"), Triple("range", "区间", "只分享选定的消息范围"), Triple("answers", "仅回答", "只分享 AI 回答，不含你的提问")).forEach { (value, title, hint) ->
                    Column(Modifier.fillMaxWidth().border(1.dp, if (shareScope == value) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline, RoundedCornerShape(8.dp)).background(if (shareScope == value) MaterialTheme.colorScheme.primary.copy(alpha = .05f) else MaterialTheme.colorScheme.background, RoundedCornerShape(8.dp)).clickable(enabled = !busy) { shareScope = value }.padding(10.dp)) {
                        Text(title, fontSize = 13.sp, fontWeight = FontWeight.Medium); Text(hint, fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                if (shareScope == "range") {
                    if (loadingMessages) Text("正在加载消息…", fontSize = 12.sp)
                    ShareMessagePicker("起点消息", messages, fromId, !busy) { fromId = it }
                    ShareMessagePicker("终点消息", messages, toId, !busy) { toId = it }
                }
                OutlinedTextField(label, { label = it.take(120) }, label = { Text("备注（可选）", fontSize = 12.sp) }, singleLine = true, enabled = !busy, modifier = Modifier.fillMaxWidth())
                OutlinedTextField(maxViews, { maxViews = it }, label = { Text("查看次数上限（可选）", fontSize = 12.sp) }, placeholder = { Text("不限", fontSize = 12.sp) }, singleLine = true, enabled = !busy, isError = !viewsValid, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number), modifier = Modifier.fillMaxWidth())
                if (!viewsValid) Text("请输入正整数，或留空表示不限。", fontSize = 11.sp, color = MaterialTheme.colorScheme.error)
                if (link != null) Column(Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(8.dp)).padding(10.dp)) {
                    Text("分享链接已生成", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    Text(link, fontSize = 11.sp, lineHeight = 18.sp)
                    Row {
                        TextButton(onClick = { clipboard.setText(AnnotatedString(link)); copied = true }) { WebGlyph(if (copied) WebIcon.Check else WebIcon.Copy, modifier = Modifier.size(14.dp)); Spacer(Modifier.width(4.dp)); Text(if (copied) "已复制" else "复制链接", fontSize = 12.sp) }
                        TextButton(onClick = { context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply { type = "text/plain"; putExtra(Intent.EXTRA_TEXT, link) }, "分享对话")) }) { WebGlyph(WebIcon.Share2, modifier = Modifier.size(14.dp)); Spacer(Modifier.width(4.dp)); Text("分享", fontSize = 12.sp) }
                    }
                }
                if (loading) Text("正在加载已有分享…", fontSize = 12.sp)
                if (shares.isNotEmpty()) {
                    Text("已有的分享", fontSize = 12.sp, fontWeight = FontWeight.SemiBold)
                    shares.forEach { share -> Column(Modifier.fillMaxWidth().border(1.dp, MaterialTheme.colorScheme.outline, RoundedCornerShape(8.dp)).padding(10.dp)) {
                        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("${when (share.scope) { "range" -> "区间"; "answers" -> "仅回答"; else -> "整段" }} · ${share.messageCount} 条消息", fontSize = 11.sp, modifier = Modifier.weight(1f))
                            TextButton(onClick = { runAction { if (vm.revokeShare(share.id)) reload() else error = vm.error.value } }, enabled = !busy, contentPadding = PaddingValues(horizontal = 5.dp, vertical = 0.dp)) { Text("撤销分享", fontSize = 11.sp) }
                        }
                        share.tokens.forEach { token -> Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                            Text("${token.tokenPrefix}… ${token.label.ifBlank { "无备注" }} · ${token.viewCount}${token.maxViews?.let { "/$it" }.orEmpty()} 次", fontSize = 10.sp, lineHeight = 17.sp, modifier = Modifier.weight(1f))
                            if (token.revokedAt != null) Text("已撤销", fontSize = 10.sp)
                            else TextButton(onClick = { runAction { if (vm.revokeToken(share.id, token.id)) reload() else error = vm.error.value } }, enabled = !busy, contentPadding = PaddingValues(horizontal = 5.dp, vertical = 0.dp)) { Text("撤销链接", fontSize = 10.sp) }
                        } }
                    } }
                }
                if (error != null) Text(error.orEmpty(), fontSize = 12.sp, lineHeight = 19.sp, color = MaterialTheme.colorScheme.error)
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.End) {
                    TextButton(onClick = rememberWebDialogCloseAction(onDismiss), enabled = !busy) { Text("关闭", fontSize = 12.sp) }
                    Button(onClick = { runAction {
                        val result = vm.createShare(session.id, shareScope, label.trim(), maxViewsValue, fromId.takeIf { shareScope == "range" }, toId.takeIf { shareScope == "range" })
                        if (result != null) { created = result; copied = false; reload() } else error = vm.error.value ?: "创建分享失败"
                    } }, enabled = canCreate) { Text(if (busy) "处理中…" else "创建分享", fontSize = 12.sp) }
                }
            }
        }
    }
}

@Composable
private fun ShareMessagePicker(label: String, messages: List<Message>, selectedId: String, enabled: Boolean, onSelect: (String) -> Unit) {
    var open by remember { mutableStateOf(false) }
    val selected = messages.firstOrNull { it.id == selectedId }
    Box {
        OutlinedButton(onClick = { open = true }, enabled = enabled, modifier = Modifier.fillMaxWidth()) { Text(if (selected == null) "选择$label" else "$label：${selected.content.take(32)}", fontSize = 12.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) }
        DropdownMenu(open, { open = false }, modifier = Modifier.heightIn(max = 320.dp)) {
            messages.forEachIndexed { index, message -> DropdownMenuItem(text = { Text("#${index + 1} · ${if (message.role == "user") "我" else "AI"} · ${message.content.replace(Regex("\\s+"), " ").take(42).ifBlank { "（空消息）" }}", fontSize = 12.sp, maxLines = 2, overflow = TextOverflow.Ellipsis) }, onClick = { onSelect(message.id); open = false }) }
        }
    }
}
