package com.learngraph.mobile.ui.home

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.interaction.MutableInteractionSource
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.selection.toggleable
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.platform.LocalViewConfiguration
import androidx.compose.ui.platform.ViewConfiguration
import androidx.compose.ui.hapticfeedback.HapticFeedback
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.DialogProperties
import com.learngraph.mobile.data.NativeDeletionPlan
import com.learngraph.mobile.data.NativeProject
import com.learngraph.mobile.data.Session
import com.learngraph.mobile.data.NativeSessionActivity
import com.learngraph.mobile.data.NativeSidebarActivityPolicy
import com.learngraph.mobile.ui.components.WebGlyph
import com.learngraph.mobile.ui.components.WebIcon
import com.learngraph.mobile.ui.components.WebSpinner
import com.learngraph.mobile.ui.components.WebDropdownMenu as DropdownMenu
import com.learngraph.mobile.ui.components.WebDropdownMenuItem as DropdownMenuItem
import com.learngraph.mobile.ui.components.WebAlertDialog as AlertDialog
import com.learngraph.mobile.ui.components.rememberWebDialogCloseAction
import com.learngraph.mobile.ui.components.webClickFeedback
import com.learngraph.mobile.ui.components.webDrawerSwipeExcluded
import com.learngraph.mobile.ui.theme.WebText as Text
import kotlinx.coroutines.launch
import kotlinx.serialization.json.*

private data class SidebarDeleteTarget(val projects: List<String>, val sessions: List<String>)
private data class SidebarRenameTarget(val id: String, val title: String, val project: Boolean)

/** Actual project/session operations; no management button redirects to the full-page client. */
@OptIn(ExperimentalFoundationApi::class)
@Composable
internal fun NativeSidebarContent(
    vm: HomeViewModel,
    sessions: List<Session>,
    projects: List<NativeProject>,
    loading: Boolean,
    selectedId: String?,
    selectedProjectId: String?,
    storageScope: String,
    onSelect: (Session) -> Unit,
    onSelectProject: (String?) -> Unit,
    onNewChat: (String?) -> Unit,
    onSearchSession: (String) -> Unit,
    onNavigate: (String) -> Unit,
    onDeletedCurrent: () -> Unit,
    modifier: Modifier = Modifier,
    topContent: @Composable () -> Unit,
) {
    val context = LocalContext.current
    val activitySnapshot by vm.sidebarActivity.collectAsState()
    val activity = activitySnapshot.activities
    val prefs = remember(context) { context.getSharedPreferences("lg_native_sidebar", Context.MODE_PRIVATE) }
    var folders by remember(storageScope) { mutableStateOf(readNativeFolders(prefs.getString(storageScope, null))) }
    fun updateFolders(next: Map<String, Boolean>) {
        folders = next
        prefs.edit().putString(storageScope, buildJsonObject { next.forEach { (key, value) -> put(key, value) } }.toString()).apply()
    }
    fun toggleFolder(key: String, open: Boolean) = updateFolders(folders + (key to open))
    val scope = rememberCoroutineScope()
    var searching by remember { mutableStateOf(false) }
    var creatingProject by remember { mutableStateOf(false) }
    var projectName by remember { mutableStateOf("") }
    var selectionMode by remember { mutableStateOf(false) }
    var selectedSessions by remember { mutableStateOf(emptySet<String>()) }
    var selectedProjects by remember { mutableStateOf(emptySet<String>()) }
    var sessionMenu by remember { mutableStateOf<String?>(null) }
    var projectMenu by remember { mutableStateOf<String?>(null) }
    var moveTarget by remember { mutableStateOf<Session?>(null) }
    var moveExpanded by remember { mutableStateOf(true) }
    var renameTarget by remember { mutableStateOf<SidebarRenameTarget?>(null) }
    var renameExpanded by remember { mutableStateOf(true) }
    var modalError by remember { mutableStateOf<String?>(null) }
    var shareTarget by remember { mutableStateOf<Session?>(null) }
    var deleteTarget by remember { mutableStateOf<SidebarDeleteTarget?>(null) }
    var deleteExpanded by remember { mutableStateOf(true) }
    var deletePlan by remember { mutableStateOf<NativeDeletionPlan?>(null) }
    var deleteError by remember { mutableStateOf<String?>(null) }
    var deleteCheckEpoch by remember { mutableIntStateOf(0) }
    var checkingImpact by remember { mutableStateOf(false) }
    var deleting by remember { mutableStateOf(false) }
    var mutating by remember { mutableStateOf(false) }
    var showArchived by remember { mutableStateOf(false) }
    val active = sessions.filter { it.status != "archived" }
    val activeProjectId = sessions.firstOrNull { it.id == selectedId }?.projectId
    val archived = sessions.filter { it.status == "archived" }.sortedWith(NativeSidebarActivityPolicy.comparator(sessions, activity))
    val knownProjects = projects.map { it.id }.toSet()
    // A missing/deleted project must not hide its surviving conversations.
    val ungrouped = sessions.map { if (it.projectId != null && it.projectId !in knownProjects) it.copy(projectId = null) else it }
    val roots = NativeSidebarTree.forest(ungrouped, null, activity = activity)
    val recentRows = if (folders["section:recent"] != false) NativeSidebarTree.rows(roots, folders, selectedId) else emptyList()
    val projectTrees = projects.associate { it.id to NativeSidebarTree.forest(sessions, it.id, activity = activity) }
    val visibleProjects = projects
    val standalone = NativeSidebarTree.standaloneSelection(sessions, selectedSessions, selectedProjects)
    val selectedCount = selectedProjects.size + standalone.size
    val selectable = active.map { it.id }.toSet()
    val folderKeys = projects.map { "project:${it.id}" } + sessions.filter { candidate -> sessions.any { it.parentSessionId == candidate.id } }.map { "session:${it.id}" }
    val anyFolderOpen = projects.any { folders["project:${it.id}"] ?: (it.id == activeProjectId) } ||
        (listOf(roots) + projectTrees.values).any { tree -> NativeSidebarTree.rows(tree, folders, selectedId).any { it.node.children.isNotEmpty() && it.open } }

    fun mutate(action: suspend () -> Unit) {
        if (mutating) return
        mutating = true
        scope.launch { try { action() } finally { mutating = false } }
    }
    fun toggleSession(session: Session) {
        if (session.id in selectedSessions || session.projectId in selectedProjects) {
            if (session.projectId in selectedProjects) selectedSessions = (selectedSessions + sessions.filter { it.projectId == session.projectId }.map { it.id }).filter { it != session.id }.take(100).toSet()
            selectedSessions = selectedSessions - session.id
            session.projectId?.let { selectedProjects = selectedProjects - it }
        } else if (selectedSessions.size < 100) selectedSessions = selectedSessions + session.id
        else android.widget.Toast.makeText(context, "单次最多选择 100 个会话", android.widget.Toast.LENGTH_SHORT).show()
    }
    fun toggleProject(project: NativeProject) {
        val ids = sessions.filter { it.projectId == project.id }.map { it.id }.toSet()
        if (project.id in selectedProjects) {
            selectedProjects = selectedProjects - project.id; selectedSessions = selectedSessions - ids
        } else {
            selectedProjects = selectedProjects + project.id
            selectedSessions = (selectedSessions + ids).take(100).toSet()
        }
    }
    LaunchedEffect(sessions, projects) {
        selectedSessions = selectedSessions.intersect(sessions.map { it.id }.toSet())
        selectedProjects = selectedProjects.intersect(knownProjects)
    }
    LaunchedEffect(renameTarget, moveTarget) { modalError = null; if (renameTarget != null) renameExpanded = true; if (moveTarget != null) moveExpanded = true }
    LaunchedEffect(deleteTarget, deleteCheckEpoch) {
        if (deleteTarget != null) deleteExpanded = true
        deletePlan = null; deleteError = null
        val target = deleteTarget ?: return@LaunchedEffect
        checkingImpact = true
        try {
            deletePlan = vm.prepareDeletion(target.projects, target.sessions)
            if (deletePlan == null) deleteError = vm.error.value ?: "无法获取实际删除影响，请重试。"
        } finally { checkingImpact = false }
    }

    Column(modifier) {
        LazyColumn(Modifier.weight(1f)) {
            item("primary") {
                topContent()
                HorizontalDivider(Modifier.padding(top = 20.dp, bottom = 16.dp), color = MaterialTheme.colorScheme.outline)
                Row(Modifier.fillMaxWidth().heightIn(min = 28.dp).padding(horizontal = 12.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(1.dp)) {
                    Text("会话", fontSize = 17.sp, lineHeight = (17f * 20f / 14f).sp, letterSpacing = (-.34).sp,
                        fontWeight = FontWeight.Bold, modifier = Modifier.weight(1f))
                    if (folderKeys.isNotEmpty()) SidebarAction(if (anyFolderOpen) WebIcon.Chevron else WebIcon.ChevronRight,
                        if (anyFolderOpen) "折叠所有文件夹" else "展开所有文件夹") {
                        updateFolders(folders + folderKeys.associateWith { !anyFolderOpen })
                    }
                    SidebarAction(WebIcon.Search, "搜索文件、会话、消息或 ID") { searching = true }
                    SidebarAction(WebIcon.ListChecks, if (selectionMode) "退出批量管理" else "批量管理") {
                        selectionMode = !selectionMode
                        selectedSessions = if (selectionMode) selectable.take(100).toSet() else emptySet()
                        selectedProjects = if (selectionMode) knownProjects else emptySet()
                    }
                    SidebarAction(WebIcon.Plus, "新建对话") { onNewChat(selectedProjectId) }
                    SidebarAction(WebIcon.FolderPlus, "新建项目") { creatingProject = !creatingProject }
                }
                if (creatingProject) Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    TextField(projectName, { projectName = it.take(240) }, singleLine = true, placeholder = { Text("项目名称", fontSize = 12.sp) }, modifier = Modifier.weight(1f).webDrawerSwipeExcluded())
                    SidebarAction(WebIcon.Check, "确认新建项目", enabled = !mutating && projectName.isNotBlank()) { mutate {
                        vm.createProject(projectName.trim())?.let { project -> toggleFolder("project:${project.id}", true); onSelectProject(project.id); projectName = ""; creatingProject = false }
                    } }
                    SidebarAction(WebIcon.Close, "取消新建项目", enabled = !mutating) { creatingProject = false; projectName = "" }
                }
                if (loading && sessions.isEmpty()) CircularProgressIndicator(Modifier.padding(12.dp).size(18.dp), strokeWidth = 2.dp)
            }
            visibleProjects.forEach { project ->
                val tree = projectTrees[project.id].orEmpty()
                val projectOpen = folders["project:${project.id}"] ?: (project.id == activeProjectId)
                item("project:${project.id}") {
                    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(7.dp)).background(if (selectedProjectId == project.id) MaterialTheme.colorScheme.onSurface.copy(alpha = .06f) else Color.Transparent).padding(end = 2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(1.dp)) {
                        if (selectionMode) SidebarCheckbox(project.id in selectedProjects) { toggleProject(project) }
                        Row(Modifier.weight(1f).clickable { if (selectionMode) toggleProject(project) else { onSelectProject(project.id); toggleFolder("project:${project.id}", !projectOpen) } }.padding(horizontal = 10.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                            WebGlyph(if (projectOpen) WebIcon.FolderOpen else WebIcon.Folder, modifier = Modifier.size(14.dp).graphicsLayer { scaleX = 1.1f; scaleY = 1.1f })
                            Text(project.title, fontSize = 17.sp, lineHeight = 25.5.sp,
                                color = if (selectedProjectId == project.id) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                                modifier = Modifier.weight(1f).padding(start = 6.dp))
                            if (project.status == "archived") Text("已归档", fontSize = 10.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        }
                        if (!selectionMode) {
                            if (project.status != "archived") SidebarAction(WebIcon.Plus, "在${project.title}中新建会话") { toggleFolder("project:${project.id}", true); onNewChat(project.id) }
                            Box {
                                SidebarAction(WebIcon.More, "管理项目${project.title}") { projectMenu = project.id }
                                DropdownMenu(projectMenu == project.id, { projectMenu = null }) {
                                    if (project.status != "archived") SidebarMenu("改名", WebIcon.Edit) { projectMenu = null; renameTarget = SidebarRenameTarget(project.id, project.title, true) }
                                    SidebarMenu(if (project.status == "archived") "恢复项目" else "归档项目", WebIcon.Archive) { projectMenu = null; mutate { vm.toggleProjectArchive(project) } }
                                    HorizontalDivider()
                                    SidebarMenu("永久删除", WebIcon.Trash) { projectMenu = null; deleteTarget = SidebarDeleteTarget(listOf(project.id), emptyList()) }
                                }
                            }
                        }
                    }
                }
                if (projectOpen && project.status != "archived") {
                    val rows = NativeSidebarTree.rows(tree, folders, selectedId)
                    items(rows, key = { "project-session:${it.node.session.id}" }) { row ->
                        SidebarSessionRow(row, selectedId, selectionMode, row.node.session.id in selectedSessions || project.id in selectedProjects, sessionMenu == row.node.session.id,
                            onClick = { if (selectionMode) toggleSession(row.node.session) else { if (row.node.children.isNotEmpty()) toggleFolder("session:${row.node.session.id}", !row.open); onSelect(row.node.session) } },
                            onMenu = { sessionMenu = row.node.session.id }, onDismissMenu = { sessionMenu = null },
                            onShare = { shareTarget = row.node.session }, onRename = { renameTarget = SidebarRenameTarget(row.node.session.id, row.node.session.title, false) },
                            onPin = { mutate { vm.toggleSessionPin(row.node.session) } }, onArchive = { mutate { vm.toggleSessionArchive(row.node.session) } },
                            onMove = { moveTarget = row.node.session }, onDelete = { deleteTarget = SidebarDeleteTarget(emptyList(), listOf(row.node.session.id)) },
                            activity = activity[row.node.session.id] ?: NativeSessionActivity(), voiceActive = row.node.session.id in activitySnapshot.voiceActiveSessionIds)
                    }
                    if (rows.isEmpty()) item("empty-project:${project.id}") { TextButton(onClick = { onNewChat(project.id) }, modifier = Modifier.padding(start = 20.dp)) { Text("新建此项目的对话", fontSize = 11.sp) } }
                }
            }
            item("recent-heading") {
                Row(Modifier.fillMaxWidth().clickable { toggleFolder("section:recent", folders["section:recent"] == false) }.padding(start = 10.dp, end = 10.dp, top = 12.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text("最近", fontSize = 13.sp, lineHeight = 19.5.sp, fontWeight = FontWeight.SemiBold, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.weight(1f))
                    WebGlyph(if (folders["section:recent"] == false) WebIcon.ChevronRight else WebIcon.Chevron, modifier = Modifier.size(14.dp))
                }
            }
            items(recentRows, key = { "recent:${it.node.session.id}" }) { row ->
                SidebarSessionRow(row, selectedId, selectionMode, row.node.session.id in selectedSessions, sessionMenu == row.node.session.id,
                    onClick = { if (selectionMode) toggleSession(row.node.session) else { if (row.node.children.isNotEmpty()) toggleFolder("session:${row.node.session.id}", !row.open); onSelect(row.node.session) } },
                    onMenu = { sessionMenu = row.node.session.id }, onDismissMenu = { sessionMenu = null },
                    onShare = { shareTarget = row.node.session }, onRename = { renameTarget = SidebarRenameTarget(row.node.session.id, row.node.session.title, false) },
                    onPin = { mutate { vm.toggleSessionPin(row.node.session) } }, onArchive = { mutate { vm.toggleSessionArchive(row.node.session) } },
                    onMove = { moveTarget = row.node.session }, onDelete = { deleteTarget = SidebarDeleteTarget(emptyList(), listOf(row.node.session.id)) },
                    activity = activity[row.node.session.id] ?: NativeSessionActivity(), voiceActive = row.node.session.id in activitySnapshot.voiceActiveSessionIds)
            }
            if (!loading && active.isEmpty() && projects.isEmpty()) item("empty") { Text("还没有会话", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(16.dp)) }
            if (archived.isNotEmpty()) item("archived-toggle") { TextButton(onClick = { showArchived = !showArchived }) { Text(if (showArchived) "收起已归档会话" else "已归档会话（${archived.size}）", fontSize = 11.sp) } }
            if (showArchived) items(archived, key = { "archived:${it.id}" }) { session ->
                Row(Modifier.fillMaxWidth().padding(start = 13.dp, end = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(session.title, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f).clickable { onSelect(session) })
                    TextButton(onClick = { mutate { vm.toggleSessionArchive(session) } }, enabled = !mutating) { Text("恢复", fontSize = 11.sp) }
                }
            }
        }
        if (selectionMode) Column(Modifier.fillMaxWidth().padding(horizontal = 7.dp, vertical = 4.dp)) {
            Text("$selectedCount 个已选 · ${selectedProjects.size} 项目 · ${standalone.size} 会话", fontSize = 11.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                TextButton(onClick = { val allSelected = selectable.take(100).all { it in selectedSessions } && knownProjects.all { it in selectedProjects }; selectedSessions = if (allSelected) emptySet() else selectable.take(100).toSet(); selectedProjects = if (allSelected) emptySet() else knownProjects }) { Text("全选 / 取消", fontSize = 11.sp) }
                TextButton(onClick = { deleteTarget = SidebarDeleteTarget(selectedProjects.toList(), standalone) }, enabled = selectedCount > 0 && !deleting) { Text("删除所选", fontSize = 11.sp, color = MaterialTheme.colorScheme.error) }
            }
        }
    }
    renameTarget?.let { target -> NativeRenameDialog(target.title, if (target.project) "修改项目名称" else "修改会话名称", mutating, error = modalError,
        onDismiss = { renameTarget = null }, expanded = renameExpanded, onClosed = { renameTarget = null }, onConfirm = { title ->
            mutate { if (if (target.project) vm.renameProject(target.id, title) else vm.renameSession(target.id, title)) renameExpanded = false else modalError = vm.error.value }
        }) }
    moveTarget?.let { session -> AlertDialog(expanded = moveExpanded, onClosed = { moveTarget = null },
        properties = DialogProperties(dismissOnBackPress = !mutating, dismissOnClickOutside = !mutating),
        onDismissRequest = { if (!mutating) moveTarget = null }, title = { Text("移动会话") }, text = {
        Column(Modifier.heightIn(max = 360.dp).verticalScroll(rememberScrollState())) {
            if (session.projectId != null) TextButton(onClick = { mutate { if (vm.moveSession(session.id, null)) moveExpanded = false else modalError = vm.error.value } }, enabled = !mutating) { Text("移出项目") }
            projects.filter { it.status != "archived" && it.id != session.projectId }.forEach { project -> TextButton(onClick = { mutate { if (vm.moveSession(session.id, project.id)) moveExpanded = false else modalError = vm.error.value } }, enabled = !mutating) { Text(project.title) } }
            if (projects.none { it.status != "archived" && it.id != session.projectId } && session.projectId == null) Text("请先新建一个项目", fontSize = 13.sp)
            if (modalError != null) Text(modalError.orEmpty(), fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
        }
    }, confirmButton = {}, dismissButton = { TextButton(onClick = rememberWebDialogCloseAction { moveTarget = null }, enabled = !mutating) { Text("取消") } }) }
    shareTarget?.let { session -> NativeSessionShareDialog(vm, session) { shareTarget = null } }
    if (searching) NativeWorkspaceSearchDialog(vm, onSelectSession = onSearchSession, onNavigate = onNavigate, onDismiss = { searching = false })
    if (deleteTarget != null) NativeSidebarDeleteDialog(deletePlan, checkingImpact, deleting, deleteError, onDismiss = { deleteTarget = null }, expanded = deleteExpanded,
        onClosed = { deleteTarget = null; deletePlan = null }, onRetry = {
        deleteCheckEpoch++
    }, onConfirm = {
        val plan = deletePlan ?: return@NativeSidebarDeleteDialog
        deleting = true
        scope.launch { try {
            val success = vm.executeDeletion(plan)
            if (selectedId != null && vm.sessions.value.none { it.id == selectedId }) onDeletedCurrent()
            deleteExpanded = false
            if (success) { selectedSessions = emptySet(); selectedProjects = emptySet(); selectionMode = false }
        } finally { deleting = false } }
    })
}

private fun readNativeFolders(raw: String?): Map<String, Boolean> = runCatching {
    Json.parseToJsonElement(raw ?: "{}").jsonObject.mapNotNull { (key, value) -> value.jsonPrimitive.booleanOrNull?.let { key to it } }.toMap()
}.getOrDefault(emptyMap())

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun SidebarSessionRow(row: NativeSidebarRow, selectedId: String?, selectionMode: Boolean, checked: Boolean, menuOpen: Boolean,
    onClick: () -> Unit, onMenu: () -> Unit, onDismissMenu: () -> Unit, onShare: () -> Unit, onRename: () -> Unit, onPin: () -> Unit, onArchive: () -> Unit, onMove: () -> Unit, onDelete: () -> Unit,
    activity: NativeSessionActivity = NativeSessionActivity(), voiceActive: Boolean = false) {
    val session = row.node.session
    val baseConfiguration = LocalViewConfiguration.current
    val configuration = remember(baseConfiguration) { object : ViewConfiguration by baseConfiguration { override val longPressTimeoutMillis: Long = 500 } }
    val noHaptics = remember { object : HapticFeedback { override fun performHapticFeedback(hapticFeedbackType: HapticFeedbackType) {} } }
    CompositionLocalProvider(LocalHapticFeedback provides noHaptics, LocalViewConfiguration provides configuration) {
    Row(Modifier.fillMaxWidth().clip(RoundedCornerShape(7.dp)).background(if (selectedId == session.id) MaterialTheme.colorScheme.onSurface.copy(alpha = .06f) else Color.Transparent)
        .combinedClickable(interactionSource = remember { MutableInteractionSource() }, indication = null, onClick = onClick, onLongClick = { if (!selectionMode) onMenu() })
        .padding(start = 13.dp, end = 5.dp, top = 7.dp, bottom = 7.dp), verticalAlignment = Alignment.CenterVertically) {
        if (selectionMode) SidebarCheckbox(checked) { onClick() }
        if (activity.unreadCompleted) Box(Modifier.padding(end = 6.dp).size(7.dp).background(
            if (MaterialTheme.colorScheme.background == Color.White) Color(0xFF171717) else MaterialTheme.colorScheme.onSurfaceVariant, CircleShape)
            .semantics { contentDescription = "模型回复已完成" })
        Text(row.title.ifBlank { "新对话" }, fontSize = 16.sp, lineHeight = 21.6.sp, fontWeight = FontWeight.Normal, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
        if (voiceActive) Box(Modifier.padding(start = 5.dp).size(14.dp).background(Color(0xFF536FE1), CircleShape)
            .semantics { contentDescription = "语音导师已开启" }, contentAlignment = Alignment.Center) { Text("◉", fontSize = 8.sp, lineHeight = 8.sp, color = Color.White) }
        if (activity.running) {
            CompositionLocalProvider(LocalContentColor provides Color(0xFF716BC5)) {
                WebSpinner(Modifier.padding(start = 4.dp).semantics { contentDescription = "生成中" })
            }
        }
        if (!selectionMode) {
            Box {
                // Mobile CSS keeps this hitbox's width but hides the icon; 500ms long press opens its menu.
                Spacer(Modifier.size(24.dp))
                DropdownMenu(menuOpen, onDismissMenu) {
                    SidebarMenu("分享", WebIcon.Share2) { onDismissMenu(); onShare() }
                    SidebarMenu("重命名", WebIcon.Edit) { onDismissMenu(); onRename() }
                    SidebarMenu(if (session.pinned == true) "取消置顶" else "置顶聊天", WebIcon.Pin) { onDismissMenu(); onPin() }
                    SidebarMenu("归档", WebIcon.Archive) { onDismissMenu(); onArchive() }
                    HorizontalDivider()
                    SidebarMenu("移动到", WebIcon.Folder) { onDismissMenu(); onMove() }
                    HorizontalDivider()
                    SidebarMenu("删除会话", WebIcon.Trash) { onDismissMenu(); onDelete() }
                }
            }
        }
    }
    }
}

@Composable
private fun SidebarCheckbox(checked: Boolean, onChange: () -> Unit) {
    Box(Modifier.padding(end = 8.dp).size(16.dp).clip(RoundedCornerShape(4.dp)).background(if (checked) MaterialTheme.colorScheme.primary else Color.Transparent)
        .border(1.dp, if (checked) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline, RoundedCornerShape(4.dp))
        .toggleable(checked, role = Role.Checkbox, onValueChange = { onChange() }), contentAlignment = Alignment.Center) {
        if (checked) WebGlyph(WebIcon.Check, modifier = Modifier.size(12.dp), color = MaterialTheme.colorScheme.onPrimary)
    }
}

@Composable
private fun SidebarAction(icon: WebIcon, description: String, enabled: Boolean = true, iconSize: Int = 14, onClick: () -> Unit) {
    Box(Modifier.size(24.dp).clip(RoundedCornerShape(7.dp)).webClickFeedback(enabled = enabled, hasPopup = icon == WebIcon.More, onClick = onClick).semantics { contentDescription = description }, contentAlignment = Alignment.Center) {
        WebGlyph(icon, modifier = Modifier.size(iconSize.dp).graphicsLayer { scaleX = 1.1f; scaleY = 1.1f }, color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = if (enabled) 1f else .4f))
    }
}

@Composable
private fun SidebarMenu(label: String, icon: WebIcon, onClick: () -> Unit) = DropdownMenuItem(text = { Text(label, fontSize = 12.sp) }, leadingIcon = { WebGlyph(icon, modifier = Modifier.size(14.dp)) }, onClick = onClick)
