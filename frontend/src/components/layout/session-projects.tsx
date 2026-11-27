import { useEffect, useMemo, useRef, useState, useSyncExternalStore, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from 'react'
import { Archive, Folder, FolderOpen, ListChecks, LoaderCircle, MoreHorizontal, Pencil, Pin, Plus, Save, Share2, Trash2, X } from 'lucide-react'
import { toast } from '@/lib/notifications'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuPortal, DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent, DropdownMenuSubTrigger, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { cn } from '@/lib/utils'
import { applyRangeSelection, useEscapeKey } from '@/lib/list-selection'
import { withScrollLockBypass } from '@/lib/scroll-lock'
import { isVoiceSessionActive } from '@/features/voice/voice-session-markers'
import { getSessionActivity, getSessionActivitySnapshot, sortSidebarSessions, subscribeSessionActivity } from '@/lib/session-activity'
import { sidebarFolderStateKey, type SidebarFolderState, type SidebarFolderKind, type SidebarFolderRef } from '@/lib/sidebar-folders'
import { sessionScopeId } from '@/lib/session-queries'
import type { SessionScope } from '@/api/session-browser'
import type { SessionNavigator } from './use-session-navigator'
import { VirtualSessionList, SessionPageSentinel } from './virtual-session-list'
import { branchRowDisplayTitle, flattenSidebarSessions, legacyBranchSiblingIndex, type SidebarSession, type SidebarProject } from './session-sidebar-model'
function findProjectForSession(projects: SidebarProject[], id: string) { return projects.find(project => flattenSidebarSessions(project.sessions).some(item => item.id === id)) }

const MAX_SELECTED_SESSIONS = 100;

export function SessionProjects({
  navigator,
  workspaceId,
  activeSessionId,
  creatingConversation,
  folderState,
  selectedProjectId,
  onToggleFolder,
  onToggleFolders,
  onCreateConversation,
  onCreateProject,
  onRequestProjectDeletion,
  onRenameProject,
  onToggleProjectArchive,
  onMoveSession,
  onRequestSessionBatchDeletion,
  onRequestMixedBatchDeletion,
  onBatchArchive,
  onBatchMoveSessions,
  onBatchToggleSessionPin,
  onRequestSessionDeletion,
  onRenameSession,
  onToggleSessionPin,
  onToggleSessionArchive,
  onShareSession,
  onSelectProject,
  onSelectSession,
  projects,
  ungroupedSessions,
}: {
  navigator: SessionNavigator;
  workspaceId: string;
  activeSessionId: string;
  creatingConversation: boolean;
  /** 项目和父会话文件夹共用的前端本地开合状态。 */
  folderState: SidebarFolderState;
  /** 当前新建会话要落入的项目文件夹。 */
  selectedProjectId?: string;
  /** 统一写入某一种文件夹的开合状态。 */
  onToggleFolder: (
    kind: SidebarFolderKind,
    id: string,
    open: boolean,
  ) => void;
  onToggleFolders: (folders: SidebarFolderRef[], open: boolean) => void;
  onCreateConversation: (projectId?: string | null) => Promise<void>;
  onCreateProject: (title: string) => Promise<string>;
  onRequestProjectDeletion: (project: SidebarProject) => void;
  onRenameProject: (project: SidebarProject) => void;
  onToggleProjectArchive: (project: SidebarProject) => void;
  onMoveSession: (sessionId: string, projectId?: string) => void;
  onRequestSessionBatchDeletion: (sessions: SidebarSession[]) => void;
  onRequestMixedBatchDeletion: (
    projects: SidebarProject[],
    sessions: SidebarSession[],
  ) => void;
  onBatchArchive?: (projects: SidebarProject[], sessions: SidebarSession[]) => Promise<void> | void;
  onBatchMoveSessions?: (sessions: SidebarSession[], projectId?: string) => Promise<void> | void;
  onBatchToggleSessionPin?: (sessions: SidebarSession[], pinned: boolean) => Promise<void> | void;
  onRequestSessionDeletion: (session: SidebarSession) => void;
  onRenameSession: (session: SidebarSession, title: string) => Promise<boolean>;
  onToggleSessionPin: (session: SidebarSession) => void;
  onToggleSessionArchive: (session: SidebarSession) => void;
  onShareSession: (session: SidebarSession) => void;
  onSelectProject: (projectId?: string) => void;
  onSelectSession: (sessionId: string, project?: SidebarProject) => void;
  projects: SidebarProject[];
  ungroupedSessions: SidebarSession[];
}) {
  const [voiceMarkerVersion, setVoiceMarkerVersion] = useState(0);
  useEffect(() => {
    const refresh = () => setVoiceMarkerVersion((value) => value + 1);
    window.addEventListener("learngraph:voice-session-marker", refresh);
    window.addEventListener("storage", refresh);
    return () => {
      window.removeEventListener("learngraph:voice-session-marker", refresh);
      window.removeEventListener("storage", refresh);
    };
  }, []);
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [renamingSessionId, setRenamingSessionId] = useState("");
  const [renameValue, setRenameValue] = useState("");
  const [renamePending, setRenamePending] = useState(false);
  const [selectionMode, setSelectionMode] = useState(false);
  const [selectedSessionIds, setSelectedSessionIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [selectedProjectIds, setSelectedProjectIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [batchBusy, setBatchBusy] = useState(false);
  const sessionAnchorRef = useRef<string | null>(null);
  const sessionShiftRef = useRef(false);
  // 会话「更多」菜单受控态（长按/右键唤起，与 MoreHorizontal 触发共用）
  const [menuOpenFor, setMenuOpenFor] = useState<string | null>(null);
  const [projectMenuOpenFor, setProjectMenuOpenFor] = useState<string | null>(null);
  const longPressRef = useRef<{
    timer: number | null;
    id: string | null;
    fired: boolean;
    x: number;
    y: number;
  }>({ timer: null, id: null, fired: false, x: 0, y: 0 });
  const beginLongPress =
    (sessionId: string) => (event: ReactPointerEvent<HTMLElement>) => {
      if (event.pointerType === "mouse" && event.button !== 0) return;
      const lp = longPressRef.current;
      lp.id = sessionId;
      lp.fired = false;
      lp.x = event.clientX;
      lp.y = event.clientY;
      if (lp.timer != null) window.clearTimeout(lp.timer);
      lp.timer = window.setTimeout(() => {
        lp.fired = true;
        setMenuOpenFor(sessionId);
      }, 500);
    };
  const cancelLongPress = () => {
    const lp = longPressRef.current;
    if (lp.timer != null) {
      window.clearTimeout(lp.timer);
      lp.timer = null;
    }
    lp.id = null;
  };
  const sessionActivity = useSyncExternalStore(
    subscribeSessionActivity,
    getSessionActivitySnapshot,
    getSessionActivitySnapshot,
  );
  // Keep at most the selected 100 records when a bidirectional page is evicted.
  // Leaving the render window or search page must never silently deselect them.
  const selectedRecords = useRef(new Map<string, SidebarSession>());
  const allSessions = useMemo(() => {
    const loaded = [...projects.flatMap(project => flattenSidebarSessions(project.sessions)), ...flattenSidebarSessions(ungroupedSessions)];
    const records = selectedRecords.current;
    for (const id of records.keys()) if (!selectedSessionIds.has(id)) records.delete(id);
    for (const session of loaded) if (selectedSessionIds.has(session.id)) records.set(session.id, session);
    return [...new Map([...records.values(), ...loaded].map(session => [session.id, session])).values()];
  }, [projects, ungroupedSessions, selectedSessionIds]);
  useEffect(() => {
    const deleted = (event: Event) => {
      const detail = (event as CustomEvent<{ workspaceId: string; sessionIds: string[]; projectIds: string[] }>).detail;
      if (detail?.workspaceId !== workspaceId) return;
      const sessions = new Set(detail.sessionIds), projects = new Set(detail.projectIds);
      const removed = new Set(detail.sessionIds);
      for (const [id, record] of selectedRecords.current) {
        if (sessions.has(id) || (record.project_id && projects.has(record.project_id))) {
          selectedRecords.current.delete(id); removed.add(id);
        }
      }
      setSelectedSessionIds(previous => new Set([...previous].filter(id => !removed.has(id))));
      setSelectedProjectIds(previous => new Set([...previous].filter(id => !projects.has(id))));
    };
    window.addEventListener('learngraph:sessions-deleted', deleted);
    return () => window.removeEventListener('learngraph:sessions-deleted', deleted);
  }, [workspaceId]);
  const projectsOpen =
    folderState[sidebarFolderStateKey("section", "projects")] !== false;
  const recentOpen =
    folderState[sidebarFolderStateKey("section", "recent")] !== false;
  const visibleProjects = useMemo(
    () =>
      projects.map((project) => ({
        ...project, sessions: sortSidebarSessions(project.sessions, sessionActivity),
      })),
    [projects, sessionActivity],
  );
  const visibleUngroupedSessions = useMemo(
    () =>
      sortSidebarSessions(ungroupedSessions, sessionActivity),
    [sessionActivity, ungroupedSessions],
  );
  const activeProjectId = useMemo(
    () => findProjectForSession(projects, activeSessionId)?.id,
    [activeSessionId, projects],
  );
  const projectFolderOpen = useMemo(() => {
    const open: Record<string, boolean> = {};
    for (const project of projects) {
      const stored = folderState[sidebarFolderStateKey("project", project.id)];
      // A currently viewed project's folder is revealed until the user makes
      // an explicit choice. An explicit local false always wins.
      open[project.id] =
        typeof stored === "boolean"
          ? stored
          : project.id === activeProjectId;
    }
    return open;
  }, [activeProjectId, folderState, projects]);
  const sessionFolderOpen = useMemo(() => {
    const open: Record<string, boolean> = {};
    const sessions = [
      ...projects.flatMap((project) => flattenSidebarSessions(project.sessions)),
      ...flattenSidebarSessions(ungroupedSessions),
    ];
    for (const session of sessions) {
      if (!session.has_children && !session.children?.length) continue;
      const stored = folderState[sidebarFolderStateKey("session", session.id)];
      const childActive = flattenSidebarSessions(session.children ?? []).some(
        (child) => child.id === activeSessionId,
      );
      open[session.id] =
        typeof stored === "boolean" ? stored : childActive;
    }
    return open;
  }, [activeSessionId, folderState, projects, ungroupedSessions]);
  const retainScopes = navigator.retainScopes;
  useEffect(() => {
    const scopes = new Set<string>();
    const retainChildren = (sessions: SidebarSession[], seen = new Set<string>()) => {
      for (const session of sessions) {
        if (seen.has(session.id) || !sessionFolderOpen[session.id]) continue;
        seen.add(session.id);
        scopes.add(`session:${session.id}`);
        retainChildren(session.children ?? [], seen);
      }
    };
    if (recentOpen) { scopes.add('recent'); retainChildren(ungroupedSessions); }
    for (const project of projects) {
      if (!projectsOpen || !projectFolderOpen[project.id] || project.status === 'archived') continue;
      scopes.add(`project:${project.id}`);
      retainChildren(project.sessions);
    }
    retainScopes(scopes);
  }, [retainScopes, projectFolderOpen, projects, projectsOpen, recentOpen, sessionFolderOpen, ungroupedSessions]);
  const folderEntries = useMemo(
    () => [
      ...projects.map((project) => ({
        kind: "project" as const,
        id: project.id,
      })),
      ...projects
        .flatMap((project) => flattenSidebarSessions(project.sessions))
        .filter((session) => session.has_children || session.children?.length)
        .map((session) => ({ kind: "session" as const, id: session.id })),
      ...flattenSidebarSessions(ungroupedSessions)
        .filter((session) => session.has_children || session.children?.length)
        .map((session) => ({ kind: "session" as const, id: session.id })),
    ],
    [projects, ungroupedSessions],
  );
  const allFoldersCollapsed =
    folderEntries.length > 0 &&
    folderEntries.every((folder) => {
      if (folder.kind === "project") return !projectFolderOpen[folder.id];
      return !sessionFolderOpen[folder.id];
    });
  function toggleAllFolders() {
    const open = allFoldersCollapsed;
    onToggleFolders(folderEntries, open);
  }
  const orderedVisibleSessionIds = useMemo(
    () => [
      ...visibleProjects.flatMap((project) =>
        flattenSidebarSessions(project.sessions).map((session) => session.id),
      ),
      ...flattenSidebarSessions(visibleUngroupedSessions).map(
        (session) => session.id,
      ),
    ],
    [visibleProjects, visibleUngroupedSessions],
  );
  const visibleSessionIds = useMemo(
    () => new Set(orderedVisibleSessionIds),
    [orderedVisibleSessionIds],
  );
  const visibleProjectIds = useMemo(
    () => new Set(visibleProjects.map((project) => project.id)),
    [visibleProjects],
  );
  const selectedProjects = projects.filter((project) =>
    selectedProjectIds.has(project.id),
  );
  // Sessions nested under a selected project are covered by project deletion.
  const coveredBySelectedProjects = useMemo(
    () =>
      new Set(
        selectedProjects.flatMap((project) =>
          flattenSidebarSessions(project.sessions).map((session) => session.id),
        ),
      ),
    [selectedProjects],
  );
  const selectedStandaloneSessions = allSessions.filter(
    (session) =>
      selectedSessionIds.has(session.id) &&
      !coveredBySelectedProjects.has(session.id) &&
      !(session.project_id && selectedProjectIds.has(session.project_id)),
  );
  const selectedCount =
    selectedProjects.length + selectedStandaloneSessions.length;

  useEffect(() => {
    const availableSessions = new Set(allSessions.map((session) => session.id));
    const availableProjects = new Set(projects.map((project) => project.id));
    setSelectedSessionIds((current) => {
      const next = new Set(
        [...current].filter((id) => availableSessions.has(id)),
      );
      return next.size === current.size ? current : next;
    });
    setSelectedProjectIds((current) => {
      const next = new Set(
        [...current].filter((id) => availableProjects.has(id)),
      );
      return next.size === current.size ? current : next;
    });
    if (!allSessions.length && !projects.length) setSelectionMode(false);
  }, [allSessions, projects]);

  function beginSelection(project?: SidebarProject) {
    cancelLongPress();
    longPressRef.current.fired = false;
    setMenuOpenFor(null);
    setProjectMenuOpenFor(null);
    setCreatingProject(false);
    selectedRecords.current.clear();
    setSelectedProjectIds(new Set(project ? [project.id] : []));
    setSelectedSessionIds(new Set(
      project ? flattenSidebarSessions(project.sessions).slice(0, MAX_SELECTED_SESSIONS).map(session => session.id) : [],
    ));
    setSelectionMode(true);
  }

  function exitSelection() {
    setSelectionMode(false);
    setSelectedSessionIds(new Set());
    setSelectedProjectIds(new Set());
    selectedRecords.current.clear();
    sessionAnchorRef.current = null;
  }

  useEscapeKey(selectionMode, exitSelection);

  // Only loaded records are selectable here; no cross-page selection API exists.
  // At the session limit, the same control must still allow deselecting.
  const allLoadedSelected = selectedCount > 0 &&
    [...visibleProjectIds].every(id => selectedProjectIds.has(id)) &&
    ([...visibleSessionIds].every(id => selectedSessionIds.has(id) || coveredBySelectedProjects.has(id)) ||
      selectedSessionIds.size >= MAX_SELECTED_SESSIONS);

  function toggleAllLoadedSelection() {
    if (allLoadedSelected) {
      setSelectedSessionIds(new Set());
      setSelectedProjectIds(new Set());
      selectedRecords.current.clear();
      return;
    }
    setSelectedSessionIds(current => {
      const next = new Set(current);
      for (const id of visibleSessionIds) {
        if (next.size >= MAX_SELECTED_SESSIONS) break;
        next.add(id);
      }
      return next;
    });
    setSelectedProjectIds(current => new Set([...current, ...visibleProjectIds]));
  }

  function toggleSessionSelection(sessionId: string, shiftKey?: boolean) {
    const useShift = shiftKey ?? sessionShiftRef.current;
    sessionShiftRef.current = false;
    const parent = projects.find((project) =>
      flattenSidebarSessions(project.sessions).some(
        (session) => session.id === sessionId,
      ),
    );
    const coveredByProject = parent && selectedProjectIds.has(parent.id);
    const currentlySelected = selectedSessionIds.has(sessionId) || Boolean(coveredByProject);
    const nextChecked = !currentlySelected;
    if (useShift && sessionAnchorRef.current) {
      setSelectedSessionIds((current) => {
        const range = applyRangeSelection(
          current,
          orderedVisibleSessionIds,
          sessionAnchorRef.current,
          sessionId,
          nextChecked,
        );
        if (range.size <= MAX_SELECTED_SESSIONS) return range;
        const clamped = new Set(current);
        for (const id of range) {
          if (clamped.size >= MAX_SELECTED_SESSIONS) break;
          clamped.add(id);
        }
        return clamped;
      });
    } else {
      setSelectedSessionIds((current) => {
        const next = new Set(current);
        if (currentlySelected) next.delete(sessionId);
        else if (next.size < MAX_SELECTED_SESSIONS) next.add(sessionId);
        return next;
      });
    }
    sessionAnchorRef.current = sessionId;
    // Deselect a project folder if any of its sessions is unchecked.
    if (coveredByProject) {
      setSelectedProjectIds((current) => {
        const next = new Set(current);
        next.delete(parent.id);
        return next;
      });
    }
  }

  function toggleProjectSelection(project: SidebarProject) {
    const selecting = !selectedProjectIds.has(project.id);
    const sessionIds = new Set(flattenSidebarSessions(project.sessions).map(session => session.id));
    if (!selecting) {
      for (const session of allSessions) {
        if (session.project_id === project.id) sessionIds.add(session.id);
      }
    }
    setSelectedProjectIds((current) => {
      const next = new Set(current);
      if (selecting) next.add(project.id);
      else next.delete(project.id);
      return next;
    });
    // Keep state updaters pure; React may replay them in StrictMode.
    setSelectedSessionIds((sessions) => {
      const next = new Set(sessions);
      for (const id of sessionIds) {
        if (selecting) {
          if (next.size < MAX_SELECTED_SESSIONS) next.add(id);
        } else {
          next.delete(id);
        }
      }
      return next;
    });
  }

  const selectedSessionsForActions = useMemo(() => {
    const map = new Map<string, SidebarSession>();
    for (const session of selectedStandaloneSessions) map.set(session.id, session);
    for (const project of selectedProjects) {
      for (const session of flattenSidebarSessions(project.sessions)) {
        map.set(session.id, session);
      }
    }
    return [...map.values()];
  }, [selectedProjects, selectedStandaloneSessions]);

  const anySelectedSessionUnpinned = selectedSessionsForActions.some(
    (session) => !session.pinned,
  );

  async function runBatchArchive() {
    if (!onBatchArchive || !selectedCount || batchBusy) return;
    setBatchBusy(true);
    try {
      await onBatchArchive(selectedProjects, selectedStandaloneSessions);
      exitSelection();
    } finally {
      setBatchBusy(false);
    }
  }

  async function runBatchMove(projectId?: string) {
    if (!onBatchMoveSessions || !selectedSessionsForActions.length || batchBusy) return;
    setBatchBusy(true);
    try {
      await onBatchMoveSessions(selectedSessionsForActions, projectId);
      exitSelection();
    } finally {
      setBatchBusy(false);
    }
  }

  async function runBatchPin() {
    if (!onBatchToggleSessionPin || !selectedSessionsForActions.length || batchBusy) return;
    setBatchBusy(true);
    try {
      await onBatchToggleSessionPin(selectedSessionsForActions, anySelectedSessionUnpinned);
      exitSelection();
    } finally {
      setBatchBusy(false);
    }
  }

  function requestSelectedDeletion() {
    if (selectedProjects.length) {
      onRequestMixedBatchDeletion(
        selectedProjects,
        selectedStandaloneSessions,
      );
      return;
    }
    if (selectedStandaloneSessions.length) {
      onRequestSessionBatchDeletion(selectedStandaloneSessions);
    }
  }
  async function addProject() {
    const title = projectName.trim();
    if (!title) return;
    try {
      const id = await onCreateProject(title);
      // 刚建的空文件夹先展开，方便直接往里加对话；折叠回去同样会
      // 写入前端本地偏好。
      onToggleFolder("project", id, true);
      onSelectProject(id);
      setProjectName("");
      setCreatingProject(false);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "新建项目失败");
    }
  }

  function beginSessionRename(session: SidebarSession) {
    setRenamingSessionId(session.id);
    setRenameValue(session.title);
  }

  function cancelSessionRename() {
    if (renamePending) return;
    setRenamingSessionId("");
    setRenameValue("");
  }

  async function commitSessionRename(session: SidebarSession) {
    const title = renameValue.trim();
    if (!title) return;
    setRenamePending(true);
    const saved = await onRenameSession(session, title);
    setRenamePending(false);
    if (saved) {
      setRenamingSessionId("");
      setRenameValue("");
    }
  }

  function toggleSessionFolder(sessionId: string) {
    const current = sessionFolderOpen[sessionId] ?? false;
    onToggleFolder("session", sessionId, !current);
  }

  function renderSessionRow(
    session: SidebarSession,
    options: {
      project?: SidebarProject;
      ungrouped?: boolean;
      depth?: number;
      /** 1-based index among legacy 「分支.」 siblings; display only. */
      legacyBranchIndex?: number;
    } = {},
  ) {
    const depth = options.depth ?? 0;
    // Nested rows share their parent's indent, so branches created before
    // the backend wrote 「原会话名（n）」 titles are shown with the sibling
    // index instead of the legacy 「分支.」 prefix.
    const displayTitle = branchRowDisplayTitle(
      session.title,
      options.legacyBranchIndex,
    );
    const children = session.children ?? [];
    const hasChildren = session.has_children || children.length > 0;
    const open = sessionFolderOpen[session.id] ?? false;
    const childActive =
      hasChildren &&
      flattenSidebarSessions(children).some(
        (child) => child.id === activeSessionId,
      );
    // An explicit local false wins over active-child convenience expansion.
    const hasStoredOpen =
      typeof folderState[sidebarFolderStateKey("session", session.id)] ===
      "boolean";
    const effectivelyOpen = hasStoredOpen
        ? open
        : open || childActive;
    // Read the marker during render so active voice sessions are identifiable
    // even after navigating away from the conversation route.
    void voiceMarkerVersion;
    const voiceActive = isVoiceSessionActive(workspaceId, session.id);
    const activity = getSessionActivity(session.id);

    return (
      <div className="sidebar-session-tree" key={session.id}>
        <div
          className={cn(
            "sidebar-project__session group",
            options.ungrouped && depth === 0 && "sidebar-project__session--ungrouped",
            depth > 0 && "sidebar-project__session--nested",
            hasChildren && "sidebar-project__session--folder",
            activeSessionId === session.id && "is-active",
            selectionMode && "is-selecting",
          )}
        >
          {selectionMode ? (
            <Checkbox
              aria-label={`选择会话 ${displayTitle}`}
              checked={selectedSessionIds.has(session.id) || coveredBySelectedProjects.has(session.id)}
              onClickCapture={(event: ReactMouseEvent) => {
                sessionShiftRef.current = event.shiftKey;
              }}
              onCheckedChange={() => toggleSessionSelection(session.id)}
            />
          ) : null}
          {renamingSessionId === session.id ? (
            <form
              className="sidebar-session-rename"
              onSubmit={(event) => {
                event.preventDefault();
                void commitSessionRename(session);
              }}
            >
              <Input
                aria-label={`编辑会话名称 ${displayTitle}`}
                autoFocus
                disabled={renamePending}
                maxLength={200}
                onChange={(event) => setRenameValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") cancelSessionRename();
                }}
                value={renameValue}
              />
              <Button
                aria-label="保存会话名称"
                disabled={!renameValue.trim() || renamePending}
                size="icon-xs"
                type="submit"
                variant="ghost"
              >
                <Save className="size-3" />
              </Button>
              <Button
                aria-label="取消编辑会话名称"
                disabled={renamePending}
                onClick={cancelSessionRename}
                size="icon-xs"
                type="button"
                variant="ghost"
              >
                <X className="size-3" />
              </Button>
            </form>
          ) : (
            <button
              className="sidebar-project__session-title min-w-0 flex-1 truncate text-left"
              onClick={(event) => {
                if (longPressRef.current.fired) {
                  longPressRef.current.fired = false;
                  return;
                }
                if (selectionMode) {
                  toggleSessionSelection(session.id, event.shiftKey);
                  return;
                }
                // Folder rows no longer show a collapse arrow: the whole row
                // remains the single, quiet-line interaction target.
                if (hasChildren) toggleSessionFolder(session.id);
                onSelectSession(session.id, options.project);
              }}
              onContextMenu={(event) => {
                if (selectionMode) return;
                event.preventDefault();
                setMenuOpenFor(session.id);
              }}
              onPointerCancel={cancelLongPress}
              onPointerDown={selectionMode ? undefined : beginLongPress(session.id)}
              onPointerLeave={cancelLongPress}
              onPointerMove={event => { const lp = longPressRef.current; if (Math.hypot(event.clientX - lp.x, event.clientY - lp.y) > 8) cancelLongPress(); }}
              onPointerUp={cancelLongPress}
              type="button"
            >
              {hasChildren ? (
                effectivelyOpen ? (
                  <FolderOpen
                    aria-hidden="true"
                    className="size-3.5 shrink-0"
                  />
                ) : (
                  <Folder
                    aria-hidden="true"
                    className="size-3.5 shrink-0"
                  />
                )
              ) : null}
              <span className="min-w-0 flex-1 truncate">{displayTitle}</span>
              {voiceActive ? <span aria-label="语音导师已开启" className="sidebar-session__voice" title="语音导师已开启">◉</span> : null}
              {activity.running || activity.unreadCompleted ? (
                <span className="sidebar-session__activity" role="status">
                  {activity.running ? (
                    <LoaderCircle
                      aria-label="生成中"
                      className="sidebar-session__running size-3 shrink-0"
                    />
                  ) : (
                    <span
                      aria-label="模型回复已完成"
                      className="sidebar-session__unread-dot"
                      title="模型回复已完成"
                    />
                  )}
                </span>
              ) : null}
            </button>
          )}
          {!selectionMode && renamingSessionId !== session.id ? (
            <>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    aria-label={
                      session.pinned
                        ? `取消置顶 ${displayTitle}`
                        : `置顶 ${displayTitle}`
                    }
                    className={cn(
                      "sidebar-project__pin",
                      session.pinned && "is-pinned",
                    )}
                    onClick={() => onToggleSessionPin(session)}
                    size="icon-xs"
                    type="button"
                    variant="ghost"
                  >
                    <Pin className="size-3" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="right">
                  {session.pinned ? "取消置顶" : "置顶会话"}
                </TooltipContent>
              </Tooltip>
              <DropdownMenu
                onOpenChange={(open) =>
                  setMenuOpenFor(open ? session.id : null)
                }
                open={menuOpenFor === session.id}
              >
                <DropdownMenuTrigger asChild>
                  <Button
                    aria-label={`更多会话操作 ${displayTitle}`}
                    className="sidebar-project__more"
                    size="icon-xs"
                    variant="ghost"
                  >
                    <MoreHorizontal className="size-3" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  className="w-48"
                  side="bottom"
                >
                  <DropdownMenuItem onSelect={() => onShareSession(session)}>
                    <Share2 className="size-3.5" />
                    分享
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => beginSessionRename(session)}
                  >
                    <Pencil className="size-3.5" />
                    重命名
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => onToggleSessionPin(session)}
                  >
                    <Pin
                      className={cn(
                        "size-3.5",
                        session.pinned && "sidebar-project__pin-icon--pinned",
                      )}
                    />
                    {session.pinned ? "取消置顶" : "置顶聊天"}
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onSelect={() => onToggleSessionArchive(session)}
                  >
                    <Archive className="size-3.5" />
                    {session.status === "archived" ? "恢复" : "归档"}
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuSub>
                    <DropdownMenuSubTrigger
                      disabled={!options.project && projects.length === 0}
                    >
                      <Folder className="size-3.5" />
                      移动到
                    </DropdownMenuSubTrigger>
                    <DropdownMenuPortal>
                      <DropdownMenuSubContent
                        className="max-h-[min(20rem,var(--radix-dropdown-menu-content-available-height))] w-60 max-w-[calc(100vw-2rem)] overflow-x-hidden overflow-y-auto"
                        onTouchMove={withScrollLockBypass()}
                        onWheel={withScrollLockBypass()}
                      >
                        {options.project ? (
                          <>
                            <DropdownMenuItem
                              onSelect={() => onMoveSession(session.id)}
                            >
                              <Folder className="size-3.5" />
                              移出项目
                            </DropdownMenuItem>
                            {projects.length > 1 ? <DropdownMenuSeparator /> : null}
                          </>
                        ) : null}
                        {projects
                          .filter((entry) => entry.id !== options.project?.id)
                          .map((target) => (
                            <DropdownMenuItem
                              key={target.id}
                              onSelect={() => onMoveSession(session.id, target.id)}
                              title={target.title}
                            >
                              <Folder className="size-3.5" />
                              <span className="min-w-0 truncate">{target.title}</span>
                            </DropdownMenuItem>
                          ))}
                      </DropdownMenuSubContent>
                    </DropdownMenuPortal>
                  </DropdownMenuSub>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onSelect={() => onRequestSessionDeletion(session)}
                    variant="destructive"
                  >
                    <Trash2 className="size-3.5" />
                    删除会话
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          ) : null}
        </div>
      </div>
    );
  }

  type NavigatorRow =
    | { key: string; kind: "project"; project: SidebarProject }
    | { key: string; kind: "projects" | "recent" | "project-form" }
    | { key: string; kind: "page"; scope: SessionScope; direction: "next" | "previous" }
    | { key: string; kind: "session"; session: SidebarSession; project?: SidebarProject; depth: number; legacyBranchIndex?: number };
  const rows = useMemo(() => {
    const result: NavigatorRow[] = [];
    const addPage = (scope: SessionScope, direction: "next" | "previous") => {
      const state = navigator.scopes.get(sessionScopeId(scope));
      if (direction === "previous" && !state?.hasPreviousPage) return;
      if (direction === "next" && state?.isSuccess && !state.hasNextPage && state.data?.pages.some(page => page.items.length)) return;
      result.push({ key: `page:${sessionScopeId(scope)}:${direction}`, kind: "page", scope, direction });
    };
    const append = (sessions: SidebarSession[], project?: SidebarProject, depth = 0, seen = new Set<string>()) => {
      sessions.forEach((session, index) => {
        if (seen.has(session.id)) return;
        seen.add(session.id);
        result.push({ key: `session:${session.id}`, kind: "session", session, project, depth,
          legacyBranchIndex: depth ? legacyBranchSiblingIndex(sessions, index) : undefined });
        if ((session.has_children || session.children?.length) && sessionFolderOpen[session.id]) {
          const scope: SessionScope = { parent_session_id: session.id, roots_only: false };
          addPage(scope, "previous");
          append(session.children ?? [], project, depth + 1, seen);
          addPage(scope, "next");
        }
      });
    };
    result.push({ key: "projects", kind: "projects" });
    if (projectsOpen) {
      if (creatingProject) result.push({ key: "project-form", kind: "project-form" });
      for (const project of visibleProjects) {
        result.push({ key: `project:${project.id}`, kind: "project", project });
        if (projectFolderOpen[project.id] && project.status !== "archived") {
          const scope: SessionScope = { project_id: project.id, roots_only: true };
          addPage(scope, "previous"); append(project.sessions, project); addPage(scope, "next");
        }
      }
    }
    result.push({ key: "recent", kind: "recent" });
    if (recentOpen) {
      const scope: SessionScope = { ungrouped: true, roots_only: true };
      addPage(scope, "previous"); append(visibleUngroupedSessions); addPage(scope, "next");
    }
    return result;
  }, [creatingProject, navigator.scopes, projectFolderOpen, projectsOpen, recentOpen, sessionFolderOpen, visibleProjects, visibleUngroupedSessions]);
  useEffect(() => () => {
    if (longPressRef.current.timer != null) window.clearTimeout(longPressRef.current.timer);
  }, []);

  function renderProjectHeader(project: SidebarProject) {
    const open = projectFolderOpen[project.id] ?? false;
    return <section className="sidebar-project"><div className={cn("sidebar-project__heading group", selectedProjectId === project.id && "is-current", selectionMode && "is-selecting")}>
      {selectionMode ? <Checkbox aria-label={`选择项目 ${project.title}`} checked={selectedProjectIds.has(project.id)} className="sidebar-project__select" onCheckedChange={() => toggleProjectSelection(project)} /> : null}
      <button aria-expanded={open} aria-label={`${open ? "折叠" : "展开"}文件夹 ${project.title}`} className="sidebar-project__folder-toggle" type="button" onClick={() => {
        if (selectionMode) { toggleProjectSelection(project); return; }
        onToggleFolder("project", project.id, !open);
      }}>
        {open ? <FolderOpen aria-hidden="true" className="size-3.5" /> : <Folder aria-hidden="true" className="size-3.5" />}
      </button>
      <button aria-label={`选择项目 ${project.title}`} aria-pressed={selectedProjectId === project.id} className="sidebar-project__header" type="button" onClick={() => {
        if (selectionMode) { toggleProjectSelection(project); return; }
        onSelectProject(project.id);
      }}>
        <span>{project.title}</span>{project.status === "archived" ? <small>已归档</small> : null}
      </button>
      {!selectionMode && project.status !== "archived" ? <Button aria-label={`在文件夹 ${project.title} 中新建会话`} className="sidebar-project__new-session" disabled={creatingConversation} size="icon-xs" type="button" variant="ghost" onClick={() => {
        onSelectProject(project.id); onToggleFolder("project", project.id, true); void onCreateConversation(project.id);
      }}><Plus className="size-3.5" /></Button> : null}
      {!selectionMode ? <DropdownMenu open={projectMenuOpenFor === project.id} onOpenChange={open => setProjectMenuOpenFor(open ? project.id : null)}><DropdownMenuTrigger asChild><Button aria-label={`管理项目 ${project.title}`} className="sidebar-project__delete-project" size="icon-xs" type="button" variant="ghost"><MoreHorizontal className="size-3.5" /></Button></DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-40" side="right">
          {project.status !== "archived" ? <DropdownMenuItem onSelect={() => onRenameProject(project)}><Pencil className="size-3.5" />改名</DropdownMenuItem> : null}
          <DropdownMenuItem onSelect={() => onToggleProjectArchive(project)}><Archive className="size-3.5" />{project.status === "archived" ? "恢复项目" : "归档项目"}</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => beginSelection(project)}><ListChecks className="size-3.5" />批量管理</DropdownMenuItem>
          <DropdownMenuSeparator /><DropdownMenuItem onSelect={() => onRequestProjectDeletion(project)} variant="destructive"><Trash2 className="size-3.5" />永久删除</DropdownMenuItem>
        </DropdownMenuContent></DropdownMenu> : null}
    </div></section>;
  }

  function renderSectionHeader(section: "projects" | "recent") {
    const isProjects = section === "projects";
    const label = isProjects ? "项目" : "最近";
    const open = isProjects ? projectsOpen : recentOpen;
    return <section className={cn("sidebar-section", !isProjects && "sidebar-section--recent")} aria-label={label}>
      <div className="sidebar-section__heading">
        <button aria-expanded={open} aria-label={`${open ? "折叠" : "展开"}${label}分组`} className="sidebar-section__toggle" type="button" onClick={() => onToggleFolder("section", section, !open)}>{label}</button>
        <div className="sidebar-section__actions">
          {isProjects ? <Tooltip><TooltipTrigger asChild><Button aria-label="新建项目" onClick={() => { onToggleFolder("section", "projects", true); setCreatingProject(value => !value); }} size="icon-xs" variant="ghost"><Plus className="size-3.5" /></Button></TooltipTrigger><TooltipContent side="right">新建项目</TooltipContent></Tooltip> : <>
            <Tooltip><TooltipTrigger asChild><Button aria-label={selectionMode ? "退出多选" : "多选会话"} aria-pressed={selectionMode} className="sidebar-section__select" onClick={() => selectionMode ? exitSelection() : beginSelection()} size="icon-xs" variant={selectionMode ? "secondary" : "ghost"}><ListChecks className="size-3.5" /></Button></TooltipTrigger><TooltipContent side="right">{selectionMode ? "退出多选 (Esc)" : "多选管理"}</TooltipContent></Tooltip>
            <Tooltip><TooltipTrigger asChild><Button aria-label="新建最近对话" className="sidebar-section__create" disabled={creatingConversation} onClick={() => { onToggleFolder("section", "recent", true); void onCreateConversation(null); }} size="icon-xs" variant="ghost"><Plus className="size-3.5" /></Button></TooltipTrigger><TooltipContent side="right">新建对话</TooltipContent></Tooltip>
          </>}
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button aria-label={isProjects ? "管理项目分组" : "管理最近会话"} className="sidebar-section__more" size="icon-xs" variant="ghost"><MoreHorizontal className="size-3.5" /></Button></DropdownMenuTrigger>
            <DropdownMenuContent align="end" side="right" className="w-44">
              {isProjects && folderEntries.length ? <DropdownMenuItem onSelect={toggleAllFolders}>{allFoldersCollapsed ? <FolderOpen className="size-3.5" /> : <Folder className="size-3.5" />}{allFoldersCollapsed ? "展开所有文件夹" : "折叠所有文件夹"}</DropdownMenuItem> : null}
              <DropdownMenuItem onSelect={() => selectionMode ? exitSelection() : beginSelection()}><ListChecks className="size-3.5" />{selectionMode ? "退出批量管理" : "批量管理"}</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    </section>;
  }

  return (
    <section className="sidebar-recent sidebar-sessions" aria-label="项目和最近会话">
      <div className="sidebar-sessions__list">
        <VirtualSessionList rows={rows} workspaceId={workspaceId}
          pinnedKeys={[...([menuOpenFor, renamingSessionId].filter(Boolean).map(id => `session:${id}`)), ...(projectMenuOpenFor ? [`project:${projectMenuOpenFor}`] : []), ...(creatingProject ? ["project-form"] : [])]}
          renderRow={row => {
            if (row.kind === "projects" || row.kind === "recent") return renderSectionHeader(row.kind);
            if (row.kind === "project") return renderProjectHeader(row.project);
            if (row.kind === "session") return renderSessionRow(row.session, { project: row.project, ungrouped: !row.project, depth: row.depth, legacyBranchIndex: row.legacyBranchIndex });
            if (row.kind === "page") return <SessionPageSentinel scope={row.scope} navigator={navigator} direction={row.direction} />;
            return <form className="sidebar-sessions__project-form" onSubmit={event => { event.preventDefault(); void addProject(); }}>
              <Input aria-label="项目名称" autoFocus onChange={event => setProjectName(event.currentTarget.value)} onKeyDown={event => { if (event.key === "Escape") setCreatingProject(false); }} placeholder="项目名称" value={projectName} />
              <Button aria-label="确认新建项目" disabled={!projectName.trim()} size="icon-xs" type="submit"><Plus className="size-3.5" /></Button>
            </form>;
          }} />
      </div>
      {selectionMode ? (
        <div className="sidebar-sessions__batch-bar flex-wrap gap-y-1.5">
          <div className="flex w-full items-center justify-between gap-1">
            <div>
              <strong>{selectedCount}</strong>
              <span> 个已选</span>
              {selectedProjects.length ? (
                <span className="sidebar-sessions__batch-detail">
                  {" "}
                  {selectedProjects.length} 项目
                  {selectedStandaloneSessions.length
                    ? ` ${selectedStandaloneSessions.length} 会话`
                    : ""}
                </span>
              ) : null}
            </div>
            <div className="flex items-center gap-0.5">
              <Button
                disabled={batchBusy}
                onClick={toggleAllLoadedSelection}
                size="xs"
                title={`最多选择 ${MAX_SELECTED_SESSIONS} 个会话`}
                variant="ghost"
              >
                {allLoadedSelected ? "取消全选" : "全选已加载"}
              </Button>
              <Button
                aria-label="退出批量管理"
                disabled={batchBusy}
                onClick={exitSelection}
                size="icon-xs"
                title="退出批量管理 (Esc)"
                variant="ghost"
              >
                <X className="size-3" />
              </Button>
            </div>
          </div>
          <div className="flex w-full flex-wrap items-center gap-1">
            {onBatchToggleSessionPin ? (
              <Button
                aria-label={anySelectedSessionUnpinned ? "批量置顶" : "批量取消置顶"}
                disabled={batchBusy || !selectedSessionsForActions.length}
                onClick={() => void runBatchPin()}
                size="xs"
                title={anySelectedSessionUnpinned ? "批量置顶选中会话" : "批量取消置顶选中会话"}
                variant="outline"
              >
                <Pin className="size-3" />
                {anySelectedSessionUnpinned ? "置顶" : "取消置顶"}
              </Button>
            ) : null}
            {onBatchMoveSessions ? (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    aria-label="批量移动到项目"
                    disabled={batchBusy || !selectedSessionsForActions.length}
                    size="xs"
                    variant="outline"
                  >
                    <Folder className="size-3" />
                    移动
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="max-h-60 w-48 overflow-y-auto" side="top">
                  <DropdownMenuItem onSelect={() => void runBatchMove(undefined)}>
                    <Folder className="size-3.5" />
                    移出项目
                  </DropdownMenuItem>
                  {projects.filter((entry) => entry.status !== "archived").length ? <DropdownMenuSeparator /> : null}
                  {projects
                    .filter((entry) => entry.status !== "archived")
                    .map((target) => (
                      <DropdownMenuItem
                        key={target.id}
                        onSelect={() => void runBatchMove(target.id)}
                        title={target.title}
                      >
                        <Folder className="size-3.5" />
                        <span className="min-w-0 truncate">{target.title}</span>
                      </DropdownMenuItem>
                    ))}
                </DropdownMenuContent>
              </DropdownMenu>
            ) : null}
            {onBatchArchive ? (
              <Button
                aria-label="批量归档"
                disabled={batchBusy || !selectedCount}
                onClick={() => void runBatchArchive()}
                size="xs"
                variant="outline"
              >
                <Archive className="size-3" />
                归档
              </Button>
            ) : null}
            <Button
              aria-label="批量删除"
              className="ml-auto"
              disabled={batchBusy || !selectedCount}
              onClick={requestSelectedDeletion}
              size="xs"
              variant="destructive"
            >
              <Trash2 className="size-3" />
              删除
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}
