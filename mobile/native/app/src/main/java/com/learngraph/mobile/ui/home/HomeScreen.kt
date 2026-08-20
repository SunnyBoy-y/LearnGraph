package com.learngraph.mobile.ui.home

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
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.ExitToApp
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.AlertDialog
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.learngraph.mobile.data.Session
import com.learngraph.mobile.ui.theme.BrandBlue
import com.learngraph.mobile.ui.theme.BrandViolet
import kotlinx.coroutines.launch

/**
 * 会话列表页：新建/删除/刷新会话，右上菜单含退出登录。
 * 异形屏：TopAppBar 顶部用 statusBars inset，底部 FAB 避让 navigationBars。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun HomeScreen(
    vm: HomeViewModel,
    onOpenChat: (String) -> Unit,
    onLogout: () -> Unit,
) {
    val sessions by vm.sessions.collectAsState()
    val loading by vm.loading.collectAsState()
    val error by vm.error.collectAsState()
    val scope = rememberCoroutineScope()

    var menuOpen by remember { mutableStateOf(false) }
    var deleteTarget by remember { mutableStateOf<Session?>(null) }
    var deleteConfirm by remember { mutableStateOf<String?>(null) }
    var deleting by remember { mutableStateOf(false) }

    Scaffold(
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
        topBar = {
            TopAppBar(
                title = { Text("会话") },
                navigationIcon = {
                    IconButton(onClick = { vm.refresh() }) {
                        Icon(Icons.Default.Refresh, contentDescription = "刷新")
                    }
                },
                actions = {
                    IconButton(onClick = { menuOpen = true }) {
                        Icon(Icons.Default.MoreVert, contentDescription = "菜单")
                    }
                    DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                        DropdownMenuItem(
                            text = { Text("退出登录") },
                            leadingIcon = { Icon(Icons.Default.ExitToApp, null) },
                            onClick = {
                                menuOpen = false
                                vm.logout()
                                onLogout()
                            },
                        )
                    }
                },
                modifier = Modifier.windowInsetsPadding(WindowInsets.statusBars),
            )
        },
        floatingActionButton = {
            FloatingActionButton(
                onClick = {
                    scope.launch {
                        val s = vm.createSession()
                        if (s != null) onOpenChat(s.id)
                    }
                },
            ) {
                Icon(Icons.Default.Add, contentDescription = "新建会话")
            }
        },
    ) { padding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .windowInsetsPadding(WindowInsets.navigationBars),
        ) {
            when {
                loading && sessions.isEmpty() -> {
                    CircularProgressIndicator(modifier = Modifier.align(Alignment.Center))
                }
                sessions.isEmpty() && !loading -> {
                    Column(
                        modifier = Modifier.align(Alignment.Center),
                        horizontalAlignment = Alignment.CenterHorizontally,
                    ) {
                        Text("还没有会话", style = MaterialTheme.typography.titleMedium)
                        Spacer(Modifier.height(8.dp))
                        Text(
                            "点右下角 + 新建一个对话",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                else -> {
                    LazyColumn(
                        modifier = Modifier.fillMaxSize(),
                        contentPadding = PaddingValues(vertical = 8.dp),
                    ) {
                        items(sessions, key = { it.id }) { session ->
                            SessionRow(
                                session = session,
                                onClick = { onOpenChat(session.id) },
                                onDelete = { deleteTarget = session },
                            )
                        }
                    }
                }
            }

            if (error != null && sessions.isNotEmpty()) {
                Text(
                    text = error.orEmpty(),
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier
                        .align(Alignment.BottomCenter)
                        .padding(16.dp),
                )
            }
        }
    }

    // 删除确认流程：先查 impact 文案，再确认删除
    deleteTarget?.let { target ->
        val confirmText = deleteConfirm
        AlertDialog(
            onDismissRequest = { deleteTarget = null; deleteConfirm = null },
            title = { Text("删除会话") },
            text = {
                if (confirmText == null) {
                    Text("正在查询删除影响…")
                } else {
                    Text(confirmText)
                }
            },
            confirmButton = {
                androidx.compose.material3.TextButton(
                    enabled = confirmText != null && !deleting,
                    onClick = {
                        deleting = true
                        scope.launch {
                            vm.deleteSession(target.id, confirmText.orEmpty())
                            deleting = false
                            deleteTarget = null
                            deleteConfirm = null
                        }
                    },
                ) {
                    Text("删除")
                }
            },
            dismissButton = {
                androidx.compose.material3.TextButton(onClick = { deleteTarget = null; deleteConfirm = null }) {
                    Text("取消")
                }
            },
        )
    }

    // 查询删除影响
    androidx.compose.runtime.LaunchedEffect(deleteTarget?.id) {
        deleteConfirm = null
        deleteTarget?.let { t ->
            deleteConfirm = vm.deleteImpact(t.id)
        }
    }
}

@Composable
private fun SessionRow(
    session: Session,
    onClick: () -> Unit,
    onDelete: () -> Unit,
) {
    Card(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 5.dp)
            .clickable(onClick = onClick),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        elevation = CardDefaults.cardElevation(defaultElevation = 1.dp),
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 14.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // 会话图标（首字母徽标）
            Box(
                modifier = Modifier
                    .size(38.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(
                        Brush.linearGradient(listOf(BrandBlue, BrandViolet)),
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Text(
                    text = session.title.ifBlank { "新" }.firstOrNull()?.uppercase() ?: "新",
                    color = Color.White,
                    style = MaterialTheme.typography.titleMedium,
                )
            }
            Spacer(Modifier.width(12.dp))
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = session.title.ifBlank { "新会话" },
                    style = MaterialTheme.typography.bodyLarge,
                    maxLines = 1,
                )
                session.updatedAt?.let {
                    Spacer(Modifier.height(2.dp))
                    Text(
                        text = it.replace("T", " ").take(16),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            IconButton(onClick = onDelete) {
                Icon(
                    Icons.Default.Delete,
                    contentDescription = "删除",
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
