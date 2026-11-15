import "./graph-rail.css";
import { scheduleSessionRefresh } from "@/lib/session-refresh";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
} from "react"
import { lazy, memo, Suspense } from "react";
import { applyAppearance } from "@/lib/appearance";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Link,
  NavLink,
  Outlet,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import {
  Activity,
  Archive,
  BadgeCheck,
  Bot,
  Brain,
  CalendarDays,
  ChevronDown,
  ChevronRight,
  CircleDot,
  Files,
  Focus,
  GraduationCap,
  Home,
  LoaderCircle,
  LogOut,
  Menu,
  MessageSquareText,
  MoreHorizontal,
  Network,
  PanelLeftClose,
  Pause,
  Package,
  Pencil,
  Play,
  Plus,
  Save,
  Search,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Split,
  Target,
  X,
} from "lucide-react";
import { toast } from '@/lib/notifications';

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useModalDrawerFocus } from "./use-modal-drawer-focus";
import { useWorkspaceRailResize } from "./use-workspace-rail-resize";
import { useSessionNavigator } from "./use-session-navigator";
import { SessionProjects } from "./session-projects";
const WorkspaceSearchDialog = lazy(() => import("./workspace-search-dialog").then(module => ({ default: module.WorkspaceSearchDialog })));
import { toSidebarSession, nestSidebarSessions, flattenSidebarSessions, type SidebarSession, type SidebarProject } from "./session-sidebar-model";
import { useSessionDetail, updateSessionCaches, summaryOf } from "@/lib/session-queries";
import { getSessionCounts, getSessionAncestors, listSessionPage, getSession } from "@/api/session-browser";
import { createGraphOpenCoordinator, graphVisit, rememberGraphVisit, pickGraphConversation } from "@/lib/graph-folder-navigation";
import { claimLearningNodeNavigation } from "@/lib/learning-node-binding";
import { openGraphFolder } from "@/api/workflow";
import { ActivityHeatmap } from "@/components/shared/activity-heatmap";
import { DeleteImpactDialog } from "@/components/shared/delete-impact-dialog";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Sheet,
  SheetContent,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { defaultUserAvatar } from "@/lib/user-avatar";
import { workspaceQueryKey } from "@/lib/query-keys";
import { SessionGraphBindingMenu } from "@/components/layout/session-graph-binding";
import {
  useSessionGraphBinding,
  publishLearningNodeContext,
  type LearningNodeContext,
} from "@/lib/session-graph-binding";
import { SettingsModal } from "@/components/layout/settings-modal";
import { SidebarVersion } from "@/components/layout/sidebar-version";
import { RouteMotion } from "@/components/shared/route-motion";
import { NativeActions } from "@/features/mobile/NativeActions";
import { PendingShareConsumer } from "@/features/mobile/PendingShareConsumer";
import {
  setDrawerLayerOpen,
  setDrawerProgress,
  setDrawerScrollLock,
  useDraggingDrawer,
  useMobileDrawerDrag,
  type DrawerSide,
} from "@/lib/mobile-shell";
import {
  pauseAllPreviews,
  resumeAllPreviews,
  useAllPreviewsPaused,
} from "@/lib/preview-pause-controller";
import {
  consumePendingSelectionExplanation,
  selectionExplanationOpenEventName,
  type SelectionExplanationOpenDetail,
} from "@/features/chat/selection-explanation";
import { useAuth } from "@/features/auth/auth-context-value";
import type { KnowledgeNode, KnowledgeGraphCanvasApi } from "@/components/graph/knowledge-graph";

// F1-1: keep @xyflow/react out of the first-screen entry chunk. KnowledgeGraph
// is a named export, so map it to the default shape React.lazy requires.
const KnowledgeGraph = lazy(() =>
  import("@/components/graph/knowledge-graph").then((module) => ({
    default: module.KnowledgeGraph,
  })),
);
import { NodeExploreChain } from "@/components/graph/node-explore";
const SessionFilePreviewRail = lazy(() => import("@/features/chat/right-rail-panels").then(module => ({ default: module.SessionFilePreviewRail })));
const SideConversationRail = lazy(() => import("@/features/chat/right-rail-panels").then(module => ({ default: module.SideConversationRail })));
const MemoryProcessRail = lazy(() => import("@/features/memory/components/memory-process-rail").then(module => ({ default: module.MemoryProcessRail })));
const VoiceLatencyRail = lazy(() => import("@/features/voice/voice-debug-panel").then(module => ({ default: module.VoiceLatencyRail })));
import { SessionShareDialog } from "@/features/chat/session-share-dialog";
import {
  archiveProject,
  archiveSession,
  assignSessionProject,
  createProject as createProjectRecord,
  deleteProject as deleteProjectRecord,
  deleteSession as deleteSessionRecord,
  deleteSessionsBatch,
  getGraph,
  getDashboard,
  getMastery,
  getProjectDeleteImpact,
  getSessionDeleteImpact,
  getSessionBatchDeleteImpact,
  listGraphs,
  listNodeQuestions,
  listProjects,
  listSettings,
  restoreProject,
  restoreSession,
  updateGraphNode,
  updateProject as updateProjectRecord,
  updateSession,
} from "@/api";
import { createSession } from "@/api/sessions";
import {
  clearDraftSessionId,
  getDraftSessionId,
  isDefaultDraftTitle,
  setDraftSessionId,
} from "@/lib/draft-session";
import {
  getSessionActivitySnapshot,
  markSessionViewed,
  subscribeSessionActivity,
} from "@/lib/session-activity";
import {
  defaultComposerPrefsForResponseMode,
  hasSessionComposerPrefs,
  setSessionComposerPrefs,
} from "@/lib/session-composer-prefs";
import { readChatDefaultResponseMode } from "@/lib/workspace-settings";
import { isVoiceDebugPanelEnabled } from "@/lib/workspace-settings";
import {
  readSidebarFolderState,
  readSidebarProjectFolders,
  SIDEBAR_FOLDER_STATE_EVENT,
  SIDEBAR_FOLDER_STATE_STORAGE_KEY,
  sidebarFolderStateKey,
  writeSidebarFolderState,
  type SidebarFolderKind,
  type SidebarFolderRef,
  type SidebarFolderState,
} from "@/lib/sidebar-folders";
import type { Session } from "@/types/sessions";
import type { Graph, GraphNode, GraphSummary } from "@/types/graphs";
import type { DeleteImpact } from "@/types/workflow";
import type { WorkspaceSetting } from "@/types/settings";

type NavItem = {
  label: string;
  icon: typeof Home;
  path: string;
  aliases?: string[];
};

const primaryNav: NavItem[] = [
  {
    label: "新对话",
    icon: MessageSquareText,
    // Action button only — never stay highlighted when a history session is open.
    // (Previously aliases ["/chat/", ...] made every chat route look "active".)
    path: "chat/new",
  },
  {
    label: "图谱",
    icon: Network,
    path: "chat/new?mode=goal",
    aliases: ["/graphs/", "/capabilities"],
  },
  // 「路线」不再是一级入口：Roadmap 收敛为图谱内的「学习计划」时间行动层
  // （见 GraphWorkspacePage 的 plan 面板）。旧 /goals/:goalId/roadmap 仍可打开，
  // 会重定向到对应图谱的 ?panel=plan。
  { label: "资料", icon: Files, path: "sources" },
  { label: "产物", icon: Package, path: "artifacts", aliases: ["/artifacts"] },
  { label: "定时任务", icon: CalendarDays, path: "schedules" },
];

const primaryMoreNav: NavItem[] = [
  // 「研究」入口已移除：联网搜索与网页获取页（research/search）对全部用户下线，
  // 直接访问该地址会被重定向到工作台首页（见 App.tsx 的 research/search 路由）。
  // 聊天内的联网搜索 / 网页抓取能力不受影响。
  {
    label: "练习",
    icon: GraduationCap,
    path: "practice",
    aliases: ["/practice"],
  },
  { label: "记忆", icon: Brain, path: "memory" },
];


type SidebarDeleteTarget =
  | { kind: "session"; session: SidebarSession }
  | { kind: "session_batch"; sessions: SidebarSession[] }
  | { kind: "project"; project: SidebarProject }
  | {
      kind: "mixed_batch";
      projects: SidebarProject[];
      sessions: SidebarSession[];
      projectImpacts: DeleteImpact[];
      sessionImpact?: DeleteImpact;
    };

type LearningProjectRequest = {
  graphId?: string;
  title?: string;
  nodeId?: string;
  nodeLabel?: string;
  prompt?: string;
  /** Study submits a turn, but may resume a conversation already bound to this node. */
  intent?: "study";
  /** When set, the new chat auto-send will request a graph change proposal. */
  graphAction?: "none" | "propose_create" | "propose_update";
  onError?: (message: string) => void;
};

function findProjectForSession(projects: SidebarProject[], sessionId?: string) {
  return projects.find((project) =>
    flattenSidebarSessions(project.sessions).some(
      (session) => session.id === sessionId,
    ),
  );
}

function mergeDeleteImpacts(
  title: string,
  impacts: DeleteImpact[],
): DeleteImpact {
  const merged = new Map<
    string,
    { resource_type: string; count: number; action: string }
  >();
  for (const impact of impacts) {
    for (const item of impact.impacts) {
      const key = `${item.resource_type}:${item.action}`;
      const existing = merged.get(key);
      if (existing) existing.count += item.count;
      else
        merged.set(key, {
          resource_type: item.resource_type,
          count: item.count,
          action: item.action,
        });
    }
  }
  return {
    resource_type: "mixed_batch",
    resource_id: "mixed-batch",
    title,
    confirmation_text: "mixed-batch",
    impacts: [...merged.values()],
  };
}


const LAST_LEARNED_NODE_STORAGE_KEY = "learngraph:last-learned-nodes";

function readLastLearnedNode(graphId: string): string | undefined {
  try {
    const raw = window.sessionStorage.getItem(LAST_LEARNED_NODE_STORAGE_KEY);
    if (!raw) return undefined;
    const map = JSON.parse(raw) as Record<string, string>;
    return map[graphId];
  } catch {
    return undefined;
  }
}

function rememberLastLearnedNode(graphId: string, nodeId: string) {
  try {
    const raw = window.sessionStorage.getItem(LAST_LEARNED_NODE_STORAGE_KEY);
    const map = raw ? (JSON.parse(raw) as Record<string, string>) : {};
    map[graphId] = nodeId;
    window.sessionStorage.setItem(
      LAST_LEARNED_NODE_STORAGE_KEY,
      JSON.stringify(map),
    );
  } catch {
    // Best-effort preference only.
  }
}

function pickDefaultLearningNode(graph: Graph): GraphNode | undefined {
  if (!graph.nodes.length) return undefined;
  const lastId = readLastLearnedNode(graph.id);
  if (lastId) {
    const last = graph.nodes.find((node) => node.id === lastId);
    if (last) return last;
  }
  const focused = graph.nodes.find(
    (node) => node.attention_state === "focused",
  );
  if (focused) return focused;
  // Prefer the first step after root (main trunk concept) rather than the root hub.
  const root =
    graph.nodes.find((node) => node.node_type === "root") ??
    graph.nodes.find(
      (node) => !graph.edges.some((edge) => edge.target_node_id === node.id),
    );
  if (root) {
    const firstChildId = graph.edges.find(
      (edge) =>
        edge.source_node_id === root.id &&
        (edge.relation === "contains" || edge.relation === "prerequisite"),
    )?.target_node_id;
    const firstChild = firstChildId
      ? graph.nodes.find((node) => node.id === firstChildId)
      : undefined;
    if (firstChild) return firstChild;
  }
  return (
    graph.nodes.find((node) => node.node_type !== "root") ?? graph.nodes[0]
  );
}

function isNavActive(pathname: string, item: NavItem) {
  return (
    pathname.endsWith(`/${item.path}`) ||
    item.aliases?.some((alias) => pathname.includes(alias))
  );
}

function SidebarNav({
  mobile = false,
  collapsed = false,
  onCollapse,
  onNavigate,
}: {
  mobile?: boolean;
  collapsed?: boolean;
  onCollapse?: () => void;
  onNavigate?: () => void;
}) {
  const { isSystemAdmin } = useAuth();
  const { pathname, search } = useLocation();
  const { workspaceId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [projects, setProjects] = useState<SidebarProject[]>([]);
  const [ungroupedSessions, setUngroupedSessions] = useState<SidebarSession[]>(
    [],
  );
  const [shareTarget, setShareTarget] = useState<SidebarSession | null>(null);
  // Subscribe so completion black-dots and activity-based sort re-render live.
  const sessionActivity = useSyncExternalStore(
    subscribeSessionActivity,
    getSessionActivitySnapshot,
    getSessionActivitySnapshot,
  );
  const projectsQuery = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "projects"),
    queryFn: () => listProjects(true),
    staleTime: 30_000,
  });
  const sessionNavigator = useSessionNavigator(workspaceId);
  const routedSessionId = new URLSearchParams(search).get("sidebarSession") ?? pathname.match(/\/chat\/([^/]+)/)?.[1];
  const routedSession = useSessionDetail(workspaceId, routedSessionId);
  const ancestors = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "sessions", "ancestors", routedSessionId),
    queryFn: ({ signal }) => getSessionAncestors(routedSessionId!, { signal }),
    enabled: Boolean(routedSession.data?.parent_session_id), staleTime: 30_000, gcTime: 120_000,
  });
  const sessionRecords = useMemo(() => {
    const items = new Map(sessionNavigator.data.map(item => [item.id, item]));
    for (const ancestor of ancestors.data ?? []) items.set(ancestor.id, ancestor);
    if (routedSession.data) items.set(routedSession.data.id, summaryOf(routedSession.data, items.get(routedSession.data.id)?.has_children));
    return [...items.values()];
  }, [sessionNavigator.data, ancestors.data, routedSession.data]);
  const sessionsQuery = { data: sessionRecords, refetch: sessionNavigator.refetch };
  const settingsQueryKey = workspaceQueryKey(workspaceId, "settings");
  const settingsQuery = useQuery({
    queryKey: settingsQueryKey,
    queryFn: listSettings,
    staleTime: 30_000,
  });
  const workspaceDefaultResponseMode = useMemo(
    () =>
      readChatDefaultResponseMode(
        settingsQuery.data as WorkspaceSetting[] | undefined,
      ),
    [settingsQuery.data],
  );
  const [activeSessionId, setActiveSessionId] = useState("");
  /** The project folder targeted by both "new conversation" entry points. */
  const [selectedProjectId, setSelectedProjectId] = useState<string>();
  const routedSessionIdRef = useRef("");
  /**
   * 项目文件夹和父会话文件夹共用一份前端本地状态。按 workspace 隔离，
   * 因此切换工作区不会把另一边的折叠偏好带过来。
   */
  const [folderState, setFolderState] = useState<SidebarFolderState>(() =>
    readSidebarFolderState(workspaceId),
  );
  const folderStateRef = useRef<SidebarFolderState>(folderState);

  useEffect(() => {
    const next = readSidebarFolderState(workspaceId);
    folderStateRef.current = next;
    setFolderState(next);
  }, [workspaceId]);

  // Migrate the previous server-backed project-folder preference once. New
  // interactions are local-only, but existing users should not lose the
  // layout they already chose when this version switches storage backends.
  useEffect(() => {
    const legacy = readSidebarProjectFolders(
      settingsQuery.data as WorkspaceSetting[] | undefined,
    );
    if (!Object.keys(legacy).length) return;
    const current = folderStateRef.current;
    const migrated = { ...current };
    let changed = false;
    for (const [projectId, open] of Object.entries(legacy)) {
      const key = sidebarFolderStateKey("project", projectId);
      if (typeof migrated[key] === "boolean") continue;
      migrated[key] = open;
      changed = true;
    }
    if (!changed) return;
    folderStateRef.current = migrated;
    setFolderState(migrated);
    writeSidebarFolderState(workspaceId, migrated);
    window.dispatchEvent(new Event(SIDEBAR_FOLDER_STATE_EVENT));
  }, [settingsQuery.data, workspaceId]);

  // 其它标签页修改折叠状态时同步当前侧栏；本页点击仍然走下面的同步
  // ref + state 路径，所以不会依赖 storage 事件（浏览器不会向发起页派发它）。
  useEffect(() => {
    const handleStorage = (event: StorageEvent) => {
      if (
        event.key !== null &&
        event.key !== SIDEBAR_FOLDER_STATE_STORAGE_KEY
      )
        return;
      const next = readSidebarFolderState(workspaceId);
      folderStateRef.current = next;
      setFolderState(next);
    };
    const handleLocalChange = () => {
      const next = readSidebarFolderState(workspaceId);
      folderStateRef.current = next;
      setFolderState(next);
    };
    window.addEventListener("storage", handleStorage);
    window.addEventListener(SIDEBAR_FOLDER_STATE_EVENT, handleLocalChange);
    return () => {
      window.removeEventListener("storage", handleStorage);
      window.removeEventListener(SIDEBAR_FOLDER_STATE_EVENT, handleLocalChange);
    };
  }, [workspaceId]);

  const setFolderOpen = useCallback(
    (kind: SidebarFolderKind, id: string, open: boolean) => {
      const next = {
        ...folderStateRef.current,
        [sidebarFolderStateKey(kind, id)]: open,
      };
      folderStateRef.current = next;
      setFolderState(next);
      writeSidebarFolderState(workspaceId, next);
      window.dispatchEvent(new Event(SIDEBAR_FOLDER_STATE_EVENT));
    },
    [workspaceId],
  );
  const setFoldersOpen = useCallback(
    (folders: SidebarFolderRef[], open: boolean) => {
      const next = { ...folderStateRef.current };
      for (const folder of folders) {
        next[sidebarFolderStateKey(folder.kind, folder.id)] = open;
      }
      folderStateRef.current = next;
      setFolderState(next);
      writeSidebarFolderState(workspaceId, next);
      window.dispatchEvent(new Event(SIDEBAR_FOLDER_STATE_EVENT));
    },
    [workspaceId],
  );
  /** Empty draft kept for reuse; hidden from the sidebar until the first message. */
  const [hiddenDraftSessionId, setHiddenDraftSessionId] = useState<string | null>(
    () => getDraftSessionId(),
  );
  const draftSessionId = useRef<string | null>(getDraftSessionId());
  const creatingConversationRef = useRef(false);
  const deleteRequestId = useRef(0);
  const [deleteTarget, setDeleteTarget] = useState<SidebarDeleteTarget>();
  const [deleteImpact, setDeleteImpact] = useState<DeleteImpact>();
  const [deleteImpactError, setDeleteImpactError] = useState<string>();
  const [deleteImpactLoading, setDeleteImpactLoading] = useState(false);
  const [deleteConfirming, setDeleteConfirming] = useState(false);
  const base = `/w/${workspaceId}`;
  const runtimePrimaryNav = primaryNav.map((item) =>
    item.label === "新对话"
      ? {
          ...item,
          path: "chat/new",
        }
      : item.label === "图谱"
        ? {
            ...item,
            // 图谱工作台默认进入书架；单开某本图谱后再进入画布。
            path: "graphs",
          }
          : item,
  );
  const nav = runtimePrimaryNav.slice(0, 5);
  const moreNav = primaryMoreNav;
  const moreActive = moreNav.some((item) => isNavActive(pathname, item));
  const publishProjectContext = useCallback(
    (project: SidebarProject | undefined, sessionId?: string) => {
      window.dispatchEvent(
        new CustomEvent("learngraph:project-context", {
          detail: { workspaceId, project, sessionId },
        }),
      );
    },
    [workspaceId],
  );

  useEffect(() => {
    const currentSessionId =
      new URLSearchParams(search).get("sidebarSession") ??
      pathname.match(/\/chat\/([^/]+)/)?.[1];
    if (!currentSessionId) return;
    setActiveSessionId(currentSessionId);
    const draftProjectId = new URLSearchParams(search).get("project");
    const currentProject = currentSessionId === "new"
      ? projects.find((project) => project.id === draftProjectId)
      : findProjectForSession(projects, currentSessionId);
    publishProjectContext(currentProject, currentSessionId);
    // The blank canvas carries only its explicitly selected folder. It has no
    // persisted session or sidebar row until the user sends content.
    if (currentSessionId === "new") {
      routedSessionIdRef.current = currentSessionId;
      setSelectedProjectId(draftProjectId ?? undefined);
      return;
    }
    // A route change means the user selected a conversation, so its parent
    // folder becomes the next-create target. Do not overwrite a folder that
    // the user explicitly clicked merely because the project query refetched.
    const routedSession = sessionsQuery.data?.find(
      (session) => session.id === currentSessionId,
    );
    const projectReady =
      Boolean(routedSession) &&
      (!routedSession?.project_id || currentProject?.id === routedSession.project_id);
    if (routedSessionIdRef.current !== currentSessionId && projectReady) {
      routedSessionIdRef.current = currentSessionId;
      setSelectedProjectId(currentProject?.id);
    }
  }, [
    pathname,
    projects,
    publishProjectContext,
    search,
    sessionsQuery.data,
    workspaceId,
  ]);

  useEffect(() => {
    if (
      selectedProjectId &&
      projectsQuery.data &&
      !projects.some((project) => project.id === selectedProjectId)
    ) {
      setSelectedProjectId(undefined);
    }
  }, [projects, projectsQuery.data, selectedProjectId]);

  const trackDraftSession = useCallback(
    (sessionId: string, projectId: string | null = null) => {
      draftSessionId.current = sessionId;
      setDraftSessionId(sessionId, projectId);
      setHiddenDraftSessionId(sessionId);
    },
    [],
  );

  const releaseDraftSession = useCallback((sessionId?: string) => {
    if (sessionId && draftSessionId.current !== sessionId) return;
    draftSessionId.current = null;
    clearDraftSessionId(sessionId);
    setHiddenDraftSessionId(null);
  }, []);

  useEffect(() => {
    const markDraftStarted = (event: Event) => {
      const sessionId = (event as CustomEvent<{ sessionId?: string }>).detail
        ?.sessionId;
      if (!sessionId) return;
      // First real message turns the draft into a normal sidebar session.
      if (sessionId === draftSessionId.current) {
        releaseDraftSession(sessionId);
      }
    };
    window.addEventListener("learngraph:session-started", markDraftStarted);
    return () =>
      window.removeEventListener(
        "learngraph:session-started",
        markDraftStarted,
      );
  }, [releaseDraftSession]);

  useEffect(() => {
    if (!projectsQuery.data || !sessionsQuery.data) return;
    // Absence from a lazy page does NOT mean a draft was deleted.
    // Empty unused draft stays out of the list once the user leaves its chat route
    // without sending a message. Re-open via "新对话" reuses the same draft.
    const viewingDraft =
      Boolean(hiddenDraftSessionId) &&
      pathname.includes(`/chat/${hiddenDraftSessionId}`);
    const hideDraftId = viewingDraft ? null : hiddenDraftSessionId;
    // One pass over loaded summaries, not one full filter per project.
    const grouped = new Map<string, SidebarSession[]>();
    for (const session of sessionsQuery.data) {
      if (session.status === "archived" || session.id === hideDraftId) continue;
      const key = session.project_id ?? "";
      const group = grouped.get(key) ?? [];
      group.push(toSidebarSession(session));
      grouped.set(key, group);
    }
    setProjects(projectsQuery.data.map(project => ({
      id: project.id, title: project.title, status: project.status,
      graphId: project.primary_graph_id ?? undefined,
      graphTitle: project.primary_graph_id ? project.title : undefined,
      goalId: project.primary_goal_id ?? undefined,
      sessions: nestSidebarSessions(grouped.get(project.id) ?? [], sessionActivity),
    })));
    setUngroupedSessions(nestSidebarSessions(grouped.get("") ?? [], sessionActivity));
  }, [
    hiddenDraftSessionId,
    pathname,
    projectsQuery.data,
    releaseDraftSession,
    sessionActivity,
    sessionsQuery.data,
  ]);

  useEffect(() => {
    const addCreatedSession = (event: Event) => {
      const session = (event as CustomEvent<{ session?: Session }>).detail
        ?.session;
      if (!session) return;
      queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) => [
        session,
        ...(current ?? []).filter((item) => item.id !== session.id),
      ]);
      // Empty default-titled drafts stay out of the sidebar until used
      // (or while the user is still viewing that draft route).
      if (
        !session.project_id &&
        isDefaultDraftTitle(session.title) &&
        session.status === "active"
      ) {
        trackDraftSession(session.id);
        if (
          pathname.includes(`/chat/${session.id}`) ||
          pathname.includes("/chat/new")
        ) {
          setUngroupedSessions((current) =>
            current.some((item) => item.id === session.id)
              ? current
              : nestSidebarSessions(
                  [
                    toSidebarSession(session),
                    ...flattenSidebarSessions(current).map(
                      ({ children: _children, ...item }) => item,
                    ),
                  ],
                  sessionActivity,
                ),
          );
        }
        return;
      }
      const sidebarSession = toSidebarSession(session);
      if (session.project_id) {
        setProjects((current) =>
          current.map((project) =>
            project.id === session.project_id
              ? {
                  ...project,
                  sessions: nestSidebarSessions(
                    [
                      sidebarSession,
                      ...flattenSidebarSessions(project.sessions)
                        .filter((item) => item.id !== session.id)
                        .map(({ children: _children, ...item }) => item),
                    ],
                    sessionActivity,
                  ),
                }
              : project,
          ),
        );
        return;
      }
      setUngroupedSessions((current) =>
        nestSidebarSessions(
          [
            sidebarSession,
            ...flattenSidebarSessions(current)
              .filter((item) => item.id !== session.id)
              .map(({ children: _children, ...item }) => item),
          ],
          sessionActivity,
        ),
      );
    };
    window.addEventListener("learngraph:session-created", addCreatedSession);
    return () =>
      window.removeEventListener(
        "learngraph:session-created",
        addCreatedSession,
      );
  }, [pathname, queryClient, sessionActivity, trackDraftSession, workspaceId]);

  useEffect(() => {
    const bindProjectGraph = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          projectId?: string;
          graphId?: string;
          graphTitle?: string;
        }>
      ).detail;
      if (!detail?.projectId || !detail.graphId) return;
      setProjects((current) =>
        current.map((project) =>
          project.id === detail.projectId
            ? {
                ...project,
                graphId: detail.graphId,
                graphTitle:
                  detail.graphTitle?.trim() ||
                  project.graphTitle ||
                  project.title,
              }
            : project,
        ),
      );
      void updateProjectRecord(detail.projectId, {
        primary_graph_id: detail.graphId,
      })
        .then(() => queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "projects") }))
        .catch((error: Error) => toast.error(error.message));
      window.dispatchEvent(
        new CustomEvent("learngraph:project-graph-bound", {
          detail: { ...detail, workspaceId },
        }),
      );
    };
    window.addEventListener("learngraph:bind-project-graph", bindProjectGraph);
    return () =>
      window.removeEventListener(
        "learngraph:bind-project-graph",
        bindProjectGraph,
      );
  }, [queryClient, workspaceId]);

  const createConversation = useCallback(
    async (
      input?:
        | string
        | {
            projectId?: string;
            pendingPrompt?: string;
            project?: SidebarProject;
            learningNode?: LearningNodeContext;
            pendingGraphAction?: "none" | "propose_create" | "propose_update";
            /** Identifies the pinned top-level button from project-row actions. */
            fromNewConversationNav?: boolean;
            onError?: (message: string) => void;
            /** When true, always create a fresh session (project / learning entry). */
            forceNew?: boolean;
            canNavigate?: () => boolean;
          },
    ) => {
      const options =
        typeof input === "string" ? { projectId: input } : (input ?? {});
      const projectId = options.projectId;
      const project = options.project ?? projects.find((entry) => entry.id === projectId);
      const isEmptyDraftRequest = Boolean(
        !options.forceNew &&
          !options.pendingPrompt &&
          !options.learningNode,
      );
      if (isEmptyDraftRequest) {
        const currentParams = new URLSearchParams(search);
        // Repeated clicks keep the same canvas and any unsent input intact.
        if (
          pathname === `${base}/chat/new` &&
          (currentParams.get("project") ?? undefined) === projectId &&
          !currentParams.has("mode")
        ) {
          onNavigate?.();
          return;
        }
        window.dispatchEvent(new CustomEvent("learngraph:new-conversation"));
        releaseDraftSession();
        setActiveSessionId("new");
        setSelectedProjectId(projectId);
        publishProjectContext(project, "new");
        const query = projectId ? `?project=${encodeURIComponent(projectId)}` : "";
        navigate(`${base}/chat/new${query}`);
        onNavigate?.();
        return;
      }
      // 「新会话」是一次会话切换，不是"在同一个会话里继续"：通话属于它所在的那个会话，
      // 必须先挂断再建新的。不这么做，新会话会被上一通电话的自动拨号带走（语音页
      // 监听这个事件做挂断与退出语音模式）。
      window.dispatchEvent(new CustomEvent("learngraph:new-conversation"));
      if (creatingConversationRef.current) return;
      creatingConversationRef.current = true;
      setCreatingConversation(true);
      try {
        const payload = {
          memory_enabled: true,
          title: "新会话",
          goal_id: project?.goalId ?? null,
          graph_id: project?.graphId ?? null,
          current_node_id: options.learningNode?.nodeId ?? null,
          project_id: project?.id ?? null,
        };
        const session = await createSession(payload);
        updateSessionCaches(queryClient, workspaceId, session);
        // Seed the workspace default response mode before the chat canvas
        // hydrates, so a new draft does not inherit another session's "极速".
        // If the workspace setting is still loading (e.g. the user clicked
        // "新对话" before /settings resolved), fetch it first — otherwise the
        // seed would silently fall back to the product default (agentic) and
        // a workspace default of 思考/极速 would never take effect on new
        // chats because the chat canvas treats the seeded prefs as final.
        if (!hasSessionComposerPrefs(session.id)) {
          let resolvedMode = workspaceDefaultResponseMode;
          if (settingsQuery.isPending) {
            try {
              const settings =
                (await queryClient.fetchQuery({
                  queryKey: workspaceQueryKey(workspaceId, "settings"),
                  queryFn: listSettings,
                  staleTime: 30_000,
                })) as WorkspaceSetting[] | undefined;
              resolvedMode = readChatDefaultResponseMode(settings);
            } catch {
              // Keep the memoized value; settings may still resolve later
              // and the chat canvas restore-effect will re-seed.
            }
          }
          setSessionComposerPrefs(
            session.id,
            defaultComposerPrefsForResponseMode(resolvedMode),
          );
        }
        queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) => [
          session,
          ...(current ?? []).filter((item) => item.id !== session.id),
        ]);
        const sidebarSession = toSidebarSession(session);
        if (projectId) {
          // Project-scoped chats appear immediately under the project.
          setProjects((current) =>
            current.map((entry) =>
              entry.id === projectId
                ? {
                    ...entry,
                    sessions: nestSidebarSessions(
                      [
                        sidebarSession,
                        ...flattenSidebarSessions(entry.sessions)
                          .filter((item) => item.id !== session.id)
                          .map(({ children: _children, ...item }) => item),
                      ],
                      sessionActivity,
                    ),
                  }
                : entry,
            ),
          );
        } else if (
          options.pendingPrompt ||
          options.learningNode
        ) {
          // Learning-entry paths already have content intent — list them.
          releaseDraftSession();
          setUngroupedSessions((current) =>
            nestSidebarSessions(
              [
                sidebarSession,
                ...flattenSidebarSessions(current)
                  .filter((item) => item.id !== session.id)
                  .map(({ children: _children, ...item }) => item),
              ],
              sessionActivity,
            ),
          );
        }
        if (options.canNavigate && !options.canNavigate()) return;
        if (options.learningNode) {
          rememberGraphVisit(workspaceId, options.learningNode.graphId, session.id);
          if (options.learningNode.nodeId) rememberGraphVisit(workspaceId, `${options.learningNode.graphId}/${options.learningNode.nodeId}`, session.id);
        }
        setActiveSessionId(session.id);
        setSelectedProjectId(project?.id);
        publishProjectContext(project, session.id);
        if (options.learningNode)
          publishLearningNodeContext({
            ...options.learningNode,
            sessionId: session.id,
          });
        const navigationState =
          options.pendingPrompt || options.learningNode
            ? {
                state: {
                  ...(options.pendingPrompt
                    ? { pendingPrompt: options.pendingPrompt }
                    : {}),
                  ...(options.learningNode
                    ? { learningNode: options.learningNode }
                    : {}),
                  ...(options.pendingGraphAction
                    ? { pendingGraphAction: options.pendingGraphAction }
                    : {}),
                },
              }
            : undefined;
        navigate(`${base}/chat/${session.id}`, navigationState);
        onNavigate?.();
      } catch (error) {
        const message = error instanceof Error ? error.message : "无法创建新会话";
        options.onError?.(message);
        toast.error(message);
      } finally {
        creatingConversationRef.current = false;
        setCreatingConversation(false);
      }
    },
    [
      base,
      navigate,
      onNavigate,
      pathname,
      projects,
      publishProjectContext,
      queryClient,
      releaseDraftSession,
      settingsQuery.isPending,
      sessionActivity,
      search,
      workspaceId,
      workspaceDefaultResponseMode,
    ],
  );

  const graphRoute = useRef(`${pathname}${search}`);
  graphRoute.current = `${pathname}${search}`;
  const graphOpens = useRef(createGraphOpenCoordinator());
  useEffect(() => { graphOpens.current.invalidate(); }, [pathname, search]);
  const graphCreates = useRef<Promise<void>>(Promise.resolve());
  useEffect(() => { const coordinator = graphOpens.current; return () => coordinator.invalidate(); }, [workspaceId]);
  const openLearningProject = useCallback(
    (request: LearningProjectRequest) => {
      const graphId = request.graphId?.trim();
      if (!graphId) return Promise.resolve();
      const openKey = JSON.stringify([workspaceId, graphId, request.nodeId, request.prompt, request.graphAction, request.intent]);
      const originRoute = graphRoute.current;
      return graphOpens.current.run(openKey, async (latestRequest) => {
        const isLatest = () => latestRequest() && graphRoute.current === originRoute;
        const record = await openGraphFolder(graphId);
        if (!isLatest()) return;
        queryClient.setQueryData<import("@/types/workflow").Project[]>(workspaceQueryKey(workspaceId, "projects"), current =>
          [record, ...(current ?? []).filter(item => item.id !== record.id)]);
        const project: SidebarProject = {
          id: record.id, title: record.title, status: record.status,
          graphId, graphTitle: request.title ?? record.title,
          goalId: record.primary_goal_id ?? undefined, sessions: [],
        };
        setProjects(current => current.some(item => item.id === project.id)
          ? current.map(item => item.id === project.id ? { ...item, ...project, sessions: item.sessions } : item)
          : [...current, project]);
        const remembered = graphVisit(workspaceId, request.nodeId ? `${graphId}/${request.nodeId}` : graphId)?.sessionId;
        const page = await listSessionPage({ project_id: project.id, graph_id: graphId, node_id: request.nodeId, session_kind: "main", roots_only: false, pinned_first: false });
        if (!isLatest()) return;
        // The server filters node_id; summaries intentionally omit current_node_id.
        // Validate the durable binding on the fetched detail below instead.
        const recent = pickGraphConversation(page.items, project.id, graphId, { rememberedSessionId: remembered });
        let detail: Session | undefined;
        const candidateIds = [...new Set([remembered, recent?.id].filter((id): id is string => Boolean(id)))];
        for (const candidateId of candidateIds) {
          let candidate: Session;
          try { candidate = await getSession(candidateId); } catch (error) {
            if (error instanceof Error && "status" in error && error.status === 404) continue;
            throw error;
          }
          if (!isLatest()) return;
          if (candidate.project_id === project.id && candidate.graph_id === graphId
            && candidate.status !== "archived" && candidate.session_kind !== "side"
            && (!request.nodeId || candidate.current_node_id === request.nodeId)) { detail = candidate; break; }
        }
        if (!isLatest()) return;
        setFolderOpen("project", project.id, true);
        setSelectedProjectId(project.id);
        window.dispatchEvent(new CustomEvent("learngraph:graph-folder-selected", { detail: { workspaceId, graphId, projectId: project.id } }));
        rememberGraphVisit(workspaceId, graphId, detail?.id);
        const explicitTask = Boolean(request.prompt || request.graphAction && request.graphAction !== "none");
        if (detail && (!explicitTask || request.intent === "study")) {
          window.dispatchEvent(new CustomEvent("learngraph:new-conversation"));
          updateSessionCaches(queryClient, workspaceId, detail);
          setActiveSessionId(detail.id);
          publishProjectContext(project, detail.id);
          if (request.nodeId) rememberGraphVisit(workspaceId, `${graphId}/${request.nodeId}`, detail.id);
          // Reused and newly-created destinations use the same one-shot route
          // contract; a context event alone only prefills the composer.
          navigate(`${base}/chat/${detail.id}`, request.prompt ? { state: {
            pendingPrompt: request.prompt.trim(),
            learningNode: { graphId, nodeId: request.nodeId, nodeLabel: request.nodeLabel },
            ...(request.graphAction ? { pendingGraphAction: request.graphAction } : {}),
          } } : undefined);
          onNavigate?.();
          return;
        }
        if (!request.nodeId && !explicitTask) {
          await createConversation({ projectId: project.id, project });
          return;
        }
        const creation = graphCreates.current.then(async () => {
          if (!isLatest()) return;
          await createConversation({
          projectId: project.id, project, pendingPrompt: request.prompt?.trim(), pendingGraphAction: request.graphAction,
          learningNode: { graphId, nodeId: request.nodeId, nodeLabel: request.nodeLabel },
          onError: request.onError, canNavigate: isLatest,
          });
        });
        graphCreates.current = creation.catch(() => undefined);
        await creation;
      });
    },
    [base, createConversation, navigate, onNavigate, publishProjectContext, queryClient, setFolderOpen, workspaceId],
  );

  useEffect(() => {
    const open = (event: Event) => {
      const detail = (event as CustomEvent<LearningProjectRequest>).detail ?? {};
      if (!detail.graphId?.trim() || !claimLearningNodeNavigation(event)) return;
      void openLearningProject(detail).catch((error) => {
        const message = error instanceof Error ? error.message : "无法打开学习对话";
        detail.onError?.(message);
        toast.error(message);
      });
    };
    window.addEventListener("learngraph:open-learning-project", open);
    return () =>
      window.removeEventListener("learngraph:open-learning-project", open);
  }, [openLearningProject]);

  async function moveSession(sessionId: string, projectId?: string) {
    const session =
      flattenSidebarSessions(ungroupedSessions).find(
        (item) => item.id === sessionId,
      ) ??
      projects
        .flatMap((project) => flattenSidebarSessions(project.sessions))
        .find((item) => item.id === sessionId);
    if (!session) return;
    // Moving detaches the session as a root in the target group; children stay
    // nested via parent_session_id and re-nest after the sessions refetch.
    const moved: SidebarSession = {
      ...session,
      children: undefined,
      parent_session_id: session.parent_session_id ?? null,
    };
    setProjects((current) =>
      current.map((project) => {
        const remaining = nestSidebarSessions(
          flattenSidebarSessions(project.sessions)
            .filter((item) => item.id !== sessionId)
            .map(({ children: _children, ...item }) => item),
          sessionActivity,
        );
        if (project.id !== projectId) return { ...project, sessions: remaining };
        return {
          ...project,
          sessions: nestSidebarSessions(
            [
              moved,
              ...flattenSidebarSessions(remaining).map(
                ({ children: _children, ...item }) => item,
              ),
            ],
            sessionActivity,
          ),
        };
      }),
    );
    setUngroupedSessions((current) => {
      const remaining = nestSidebarSessions(
        flattenSidebarSessions(current)
          .filter((item) => item.id !== sessionId)
          .map(({ children: _children, ...item }) => item),
        sessionActivity,
      );
      if (projectId) return remaining;
      return nestSidebarSessions(
        [
          moved,
          ...flattenSidebarSessions(remaining).map(
            ({ children: _children, ...item }) => item,
          ),
        ],
        sessionActivity,
      );
    });
    if (draftSessionId.current === sessionId) releaseDraftSession(sessionId);
    if (sessionId === activeSessionId)
      publishProjectContext(
        projectId
          ? projects.find((project) => project.id === projectId)
          : undefined,
        sessionId,
      );
    try {
      await assignSessionProject(sessionId, projectId ?? null);
      await scheduleSessionRefresh(queryClient, workspaceId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "移动会话失败");
      await sessionsQuery.refetch();
    }
  }

  function closeDeleteDialog() {
    deleteRequestId.current += 1;
    setDeleteTarget(undefined);
    setDeleteImpact(undefined);
    setDeleteImpactError(undefined);
    setDeleteImpactLoading(false);
  }

  async function requestDeletion(target: SidebarDeleteTarget) {
    const requestId = deleteRequestId.current + 1;
    deleteRequestId.current = requestId;
    setDeleteTarget(target);
    setDeleteImpact(undefined);
    setDeleteImpactError(undefined);
    setDeleteImpactLoading(true);
    try {
      if (target.kind === "mixed_batch") {
        const projectImpacts = await Promise.all(
          target.projects.map((project) => getProjectDeleteImpact(project.id)),
        );
        const sessionImpact = target.sessions.length
          ? await getSessionBatchDeleteImpact(
              target.sessions.map((session) => session.id),
            )
          : undefined;
        const impact = mergeDeleteImpacts(
          [
            target.projects.length
              ? `${target.projects.length} 个项目`
              : null,
            target.sessions.length
              ? `${target.sessions.length} 个会话`
              : null,
          ]
            .filter(Boolean)
            .join(" + "),
          [
            ...projectImpacts,
            ...(sessionImpact ? [sessionImpact] : []),
          ],
        );
        if (deleteRequestId.current === requestId) {
          setDeleteTarget({
            ...target,
            projectImpacts,
            sessionImpact,
          });
          setDeleteImpact(impact);
        }
        return;
      }
      const impact =
        target.kind === "session"
          ? await getSessionDeleteImpact(target.session.id)
          : target.kind === "session_batch"
            ? await getSessionBatchDeleteImpact(
                target.sessions.map((session) => session.id),
              )
            : await getProjectDeleteImpact(target.project.id);
      if (deleteRequestId.current === requestId) setDeleteImpact(impact);
    } catch (error) {
      if (deleteRequestId.current === requestId)
        setDeleteImpactError(
          error instanceof Error ? error.message : "无法检查删除影响",
        );
    } finally {
      if (deleteRequestId.current === requestId) setDeleteImpactLoading(false);
    }
  }

  async function confirmDeletion() {
    if (!deleteTarget || !deleteImpact || deleteConfirming) return;
    setDeleteConfirming(true);
    setDeleteImpactError(undefined);
    try {
      const removedSessionIds = new Set(
        deleteTarget.kind === "session"
          ? [deleteTarget.session.id]
          : deleteTarget.kind === "session_batch"
            ? deleteTarget.sessions.map((session) => session.id)
            : deleteTarget.kind === "mixed_batch"
              ? [
                  ...deleteTarget.sessions.map((session) => session.id),
                  ...deleteTarget.projects.flatMap((project) =>
                    flattenSidebarSessions(project.sessions).map(
                      (session) => session.id,
                    ),
                  ),
                ]
              : flattenSidebarSessions(deleteTarget.project.sessions).map(
                  (session) => session.id,
                ),
      );
      const removedProjectIds = new Set(
        deleteTarget.kind === "project"
          ? [deleteTarget.project.id]
          : deleteTarget.kind === "mixed_batch"
            ? deleteTarget.projects.map((project) => project.id)
            : [],
      );
      const deletingActiveSession = removedSessionIds.has(activeSessionId);

      if (deleteTarget.kind === "session") {
        await deleteSessionRecord(
          deleteTarget.session.id,
          deleteImpact.confirmation_text,
        );
        if (draftSessionId.current === deleteTarget.session.id)
          releaseDraftSession(deleteTarget.session.id);
        setProjects((current) =>
          current.map((project) => ({
            ...project,
            sessions: nestSidebarSessions(
              flattenSidebarSessions(project.sessions)
                .filter((session) => session.id !== deleteTarget.session.id)
                .map(({ children: _children, ...session }) => session),
              sessionActivity,
            ),
          })),
        );
        setUngroupedSessions((current) =>
          nestSidebarSessions(
            flattenSidebarSessions(current)
              .filter((session) => session.id !== deleteTarget.session.id)
              .map(({ children: _children, ...session }) => session),
            sessionActivity,
          ),
        );
      } else if (deleteTarget.kind === "session_batch") {
        await deleteSessionsBatch(
          deleteTarget.sessions.map((session) => session.id),
          deleteImpact.confirmation_text,
        );
        if (
          draftSessionId.current &&
          removedSessionIds.has(draftSessionId.current)
        ) {
          releaseDraftSession(draftSessionId.current);
        }
        setProjects((current) =>
          current.map((project) => ({
            ...project,
            sessions: nestSidebarSessions(
              flattenSidebarSessions(project.sessions)
                .filter((session) => !removedSessionIds.has(session.id))
                .map(({ children: _children, ...session }) => session),
              sessionActivity,
            ),
          })),
        );
        setUngroupedSessions((current) =>
          nestSidebarSessions(
            flattenSidebarSessions(current)
              .filter((session) => !removedSessionIds.has(session.id))
              .map(({ children: _children, ...session }) => session),
            sessionActivity,
          ),
        );
      } else if (deleteTarget.kind === "mixed_batch") {
        // Projects first so their nested sessions are cleaned with the project API.
        for (let index = 0; index < deleteTarget.projects.length; index += 1) {
          const project = deleteTarget.projects[index];
          const impact = deleteTarget.projectImpacts[index];
          if (!impact) throw new Error(`缺少项目「${project.title}」的删除确认。`);
          await deleteProjectRecord(project.id, impact.confirmation_text);
        }
        if (deleteTarget.sessions.length && deleteTarget.sessionImpact) {
          await deleteSessionsBatch(
            deleteTarget.sessions.map((session) => session.id),
            deleteTarget.sessionImpact.confirmation_text,
          );
        }
        if (
          draftSessionId.current &&
          removedSessionIds.has(draftSessionId.current)
        ) {
          releaseDraftSession(draftSessionId.current);
        }
        setProjects((current) =>
          current
            .filter((project) => !removedProjectIds.has(project.id))
            .map((project) => ({
              ...project,
              sessions: nestSidebarSessions(
                flattenSidebarSessions(project.sessions)
                  .filter((session) => !removedSessionIds.has(session.id))
                  .map(({ children: _children, ...session }) => session),
                sessionActivity,
              ),
            })),
        );
        setUngroupedSessions((current) =>
          nestSidebarSessions(
            flattenSidebarSessions(current)
              .filter((session) => !removedSessionIds.has(session.id))
              .map(({ children: _children, ...session }) => session),
            sessionActivity,
          ),
        );
      } else {
        await deleteProjectRecord(
          deleteTarget.project.id,
          deleteImpact.confirmation_text,
        );
        setProjects((current) =>
          current.filter((project) => project.id !== deleteTarget.project.id),
        );
      }

      if (deletingActiveSession) {
        const nextSession = sessionsQuery.data?.find(
          (session) =>
            session.status !== "archived" &&
            !removedSessionIds.has(session.id) &&
            !(session.project_id && removedProjectIds.has(session.project_id)),
        );
        setActiveSessionId(nextSession?.id ?? "");
        publishProjectContext(
          findProjectForSession(projects, nextSession?.id),
          nextSession?.id,
        );
        navigate(
          nextSession ? `${base}/chat/${nextSession.id}` : `${base}/chat/new`,
          { replace: true },
        );
        onNavigate?.();
      }

      window.dispatchEvent(new CustomEvent('learngraph:sessions-deleted', {
        detail: { workspaceId, sessionIds: [...removedSessionIds], projectIds: [...removedProjectIds] },
      }));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "projects") }),
        scheduleSessionRefresh(queryClient, workspaceId),
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "memory") }),
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "memories") }),
        queryClient.invalidateQueries({ queryKey: ["memory-pending"] }),
        queryClient.invalidateQueries({ queryKey: ["memory-drafts"] }),
        queryClient.invalidateQueries({ queryKey: ["memory-episodes"] }),
        queryClient.invalidateQueries({ queryKey: ["memory-context-manifests"] }),
      ]);
      toast.success(
        deleteTarget.kind === "session"
          ? `已删除会话「${deleteTarget.session.title}」`
          : deleteTarget.kind === "session_batch"
            ? `已批量删除 ${deleteTarget.sessions.length} 个会话`
            : deleteTarget.kind === "mixed_batch"
              ? `已删除 ${deleteTarget.projects.length} 个项目与 ${deleteTarget.sessions.length} 个会话`
              : `已删除项目「${deleteTarget.project.title}」及相关影响`,
      );
      closeDeleteDialog();
    } catch (error) {
      setDeleteImpactError(
        error instanceof Error ? error.message : "删除失败，请稍后重试",
      );
    } finally {
      setDeleteConfirming(false);
    }
  }

  async function renameProject(project: SidebarProject) {
    const title = window.prompt("输入新的项目名称：", project.title)?.trim();
    if (!title || title === project.title) return;
    try {
      await updateProjectRecord(project.id, { title });
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "projects") });
      toast.success("项目名称已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "项目改名失败");
    }
  }

  function patchSidebarSessionTree(
    sessions: SidebarSession[],
    sessionId: string,
    patch: Partial<SidebarSession>,
  ): SidebarSession[] {
    let changed = false;
    const next = sessions.map((session) => {
      if (session.id === sessionId) {
        changed = true;
        return { ...session, ...patch };
      }
      if (!session.children?.length) return session;
      const children = patchSidebarSessionTree(
        session.children,
        sessionId,
        patch,
      );
      if (children === session.children) return session;
      changed = true;
      return { ...session, children };
    });
    return changed ? next : sessions;
  }

  function patchSidebarSession(sessionId: string, patch: Partial<SidebarSession>) {
    setProjects(current => current.map(project => {
      const sessions = patchSidebarSessionTree(project.sessions, sessionId, patch);
      return sessions === project.sessions ? project : { ...project, sessions };
    }));
    setUngroupedSessions(current => patchSidebarSessionTree(current, sessionId, patch));
  }
  const pendingSessionMutations = useRef(new Set<string>());

  async function renameSession(session: SidebarSession, title: string) {
    const nextTitle = title.trim();
    if (!nextTitle || nextTitle === session.title) return true;
    try {
      const updated = await updateSession(session.id, { title: nextTitle });
      patchSidebarSession(session.id, { title: updated.title });
      updateSessionCaches(queryClient, workspaceId, updated);
      toast.success("会话名称已更新");
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "会话改名失败");
      return false;
    }
  }

  async function toggleSessionPin(session: SidebarSession) {
    if (pendingSessionMutations.current.has(session.id)) return;
    pendingSessionMutations.current.add(session.id);
    patchSidebarSession(session.id, { pinned: !session.pinned });
    try {
      const updated = await updateSession(session.id, { pinned: !session.pinned });
      patchSidebarSession(session.id, { pinned: updated.pinned });
      updateSessionCaches(queryClient, workspaceId, updated);
      toast.success(updated.pinned ? "会话已置顶" : "已取消置顶");
    } catch (error) {
      patchSidebarSession(session.id, { pinned: session.pinned });
      toast.error(error instanceof Error ? error.message : "置顶状态更新失败");
    } finally {
      pendingSessionMutations.current.delete(session.id);
    }
  }

  async function toggleSessionArchive(session: SidebarSession) {
    try {
      const updated = session.status === "archived"
        ? await restoreSession(session.id)
        : await archiveSession(session.id);
      patchSidebarSession(session.id, {
        status: updated.status,
      });
      updateSessionCaches(queryClient, workspaceId, updated);
      void sessionNavigator.refetch();
      toast.success(session.status === "archived" ? "会话已恢复" : "会话已归档");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "会话状态更新失败");
    }
  }

  async function shareSession(session: SidebarSession) {
    setShareTarget(session);
  }

  async function toggleProjectArchive(project: SidebarProject) {
    try {
      if (project.status === "archived") await restoreProject(project.id);
      else await archiveProject(project.id);
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "projects") });
      toast.success(
        project.status === "archived" ? "项目已恢复" : "项目已归档",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "项目状态更新失败");
    }
  }

  // Pin only "新对话"; remaining primary nav scrolls with the session list.
  const pinnedNavItem = nav.find((item) => item.label === "新对话");
  const scrollNav = nav.filter((item) => item.label !== "新对话");

  function renderNavItem(item: NavItem) {
    const Icon = item.icon;
    // "新对话" is a create action, not a route section — never keep the
    // selected chip when the user is browsing a historical session.
    const active =
      item.label === "新对话" ? false : isNavActive(pathname, item);
    const className = cn(
      "flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
      collapsed && !mobile && "h-10 px-0",
      active &&
        "bg-sidebar-accent font-medium text-sidebar-accent-foreground",
    );
    if (item.label === "新对话") {
      return (
        <button
          aria-label="新建对话"
          className={className}
          data-nav-label={item.label}
          disabled={creatingConversation}
          key={`${item.label}-${item.path}`}
          onClick={() =>
            void createConversation({
              projectId: selectedProjectId,
              fromNewConversationNav: true,
            })
          }
          type="button"
        >
          <Icon
            className="size-4.5 shrink-0"
            strokeWidth={2.25}
          />
          <span className="sidebar-text">
            {creatingConversation ? "创建中…" : item.label}
          </span>
        </button>
      );
    }
    return (
      <NavLink
        aria-label={item.label}
        className={className}
        data-nav-label={item.label}
        key={`${item.label}-${item.path}`}
        onClick={onNavigate}
        to={`${base}/${item.path}`}
      >
        <Icon
          className={cn("size-4.5 shrink-0", active && "text-primary")}
          strokeWidth={2.25}
        />
        <span className="sidebar-text">{item.label}</span>
      </NavLink>
    );
  }

  return (
    <div
      className={cn(
        "sidebar-nav flex h-full flex-col bg-sidebar px-3 py-4 text-sidebar-foreground",
        collapsed && "is-collapsed",
        mobile && "is-mobile",
      )}
      data-sidebar-state={collapsed && !mobile ? "collapsed" : "expanded"}
    >
      <div className="sidebar-nav__top flex items-center justify-between">
        {collapsed && !mobile ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label="展开侧边栏"
                aria-expanded={false}
                className="sidebar-brand flex items-center gap-2 px-2 py-2"
                data-sidebar-toggle="expand"
                onClick={onCollapse}
                variant="ghost"
              >
                <span
                  className={cn(
                    "sidebar-brand__mark grid place-items-center",
                    collapsed && !mobile
                      ? "size-7 shrink-0 rounded-full!"
                      : "size-8 rounded-xl",
                  )}
                >
                  <Network className="size-4" />
                </span>
                <span className="sidebar-text text-lg font-semibold">
                  LearnGraph
                </span>
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">展开侧边栏</TooltipContent>
          </Tooltip>
        ) : (
          <div className="sidebar-brand-group">
          <Link
            className="sidebar-brand flex items-center gap-2 px-2 py-2"
            onClick={onNavigate}
            to={`${base}/home`}
          >
            <span
              className={cn(
                "sidebar-brand__mark grid place-items-center",
                collapsed && !mobile
                  ? "size-7 shrink-0 rounded-full!"
                  : "size-8 rounded-xl",
              )}
            >
              <Network className="size-4" />
            </span>
            <span className="sidebar-text text-lg font-semibold">
              LearnGraph
            </span>
          </Link>
          <SidebarVersion isSystemAdmin={isSystemAdmin} />
          </div>
        )}
        <div className="sidebar-nav__top-actions">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button aria-label="搜索文件、会话和消息" aria-haspopup="dialog" className="sidebar-nav__search" onClick={() => setSearchOpen(true)} size="icon-sm" variant="ghost"><Search className="size-4.5" /></Button>
            </TooltipTrigger>
            <TooltipContent side={collapsed && !mobile ? "right" : "bottom"}>搜索文件、会话和消息</TooltipContent>
          </Tooltip>
          {!mobile && !collapsed ? (
            <Button
              aria-label="折叠侧边栏"
              aria-expanded={true}
              data-sidebar-toggle="collapse"
              onClick={onCollapse}
              size="icon-sm"
              title="折叠侧边栏"
              variant="ghost"
            >
              <PanelLeftClose />
            </Button>
          ) : null}
        </div>
      </div>
      {searchOpen ? <Suspense fallback={null}><WorkspaceSearchDialog open onOpenChange={setSearchOpen} onNavigate={onNavigate} workspaceId={workspaceId} /></Suspense> : null}

      {/* Only "新对话" stays pinned; remaining nav + sessions share one scroller. */}
      {pinnedNavItem ? (
        <div className="sidebar-nav__primary mt-4">
          {renderNavItem(pinnedNavItem)}
        </div>
      ) : null}

      <div className="sidebar-nav__scroll">
        {scrollNav.length || moreNav.length ? (
          <nav
            aria-label="主导航"
            className={cn("space-y-1", !pinnedNavItem && "mt-4")}
          >
            {scrollNav.map((item) => renderNavItem(item))}
            {moreNav.length ? (
              <div className="sidebar-nav__more mt-1">
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      aria-label="更多"
                      className={cn(
                        "w-full justify-start gap-3 rounded-xl px-3",
                        moreActive &&
                          "bg-sidebar-accent font-medium text-sidebar-accent-foreground",
                      )}
                      size="sm"
                      variant="ghost"
                    >
                      <MoreHorizontal className="size-4.5" strokeWidth={2.25} />
                      <span className="sidebar-text">更多</span>
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="start" className="w-52">
                    {moreNav.map((item) => {
                      const Icon = item.icon;
                      return (
                        <DropdownMenuItem
                          key={`${item.label}-${item.path}`}
                          onSelect={() => {
                            navigate(`${base}/${item.path}`);
                            onNavigate?.();
                          }}
                        >
                          <Icon className="size-4" strokeWidth={2.25} />
                          {item.label}
                        </DropdownMenuItem>
                      );
                    })}
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            ) : null}
          </nav>
        ) : null}

        <SessionProjects
          navigator={sessionNavigator}
          activeSessionId={activeSessionId}
          creatingConversation={creatingConversation}
          folderState={folderState}
          selectedProjectId={selectedProjectId}
          onToggleFolder={setFolderOpen}
          onToggleFolders={setFoldersOpen}
          workspaceId={workspaceId}
          onCreateConversation={(projectId) => {
            const targetProjectId = projectId === null ? undefined : projectId ?? selectedProjectId;
            return createConversation({
              projectId: targetProjectId,
            });
          }}
          onCreateProject={async (title) => {
            const created = await createProjectRecord({ title });
            await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "projects") });
            return created.id;
          }}
          onRequestProjectDeletion={(project) =>
            void requestDeletion({ kind: "project", project })
          }
          onRenameProject={renameProject}
          onToggleProjectArchive={toggleProjectArchive}
          onMoveSession={moveSession}
          onRequestSessionDeletion={(session) =>
            void requestDeletion({ kind: "session", session })
          }
          onRenameSession={renameSession}
          onToggleSessionPin={(session) => void toggleSessionPin(session)}
          onToggleSessionArchive={(session) => void toggleSessionArchive(session)}
          onShareSession={(session) => void shareSession(session)}
          onRequestSessionBatchDeletion={(sessions) =>
            void requestDeletion({ kind: "session_batch", sessions })
          }
          onRequestMixedBatchDeletion={(projects, sessions) =>
            void requestDeletion({
              kind: "mixed_batch",
              projects,
              sessions,
              projectImpacts: [],
            })
          }
          onSelectSession={(sessionId, project) => {
            markSessionViewed(sessionId);
            setActiveSessionId(sessionId);
            setSelectedProjectId(project?.id);
            publishProjectContext(project, sessionId);
            navigate(`${base}/chat/${sessionId}`);
            onNavigate?.();
          }}
          onSelectProject={(projectId) => setSelectedProjectId(projectId)}
          projects={projects}
          ungroupedSessions={ungroupedSessions}
        />
      </div>

      <DeleteImpactDialog
        confirmLabel={
          deleteTarget?.kind === "project"
            ? "删除项目"
            : deleteTarget?.kind === "session_batch"
              ? `删除 ${deleteTarget.sessions.length} 个会话`
              : deleteTarget?.kind === "mixed_batch"
                ? `删除选中项`
                : "删除会话"
        }
        error={deleteImpactError}
        impact={deleteImpact}
        isConfirming={deleteConfirming}
        isLoading={deleteImpactLoading}
        objectLabel={
          deleteTarget?.kind === "project"
            ? deleteTarget.project.title
            : deleteTarget?.kind === "session_batch"
              ? `${deleteTarget.sessions.length} 个会话`
              : deleteTarget?.kind === "mixed_batch"
                ? [
                    deleteTarget.projects.length
                      ? `${deleteTarget.projects.length} 个项目`
                      : null,
                    deleteTarget.sessions.length
                      ? `${deleteTarget.sessions.length} 个会话`
                      : null,
                  ]
                    .filter(Boolean)
                    .join("、")
                : (deleteTarget?.session.title ?? "会话")
        }
        onConfirm={confirmDeletion}
        onOpenChange={(open) => {
          if (!open && !deleteConfirming) closeDeleteDialog();
        }}
        open={Boolean(deleteTarget)}
        title={
          deleteTarget?.kind === "project"
            ? `永久删除项目「${deleteTarget.project.title}」？`
            : deleteTarget?.kind === "session_batch"
              ? `永久删除选中的 ${deleteTarget.sessions.length} 个会话？`
              : deleteTarget?.kind === "mixed_batch"
                ? `永久删除选中的 ${deleteTarget.projects.length} 个项目与 ${deleteTarget.sessions.length} 个会话？`
                : deleteTarget
                  ? `永久删除会话「${deleteTarget.session.title}」？`
                  : undefined
        }
      />
      <SessionShareDialog
        onOpenChange={(next) => {
          if (!next) setShareTarget(null);
        }}
        open={Boolean(shareTarget)}
        sessionId={shareTarget?.id ?? ""}
        sessionTitle={shareTarget?.title ?? ""}
      />
      <UserMenu
        collapsed={collapsed}
        mobile={mobile}
        onNavigate={onNavigate}
      />
    </div>
  );
}

/**
 * 侧栏是一棵重树（项目 / 会话树 + 各自查询）。抽屉开合这类外壳状态变化不该让
 * 它整棵重渲染 —— 手机上它甚至是隐藏的，却要为此花掉几十毫秒。
 */
const MemoSidebarNav = memo(SidebarNav);


function UserMenu({
  mobile = false,
  collapsed = false,
  onNavigate,
}: {
  mobile?: boolean;
  collapsed?: boolean;
  /** 手机抽屉里由外壳传入：菜单项跳转前先收起抽屉，别让抽屉留在设置面板背后。 */
  onNavigate?: () => void;
}) {
  const { logout, username, avatarUrl, workspaceId, workspaceName } = useAuth();
  const navigate = useNavigate();
  const defaultAvatar = defaultUserAvatar(username);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={`打开用户菜单（${username}）`}
          className={cn(
            "sidebar-user-menu mt-auto h-auto w-full shrink-0 justify-start gap-3 overflow-hidden rounded-xl px-2 py-2",
            collapsed && !mobile && "px-0",
          )}
          variant="ghost"
        >
          <Avatar
            className={cn(
              "shrink-0",
              collapsed && !mobile ? "size-6" : "size-8",
            )}
          >
            {avatarUrl ? <AvatarImage alt={`${username}的头像`} src={avatarUrl} /> : null}
            <AvatarFallback className="sidebar-user-avatar text-xs font-semibold" style={defaultAvatar.style}>
              {defaultAvatar.initial}
            </AvatarFallback>
          </Avatar>
          <span className="sidebar-text min-w-0 flex-1 overflow-hidden text-left">
            <span
              className="block truncate text-sm font-medium"
              title={username}
            >
              {username}
            </span>
            <span
              className="block truncate text-xs font-normal text-muted-foreground"
              title={workspaceName}
            >
              {workspaceName}
            </span>
          </span>
          {!mobile && !collapsed ? (
            <ChevronDown className="size-3.5 text-muted-foreground" />
          ) : null}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-56" side="top">
        <DropdownMenuLabel>个人工作区</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            onNavigate?.();
            navigate(`/w/${workspaceId}/settings/general`);
          }}
        >
          <Settings />
          设置
        </DropdownMenuItem>
        <DropdownMenuItem
          onSelect={() => {
            onNavigate?.();
            navigate(`/w/${workspaceId}/memory`);
          }}
        >
          <Archive />
          工作区记忆
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          onSelect={() => {
            onNavigate?.();
            void logout();
          }}
          variant="destructive"
        >
          <LogOut />
          退出登录
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** 顶栏 / 阅读页头的导航按钮：只负责请求打开抽屉，状态由外壳持有。 */
function MobileNavTrigger({ onOpen }: { onOpen: () => void }) {
  return (
    <Button
      aria-label="打开导航"
      className="topbar-menu-trigger lg:hidden"
      onClick={onOpen}
      size="icon"
      variant="ghost"
    >
      <Menu className="size-5" />
    </Button>
  );
}

/**
 * 左侧导航抽屉：常驻 DOM（收起时 visibility: hidden），位移由 CSS 变量
 * `--lg-left-drawer-progress` 驱动 —— 展开/收起补间、跟手拖拽、遮罩浓度共用
 * 同一个进度值。内容趁空闲预热后一直保留：现挂载一次要 100ms+（会话列表 +
 * 其查询），如果发生在跟手第一帧，抽屉会先空着、卡住，再突然跳出内容。
 */
function MobileNavDrawer({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const dragging = useDraggingDrawer("left");
  const [contentMounted, setContentMounted] = useState(false);
  const panelRef = useRef<HTMLDivElement | null>(null);
  const handleNavigate = useCallback(() => onOpenChange(false), [onOpenChange]);

  useEffect(() => {
    if (contentMounted) return;
    // 抽屉只在窄屏用得上；宽屏桌面侧栏常驻，不必再多挂一份。
    if (!window.matchMedia("(max-width: 1024px)").matches) return;
    const windowWithIdle = window as Window & {
      requestIdleCallback?: (
        callback: () => void,
        options?: { timeout: number },
      ) => number;
      cancelIdleCallback?: (handle: number) => void;
    };
    if (typeof windowWithIdle.requestIdleCallback === "function") {
      const handle = windowWithIdle.requestIdleCallback(
        () => setContentMounted(true),
        { timeout: 1500 },
      );
      return () => windowWithIdle.cancelIdleCallback?.(handle);
    }
    const timer = window.setTimeout(() => setContentMounted(true), 600);
    return () => window.clearTimeout(timer);
  }, [contentMounted]);

  useEffect(() => {
    // 预热还没轮到时就被拉起：宁可卡一帧，也不能是空面板。
    if (open || dragging) setContentMounted(true);
  }, [dragging, open]);

  useModalDrawerFocus(panelRef, open, handleNavigate);

  return (
    <>
      <div
        aria-hidden="true"
        className={cn("mobile-nav-drawer-overlay", open && "is-open")}
        data-drawer-surface="nav"
        onClick={() => onOpenChange(false)}
      />
      <div
        aria-label="LearnGraph 导航"
        aria-hidden={!open && !dragging}
        aria-modal={open ? true : undefined}
        className={cn("mobile-nav-drawer", open && "is-open")}
        data-drawer-state={open ? "open" : "closed"}
        data-drawer-surface="nav"
        inert={!open && !dragging ? true : undefined}
        ref={panelRef}
        role="dialog"
        tabIndex={-1}
      >
        {contentMounted ? (
          <MemoSidebarNav mobile onNavigate={handleNavigate} />
        ) : null}
      </div>
    </>
  );
}

const topbarStatusLabels: Record<string, string> = {
  database: "数据库",
  local_storage: "本地存储",
  model_provider: "模型服务",
  search_provider: "搜索服务",
};

function statusIsHealthy(status: string) {
  return ["healthy", "healthy_local", "enabled"].includes(status);
}

/** Bound goal/graph/node info for the active chat session, bridged from the
 *  chat page via the `learngraph:chat-header` window event. */
type ChatHeaderInfo = {
  goalBound?: boolean;
  graphTitle?: string;
  learningNodeActive?: boolean;
  learningNodeLabel?: string;
  modelConnected: boolean;
};

/** Shared body of the merged status + conversation-context menu. Both the
 *  desktop dot trigger and the mobile page-tools menu render this so the two
 *  former menus (系统状态 / 上下文) stay consistent across breakpoints. */
function TopBarStatusMenuBody({
  chatHeader,
  contextActive,
  isChat,
  systemStatuses,
  workspaceName,
}: {
  chatHeader?: ChatHeaderInfo;
  contextActive: boolean;
  isChat: boolean;
  systemStatuses: [string, string][];
  workspaceName: string;
}) {
  return (
    <>
      <DropdownMenuLabel>{workspaceName || "当前工作区"}</DropdownMenuLabel>
      <DropdownMenuSeparator />
      <DropdownMenuItem disabled>
        <Activity className="size-4" />
        API 会话有效
      </DropdownMenuItem>
      {isChat ? (
        <>
          <DropdownMenuItem disabled>
            <Bot className="size-4" />
            {chatHeader?.modelConnected ? "模型已连接" : "模型不可用"}
          </DropdownMenuItem>
          {contextActive ? (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuLabel>本轮对话上下文</DropdownMenuLabel>
              {chatHeader?.goalBound ? (
                <div className="chat-context-menu__row">
                  <Target className="size-3.5" />
                  <span className="min-w-0 flex-1 truncate">已绑定目标</span>
                </div>
              ) : null}
            </>
          ) : null}
        </>
      ) : systemStatuses.length ? (
        systemStatuses.map(([key, value]) => (
          <DropdownMenuItem disabled key={key}>
            <CircleDot className="size-4" />
            <span className="flex-1">{topbarStatusLabels[key] ?? key}</span>
            <span className="text-xs text-muted-foreground">
              {statusIsHealthy(value) ? "正常" : "不可用"}
            </span>
          </DropdownMenuItem>
        ))
      ) : (
        <DropdownMenuItem disabled>正在读取运行状态…</DropdownMenuItem>
      )}
    </>
  );
}

function TopBar({
  graphOpen,
  onOpenActivity,
  onOpenNav,
  onToggleGraph,
  railCollapsed,
  onToggleRail,
}: {
  graphOpen?: boolean;
  onOpenActivity: () => void;
  onOpenNav: () => void;
  onToggleGraph?: () => void;
  railCollapsed?: boolean;
  onToggleRail?: () => void;
}) {
  const { pathname, search } = useLocation();
  const { workspaceId = "" } = useParams();
  const sessionId = new URLSearchParams(search).get("sidebarSession") ?? pathname.match(/\/chat\/([^/]+)/)?.[1];
  const auth = useAuth();
  const isChat = pathname.includes("/chat/");
  const isMemory = pathname.includes("/memory");
  // 页面预览的全局暂停只属于「产物」页，其它页面顶栏不再常驻该按钮。
  const isArtifacts = pathname.includes("/artifacts");
  const previewsPaused = useAllPreviewsPaused();
  const dashboard = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "dashboard"),
    queryFn: getDashboard,
    staleTime: 30_000,
    enabled: !isChat,
  });
  const [chatHeader, setChatHeader] = useState<ChatHeaderInfo>();
  useEffect(() => {
    // 信息密度固定为「舒适」，不再提供切换：清掉旧偏好与残留样式。
    delete document.documentElement.dataset.density;
    window.localStorage.removeItem("lg-information-density");
  }, []);
  useEffect(() => {
    const updateHeader = (event: Event) => {
      const detail = (event as CustomEvent<ChatHeaderInfo>).detail;
      if (detail) setChatHeader(detail);
    };
    window.addEventListener("learngraph:chat-header", updateHeader);
    return () =>
      window.removeEventListener("learngraph:chat-header", updateHeader);
  }, []);
  const systemStatuses = Object.entries(dashboard.data?.system_status ?? {});
  const systemHealthy = isChat
    ? Boolean(chatHeader?.modelConnected)
    : systemStatuses.length > 0 && systemStatuses.every(([, value]) => statusIsHealthy(value));
  // Graph and node bindings live in the menu's binding card; goal context
  // remains beside the connection status.
  const contextActive = isChat && Boolean(chatHeader?.goalBound);
  return (
    <header className="workspace-topbar">
      <div className="workspace-topbar__left">
        <MobileNavTrigger onOpen={onOpenNav} />
        {isChat ? (
          // The chat page portals its model picker in here on phone widths.
          <div className="topbar-model-slot" id="topbar-model-slot" />
        ) : null}
      </div>
      <div className="workspace-topbar__actions">
        {isArtifacts ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-pressed={previewsPaused}
                aria-label={previewsPaused ? "恢复全部页面预览" : "暂停全部页面预览"}
                className="shrink-0"
                onClick={() => (previewsPaused ? resumeAllPreviews() : pauseAllPreviews())}
                size="icon-sm"
                title={previewsPaused ? "恢复全部页面预览" : "暂停全部页面预览"}
                variant={previewsPaused ? "default" : "ghost"}
              >
                {previewsPaused ? <Play className="size-4" /> : <Pause className="size-4" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">
              {previewsPaused ? "恢复全部页面预览" : "暂停全部页面预览"}
            </TooltipContent>
          </Tooltip>
        ) : null}
        {onToggleGraph ? (
          <Button
            aria-expanded={graphOpen}
            aria-label={
              graphOpen
                ? isMemory
                  ? "收起 AI 导师过程面板"
                  : "收起图谱面板"
                : isMemory
                  ? "展开 AI 导师过程面板"
                  : "展开图谱面板"
            }
            className="topbar-graph-toggle shrink-0"
            onClick={onToggleGraph}
            size="icon-sm"
            variant="ghost"
          >
            {isMemory ? <Brain className="size-4" /> : <Network className="size-4" />}
          </Button>
        ) : null}
        <div className="hidden items-center gap-0.5 md:flex">
          {onToggleRail ? (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  aria-expanded={!railCollapsed}
                  aria-label={
                    railCollapsed
                      ? isMemory
                        ? "展开 AI 导师过程"
                        : "展开图谱书架"
                      : isMemory
                        ? "折叠 AI 导师过程"
                        : "折叠图谱书架"
                  }
                  className="topbar-rail-toggle shrink-0"
                  data-rail-toggle={railCollapsed ? "expand" : "collapse"}
                  onClick={onToggleRail}
                  size="icon-sm"
                  title={
                    railCollapsed
                      ? isMemory
                        ? "展开 AI 导师过程"
                        : "展开图谱书架"
                      : isMemory
                        ? "折叠 AI 导师过程"
                        : "折叠图谱书架"
                  }
                  variant="ghost"
                >
                  {isMemory ? <Brain className="size-4" /> : <Network className="size-4" />}
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">
                {railCollapsed
                  ? isMemory
                    ? "展开 AI 导师过程"
                    : "展开图谱书架"
                  : isMemory
                    ? "折叠 AI 导师过程"
                    : "折叠图谱书架"}
              </TooltipContent>
            </Tooltip>
          ) : null}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              aria-label="打开页面工具"
              className="topbar-status relative shrink-0"
              size="icon-sm"
              title="图谱绑定、系统状态与学习活动"
              variant="ghost"
            >
              <SlidersHorizontal className="size-4" />
              <span
                aria-hidden="true"
                className={cn(
                  "topbar-status__dot topbar-status__dot--badge",
                  systemHealthy && "is-healthy",
                )}
              />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="chat-context-menu w-72">
            {isChat ? <>
              <SessionGraphBindingMenu key={sessionId} workspaceId={workspaceId} sessionId={sessionId} />
              <DropdownMenuSeparator />
            </> : null}
            <DropdownMenuItem onSelect={onOpenActivity}>
              <CalendarDays className="size-4" />
              学习活动
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <TopBarStatusMenuBody
              chatHeader={chatHeader}
              contextActive={contextActive}
              isChat={isChat}
              systemStatuses={systemStatuses}
              workspaceName={auth.workspaceName}
            />
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

export function ContextRail({
  collapsed = false,
  onSelectionExplanationChange,
  drawerSwipe = "ignore",
}: {
  collapsed?: boolean;
  onSelectionExplanationChange?: (open: boolean) => void;
  drawerSwipe?: "ignore" | "allow";
} = {}) {
  const { pathname, search } = useLocation();
  const { workspaceId = "" } = useParams();
  const [projectContext, setProjectContext] = useState<{
    project?: SidebarProject;
    sessionId?: string;
  }>();
  const [selectionExplanation, setSelectionExplanation] =
    useState<SelectionExplanationOpenDetail | null>(null);
  const [contentMounted, setContentMounted] = useState(!collapsed);
  useEffect(() => { if (!collapsed) setContentMounted(true); }, [collapsed]);
  const railProjects = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "projects"),
    queryFn: () => listProjects(),
    staleTime: 30_000,
  });
  const railCounts = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "sessions", "counts"),
    queryFn: ({ signal }) => getSessionCounts({ signal }),
    staleTime: 30_000, enabled: !collapsed && !pathname.includes("/chat/") && !pathname.includes("/settings/"),
  });
  const isSettings = pathname.includes("/settings/");
  const isMemory = pathname.includes("/memory");
  const isGoalClarify = pathname.includes("/goals/new/clarify");
  const isGraph = pathname.includes("/graphs/");
  const isChat = pathname.includes("/chat/");
  const isGoalMode = isChat && new URLSearchParams(search).get("mode") === "goal";
  const sessionId =
    new URLSearchParams(search).get("sidebarSession") ??
    pathname.match(/\/chat\/([^/]+)/)?.[1];
  const railSession = useSessionDetail(workspaceId, sessionId);
  const activeSession = railSession.data;
  const creationParentSessionId = activeSession?.parent_session_id ?? sessionId;
  const showActivity = [
    "/home",
    "/sources",
    "/evidence/",
    "/research/",
    "/practice",
  ].some((route) => pathname.includes(route));
  const items = useMemo(
    () =>
      isSettings
        ? [
            ["作用域", "仅当前工作区"],
            ["密钥", "浏览器不保存明文"],
            ["审计", "敏感操作留痕"],
            ["远程能力", "默认关闭"],
          ]
        : [
            [
              "Projects",
              `${railProjects.data?.filter((item) => item.status !== "archived").length ?? 0} 个活跃项目`,
            ],
            [
              "Sessions",
              `${railCounts.data?.active ?? "—"} 个活跃会话`,
            ],
            [
              "Memory",
              `${railCounts.data?.memory_enabled ?? "—"} 个会话已启用`,
            ],
            ["Scope", sessionId ? "当前会话" : "当前工作区"],
          ],
    [isSettings, railProjects.data, railCounts.data, sessionId],
  );

  useEffect(() => {
    const session = railSession.data;
    const draftProjectId = new URLSearchParams(search).get("project");
    const project = railProjects.data?.find(
      (item) => item.id === (session?.project_id ?? (sessionId === "new" ? draftProjectId : undefined)),
    );
    setProjectContext({
      project: project
        ? {
            id: project.id,
            title: project.title,
            status: project.status,
            graphId: project.primary_graph_id ?? undefined,
            graphTitle: project.title,
            sessions: [],
          }
        : undefined,
      sessionId,
    });
  }, [railProjects.data, railSession.data, sessionId, search]);

  useEffect(() => {
    const update = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          workspaceId?: string;
          project?: SidebarProject;
          sessionId?: string;
        }>
      ).detail;
      if (!detail || detail.workspaceId !== workspaceId) return;
      setProjectContext({
        project: detail.project,
        sessionId: detail.sessionId,
      });
    };
    window.addEventListener("learngraph:project-context", update);
    return () => {
      window.removeEventListener("learngraph:project-context", update);
    };
  }, [sessionId, workspaceId]);

  useEffect(() => {
    setSelectionExplanation(null);

    const applyDetail = (
      detail: (SelectionExplanationOpenDetail & { sessionId?: string }) | null,
    ) => {
      if (!detail) return;
      const parentSessionId = detail.parentSessionId || detail.sessionId;
      if (!parentSessionId || parentSessionId !== sessionId) return;
      if (!detail.selectedText?.trim() && !detail.recordId) return;
      setSelectionExplanation({
        ...detail,
        parentSessionId,
        sourceMessageId: detail.sourceMessageId || "",
        selectedText: detail.selectedText?.trim() || "",
      });
    };

    // Claim any open that fired while this rail was unmounted (collapsed).
    applyDetail(consumePendingSelectionExplanation(sessionId));

    const openExplanation = (event: Event) => {
      applyDetail(
        (event as CustomEvent<SelectionExplanationOpenDetail & {
          sessionId?: string;
        }>).detail,
      );
    };
    window.addEventListener(
      selectionExplanationOpenEventName(),
      openExplanation,
    );
    return () =>
      window.removeEventListener(
        selectionExplanationOpenEventName(),
        openExplanation,
      );
  }, [sessionId]);

  useEffect(() => {
    onSelectionExplanationChange?.(Boolean(selectionExplanation && isChat));
  }, [isChat, onSelectionExplanationChange, selectionExplanation]);

  const activeProject =
    projectContext && projectContext.sessionId === sessionId
      ? projectContext.project
      : undefined;

  return (
    <aside
      aria-hidden={collapsed || undefined}
      className="context-rail min-h-svh bg-card px-4 py-5"
      data-rail-state={collapsed ? "collapsed" : "expanded"}
      data-drawer-swipe={drawerSwipe}
      // Collapsed rail stays mounted for CSS slide; inert keeps it out of tab order.
      inert={collapsed || undefined}
    >
      {contentMounted && ((isGoalClarify || isGoalMode) && !selectionExplanation ? (
        <GoalGraphPreviewRail />
      ) : isGraph ? (
        <GraphWorkspaceRail />
      ) : isChat ? (
        <ChatGraphRail
          selectionExplanation={selectionExplanation}
          onCloseExplanation={() => setSelectionExplanation(null)}
          creationParentSessionId={creationParentSessionId}
          project={activeProject}
          session={activeSession}
          sessionId={sessionId}
          workspaceId={workspaceId}
        />
      ) : isMemory ? (
        <Suspense fallback={<div role="status">正在打开导师过程…</div>}><MemoryProcessRail workspaceId={workspaceId} /></Suspense>
      ) : showActivity ? (
        <ActivityRail />
      ) : (
        <div className="sticky top-5 space-y-5">
          <div>
            <p className="text-sm font-semibold">
              {isSettings ? "安全边界" : "当前上下文"}
            </p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              {isSettings
                ? "配置变更只在任务边界生效。"
                : "回答仅注入明确选择的资源。"}
            </p>
          </div>
          <dl className="space-y-3">
            {items.map(([label, value]) => (
              <div className="border-b pb-3" key={label}>
                <dt className="text-xs uppercase tracking-wide text-muted-foreground">
                  {label}
                </dt>
                <dd className="mt-1 text-sm font-medium">{value}</dd>
              </div>
            ))}
          </dl>
          <div className="rounded-xl border bg-muted/35 p-3">
            <p className="flex items-center gap-2 text-xs font-medium">
              <ShieldCheck className="size-4 text-primary" />
              能力边界
            </p>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">
              每次模型、搜索与研究结果都保留 Provider
              能力标记；本地演示结果不会伪装成远程调用。
            </p>
          </div>
        </div>
      ))}
    </aside>
  );
}

/** 图谱 / 上下文侧栏同样是重树：只在自身数据或抽屉开合相关 props 变化时更新。 */
const MemoContextRail = memo(ContextRail);

function ActivityRail() {
  return (
    <section className="activity-rail" aria-label="学习活动日历">
      <ActivityHeatmap variant="rail" />
    </section>
  );
}

function ChatGraphRail({
  selectionExplanation,
  onCloseExplanation,
  creationParentSessionId,
  project,
  session,
  sessionId,
  workspaceId,
}: {
  selectionExplanation?: SelectionExplanationOpenDetail | null;
  onCloseExplanation?: () => void;
  creationParentSessionId?: string;
  project?: SidebarProject;
  session?: Session;
  sessionId?: string;
  workspaceId: string;
}) {
  const queryClient = useQueryClient();
  const [view, setView] = useState<
    | "learning"
    | "capability"
    | "files"
    | "conversation"
    | "voice"
  >("learning");
  const [sideConversationFocusId, setSideConversationFocusId] = useState<string>();
  const [creatingSideConversation, setCreatingSideConversation] = useState(false);
  const creatingSideConversationRef = useRef(false);
  useEffect(() => {
    setSideConversationFocusId(undefined);
  }, [sessionId]);
  const { binding: sessionBinding, boundNodeId } = useSessionGraphBinding(workspaceId, sessionId);
  const [bookshelfOpen, setBookshelfOpen] = useState(false);
  useEffect(() => {
    const opened = (event: Event) => {
      if ((event as CustomEvent<{ workspaceId: string }>).detail?.workspaceId === workspaceId) setBookshelfOpen(false);
    };
    window.addEventListener("learngraph:graph-folder-selected", opened);
    return () => window.removeEventListener("learngraph:graph-folder-selected", opened);
  }, [workspaceId]);
  useEffect(() => {
    setBookshelfOpen(false);
    if (session?.graph_id && sessionId && sessionId !== "new") {
      rememberGraphVisit(workspaceId, session.graph_id, sessionId);
      if (session.current_node_id) rememberGraphVisit(workspaceId, `${session.graph_id}/${session.current_node_id}`, sessionId);
    }
  }, [workspaceId, sessionId, session?.graph_id, session?.current_node_id]);
  const binding = sessionBinding ?? (!session && project?.graphId ? {
    graphId: project.graphId,
    graphTitle: project.graphTitle || project.title,
  } : undefined);

  const settings = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "settings"),
    queryFn: listSettings,
    staleTime: 30_000,
  });
  const voiceDebugEnabled = isVoiceDebugPanelEnabled(settings.data);

  // 同一个约定：语音调试面板关掉时栏目整个不渲染，视点也不能停在它上面。
  useEffect(() => {
    if (!voiceDebugEnabled && view === "voice") setView("learning");
  }, [voiceDebugEnabled, view]);

  // Keep the selected rail tab while switching between conversations. This
  // makes the right side feel like a persistent workspace instead of a page
  // fragment that resets every time the main chat changes.
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem("learngraph:right-rail-view");
      if (
        stored === "learning" ||
        stored === "capability" ||
        stored === "files" ||
        stored === "conversation" ||
        stored === "voice"
      ) {
        setView(stored);
      }
    } catch {
      // Local storage is optional; the rail remains fully usable in private
      // browsing modes where storage can be denied.
    }
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem("learngraph:right-rail-view", view);
    } catch {
      // See the read-side comment above.
    }
  }, [view]);

  const createSideConversation = useCallback(async () => {
    setView("conversation");
    if (creatingSideConversationRef.current) return;
    creatingSideConversationRef.current = true;
    setCreatingSideConversation(true);
    try {
      const parentSessionId =
        session?.parent_session_id ??
        (sessionId && sessionId !== "new" ? sessionId : null);
      const created = await createSession({
        title: "侧边对话",
        session_kind: "side",
        parent_session_id: parentSessionId,
      });
      queryClient.setQueryData<Session[]>(
        workspaceQueryKey(workspaceId, "sessions"),
        (current) => [
          created,
          ...(current ?? []).filter((item) => item.id !== created.id),
        ],
      );
      onCloseExplanation?.();
      setSideConversationFocusId(created.id);
      toast.success("已创建侧边会话");
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "创建侧边会话失败",
      );
    } finally {
      creatingSideConversationRef.current = false;
      setCreatingSideConversation(false);
    }
  }, [
    queryClient,
    onCloseExplanation,
    session?.parent_session_id,
    sessionId,
    workspaceId,
  ]);

  useEffect(() => {
    if (selectionExplanation) setView("conversation");
  }, [selectionExplanation]);

  const openGraph = (graph: GraphSummary) => {
    window.dispatchEvent(new CustomEvent("learngraph:open-learning-project", {
      detail: { graphId: graph.id, title: graph.title },
    }));
  };

  return (
    <Tabs className="chat-graph-rail" value={view} onValueChange={value => setView(value as typeof view)}>
      <div className="chat-graph-rail__switch">
        <TabsList aria-label="右侧工作区" className="chat-graph-rail__tabs" variant="line">
          <TabsTrigger value="learning">当前学习</TabsTrigger>
          <TabsTrigger value="conversation">侧边对话</TabsTrigger>
          <TabsTrigger value="files">会话文件</TabsTrigger>
          {view === "capability" ? <TabsTrigger value="capability">能力成长</TabsTrigger> : null}
          {voiceDebugEnabled ? <TabsTrigger value="voice">语音延迟</TabsTrigger> : null}
        </TabsList>
        <DropdownMenu>
          <DropdownMenuTrigger asChild><Button size="icon-xs" variant="ghost" aria-label="更多工作区"><MoreHorizontal className="size-3.5" /></Button></DropdownMenuTrigger>
          <DropdownMenuContent align="end"><DropdownMenuItem onSelect={() => setView("capability")}>能力成长</DropdownMenuItem></DropdownMenuContent>
        </DropdownMenu>
        <Button aria-label="新建侧边对话" className="chat-graph-rail__add" disabled={creatingSideConversation} onClick={() => void createSideConversation()} size="icon-xs" title="新建侧边对话" variant="ghost">
          {creatingSideConversation ? <LoaderCircle className="size-3.5 animate-spin" /> : <Plus className="size-3.5" />}
        </Button>
      </div>
      <TabsContent value="conversation" className="chat-graph-rail__panel">
        <Suspense fallback={<div role="status">正在打开侧边对话…</div>}><SideConversationRail currentSessionId={sessionId} focusSessionId={sideConversationFocusId} selectionDetail={selectionExplanation} creationParentSessionId={creationParentSessionId ?? sessionId} onCloseSelection={onCloseExplanation} workspaceId={workspaceId} /></Suspense>
      </TabsContent>
      <TabsContent value="files" className="chat-graph-rail__panel"><Suspense fallback={<div role="status">正在打开会话文件…</div>}><SessionFilePreviewRail sessionId={sessionId} workspaceId={workspaceId} /></Suspense></TabsContent>
      {voiceDebugEnabled ? <TabsContent value="voice" className="chat-graph-rail__panel"><Suspense fallback={null}><VoiceLatencyRail /></Suspense></TabsContent> : null}
      <TabsContent value="learning" className="chat-graph-rail__panel">
        {binding && !bookshelfOpen ? <BoundGraphRail graphId={binding.graphId} boundNodeId={boundNodeId ?? undefined} sessionId={sessionId} title={binding.graphTitle} folderTitle={project?.title} onSwitchGraph={() => setBookshelfOpen(true)} workspaceId={workspaceId} /> : <ProjectBookshelf onOpen={openGraph} onBack={binding ? () => setBookshelfOpen(false) : undefined} workspaceId={workspaceId} />}
      </TabsContent>
      <TabsContent value="capability" className="chat-graph-rail__panel"><CapabilityGraphRail workspaceId={workspaceId} /></TabsContent>
    </Tabs>
  );
}

function ProjectBookshelf({ onOpen, onBack, workspaceId }: {
  onOpen: (graph: GraphSummary) => void;
  onBack?: () => void;
  workspaceId: string;
}) {
  const [search, setSearch] = useState("");
  const [showAll, setShowAll] = useState(false);
  const graphs = useQuery({ queryKey: workspaceQueryKey(workspaceId, "graphs"), queryFn: listGraphs, staleTime: 30_000 });
  const books = [...(graphs.data ?? [])].sort((a, b) =>
    (graphVisit(workspaceId, b.id)?.openedAt ?? 0) - (graphVisit(workspaceId, a.id)?.openedAt ?? 0)
    || a.title.localeCompare(b.title));
  const query = search.trim().toLocaleLowerCase();
  const matching = books.filter(graph => graph.title.toLocaleLowerCase().includes(query));
  const visible = query || showAll ? matching : matching.slice(0, 6);
  return <section aria-label="图谱书架" className="project-bookshelf chat-graph-rail__canvas">
    <div className="graph-rail-heading project-bookshelf__heading">
      <div className="project-bookshelf__identity"><strong>图谱书架</strong>{graphs.isSuccess ? <span>{books.length} 个图谱</span> : null}</div>
      {onBack ? <Button size="xs" variant="ghost" onClick={onBack}>返回当前学习</Button> : null}
    </div>
    <div className="project-bookshelf__search">
      <Search aria-hidden="true" />
      <Input aria-label="搜索图谱" placeholder="搜索图谱" value={search} onChange={event => setSearch(event.target.value)} />
      {search ? <Button aria-label="清除图谱搜索" size="icon-xs" variant="ghost" onClick={() => setSearch("")}><X aria-hidden="true" className="size-3.5" /></Button> : null}
    </div>
    {graphs.isPending ? <p className="project-bookshelf__message" role="status"><LoaderCircle aria-hidden="true" className="size-4 animate-spin" />正在读取图谱…</p> : null}
    {graphs.isError ? <div className="project-bookshelf__message" role="alert"><span>图谱读取失败</span><Button size="xs" variant="outline" onClick={() => void graphs.refetch()}>重试</Button></div> : null}
    <div className="project-bookshelf__list">
      {visible.map(graph => <button key={graph.id} onClick={() => onOpen(graph)} type="button" aria-label={`打开图谱文件夹 ${graph.title}`}>
        <span aria-hidden="true" className="project-bookshelf__icon"><Network /></span>
        <span className="project-bookshelf__content"><strong title={graph.title}>{graph.title}</strong><small>{graph.status === "published" ? "已发布" : "候选图谱"}</small></span>
        <ChevronRight aria-hidden="true" className="project-bookshelf__chevron size-3.5" />
      </button>)}
    </div>
    {!graphs.isPending && !graphs.isError && !visible.length ? <p className="project-bookshelf__message" role="status">{query ? "没有匹配的图谱，试试其他关键词" : "暂无图谱"}</p> : null}
    {!query && matching.length > 6 ? <Button className="project-bookshelf__more" aria-expanded={showAll} size="xs" variant="ghost" onClick={() => setShowAll(value => !value)}>{showAll ? "收起列表" : `查看全部 ${matching.length} 个图谱`}</Button> : null}
  </section>;
}

function toRailKnowledgeGraph(
  graph: Graph,
  exploreCounts: Record<string, number> = {},
): {
  nodes: KnowledgeNode[];
  edges: Array<{
    id: string;
    source: string;
    target: string;
    label: string;
    type: "smoothstep";
  }>;
} {
  // Prefer contains as the teaching hierarchy (same as graph workbench).
  const containedNodeIds = new Set(
    graph.edges
      .filter((edge) => edge.relation === "contains")
      .map((edge) => edge.target_node_id),
  );
  const fallbackTargets = new Set(graph.edges.map((edge) => edge.target_node_id));
  const hasDeclaredRoot = graph.nodes.some((node) => node.node_type === "root");
  return {
    nodes: graph.nodes.map((node, index) => ({
      id: node.id,
      type: "knowledge",
      position: {
        x: 100 + Math.floor(index / 3) * 220,
        y: 90 + (index % 3) * 120,
      },
      data: {
        label: node.label,
        description: node.description,
        stars: node.mastery_stars,
      achievementScore: node.achievement_score,
        state: node.retrieval_state,
        evidence: node.evidence_state,
        focused: node.attention_state === "focused",
        nodeType: node.node_type,
        targetWeight: node.target_weight,
        exploreCount: exploreCounts[node.id] ?? 0,
        mastered: node.attention_state === "mastered",
        root:
          node.node_type === "root" ||
          (!hasDeclaredRoot &&
            !(containedNodeIds.size
              ? containedNodeIds.has(node.id)
              : fallbackTargets.has(node.id))),
      },
    })),
    edges: graph.edges.map((edge) => ({
      id: edge.id,
      source: edge.source_node_id,
      target: edge.target_node_id,
      label: edge.relation,
      type: "smoothstep" as const,
      data: { relation: edge.relation },
    })),
  };
}

function BoundGraphRail({
  boundNodeId,
  graphId,
  folderTitle,
  onSwitchGraph,
  selectedNodeId: requestedNodeId,
  sessionId,
  title,
  workspaceId,
}: {
  boundNodeId?: string;
  graphId: string;
  folderTitle?: string;
  onSwitchGraph?: () => void;
  selectedNodeId?: string;
  sessionId?: string;
  title: string;
  workspaceId: string;
}) {
  const queryClient = useQueryClient();
  const graphQuery = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graph", graphId),
    queryFn: () => getGraph(graphId),
    staleTime: 30_000,
    enabled: Boolean(graphId),
  });
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [nodeSearch, setNodeSearch] = useState("");
  const canvasApi = useRef<KnowledgeGraphCanvasApi | null>(null);
  const receiveCanvasApi = useCallback((api: KnowledgeGraphCanvasApi) => { canvasApi.current = api; }, []);
  const [editing, setEditing] = useState(false);
  const draftRef = useRef({ label: "", description: "" });
  const [explorePanelOpen, setExplorePanelOpen] = useState(false);
  const [validation, setValidation] = useState<{
    errors: string[];
    suggestions: string[];
  }>();

  useEffect(() => {
    setSelectedNodeId(boundNodeId ?? requestedNodeId);
    setNodeSearch("");
    setEditing(false);
    setExplorePanelOpen(false);
    setValidation(undefined);
  }, [boundNodeId, graphId, requestedNodeId, sessionId]);

  const graph = graphQuery.data;

  useEffect(() => {
    if (!graph) return;
    if (boundNodeId || requestedNodeId) return;
    if (selectedNodeId && graph.nodes.some((node) => node.id === selectedNodeId))
      return;
    const preferred = pickDefaultLearningNode(graph);
    if (!preferred) return;
    setSelectedNodeId(preferred.id);
  }, [boundNodeId, graph, graphId, requestedNodeId, selectedNodeId]);

  const selectedNode =
    graph?.nodes.find((node) => node.id === selectedNodeId) ??
    (graph ? pickDefaultLearningNode(graph) : undefined);
  const boundNode = graph?.nodes.find((node) => node.id === boundNodeId);
  const effectiveNodeId = selectedNodeId ?? selectedNode?.id;
  const questionsQuery = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "node-questions", graphId, effectiveNodeId),
    queryFn: () => listNodeQuestions(graphId, effectiveNodeId!),
    staleTime: 30_000,
    enabled: Boolean(effectiveNodeId),
    refetchOnWindowFocus: true,
  });
  // Accumulate explore counts as the user selects different cards so
  // previously visited nodes keep "深入 ×N" instead of flipping back to 未深入.
  const [exploreCounts, setExploreCounts] = useState<Record<string, number>>(
    {},
  );
  useEffect(() => {
    if (!effectiveNodeId || questionsQuery.data === undefined) return;
    const next = questionsQuery.data.length;
    setExploreCounts((current) =>
      current[effectiveNodeId] === next
        ? current
        : { ...current, [effectiveNodeId]: next },
    );
  }, [effectiveNodeId, questionsQuery.data]);
  // Keep every branch in the rail; selection only controls the inspector.
  const railGraph = useMemo(
    () => (graph ? toRailKnowledgeGraph(graph, exploreCounts) : undefined),
    [exploreCounts, graph],
  );
  const searchMatches = useMemo(() => nodeSearch.trim() ? graph?.nodes.filter(node =>
    node.label.toLocaleLowerCase().includes(nodeSearch.trim().toLocaleLowerCase())) ?? [] : [], [graph, nodeSearch]);
  // Same query key as chat completion invalidation so "未深入" flips live.
  const questions = questionsQuery.data ?? [];

  useEffect(() => {
    if (!selectedNode) return;
    draftRef.current = {
      label: selectedNode.label,
      description: selectedNode.description,
    };
  }, [selectedNode]);

  const update = useMutation({
    mutationFn: ({
      nodeId,
      label,
      description,
    }: {
      nodeId: string;
      label: string;
      description: string;
    }) =>
      updateGraphNode(graphId, nodeId, {
        expected_revision: graph?.revision,
        label,
        description,
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData<Graph>(workspaceQueryKey(workspaceId, "graph", graphId), (current) =>
        current
          ? {
              ...current,
              nodes: current.nodes.map((node) =>
                node.id === updated.id ? { ...node, ...updated } : node,
              ),
            }
          : current,
      );
      setEditing(false);
      setValidation(undefined);
      toast.success("图谱节点已更新");
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graph", graphId) });
    },
    onError: (error) => {
      toast.error(error.message);
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graph", graphId) });
    },
  });
  const focus = useMutation({
    mutationFn: (nodeId: string) =>
      updateGraphNode(graphId, nodeId, { attention_state: "focused" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graph", graphId) });
      toast.success("已设为重点节点");
    },
    onError: (error) => toast.error(error.message),
  });
  const masteryMark = useMutation({
    mutationFn: ({
      nodeId,
      mastered,
    }: {
      nodeId: string;
      mastered: boolean;
    }) =>
      updateGraphNode(graphId, nodeId, {
        attention_state: mastered ? "mastered" : "normal",
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData<Graph>(workspaceQueryKey(workspaceId, "graph", graphId), (current) =>
        current
          ? {
              ...current,
              nodes: current.nodes.map((node) =>
                node.id === updated.id ? { ...node, ...updated } : node,
              ),
            }
          : current,
      );
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graph", graphId) });
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "mastery") });
      toast.success(
        updated.attention_state === "mastered"
          ? "已标记为已掌握，将出现在能力成长图谱"
          : "已取消已掌握标记",
      );
    },
    onError: (error) => toast.error(error.message),
  });

  function startEditing() {
    if (!selectedNode) return;
    setEditing(true);
    setValidation(undefined);
  }

  function openExploreHistory(nodeId: string) {
    setSelectedNodeId(nodeId);
    setEditing(false);
    setExplorePanelOpen(true);
  }

  function assessDraft() {
    if (!selectedNode || !graph)
      return { errors: ["请选择一个节点。"], suggestions: [] };
    const label = draftRef.current.label.trim();
    const description = draftRef.current.description.trim();
    const errors: string[] = [];
    const suggestions: string[] = [];
    if (label.length < 2) errors.push("节点名称至少需要 2 个字符。");
    if (label.length > 32)
      suggestions.push("建议将节点名称控制在 32 个字符以内，便于在树中扫描。");
    if (!description)
      suggestions.push("建议补充一句定义或学习边界，AI 会据此组织追问。");
    if (
      graph.nodes.some(
        (node) => node.id !== selectedNode.id && node.label.trim() === label,
      )
    )
      errors.push("同一图谱中不能存在同名节点。");
    return { errors, suggestions };
  }

  function save(force = false) {
    if (!selectedNode || !graph) return;
    const next = assessDraft();
    setValidation(next);
    if (next.errors.length || (next.suggestions.length && !force)) return;
    const payload = {
      label: draftRef.current.label.trim(),
      description: draftRef.current.description.trim(),
    };
    update.mutate({ nodeId: selectedNode.id, ...payload });
  }

  if (graphQuery.isPending)
    return (
      <div className="chat-graph-rail__canvas grid place-items-center text-xs text-muted-foreground">
        正在读取会话图谱…
      </div>
    );
  if (graphQuery.isError || !graph || !railGraph)
    return (
      <div className="chat-graph-rail__canvas grid place-items-center px-4 text-center text-xs text-muted-foreground">
        图谱暂时无法读取。
        <Button
          className="mt-2"
          onClick={() => void graphQuery.refetch()}
          size="xs"
          variant="outline"
        >
          重试
        </Button>
      </div>
    );

  const childNodes = selectedNode
    ? graph.edges
        .filter((edge) => edge.source_node_id === selectedNode.id)
        .map((edge) =>
          graph.nodes.find((node) => node.id === edge.target_node_id),
        )
        .filter((node): node is NonNullable<typeof node> => Boolean(node))
    : [];
  const parentNode = selectedNode
    ? (() => {
        const edge = graph.edges.find(
          (item) => item.target_node_id === selectedNode.id,
        );
        return edge
          ? graph.nodes.find((node) => node.id === edge.source_node_id)
          : undefined;
      })()
    : undefined;

  function askNode(question: string, graphAction?: "propose_update") {
    if (!selectedNode) return;
    window.dispatchEvent(new CustomEvent("learngraph:open-learning-project", { detail: {
      graphId, title: graph?.title ?? title, nodeId: selectedNode.id, nodeLabel: selectedNode.label,
      prompt: question, graphAction,
    } }));
  }

  async function studyNode() {
    if (!selectedNode) return;
    rememberLastLearnedNode(graphId, selectedNode.id);
    // Submit through the existing pendingPrompt route contract, not a draft-only
    // node selection. Preview clicks must never publish this intent.
    window.dispatchEvent(
      new CustomEvent("learngraph:open-learning-project", {
        detail: {
          graphId,
          title: graph?.title ?? selectedNode.label,
          nodeId: selectedNode.id,
          nodeLabel: selectedNode.label,
          prompt: `什么是 ${selectedNode.label}？`,
          intent: "study",
        },
      }),
    );
  }

  async function studyNextNode() {
    if (!selectedNode || !graph) return;
    const next =
      childNodes[0] ??
      graph.nodes.find(
        (node) =>
          node.id !== selectedNode.id &&
          node.node_type !== "root" &&
          (node.mastery_stars ?? 0) < 3,
      );
    if (!next) {
      toast.message("当前没有更合适的下一个节点。");
      return;
    }
    rememberLastLearnedNode(graphId, next.id);
    window.dispatchEvent(
      new CustomEvent("learngraph:open-learning-project", {
        detail: {
          graphId,
          title: graph?.title ?? next.label,
          nodeId: next.id,
          nodeLabel: next.label,
          prompt: `什么是 ${next.label}？`,
          intent: "study",
        },
      }),
    );
  }

  function compareWithParent() {
    if (!selectedNode || !parentNode) {
      toast.message("当前节点没有父节点可对比。");
      return;
    }
    askNode(
      `请对比「${parentNode.label}」与「${selectedNode.label}」的定义、边界、前置关系与适用场景，用表格或要点列出异同。`,
    );
  }

  async function splitNode() {
    if (!selectedNode) return;
    const childHint = childNodes.length
      ? `已有下级：${childNodes
          .slice(0, 8)
          .map((child) => child.label)
          .join("、")}。不要重复创建近义子节点。`
      : "该节点目前还没有下级。";
    const prompt =
      `请对当前学习节点「${selectedNode.label}」做图谱拆分细化：` +
      `在保持教学树 contains 结构的前提下，增加 2～5 个更具体的子概念/练习节点，` +
      `或修正本节点定义；与现有概念去重。${childHint}` +
      `输出需进入图谱变更审核，不要声称已写入正式图谱。`;
    askNode(prompt, "propose_update");
  }

  function toggleMastered() {
    if (!selectedNode) return;
    const mastered = selectedNode.attention_state === "mastered";
    masteryMark.mutate({ nodeId: selectedNode.id, mastered: !mastered });
  }

  return (
    <section
      aria-label={`${title} 右侧图谱`}
      className="bound-graph-rail chat-graph-rail__canvas"
    >
      <div className="graph-rail-navigation">
        <div className="graph-rail-heading">
          <div className="graph-rail-heading__identity"><strong title={graph.title || title}>{graph.title || title}</strong>
            {folderTitle ? <span title={folderTitle}>文件夹：{folderTitle}</span> : null}</div>
          {onSwitchGraph ? <Button size="xs" variant="ghost" onClick={onSwitchGraph}>切换图谱</Button> : null}
        </div>
        <div className="graph-rail-navigation__tools">
          <Input aria-label="搜索图谱节点" placeholder="搜索节点" value={nodeSearch} onChange={event => setNodeSearch(event.target.value)} />
          <Button size="icon-xs" variant="ghost" aria-label="回到当前学习节点" title="回到当前学习节点" onClick={() => {
            const target = boundNodeId ?? requestedNodeId ?? pickDefaultLearningNode(graph)?.id;
            if (target) { setSelectedNodeId(target); setNodeSearch(""); setEditing(false); setExplorePanelOpen(false); canvasApi.current?.focusNode(target); }
          }}><Focus className="size-3.5" /></Button>
          <Button size="xs" variant="ghost" asChild><Link to={`/w/${workspaceId}/graphs/${graphId}`}>完整图谱</Link></Button>
        </div>
        {nodeSearch.trim() ? <div className="graph-rail-search" aria-label="节点搜索结果">
          <span role="status">{searchMatches.length ? `${searchMatches.length} 个匹配节点` : "没有匹配的节点"}</span>
          {searchMatches.slice(0, 30).map(node => <button type="button" key={node.id} onClick={() => {
            setSelectedNodeId(node.id); setNodeSearch(""); setEditing(false); setExplorePanelOpen(false); setValidation(undefined);
            canvasApi.current?.focusNode(node.id);
          }}>{node.label}</button>)}
          {searchMatches.length > 30 ? <span>继续输入可缩小范围</span> : null}
        </div> : null}
      </div>
      <Suspense fallback={<div className="grid min-h-56 place-items-center text-xs text-muted-foreground" role="status">正在打开图谱…</div>}><KnowledgeGraph
        key={graphId}
        className="bound-graph-rail__canvas"
        currentId={boundNodeId}
        compact
        highlightNeighborhood={false}
        edges={railGraph.edges}
        interactive
        layout="tree"
        maximumZoom={2.4}
        minimumZoom={0.01}
        initialFit="view"
        onCanvasApi={receiveCanvasApi}
        showHeading={false}
        toolbarMode="minimal"
        nodes={railGraph.nodes}
        onOpenExplore={(node) => openExploreHistory(node.id)}
        onSelect={(node) => {
          // Selecting a card previews it; binding is explicit in page tools.
          setSelectedNodeId(node.id);
          setEditing(false);
          setExplorePanelOpen(false);
          setValidation(undefined);
        }}
        selectedId={selectedNode?.id}
        showZoomControls
        studyOnSelect={false}
        title={graph.title || title}
      /></Suspense>
      {selectedNode ? (
        <div className="bound-graph-rail__inspector">
          <div aria-label="会话节点绑定" className="graph-rail-selection" role="status">
            <strong>{selectedNode.label}</strong>
            <span className="graph-rail-context-badge">{selectedNode.id === boundNodeId ? "当前学习" : "预览"}</span>
            {boundNode && selectedNode.id !== boundNodeId ? <span className="graph-rail-selection__current">正在学：{boundNode.label}</span> : null}
          </div>
          {selectedNode.description ? <p className="graph-rail-definition">{selectedNode.description}</p> : null}
          {explorePanelOpen && questions.length ? (
            <NodeExploreChain
              onClose={() => setExplorePanelOpen(false)}
              onJump={(round) => {
                const message = document.getElementById(
                  `conversation-jump-${round.id}`,
                );
                if (!message) {
                  toast.message("这条历史问话不在当前会话中。");
                  return;
                }
                window.dispatchEvent(
                  new CustomEvent("learngraph:conversation-jump", {
                    detail: { block: "center", messageId: round.id },
                  }),
                );
                setExplorePanelOpen(false);
              }}
              rounds={questions}
              title={`${selectedNode.label} 的历史问话`}
            />
          ) : null}
          {editing ? (
            <div className="graph-rail-editor">
              <label>
                节点名称
                <Input
                  aria-label="节点名称"
                  onChange={(event) =>
                    (draftRef.current = {
                      ...draftRef.current,
                      label: event.currentTarget.value,
                    })
                  }
                  defaultValue={draftRef.current.label}
                />
              </label>
              <label>
                学习说明
                <textarea
                  aria-label="节点学习说明"
                  onChange={(event) =>
                    (draftRef.current = {
                      ...draftRef.current,
                      description: event.currentTarget.value,
                    })
                  }
                  rows={3}
                  defaultValue={draftRef.current.description}
                />
              </label>
              {validation ? (
                <div
                  className={
                    validation.errors.length
                      ? "graph-rail-validation is-error"
                      : "graph-rail-validation"
                  }
                >
                  {validation.errors.map((item) => (
                    <p key={item}>• {item}</p>
                  ))}
                  {validation.suggestions.map((item) => (
                    <p key={item}>建议：{item}</p>
                  ))}
                </div>
              ) : null}
              <div className="graph-rail-editor__actions">
                <Button
                  onClick={() => {
                    setEditing(false);
                    setValidation(undefined);
                  }}
                  size="xs"
                  variant="ghost"
                >
                  取消
                </Button>
                <Button onClick={() => save(false)} size="xs">
                  <Save className="size-3.5" />
                  保存并校验
                </Button>
                {validation?.suggestions.length && !validation.errors.length ? (
                  <Button
                    onClick={() => save(true)}
                    size="xs"
                    variant="outline"
                  >
                    拒绝建议，强制更新
                  </Button>
                ) : null}
              </div>
            </div>
          ) : (
            <>
              <div className="bound-graph-rail__actions">
                <Button onClick={(event) => { if (event.detail < 2) void studyNode(); }} size="xs"><Play className="size-3.5" />{selectedNode.id === boundNodeId ? "继续学习" : "学习此节点"}</Button>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild><Button size="xs" variant="ghost" aria-label="更多节点操作"><MoreHorizontal className="size-3.5" />更多</Button></DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-44">
                    <DropdownMenuItem onSelect={() => void studyNextNode()}><GraduationCap className="size-3.5" />学习下一节点</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => askNode(`请解释「${selectedNode.label}」的定义、边界与典型例子。`)}>百科讲解</DropdownMenuItem>
                    <DropdownMenuItem onSelect={compareWithParent} disabled={!parentNode}>对比上级</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => askNode(`围绕「${selectedNode.label}」出 3 道由浅入深的自测题，并给出参考答案要点。`)}>自测题</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => askNode(`「${selectedNode.label}」常见误区有哪些？各给一个纠正方式。`)}>常见误区</DropdownMenuItem>
                    <DropdownMenuItem disabled={!questions.length} onSelect={() => setExplorePanelOpen(open => !open)}><MessageSquareText className="size-3.5" />历史提问{questions.length ? `（${questions.length}）` : ""}</DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem disabled={masteryMark.isPending} onSelect={toggleMastered}><BadgeCheck className="size-3.5" />{selectedNode.attention_state === "mastered" ? "取消已掌握" : "标为已掌握"}</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => focus.mutate(selectedNode.id)} disabled={focus.isPending || selectedNode.attention_state === "focused"}><Focus className="size-3.5" />设为重点</DropdownMenuItem>
                    <DropdownMenuItem onSelect={() => void splitNode()}><Split className="size-3.5" />拆分节点</DropdownMenuItem>
                    <DropdownMenuItem onSelect={startEditing}><Pencil className="size-3.5" />编辑节点</DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            </>
          )}
        </div>
      ) : null}
    </section>
  );
}

function CapabilityGraphRail({ workspaceId }: { workspaceId: string }) {
  const [maxDepth, setMaxDepth] = useState(1);
  const mastery = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "mastery"),
    queryFn: getMastery,
    staleTime: 30_000,
  });
  // Only user-declared mastered nodes enter the capability graph.
  const masteredNodes = useMemo(
    () =>
      (mastery.data ?? []).filter(
        (item) => item.attention_state === "mastered",
      ),
    [mastery.data],
  );
  const visible = useMemo(() => {
    const root: KnowledgeNode = {
      id: "capability-root",
      type: "knowledge",
      position: { x: 0, y: 0 },
      data: {
        label: "能力成长",
        root: true,
        stars: 0,
        state: `${masteredNodes.length} 个已掌握概念`,
      },
    };
    const children: KnowledgeNode[] = masteredNodes.map((item) => ({
      id: `capability-${item.node_id}`,
      type: "knowledge",
      position: { x: 0, y: 0 },
      data: {
        label: item.label,
        stars: item.mastery_stars,
        state: "mastered",
        mastered: true,
        evidence: `${item.accepted_evidence_count} 条已接受证据`,
      },
    }));
    const nodes = maxDepth === 0 ? [root] : [root, ...children];
    return {
      nodes,
      edges:
        maxDepth === 0
          ? []
          : children.map((node) => ({
              id: `capability-root-${node.id}`,
              source: "capability-root",
              target: node.id,
              type: "smoothstep",
              style: { stroke: "#c7cbc7" },
            })),
    };
  }, [masteredNodes, maxDepth]);
  return (
    <section
      aria-label="能力成长图谱"
      className="capability-graph-rail chat-graph-rail__canvas"
    >
      <div className="capability-graph-rail__toolbar">
        <div>
          <p>能力成长图谱</p>
          <span>
            仅展示已标记「已掌握」的节点 · 共 {masteredNodes.length} 个
          </span>
        </div>
        <div role="group" aria-label="能力层级">
          <Button
            aria-pressed={maxDepth === 0}
            onClick={() => setMaxDepth(0)}
            size="xs"
            variant={maxDepth === 0 ? "secondary" : "ghost"}
          >
            根能力
          </Button>
          <Button
            aria-pressed={maxDepth > 0}
            onClick={() => setMaxDepth(1)}
            size="xs"
            variant={maxDepth > 0 ? "secondary" : "ghost"}
          >
            已掌握概念
          </Button>
        </div>
      </div>
      <Suspense fallback={null}><KnowledgeGraph
        className="capability-graph-rail__canvas"
        compact
        edges={visible.edges}
        layout="tree"
        maximumZoom={2.4}
        minimumZoom={0.15}
        nodes={visible.nodes}
        showZoomControls
        title={
          maxDepth === 0
            ? "能力成长"
            : masteredNodes.length
              ? "能力成长图谱"
              : "暂无已掌握节点"
        }
      /></Suspense>
    </section>
  );
}

type GoalGraphPreview = {
  composerText: string;
  submittedPrompt: string;
  title?: string;
  answers: string[];
  questionCount: number;
  phase: "draft" | "building" | "clarifying" | "reviewing" | "approved";
  graphNodes: Graph["nodes"];
  graphEdges: Graph["edges"];
};

const initialGoalGraphPreview: GoalGraphPreview = {
  composerText: "",
  submittedPrompt: "",
  answers: [],
  questionCount: 0,
  phase: "draft",
  graphNodes: [],
  graphEdges: [],
};

function GoalGraphPreviewRail() {
  const [preview, setPreview] = useState<GoalGraphPreview>(
    initialGoalGraphPreview,
  );
  useEffect(() => {
    const updatePreview = (event: Event) => {
      const detail = (event as CustomEvent<Partial<GoalGraphPreview>>).detail;
      if (!detail) return;
      setPreview((current) => ({
        ...current,
        ...detail,
        answers: Array.isArray(detail.answers)
          ? detail.answers
          : current.answers,
        graphNodes: Array.isArray(detail.graphNodes)
          ? detail.graphNodes
          : current.graphNodes,
        graphEdges: Array.isArray(detail.graphEdges)
          ? detail.graphEdges
          : current.graphEdges,
      }));
    };
    window.addEventListener("learngraph:goal-graph-preview", updatePreview);
    return () =>
      window.removeEventListener(
        "learngraph:goal-graph-preview",
        updatePreview,
      );
  }, []);

  const graph = useMemo(() => {
    return {
      nodes: preview.graphNodes.map<KnowledgeNode>((node) => ({
        id: node.id,
        type: "knowledge",
        position: { x: 0, y: 0 },
        data: {
          label: node.label,
          description: node.description,
          root: node.node_type === "root",
          state: preview.phase === "approved" ? "已发布" : "待审核",
          evidence: "候选图谱",
        },
      })),
      edges: preview.graphEdges.map((edge) => ({
        id: edge.id,
        source: edge.source_node_id,
        target: edge.target_node_id,
        type: "smoothstep",
      })),
    };
  }, [preview.graphEdges, preview.graphNodes, preview.phase]);

  const phaseLabel =
    preview.phase === "building"
      ? "正在生成结构"
      : preview.phase === "reviewing"
        ? `${preview.graphNodes.length} 个候选节点待审核`
        : preview.phase === "approved"
          ? "目标图谱已发布"
      : preview.phase === "clarifying"
        ? `${preview.answers.length}/${preview.questionCount || 1} 项已确认`
        : preview.composerText || preview.submittedPrompt
          ? "学习意向已同步"
          : "输入目标后开始生长";
  return (
    <div className="chat-graph-rail goal-graph-rail">
      <div className="goal-graph-rail__header">
        <div>
          <p>目标图谱预览</p>
          <span>{phaseLabel}</span>
        </div>
        <Badge className="font-normal" variant="secondary">
          {preview.phase === "approved"
            ? "已发布"
            : preview.phase === "reviewing"
              ? "待审核"
            : "待生成"}
        </Badge>
      </div>
      {graph.nodes.length ? (
        <Suspense fallback={null}><KnowledgeGraph
          className="chat-graph-rail__canvas"
          compact
          edges={graph.edges}
          interactive
          layout="tree"
          maximumZoom={2.4}
          minimumZoom={0.15}
          nodes={graph.nodes}
          showZoomControls
          title="当前目标图谱"
        /></Suspense>
      ) : (
        <div className="goal-graph-rail__empty">
          <Network aria-hidden="true" className="size-5" />
          <div>
            <strong>
              {preview.title ||
                preview.submittedPrompt ||
                preview.composerText ||
                "尚未描述学习目标"}
            </strong>
            <span>
              {preview.title || preview.submittedPrompt || preview.composerText
                ? "候选图谱会在 Goal 确认后出现。"
                : "输入目标后开始澄清；这里不会预填虚构节点。"}
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

function graphIdFromPathname(pathname: string) {
  const segment = pathname.match(/\/graphs\/([^/?#]+)/)?.[1];
  if (!segment) return "";
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

function GraphWorkspaceRail() {
  const { graphId: paramGraphId = "", workspaceId = "" } = useParams();
  const { pathname, search } = useLocation();
  // ContextRail is rendered by the parent workspace route. Derive the child
  // graph id from the URL as well, so the rail never falls back to GET /graphs/.
  const graphId = paramGraphId || graphIdFromPathname(pathname);
  const [view, setView] = useState<"learning" | "capability">("learning");
  const graph = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "graph", graphId),
    queryFn: () => getGraph(graphId),
    staleTime: 30_000,
    enabled: Boolean(graphId),
  });
  const selectedNodeId = new URLSearchParams(search).get("node") ?? undefined;
  const pendingGoalId = new URLSearchParams(search).get("pendingGoal");
  const isLearning = view === "learning";
  const railGraph = useMemo(() => {
    if (
      !graph.data ||
      !Array.isArray(graph.data.nodes) ||
      !Array.isArray(graph.data.edges)
    )
      return null;
    return toRailKnowledgeGraph(graph.data);
  }, [graph.data]);
  const title = graph.data?.title ?? "当前学习图谱";

  return (
    <div className="chat-graph-rail graph-workspace-rail">
      <div
        className="chat-graph-rail__switch"
        role="tablist"
        aria-label="图谱视图"
      >
        <Button
          aria-selected={isLearning}
          onClick={() => setView("learning")}
          role="tab"
          size="xs"
          variant={isLearning ? "secondary" : "ghost"}
        >
          当前学习图谱
        </Button>
        <Button
          aria-selected={!isLearning}
          onClick={() => setView("capability")}
          role="tab"
          size="xs"
          variant={!isLearning ? "secondary" : "ghost"}
        >
          能力成长图谱
        </Button>
      </div>
      {isLearning ? (
        pendingGoalId ? (
          <div className="chat-graph-rail__canvas grid place-items-center px-5 text-center">
            <div>
              <CircleDot className="mx-auto size-5 text-muted-foreground" />
              <p className="mt-3 text-sm font-semibold">学习意向尚未生成图谱</p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                完成目标澄清和候选图谱审核后，Inspector 才会显示真实节点与关系。
              </p>
            </div>
          </div>
        ) : railGraph ? (
          <BoundGraphRail
            graphId={graphId}
            selectedNodeId={selectedNodeId}
            title={title}
            workspaceId={workspaceId}
          />
        ) : (
          <div
            aria-live="polite"
            className="chat-graph-rail__canvas grid place-items-center px-4 text-center text-xs text-muted-foreground"
          >
            {!graphId || graph.isError || graph.data ? (
              <div>
                <p>
                  {!graphId
                    ? "当前路由没有绑定图谱。"
                    : "当前图谱读取失败，没有使用演示内容替代。"}
                </p>
                <Button
                  disabled={!graphId}
                  className="mt-3"
                  onClick={() => void graph.refetch()}
                  size="xs"
                  variant="outline"
                >
                  重试
                </Button>
              </div>
            ) : (
              "正在读取当前图谱…"
            )}
          </div>
        )
      ) : (
        <CapabilityGraphRail workspaceId={workspaceId} />
      )}
    </div>
  );
}

/**
 * 路由内容（当前页面）单独隔一层：抽屉开合、活动抽屉这类外壳状态变化时，
 * 当前页面不该跟着重渲染（首页 dashboard 的图表重渲染一次就是几十毫秒）。
 * 路由变化仍会经 RouteContext 让这里重新渲染。
 */
const ShellOutlet = memo(function ShellOutlet() {
  return (
    <RouteMotion>
      <Outlet />
    </RouteMotion>
  );
});

/**
 * The settings pages are route-level lazy chunks. Keep this fallback inside
 * the already-mounted settings window; the app-wide fallback says that the
 * whole workspace is loading and causes a visible flash between categories.
 */
function SettingsOutletFallback() {
  return (
    <div
      aria-busy="true"
      aria-label="正在打开设置"
      className="flex min-h-full flex-col gap-4 p-5"
      data-settings-route-loading="true"
    >
      <div className="h-6 w-2/5 animate-pulse rounded-md bg-muted" />
      <div className="h-4 w-4/5 animate-pulse rounded-md bg-muted" />
      <div className="grid gap-3 pt-3 sm:grid-cols-2">
        <div className="h-24 animate-pulse rounded-xl border bg-muted/40" />
        <div className="h-24 animate-pulse rounded-xl border bg-muted/40" />
      </div>
    </div>
  );
}

const SettingsOutlet = memo(function SettingsOutlet() {
  return (
    <Suspense fallback={<SettingsOutletFallback />}>
      <Outlet />
    </Suspense>
  );
});

export function WorkspaceShell() {
  const { pathname, search } = useLocation();
  const navigate = useNavigate();
  const { workspaceId = "" } = useParams();
  const settings = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "settings"),
    queryFn: listSettings,
    staleTime: 30_000,
  });
  const [collapsed, setCollapsed] = useState(
    () => window.localStorage.getItem("lg-sidebar-collapsed") !== "false",
  );
  const [railCollapsed, setRailCollapsed] = useState(
    () => window.localStorage.getItem("lg-rail-collapsed") === "true",
  );
  const [railWidth, setRailWidth] = useState(360);
  const [activityOpen, setActivityOpen] = useState(false);
  // Narrow screens hide the context rail; this re-opens it as a right drawer.
  const [graphDrawerOpen, setGraphDrawerOpen] = useState(false);
  // 窄屏左侧导航抽屉：常驻 DOM，位移由 CSS 进度变量驱动。
  const [navOpen, setNavOpen] = useState(false);
  // Keep the inspector mounted while 划词解释 is open, even if the graph rail
  // was folded — otherwise the independent canvas cannot appear.
  const [selectionExplanationOpen, setSelectionExplanationOpen] = useState(false);
  const isChat = pathname.includes("/chat/");
  const isDocumentReader = pathname.includes("/documents/");
  const isSettings = pathname.includes("/settings/");
  const isMemory = pathname.includes("/memory");
  // Remember the last non-settings location so closing the modal can restore it.
  const settingsOriginRef = useRef<string | null>(null);
  useEffect(() => {
    if (!pathname.includes("/settings/")) {
      settingsOriginRef.current = `${pathname}${search}`;
    }
  }, [pathname, search]);
  const isGoalClarify =
    pathname.includes("/goals/new/clarify") ||
    (isChat && new URLSearchParams(search).get("mode") === "goal");
  // The process rail is also useful on the memory report page. Keep it in the
  // same shell column as chat so it can be opened/collapsed consistently.
  const showContextRail = isChat || isGoalClarify || isMemory;
  const showRailToggle = (isChat || isMemory) && !isGoalClarify;
  useEffect(() => {
    setGraphDrawerOpen(false);
    setSelectionExplanationOpen(false);
  }, [pathname]);
  useEffect(() => {
    if (!graphDrawerOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setGraphDrawerOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [graphDrawerOpen]);
  useEffect(() => {
    // Listen at the shell level so a collapsed rail can re-open before ContextRail mounts.
    const forceOpen = (event: Event) => {
      const detail = (
        event as CustomEvent<SelectionExplanationOpenDetail & { sessionId?: string }>
      ).detail;
      if (!detail) return;
      if (!detail.selectedText?.trim() && !detail.recordId) return;
      if (!isChat) return;
      setSelectionExplanationOpen(true);
      if (railCollapsed) {
        setRailCollapsed(false);
        window.localStorage.setItem("lg-rail-collapsed", "false");
      }
      // Narrow layouts hide the persistent rail and use a drawer instead.
      if (window.matchMedia("(max-width: 1100px)").matches) {
        setGraphDrawerOpen(true);
      }
    };
    window.addEventListener(selectionExplanationOpenEventName(), forceOpen);
    return () =>
      window.removeEventListener(selectionExplanationOpenEventName(), forceOpen);
  }, [isChat, railCollapsed]);
  useEffect(() => {
    const value = settings.data?.find(
      (item) => item.key === "ui.preferences",
    )?.value;
    if (settings.isSuccess) applyAppearance(value);
  }, [settings.data, settings.isSuccess]);
  const toggleSidebar = useCallback(() => {
    setCollapsed((current) => {
      window.localStorage.setItem("lg-sidebar-collapsed", String(!current));
      return !current;
    });
  }, []);
  function toggleRail() {
    setRailCollapsed((current) => {
      const next = !current;
      window.localStorage.setItem("lg-rail-collapsed", String(next));
      return next;
    });
  }
  const collapseRailFromResize = useCallback(() => {
    setRailCollapsed(true);
    window.localStorage.setItem("lg-rail-collapsed", "true");
  }, []);
  const railResize = useWorkspaceRailResize({
    width: railWidth, sidebarWidth: collapsed ? 50 : 268,
    disabled: railCollapsed && !selectionExplanationOpen,
    setWidth: setRailWidth, collapse: collapseRailFromResize,
  });
  const shellStyle = {
    "--sidebar-width": collapsed ? "50px" : "268px",
    "--rail-width": `${railResize.value}px`,
  } as CSSProperties;
  // Keep the rail mounted while collapsing so CSS can animate width / slide.
  // Only pages without a context rail use --no-inspector (true unmount).
  const railAvailable = showContextRail && !isSettings;
  const railCollapsedEffective =
    Boolean(showRailToggle && railCollapsed && !selectionExplanationOpen);
  const handleSelectionExplanationChange = useCallback((open: boolean) => {
    setSelectionExplanationOpen(open);
    if (!open) {
      // Leave graph drawer state alone on desktop; only clear the forced-open flag.
    }
  }, []);
  const openNavDrawer = useCallback(() => setNavOpen(true), []);
  // 抽屉位移统一由 CSS 变量承载：展开/收起补间、跟手拖拽、遮罩浓度共用一个进度。
  useEffect(() => {
    setDrawerProgress("left", navOpen ? 1 : 0);
  }, [navOpen]);
  useEffect(() => {
    setDrawerProgress("right", graphDrawerOpen ? 1 : 0);
  }, [graphDrawerOpen]);
  useEffect(() => {
    setDrawerScrollLock(navOpen);
    return () => setDrawerScrollLock(false);
  }, [navOpen]);
  // 抽屉展开期间把 Radix 的 portal 弹层抬到抽屉之上：抽屉面板是 z-index
  // 74/70，而弹层（头像菜单、「更多」、会话操作、确认弹窗）都 portal 到 body
  // 且层级写死 z-50，不标记就会被抽屉整个盖住，看起来就是「点了没反应」。
  useEffect(() => {
    setDrawerLayerOpen("left", navOpen);
    return () => setDrawerLayerOpen("left", false);
  }, [navOpen]);
  useEffect(() => {
    setDrawerLayerOpen("right", graphDrawerOpen);
    return () => setDrawerLayerOpen("right", false);
  }, [graphDrawerOpen]);
  const commitDrawerDrag = useCallback((side: DrawerSide, open: boolean) => {
    if (side === "left") {
      setNavOpen(open);
      return;
    }
    setGraphDrawerOpen(open);
    if (!open) setSelectionExplanationOpen(false);
  }, []);
  // 全屏手势：右滑拉出左侧导航，左滑拉出右侧图谱抽屉；在面板/遮罩上反向拖拽收起。
  useMobileDrawerDrag({
    leftAvailable: true,
    onCommit: commitDrawerDrag,
    rightAvailable: railAvailable,
  });
  return (
    <div
      className={cn(
        "workspace-shell",
        collapsed && "workspace-shell--collapsed",
        !railAvailable && "workspace-shell--no-inspector",
        railAvailable &&
          railCollapsedEffective &&
          "workspace-shell--rail-collapsed",
        showContextRail && graphDrawerOpen && "workspace-shell--graph-open",
      )}
      data-rail-state={railAvailable ? (railCollapsedEffective ? "collapsed" : "expanded") : "unavailable"}
      data-sidebar-state={collapsed ? "collapsed" : "expanded"}
      style={shellStyle}
    >
      <aside
        aria-label="主导航"
        className="workspace-sidebar min-h-svh overflow-hidden bg-sidebar"
        data-sidebar-state={collapsed ? "collapsed" : "expanded"}
      >
        <div className="sticky top-0 h-svh w-full">
          <MemoSidebarNav collapsed={collapsed} onCollapse={toggleSidebar} />
        </div>
      </aside>
      <main
        className={cn(
          "workspace-main relative min-h-svh",
          isChat && "workspace-main--chat",
          isGoalClarify && "workspace-main--goal",
        )}
      >
        {isSettings ? (
          <SettingsModal
            open
            onClose={() => {
              navigate(
                settingsOriginRef.current || `/w/${workspaceId}/home`,
              );
            }}
          >
            <SettingsOutlet />
          </SettingsModal>
        ) : (
          <>
            {isDocumentReader ? (
              <header className="sticky top-0 z-30 flex min-h-14 items-center gap-2 border-b bg-background/92 px-3 backdrop-blur-xl lg:hidden">
                <MobileNavTrigger onOpen={openNavDrawer} />
                <span className="text-sm font-semibold">LearnGraph</span>
              </header>
            ) : (
              <TopBar
                graphOpen={graphDrawerOpen}
                onOpenActivity={() => setActivityOpen(true)}
                onOpenNav={openNavDrawer}
                onToggleGraph={
                  showContextRail
                    ? () => setGraphDrawerOpen((current) => !current)
                    : undefined
                }
                onToggleRail={showRailToggle ? toggleRail : undefined}
                railCollapsed={showRailToggle ? railCollapsed : undefined}
              />
            )}
            <ShellOutlet />
          </>
        )}
      </main>
      {/* 左侧导航抽屉：常驻 DOM，位移由 --lg-left-drawer-progress 驱动 */}
      <MobileNavDrawer onOpenChange={setNavOpen} open={navOpen} />
      {railAvailable ? (
        <>
          <div
            aria-hidden={railCollapsedEffective || undefined}
            aria-label={isMemory ? "调整 AI 导师过程栏宽度" : "调整图谱栏宽度"}
            aria-orientation="vertical"
            className="workspace-resizer"
            onPointerDown={railResize.begin}
            onKeyDown={railResize.onKeyDown}
            tabIndex={railCollapsedEffective ? -1 : 0}
            aria-valuemin={railResize.minimum}
            aria-valuemax={railResize.maximum}
            aria-valuenow={railResize.value}
            aria-valuetext={`${railResize.value} 像素`}
            role="separator"
          />
          {/* Backdrop / close stay mounted so narrow layouts can fade them out. */}
          <div
            aria-hidden="true"
            className="graph-drawer-backdrop"
            data-drawer-surface="graph"
            onClick={() => {
              setGraphDrawerOpen(false);
              setSelectionExplanationOpen(false);
            }}
          />
          <MemoContextRail
            // Desktop fold, or narrow drawer closed: keep mounted for CSS but not interactive.
            collapsed={railCollapsedEffective && !graphDrawerOpen}
            onSelectionExplanationChange={handleSelectionExplanationChange}
          />
          <Button
            aria-label={isMemory ? "收起 AI 导师过程面板" : "收起图谱面板"}
            className="graph-drawer-close"
            onClick={() => {
              setGraphDrawerOpen(false);
              setSelectionExplanationOpen(false);
            }}
            size="icon-sm"
            variant="secondary"
          >
            <X className="size-4" />
          </Button>
        </>
      ) : null}
      <Sheet onOpenChange={setActivityOpen} open={activityOpen}>
        <SheetContent className="activity-drawer !w-[min(360px,92vw)] !max-w-[360px]">
          <SheetTitle className="sr-only">学习活动</SheetTitle>
          <div className="min-h-0 flex-1 overflow-y-auto p-5 pt-12">
            <ActivityRail />
          </div>
        </SheetContent>
      </Sheet>
      <PendingShareConsumer />
      <NativeActions />
    </div>
  );
}
