import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from "react";
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
  BookOpen,
  Brain,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronRight,
  CircleDot,
  CircleDollarSign,
  Database,
  FileSearch,
  Files,
  Focus,
  Folder,
  FolderPlus,
  GraduationCap,
  Home,
  ListChecks,
  LoaderCircle,
  LogOut,
  Menu,
  MessageSquareText,
  MoreHorizontal,
  Network,
  PanelLeftClose,
  Palette,
  Pencil,
  Pin,
  Play,
  Plus,
  Route,
  Save,
  Search,
  Share2,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Split,
  Trash2,
  UsersRound,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
  SheetTrigger,
} from "@/components/ui/sheet";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useAuth } from "@/features/auth/auth-context-value";
import {
  KnowledgeGraph,
  type KnowledgeNode,
} from "@/components/graph/knowledge-graph";
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
  listGoals,
  listActions,
  listNodeQuestions,
  listProjects,
  listSessions,
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
  getSessionActivity,
  getSessionActivitySnapshot,
  markSessionViewed,
  sortSidebarSessions,
  subscribeSessionActivity,
} from "@/lib/session-activity";
import type { Session } from "@/types/sessions";
import type { Graph, GraphNode, GraphSummary } from "@/types/graphs";
import type { DeleteImpact } from "@/types/workflow";

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
    path: "chat/new",
    aliases: ["/chat/", "/learn/joint"],
  },
  {
    label: "图谱",
    icon: Network,
    path: "chat/new?mode=goal",
    aliases: ["/graphs/", "/capabilities"],
  },
  {
    label: "路线",
    icon: Route,
    path: "chat/new?mode=goal",
    aliases: ["/roadmap"],
  },
  { label: "资料", icon: Files, path: "sources" },
];

const primaryMoreNav: NavItem[] = [
  {
    label: "证据",
    icon: BadgeCheck,
    path: "mastery",
    aliases: ["/mastery", "/evidence/"],
  },
  {
    label: "研究",
    icon: FileSearch,
    path: "research/search",
    aliases: ["/research/"],
  },
  {
    label: "练习",
    icon: GraduationCap,
    path: "practice",
    aliases: ["/practice"],
  },
  { label: "记忆", icon: Brain, path: "memory" },
];

type SidebarSession = {
  id: string;
  title: string;
  pinned: boolean;
  status: string;
  updated_at?: string | null;
};
type SidebarProject = {
  id: string;
  title: string;
  status: string;
  sessions: SidebarSession[];
  /** Old stored projects omit graph bindings and remain valid. */
  graphId?: string;
  graphTitle?: string;
  goalId?: string;
};

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

type LearningNodeContext = {
  graphId: string;
  nodeId?: string;
  nodeLabel?: string;
};

type LearningProjectRequest = {
  graphId?: string;
  title?: string;
  nodeId?: string;
  nodeLabel?: string;
  prompt?: string;
  /** When set, the new chat auto-send will request a graph change proposal. */
  graphAction?: "none" | "propose_create" | "propose_update";
};

const LEARNING_NODE_CONTEXT_STORAGE_KEY = "learngraph:active-learning-node";

function findProjectForSession(projects: SidebarProject[], sessionId?: string) {
  return projects.find((project) =>
    project.sessions.some((session) => session.id === sessionId),
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

function toSidebarSession(session: {
  id: string;
  title: string;
  pinned: boolean;
  status: string;
  updated_at?: string | null;
}): SidebarSession {
  return {
    id: session.id,
    title: session.title || "新会话",
    pinned: session.pinned,
    status: session.status,
    updated_at: session.updated_at ?? null,
  };
}

function publishLearningNodeContext(context: LearningNodeContext) {
  try {
    window.sessionStorage.setItem(
      LEARNING_NODE_CONTEXT_STORAGE_KEY,
      JSON.stringify(context),
    );
  } catch {
    // The in-page event remains enough for the current chat surface.
  }
  window.dispatchEvent(
    new CustomEvent("learngraph:learning-node-selected", { detail: context }),
  );
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

const settingsNav: NavItem[] = [
  { label: "模型 Provider", icon: Bot, path: "settings/providers" },
  { label: "用量与预算", icon: CircleDollarSign, path: "settings/usage" },
  { label: "扩展中心", icon: Sparkles, path: "settings/extensions" },
  { label: "搜索与研究", icon: Search, path: "settings/research" },
  { label: "存储迁移", icon: Database, path: "settings/storage/migrations" },
  { label: "权限审计", icon: ShieldCheck, path: "settings/audit" },
  { label: "个性化", icon: Palette, path: "settings/personalization" },
  { label: "工作区设置", icon: SlidersHorizontal, path: "settings/workspace" },
  { label: "账户与访问", icon: UsersRound, path: "settings/access" },
];

const titleMatchers: Array<[string, string, string]> = [
  ["/home", "工作区首页", "聚合目标、会话、行动计划与系统状态"],
  ["/goals/new/clarify", "目标澄清问卷", "自然语言入口与 AI 动态追问"],
  ["/confirm", "结构化 Goal 确认", "把目标转换为可审核数据"],
  ["/graph-review", "初始图谱审核", "候选节点、局部修订与发布"],
  ["/learn/joint", "多节点联合学习", "判断关联后再生成讲解与练习"],
  ["/versions", "会话分支与版本对比", "重试、换模型和分支均保留来源"],
  ["/chat/", "学习对话画布", "流式消息、多组件回复与图谱上下文"],
  ["/capabilities", "用户能力成长图谱", "与目标图谱共享概念但独立表达能力"],
  ["/graphs/", "图谱工作台", "浏览、筛选并聚焦学习节点"],
  ["/roadmap", "学习路线规划器", "将图谱转换为可执行时间块"],
  ["/mastery", "掌握度总览", "成长星级、可提取性与证据状态"],
  ["/evidence/review", "证据日志与审核箱", "低风险自动接受，高风险人工审核"],
  ["/practice/", "题目作答与讲解", "作答、评分、错因与证据回流"],
  ["/practice", "复习调度与练习中心", "复习、练习、讲解和应用统一调度"],
  ["/sources", "资料上传与解析中心", "文件、网页和截图统一进入资料资产库"],
  [
    "/research/tasks",
    "Deep Research 任务台",
    "预算、来源、状态和覆盖缺口可审核",
  ],
  ["/research/search", "联网搜索与网页获取", "普通搜索、正文抓取与引用分层"],
  ["/memory", "本地工作区记忆中心", "热区、冷区和可审计写入"],
  [
    "/settings/providers",
    "模型 Provider 管理",
    "LLM、Vision 与 Image Generation 分离配置",
  ],
  ["/settings/usage", "用量计费与预算", "按 Provider、任务和尝试记录成本"],
  [
    "/settings/extensions",
    "扩展中心",
    "Skills、MCP、可信组件、插件与运行审计的统一入口",
  ],
  [
    "/settings/research",
    "搜索与 Deep Research 设置",
    "Search、Fetch 与 Research Provider 分层",
  ],
  [
    "/settings/storage/migrations",
    "数据库与文件存储迁移向导",
    "离线预检、校验、切换与回滚",
  ],
  ["/settings/audit", "运行与权限审计", "模型、工具、图谱、记忆和迁移全部留痕"],
  ["/settings/personalization", "个性化", "基础风格、温和度、排版与表情等回答特征"],
  ["/settings/workspace", "用户与工作区设置", "账号、工作区、外观和数据控制"],
  ["/settings/access", "账户、组织与访问控制", "会话、Membership、RBAC 与工作区权限"],
];

function resolveTitle(pathname: string) {
  return titleMatchers
    .find(([pattern]) => pathname.includes(pattern))
    ?.slice(1) as [string, string] | undefined;
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
  const { pathname, search } = useLocation();
  const { workspaceId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [creatingConversation, setCreatingConversation] = useState(false);
  const [projects, setProjects] = useState<SidebarProject[]>([]);
  const [ungroupedSessions, setUngroupedSessions] = useState<SidebarSession[]>(
    [],
  );
  // Subscribe so completion black-dots and activity-based sort re-render live.
  const sessionActivity = useSyncExternalStore(
    subscribeSessionActivity,
    getSessionActivitySnapshot,
    getSessionActivitySnapshot,
  );
  const projectsQuery = useQuery({
    queryKey: ["projects"],
    queryFn: () => listProjects(true),
  });
  const sessionsQuery = useQuery({
    queryKey: ["sessions"],
    queryFn: listSessions,
  });
  const [activeSessionId, setActiveSessionId] = useState("");
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
  const isSettings = pathname.includes("/settings/");
  const graphsQuery = useQuery({
    queryKey: ["graphs"],
    queryFn: listGraphs,
    enabled: !isSettings,
  });
  const goalsQuery = useQuery({
    queryKey: ["goals"],
    queryFn: listGoals,
    enabled: !isSettings,
  });
  const firstProject = projectsQuery.data?.find(
    (item) =>
      item.status !== "archived" &&
      (item.primary_graph_id || item.primary_goal_id),
  );
  const routeGraphId = pathname.match(/\/graphs\/([^/]+)/)?.[1];
  const routeGoalId = pathname.match(/\/goals\/([^/]+)/)?.[1];
  const lastGraphId = window.localStorage.getItem("learngraph:last-graph-id");
  const contextGraph =
    graphsQuery.data?.find((graph) => graph.id === routeGraphId) ??
    graphsQuery.data?.find((graph) => graph.goal_id === routeGoalId) ??
    graphsQuery.data?.find((graph) => graph.id === lastGraphId) ??
    graphsQuery.data?.find(
      (graph) => graph.id === firstProject?.primary_graph_id,
    ) ??
    graphsQuery.data?.[0];
  const contextGoalId =
    routeGoalId ??
    contextGraph?.goal_id ??
    firstProject?.primary_goal_id ??
    goalsQuery.data?.[0]?.id;
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
        : item.label === "路线"
          ? {
              ...item,
            path: contextGoalId
              ? `goals/${contextGoalId}/roadmap`
              : "chat/new?mode=goal",
            }
          : item,
  );
  const nav = (isSettings ? settingsNav : runtimePrimaryNav).slice(0, 5);
  const moreNav = isSettings ? settingsNav.slice(5) : primaryMoreNav;
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
    publishProjectContext(
      findProjectForSession(projects, currentSessionId),
      currentSessionId,
    );
  }, [pathname, projects, publishProjectContext, search]);

  const trackDraftSession = useCallback((sessionId: string) => {
    draftSessionId.current = sessionId;
    setDraftSessionId(sessionId);
    setHiddenDraftSessionId(sessionId);
  }, []);

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
    // Drop a tracked draft that no longer exists on the server.
    if (
      hiddenDraftSessionId &&
      !sessionsQuery.data.some((session) => session.id === hiddenDraftSessionId)
    ) {
      releaseDraftSession(hiddenDraftSessionId);
    }
    // Empty unused draft stays out of the list once the user leaves its chat route
    // without sending a message. Re-open via "新对话" reuses the same draft.
    const viewingDraft =
      Boolean(hiddenDraftSessionId) &&
      pathname.includes(`/chat/${hiddenDraftSessionId}`);
    const hideDraftId = viewingDraft ? null : hiddenDraftSessionId;
    setProjects(
      projectsQuery.data.map((project) => ({
        id: project.id,
        title: project.title,
        status: project.status,
        graphId: project.primary_graph_id ?? undefined,
        graphTitle: project.primary_graph_id ? project.title : undefined,
        goalId: project.primary_goal_id ?? undefined,
        sessions: sortSidebarSessions(
          sessionsQuery.data
            .filter(
              (session) =>
                session.project_id === project.id &&
                session.status !== "archived" &&
                session.id !== hideDraftId,
            )
            .map((session) => toSidebarSession(session)),
          sessionActivity,
        ),
      })),
    );
    setUngroupedSessions(
      sortSidebarSessions(
        sessionsQuery.data
          .filter(
            (session) =>
              !session.project_id &&
              session.status !== "archived" &&
              session.id !== hideDraftId,
          )
          .map((session) => toSidebarSession(session)),
        sessionActivity,
      ),
    );
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
      queryClient.setQueryData<Session[]>(["sessions"], (current) => [
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
              : sortSidebarSessions([
                  toSidebarSession(session),
                  ...current,
                ]),
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
                  sessions: sortSidebarSessions(
                    current
                      .find((item) => item.id === project.id)
                      ?.sessions.some((item) => item.id === session.id)
                      ? project.sessions
                      : [sidebarSession, ...project.sessions],
                  ),
                }
              : project,
          ),
        );
        return;
      }
      setUngroupedSessions((current) =>
        current.some((item) => item.id === session.id)
          ? current
          : sortSidebarSessions([sidebarSession, ...current]),
      );
    };
    window.addEventListener("learngraph:session-created", addCreatedSession);
    return () =>
      window.removeEventListener(
        "learngraph:session-created",
        addCreatedSession,
      );
  }, [pathname, queryClient, trackDraftSession]);

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
        .then(() => queryClient.invalidateQueries({ queryKey: ["projects"] }))
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
            /** When true, always create a fresh session (project / learning entry). */
            forceNew?: boolean;
          },
    ) => {
      const options =
        typeof input === "string" ? { projectId: input } : (input ?? {});
      const projectId = options.projectId;
      // Reuse the single empty draft so multi-click "新对话" never spawns duplicates.
      if (
        !projectId &&
        !options.forceNew &&
        !options.pendingPrompt &&
        !options.learningNode &&
        draftSessionId.current
      ) {
        setActiveSessionId(draftSessionId.current);
        navigate(`${base}/chat/${draftSessionId.current}`);
        onNavigate?.();
        return;
      }
      if (creatingConversationRef.current) return;
      creatingConversationRef.current = true;
      setCreatingConversation(true);
      try {
        const project =
          options.project ?? projects.find((entry) => entry.id === projectId);
        const session = await createSession({
          memory_enabled: true,
          title: "新会话",
          goal_id: project?.goalId ?? null,
          graph_id: project?.graphId ?? null,
          project_id: project?.id ?? null,
        });
        queryClient.setQueryData<Session[]>(["sessions"], (current) => [
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
                    sessions: sortSidebarSessions([
                      sidebarSession,
                      ...entry.sessions,
                    ]),
                  }
                : entry,
            ),
          );
        } else if (options.pendingPrompt || options.learningNode) {
          // Learning-entry paths already have content intent — list them.
          releaseDraftSession();
          setUngroupedSessions((current) =>
            sortSidebarSessions([sidebarSession, ...current]),
          );
        } else {
          // Empty draft: track for reuse; show while viewing, hide after leave.
          trackDraftSession(session.id);
          setUngroupedSessions((current) =>
            sortSidebarSessions([sidebarSession, ...current]),
          );
        }
        setActiveSessionId(session.id);
        publishProjectContext(project, session.id);
        if (options.learningNode)
          publishLearningNodeContext(options.learningNode);
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
        toast.error(error instanceof Error ? error.message : "无法创建新会话");
      } finally {
        creatingConversationRef.current = false;
        setCreatingConversation(false);
      }
    },
    [
      base,
      navigate,
      onNavigate,
      projects,
      publishProjectContext,
      queryClient,
      releaseDraftSession,
      trackDraftSession,
    ],
  );

  const openLearningProject = useCallback(
    async (request: LearningProjectRequest) => {
      const graphId = request.graphId?.trim();
      const title = request.title?.trim();
      if (!graphId || !title) return;
      // “立即学习” is intentionally organized by the graph title: a graph can
      // therefore reopen its own named project without hijacking a differently
      // named project that happens to share the same source graph.
      const existing = projects.find((project) => project.title === title);
      const created = existing
        ? null
        : await createProjectRecord({ title, primary_graph_id: graphId });
      const project: SidebarProject = existing
        ? { ...existing, graphId, graphTitle: title }
        : {
            id: created!.id,
            title,
            status: "active",
            graphId,
            graphTitle: title,
            sessions: [],
          };
      if (
        !existing ||
        existing.graphId !== graphId ||
        existing.graphTitle !== title
      ) {
        setProjects((current) =>
          existing
            ? current.map((item) => (item.id === existing.id ? project : item))
            : [...current, project],
        );
        if (existing)
          await updateProjectRecord(existing.id, { primary_graph_id: graphId });
        await queryClient.invalidateQueries({ queryKey: ["projects"] });
      }
      const prompt =
        request.prompt?.trim() ||
        (request.nodeLabel ? `什么是 ${request.nodeLabel}？` : undefined);
      await createConversation({
        projectId: project.id,
        project,
        pendingPrompt: prompt,
        pendingGraphAction: request.graphAction,
        learningNode: {
          graphId,
          nodeId: request.nodeId,
          nodeLabel: request.nodeLabel,
        },
      });
    },
    [createConversation, projects, queryClient],
  );

  useEffect(() => {
    const open = (event: Event) => {
      void openLearningProject(
        (event as CustomEvent<LearningProjectRequest>).detail ?? {},
      );
    };
    window.addEventListener("learngraph:open-learning-project", open);
    return () =>
      window.removeEventListener("learngraph:open-learning-project", open);
  }, [openLearningProject]);

  async function moveSession(sessionId: string, projectId?: string) {
    const session =
      ungroupedSessions.find((item) => item.id === sessionId) ??
      projects
        .flatMap((project) => project.sessions)
        .find((item) => item.id === sessionId);
    if (!session) return;
    setProjects((current) =>
      current.map((project) => {
        const sessions = project.sessions.filter(
          (item) => item.id !== sessionId,
        );
        return project.id === projectId
          ? { ...project, sessions: [session, ...sessions] }
          : { ...project, sessions };
      }),
    );
    setUngroupedSessions((current) => {
      const withoutSession = current.filter((item) => item.id !== sessionId);
      return projectId ? withoutSession : [session, ...withoutSession];
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
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
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
                    project.sessions.map((session) => session.id),
                  ),
                ]
              : deleteTarget.project.sessions.map((session) => session.id),
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
            sessions: project.sessions.filter(
              (session) => session.id !== deleteTarget.session.id,
            ),
          })),
        );
        setUngroupedSessions((current) =>
          current.filter((session) => session.id !== deleteTarget.session.id),
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
            sessions: project.sessions.filter(
              (session) => !removedSessionIds.has(session.id),
            ),
          })),
        );
        setUngroupedSessions((current) =>
          current.filter((session) => !removedSessionIds.has(session.id)),
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
              sessions: project.sessions.filter(
                (session) => !removedSessionIds.has(session.id),
              ),
            })),
        );
        setUngroupedSessions((current) =>
          current.filter((session) => !removedSessionIds.has(session.id)),
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

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["projects"] }),
        queryClient.invalidateQueries({ queryKey: ["sessions"] }),
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
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success("项目名称已更新");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "项目改名失败");
    }
  }

  function patchSidebarSession(sessionId: string, patch: Partial<SidebarSession>) {
    setProjects((current) =>
      current.map((project) => ({
        ...project,
        sessions: sortSidebarSessions(
          project.sessions.map((session) =>
            session.id === sessionId ? { ...session, ...patch } : session,
          ),
        ),
      })),
    );
    setUngroupedSessions((current) =>
      sortSidebarSessions(
        current.map((session) =>
          session.id === sessionId ? { ...session, ...patch } : session,
        ),
      ),
    );
  }

  async function renameSession(session: SidebarSession, title: string) {
    const nextTitle = title.trim();
    if (!nextTitle || nextTitle === session.title) return true;
    try {
      const updated = await updateSession(session.id, { title: nextTitle });
      patchSidebarSession(session.id, { title: updated.title });
      queryClient.setQueryData<Session[]>(["sessions"], (current) =>
        current?.map((item) => (item.id === updated.id ? updated : item)),
      );
      toast.success("会话名称已更新");
      return true;
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "会话改名失败");
      return false;
    }
  }

  async function toggleSessionPin(session: SidebarSession) {
    try {
      const updated = await updateSession(session.id, { pinned: !session.pinned });
      patchSidebarSession(session.id, { pinned: updated.pinned });
      queryClient.setQueryData<Session[]>(["sessions"], (current) =>
        current?.map((item) => (item.id === updated.id ? updated : item)),
      );
      toast.success(updated.pinned ? "会话已置顶" : "已取消置顶");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "置顶状态更新失败");
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
      await queryClient.invalidateQueries({ queryKey: ["sessions"] });
      toast.success(session.status === "archived" ? "会话已恢复" : "会话已归档");
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "会话状态更新失败");
    }
  }

  async function shareSession(session: SidebarSession) {
    const url = `${window.location.origin}/w/${workspaceId}/chat/${session.id}`;
    try {
      await navigator.clipboard.writeText(url);
      toast.success("会话链接已复制");
    } catch {
      toast.error("无法复制会话链接");
    }
  }

  async function toggleProjectArchive(project: SidebarProject) {
    try {
      if (project.status === "archived") await restoreProject(project.id);
      else await archiveProject(project.id);
      await queryClient.invalidateQueries({ queryKey: ["projects"] });
      toast.success(
        project.status === "archived" ? "项目已恢复" : "项目已归档",
      );
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "项目状态更新失败");
    }
  }

  return (
    <div
      className={cn(
        "sidebar-nav flex h-full flex-col bg-sidebar px-3 py-4 text-sidebar-foreground",
        collapsed && "is-collapsed",
      )}
    >
      <div className="flex items-center justify-between">
        {collapsed && !mobile ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label="展开侧边栏"
                className="sidebar-brand flex items-center gap-2 px-2 py-2"
                onClick={onCollapse}
                variant="ghost"
              >
                <span className="sidebar-brand__mark grid size-8 place-items-center rounded-xl">
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
          <Link
            className="sidebar-brand flex items-center gap-2 px-2 py-2"
            onClick={onNavigate}
            to={`${base}/home`}
          >
            <span className="sidebar-brand__mark grid size-8 place-items-center rounded-xl">
              <Network className="size-4" />
            </span>
            <span className="sidebar-text text-lg font-semibold">
              LearnGraph
            </span>
          </Link>
        )}
        {!mobile && !collapsed ? (
          <Button
            aria-label="折叠侧边栏"
            onClick={onCollapse}
            size="icon-sm"
            title="折叠侧边栏"
            variant="ghost"
          >
            <PanelLeftClose />
          </Button>
        ) : null}
      </div>
      {isSettings ? (
        <Button
          asChild
          className="mt-3 justify-start text-muted-foreground"
          size="sm"
          variant="ghost"
        >
          <Link onClick={onNavigate} to={`${base}/home`}>
            <Home className="size-4" strokeWidth={2.25} />
            <span className="sidebar-text">返回工作区</span>
          </Link>
        </Button>
      ) : null}

      <nav
        aria-label={isSettings ? "设置导航" : "主导航"}
        className="mt-4 space-y-1"
      >
        {nav.map((item) => {
          const Icon = item.icon;
          const active = isNavActive(pathname, item);
          const className = cn(
            "flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm transition-colors hover:bg-sidebar-accent hover:text-sidebar-accent-foreground",
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
                onClick={() => void createConversation()}
                type="button"
              >
                <Icon
                  className={cn("size-4.5", active && "text-primary")}
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
              className={className}
              data-nav-label={item.label}
              key={`${item.label}-${item.path}`}
              onClick={onNavigate}
              to={`${base}/${item.path}`}
            >
              <Icon
                className={cn("size-4.5", active && "text-primary")}
                strokeWidth={2.25}
              />
              <span className="sidebar-text">{item.label}</span>
            </NavLink>
          );
        })}
      </nav>

      {moreNav.length ? (
        <div className="sidebar-nav__more mt-1">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
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

      <SessionProjects
        activeSessionId={activeSessionId}
        onCreateConversation={(projectId) =>
          projectId
            ? createConversation(projectId)
            : createConversation()
        }
        onCreateProject={async (title) => {
          const created = await createProjectRecord({ title });
          await queryClient.invalidateQueries({ queryKey: ["projects"] });
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
          publishProjectContext(project, sessionId);
          navigate(`${base}/chat/${sessionId}`);
          onNavigate?.();
        }}
        projects={projects}
        ungroupedSessions={ungroupedSessions}
      />
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
      <div className="mt-auto" />
      <UserMenu collapsed={collapsed} mobile={mobile} />
    </div>
  );
}

function SessionProjects({
  activeSessionId,
  onCreateConversation,
  onCreateProject,
  onRequestProjectDeletion,
  onRenameProject,
  onToggleProjectArchive,
  onMoveSession,
  onRequestSessionBatchDeletion,
  onRequestMixedBatchDeletion,
  onRequestSessionDeletion,
  onRenameSession,
  onToggleSessionPin,
  onToggleSessionArchive,
  onShareSession,
  onSelectSession,
  projects,
  ungroupedSessions,
}: {
  activeSessionId: string;
  onCreateConversation: (projectId?: string) => Promise<void>;
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
  onRequestSessionDeletion: (session: SidebarSession) => void;
  onRenameSession: (session: SidebarSession, title: string) => Promise<boolean>;
  onToggleSessionPin: (session: SidebarSession) => void;
  onToggleSessionArchive: (session: SidebarSession) => void;
  onShareSession: (session: SidebarSession) => void;
  onSelectSession: (sessionId: string, project?: SidebarProject) => void;
  projects: SidebarProject[];
  ungroupedSessions: SidebarSession[];
}) {
  const [expandedProjects, setExpandedProjects] = useState<
    Record<string, boolean>
  >({});
  const [creatingProject, setCreatingProject] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [sessionQuery, setSessionQuery] = useState("");
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
  const sessionActivity = useSyncExternalStore(
    subscribeSessionActivity,
    getSessionActivitySnapshot,
    getSessionActivitySnapshot,
  );
  const allSessions = useMemo(
    () => [
      ...projects.flatMap((project) => project.sessions),
      ...ungroupedSessions,
    ],
    [projects, ungroupedSessions],
  );
  const normalizedQuery = sessionQuery.trim().toLocaleLowerCase();
  const visibleProjects = useMemo(
    () =>
      projects.flatMap((project) => {
        const sessions = sortSidebarSessions(
          normalizedQuery
            ? project.sessions.filter((session) =>
                session.title.toLocaleLowerCase().includes(normalizedQuery),
              )
            : project.sessions,
          sessionActivity,
        );
        return sessions.length || !normalizedQuery
          ? [{ ...project, sessions }]
          : [];
      }),
    [normalizedQuery, projects, sessionActivity],
  );
  const visibleUngroupedSessions = useMemo(
    () =>
      sortSidebarSessions(
        normalizedQuery
          ? ungroupedSessions.filter((session) =>
              session.title.toLocaleLowerCase().includes(normalizedQuery),
            )
          : ungroupedSessions,
        sessionActivity,
      ),
    [normalizedQuery, sessionActivity, ungroupedSessions],
  );
  const visibleSessionIds = useMemo(
    () =>
      new Set([
        ...visibleProjects.flatMap((project) =>
          project.sessions.map((session) => session.id),
        ),
        ...visibleUngroupedSessions.map((session) => session.id),
      ]),
    [visibleProjects, visibleUngroupedSessions],
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
          project.sessions.map((session) => session.id),
        ),
      ),
    [selectedProjects],
  );
  const selectedStandaloneSessions = allSessions.filter(
    (session) =>
      selectedSessionIds.has(session.id) &&
      !coveredBySelectedProjects.has(session.id),
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

  function toggleSessionSelection(sessionId: string) {
    setSelectedSessionIds((current) => {
      const next = new Set(current);
      if (next.has(sessionId)) next.delete(sessionId);
      else if (next.size < 100) next.add(sessionId);
      return next;
    });
    // Deselect a project folder if any of its sessions is unchecked.
    const parent = projects.find((project) =>
      project.sessions.some((session) => session.id === sessionId),
    );
    if (parent && selectedProjectIds.has(parent.id)) {
      setSelectedProjectIds((current) => {
        if (!current.has(parent.id)) return current;
        const next = new Set(current);
        next.delete(parent.id);
        return next;
      });
    }
  }

  function toggleProjectSelection(project: SidebarProject) {
    setSelectedProjectIds((current) => {
      const next = new Set(current);
      const selecting = !next.has(project.id);
      if (selecting) next.add(project.id);
      else next.delete(project.id);
      // Keep nested session checkboxes in sync with the project folder.
      setSelectedSessionIds((sessions) => {
        const sessionNext = new Set(sessions);
        for (const session of project.sessions) {
          if (selecting) {
            if (sessionNext.size < 100) sessionNext.add(session.id);
          } else {
            sessionNext.delete(session.id);
          }
        }
        return sessionNext;
      });
      return next;
    });
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
      setExpandedProjects((current) => ({ ...current, [id]: true }));
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

  return (
    <section
      className="sidebar-recent sidebar-sessions mt-5 min-h-0 flex-1 border-t pt-4"
      aria-label="会话"
    >
      <div className="sidebar-sessions__heading group flex items-center justify-between px-3">
        <p className="text-sm font-bold text-foreground">会话</p>
        <div className="sidebar-sessions__header-actions">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label={searchOpen ? "关闭会话搜索" : "搜索会话"}
                className="sidebar-sessions__search-trigger"
                onClick={() => {
                  setSearchOpen((open) => !open);
                  if (searchOpen) setSessionQuery("");
                }}
                size="icon-xs"
                variant="ghost"
              >
                <Search className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">搜索会话</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label={selectionMode ? "退出批量管理" : "批量管理"}
                onClick={() => {
                  setSelectionMode((active) => {
                    const next = !active;
                    if (next) {
                      setSelectedSessionIds(
                        new Set(
                          allSessions.slice(0, 100).map((session) => session.id),
                        ),
                      );
                      setSelectedProjectIds(
                        new Set(projects.map((project) => project.id)),
                      );
                    } else {
                      setSelectedSessionIds(new Set());
                      setSelectedProjectIds(new Set());
                    }
                    return next;
                  });
                }}
                size="icon-xs"
                variant={selectionMode ? "secondary" : "ghost"}
              >
                <ListChecks className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">批量管理</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label="新建对话"
                onClick={() => void onCreateConversation()}
                size="icon-xs"
                variant="ghost"
              >
                <Plus className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">新建对话</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                aria-label="新建项目"
                onClick={() => setCreatingProject((open) => !open)}
                size="icon-xs"
                variant="ghost"
              >
                <FolderPlus className="size-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right">新建项目</TooltipContent>
          </Tooltip>
        </div>
      </div>
      {searchOpen ? (
        <div className="sidebar-sessions__search">
          <Search aria-hidden="true" className="size-3.5" />
          <Input
            aria-label="搜索会话标题"
            autoFocus
            onChange={(event) => setSessionQuery(event.currentTarget.value)}
            placeholder="按标题关键词搜索"
            value={sessionQuery}
          />
          {sessionQuery ? (
            <Button
              aria-label="清空会话搜索"
              onClick={() => setSessionQuery("")}
              size="icon-xs"
              variant="ghost"
            >
              <X className="size-3" />
            </Button>
          ) : null}
        </div>
      ) : null}
      {creatingProject ? (
        <form
          className="sidebar-sessions__project-form"
          onSubmit={(event) => {
            event.preventDefault();
            void addProject();
          }}
        >
          <Input
            aria-label="项目名称"
            autoFocus
            onChange={(event) => setProjectName(event.currentTarget.value)}
            placeholder="项目名称"
            value={projectName}
          />
          <Button aria-label="确认新建项目" size="icon-xs" type="submit">
            <Plus className="size-3.5" />
          </Button>
        </form>
      ) : null}
      <div className="sidebar-sessions__scroller mt-2 space-y-2">
        {visibleProjects.map((project) => {
          const open = expandedProjects[project.id] ?? true;
          return (
            <section className="sidebar-project" key={project.id}>
              <div
                className={cn(
                  "sidebar-project__heading group",
                  selectionMode && "is-selecting",
                )}
              >
                {selectionMode ? (
                  <Checkbox
                    aria-label={`选择项目 ${project.title}`}
                    checked={selectedProjectIds.has(project.id)}
                    className="sidebar-project__select"
                    onCheckedChange={() => toggleProjectSelection(project)}
                  />
                ) : null}
                <button
                  aria-expanded={open}
                  className="sidebar-project__header"
                  onClick={() => {
                    if (selectionMode) {
                      toggleProjectSelection(project);
                      return;
                    }
                    setExpandedProjects((current) => ({
                      ...current,
                      [project.id]: !open,
                    }));
                  }}
                  type="button"
                >
                  <Folder className="size-3.5" />
                  <span>{project.title}</span>
                  <small>
                    {project.status === "archived"
                      ? "已归档"
                      : project.sessions.length}
                  </small>
                  <ChevronRight
                    className={cn(
                      "size-3.5 transition-transform",
                      open && "rotate-90",
                    )}
                  />
                </button>
                {!selectionMode ? (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      aria-label={`管理项目 ${project.title}`}
                      className="sidebar-project__delete-project"
                      size="icon-xs"
                      type="button"
                      variant="ghost"
                    >
                      <MoreHorizontal className="size-3" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    align="end"
                    className="w-40"
                    side="right"
                  >
                    {project.status !== "archived" ? (
                      <DropdownMenuItem
                        onSelect={() => onRenameProject(project)}
                      >
                        <Pencil className="size-3.5" />
                        改名
                      </DropdownMenuItem>
                    ) : null}
                    <DropdownMenuItem
                      onSelect={() => onToggleProjectArchive(project)}
                    >
                      <Archive className="size-3.5" />
                      {project.status === "archived" ? "恢复项目" : "归档项目"}
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onSelect={() => onRequestProjectDeletion(project)}
                      variant="destructive"
                    >
                      <Trash2 className="size-3.5" />
                      永久删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                ) : null}
              </div>
              {open && project.status !== "archived" ? (
                <div className="sidebar-project__sessions">
                  {project.sessions.length ? (
                    project.sessions.map((session) => (
                      <div
                        className={cn(
                          "sidebar-project__session group",
                          activeSessionId === session.id && "is-active",
                          selectionMode && "is-selecting",
                        )}
                        key={session.id}
                      >
                        {selectionMode ? (
                          <Checkbox
                            aria-label={`选择会话 ${session.title}`}
                            checked={selectedSessionIds.has(session.id)}
                            onCheckedChange={() =>
                              toggleSessionSelection(session.id)
                            }
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
                              aria-label={`编辑会话名称 ${session.title}`}
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
                            onClick={() =>
                              selectionMode
                                ? toggleSessionSelection(session.id)
                                : onSelectSession(session.id, project)
                            }
                            type="button"
                          >
                            {getSessionActivity(session.id).unreadCompleted ? (
                              <span
                                aria-label="模型回复已完成"
                                className="sidebar-session__unread-dot"
                                title="模型回复已完成"
                              />
                            ) : null}
                            <span className="min-w-0 flex-1 truncate">
                              {session.title}
                            </span>
                            {getSessionActivity(session.id).running ? (
                              <LoaderCircle
                                aria-label="生成中"
                                className="sidebar-session__running size-3 shrink-0 animate-spin"
                              />
                            ) : null}
                          </button>
                        )}
                        {!selectionMode && renamingSessionId !== session.id ? (
                          <>
                            <Tooltip>
                              <TooltipTrigger asChild>
                                <Button
                                  aria-label={session.pinned ? `取消置顶 ${session.title}` : `置顶 ${session.title}`}
                                  className={cn("sidebar-project__pin", session.pinned && "is-pinned")}
                                  onClick={() => onToggleSessionPin(session)}
                                  size="icon-xs"
                                  type="button"
                                  variant="ghost"
                                >
                                  <Pin className="size-3" />
                                </Button>
                              </TooltipTrigger>
                              <TooltipContent side="right">{session.pinned ? "取消置顶" : "置顶会话"}</TooltipContent>
                            </Tooltip>
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button
                                  aria-label={`更多会话操作 ${session.title}`}
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
                                side="right"
                              >
                                <DropdownMenuItem onSelect={() => onShareSession(session)}>
                                  <Share2 className="size-3.5" />
                                  分享
                                </DropdownMenuItem>
                                <DropdownMenuItem onSelect={() => beginSessionRename(session)}>
                                  <Pencil className="size-3.5" />
                                  重命名
                                </DropdownMenuItem>
                                <DropdownMenuItem onSelect={() => onToggleSessionPin(session)}>
                                  <Pin className={cn("size-3.5", session.pinned && "sidebar-project__pin-icon--pinned")} />
                                  {session.pinned ? "取消置顶" : "置顶聊天"}
                                </DropdownMenuItem>
                                <DropdownMenuItem onSelect={() => onToggleSessionArchive(session)}>
                                  <Archive className="size-3.5" />
                                  {session.status === "archived" ? "恢复" : "归档"}
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                  onSelect={() => onMoveSession(session.id)}
                                >
                                  <Folder className="size-3.5" />
                                  移出项目
                                </DropdownMenuItem>
                                {projects
                                  .filter((entry) => entry.id !== project.id)
                                  .map((target) => (
                                    <DropdownMenuItem
                                      key={target.id}
                                      onSelect={() =>
                                        onMoveSession(session.id, target.id)
                                      }
                                    >
                                      <Folder className="size-3.5" />
                                      移至「{target.title}」
                                    </DropdownMenuItem>
                                  ))}
                                <DropdownMenuSeparator />
                                <DropdownMenuItem
                                  onSelect={() =>
                                    onRequestSessionDeletion(session)
                                  }
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
                    ))
                  ) : (
                    <button
                      className="sidebar-project__empty"
                      onClick={() => void onCreateConversation(project.id)}
                      type="button"
                    >
                      新建此项目的对话
                    </button>
                  )}
                </div>
              ) : null}
            </section>
          );
        })}
        {visibleUngroupedSessions.length ? (
          <div className="sidebar-sessions__ungrouped">
            {visibleUngroupedSessions.map((session) => (
              <div
                className={cn(
                  "sidebar-project__session sidebar-project__session--ungrouped group",
                  activeSessionId === session.id && "is-active",
                  selectionMode && "is-selecting",
                )}
                key={session.id}
              >
                {selectionMode ? (
                  <Checkbox
                    aria-label={`选择会话 ${session.title}`}
                    checked={selectedSessionIds.has(session.id)}
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
                      aria-label={`编辑会话名称 ${session.title}`}
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
                    onClick={() =>
                      selectionMode
                        ? toggleSessionSelection(session.id)
                        : onSelectSession(session.id)
                    }
                    type="button"
                  >
                    {getSessionActivity(session.id).unreadCompleted ? (
                      <span
                        aria-label="模型回复已完成"
                        className="sidebar-session__unread-dot"
                        title="模型回复已完成"
                      />
                    ) : null}
                    <span className="min-w-0 flex-1 truncate">
                      {session.title}
                    </span>
                    {getSessionActivity(session.id).running ? (
                      <LoaderCircle
                        aria-label="生成中"
                        className="sidebar-session__running size-3 shrink-0 animate-spin"
                      />
                    ) : null}
                  </button>
                )}
                {!selectionMode && renamingSessionId !== session.id ? (
                  <>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          aria-label={session.pinned ? `取消置顶 ${session.title}` : `置顶 ${session.title}`}
                          className={cn("sidebar-project__pin", session.pinned && "is-pinned")}
                          onClick={() => onToggleSessionPin(session)}
                          size="icon-xs"
                          variant="ghost"
                        >
                          <Pin className="size-3" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent side="right">{session.pinned ? "取消置顶" : "置顶会话"}</TooltipContent>
                    </Tooltip>
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          aria-label={`更多会话操作 ${session.title}`}
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
                        side="right"
                      >
                        <DropdownMenuItem onSelect={() => onShareSession(session)}>
                          <Share2 className="size-3.5" />
                          分享
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => beginSessionRename(session)}>
                          <Pencil className="size-3.5" />
                          重命名
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => onToggleSessionPin(session)}>
                          <Pin className={cn("size-3.5", session.pinned && "sidebar-project__pin-icon--pinned")} />
                          {session.pinned ? "取消置顶" : "置顶聊天"}
                        </DropdownMenuItem>
                        <DropdownMenuItem onSelect={() => onToggleSessionArchive(session)}>
                          <Archive className="size-3.5" />
                          {session.status === "archived" ? "恢复" : "归档"}
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        {projects.map((project) => (
                          <DropdownMenuItem
                            key={project.id}
                            onSelect={() =>
                              onMoveSession(session.id, project.id)
                            }
                          >
                            <Folder className="size-3.5" />
                            {project.title}
                          </DropdownMenuItem>
                        ))}
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
            ))}
          </div>
        ) : null}
        {normalizedQuery && !visibleSessionIds.size ? (
          <p className="sidebar-sessions__no-results">没有匹配的会话</p>
        ) : null}
      </div>
      {selectionMode ? (
        <div className="sidebar-sessions__batch-bar">
          <div>
            <strong>{selectedCount}</strong>
            <span> 个已选</span>
            {selectedProjects.length ? (
              <span className="sidebar-sessions__batch-detail">
                {" "}
                · {selectedProjects.length} 项目
                {selectedStandaloneSessions.length
                  ? ` · ${selectedStandaloneSessions.length} 会话`
                  : ""}
              </span>
            ) : null}
          </div>
          <Button
            onClick={() => {
              const allVisibleSessionsSelected = [...visibleSessionIds].every(
                (id) => selectedSessionIds.has(id),
              );
              const allVisibleProjectsSelected = [...visibleProjectIds].every(
                (id) => selectedProjectIds.has(id),
              );
              const allSelected =
                allVisibleSessionsSelected && allVisibleProjectsSelected;
              if (allSelected) {
                setSelectedSessionIds((current) => {
                  const next = new Set(current);
                  visibleSessionIds.forEach((id) => next.delete(id));
                  return next;
                });
                setSelectedProjectIds((current) => {
                  const next = new Set(current);
                  visibleProjectIds.forEach((id) => next.delete(id));
                  return next;
                });
              } else {
                setSelectedSessionIds((current) => {
                  const next = new Set(current);
                  for (const id of visibleSessionIds) {
                    if (next.size >= 100) break;
                    next.add(id);
                  }
                  return next;
                });
                setSelectedProjectIds((current) => {
                  const next = new Set(current);
                  visibleProjectIds.forEach((id) => next.add(id));
                  return next;
                });
              }
            }}
            size="xs"
            variant="ghost"
          >
            {[...visibleSessionIds].every((id) => selectedSessionIds.has(id)) &&
            [...visibleProjectIds].every((id) => selectedProjectIds.has(id))
              ? "取消本页"
              : "全选可见"}
          </Button>
          <Button
            disabled={!selectedCount}
            onClick={requestSelectedDeletion}
            size="xs"
            variant="destructive"
          >
            <Trash2 className="size-3" />
            删除
          </Button>
        </div>
      ) : null}
    </section>
  );
}

function UserMenu({
  mobile = false,
  collapsed = false,
}: {
  mobile?: boolean;
  collapsed?: boolean;
}) {
  const { logout, username, workspaceId, workspaceName } = useAuth();
  const navigate = useNavigate();
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          className="sidebar-user-menu mt-3 h-auto w-full shrink-0 justify-start gap-3 overflow-hidden rounded-xl px-2 py-2"
          variant="ghost"
        >
          <Avatar className="size-8 shrink-0">
            <AvatarFallback className="sidebar-user-avatar text-xs font-semibold">
              {username.slice(0, 1).toUpperCase()}
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
              className="block truncate text-[11px] font-normal text-muted-foreground"
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
          onSelect={() => navigate(`/w/${workspaceId}/settings/workspace`)}
        >
          <Settings />
          设置
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => navigate(`/w/${workspaceId}/memory`)}>
          <Archive />
          工作区记忆
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={() => void logout()} variant="destructive">
          <LogOut />
          退出登录
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function MobileNavigation() {
  const [open, setOpen] = useState(false);
  return (
    <Sheet onOpenChange={setOpen} open={open}>
      <SheetTrigger asChild>
        <Button
          aria-label="打开导航"
          className="lg:hidden"
          size="icon"
          variant="ghost"
        >
          <Menu className="size-5" />
        </Button>
      </SheetTrigger>
      <SheetContent className="w-[286px] p-0" side="left">
        <SheetTitle className="sr-only">LearnGraph 导航</SheetTitle>
        <SidebarNav mobile onNavigate={() => setOpen(false)} />
      </SheetContent>
    </Sheet>
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

function TopBar({ onOpenActivity }: { onOpenActivity: () => void }) {
  const { pathname, search } = useLocation();
  const { workspaceId = "" } = useParams();
  const auth = useAuth();
  const isChat = pathname.includes("/chat/");
  const isGoalMode = isChat && new URLSearchParams(search).get("mode") === "goal";
  const sessionId = pathname.match(/\/chat\/([^/]+)/)?.[1];
  const sessions = useQuery({
    queryKey: ["sessions"],
    queryFn: listSessions,
    enabled: isChat,
  });
  const graphs = useQuery({
    queryKey: ["graphs"],
    queryFn: listGraphs,
    enabled: isChat,
  });
  const dashboard = useQuery({
    queryKey: ["dashboard", workspaceId],
    queryFn: getDashboard,
    enabled: !isChat,
  });
  const activeSession = sessions.data?.find((session) => session.id === sessionId);
  const activeGraph = activeSession?.graph_id
    ? graphs.data?.find((graph) => graph.id === activeSession.graph_id)
    : undefined;
  const [chatHeader, setChatHeader] = useState<{
    canClose: boolean;
    graphTitle?: string;
    modelConnected: boolean;
    sessionClosed: boolean;
    sessionTitle: string;
  }>();
  const [density, setDensity] = useState<"compact" | "comfortable">(() =>
    window.localStorage.getItem("lg-information-density") === "compact"
      ? "compact"
      : "comfortable",
  );
  useEffect(() => {
    document.documentElement.dataset.density = density;
    window.localStorage.setItem("lg-information-density", density);
  }, [density]);
  useEffect(() => {
    const updateHeader = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          canClose: boolean;
          graphTitle?: string;
          modelConnected: boolean;
          sessionClosed: boolean;
          sessionTitle: string;
        }>
      ).detail;
      if (detail) setChatHeader(detail);
    };
    window.addEventListener("learngraph:chat-header", updateHeader);
    return () =>
      window.removeEventListener("learngraph:chat-header", updateHeader);
  }, []);
  const [title, description] = isGoalMode
    ? ["学习对话画布", "在对话中澄清目标并审核图谱"]
    : isChat
      ? [
          chatHeader?.sessionTitle ?? activeSession?.title ?? "新会话",
          "学习对话",
        ]
      : (resolveTitle(pathname) ?? [
          "LearnGraph",
          "目标驱动、可生长的学习图谱智能体",
        ]);
  const systemStatuses = Object.entries(dashboard.data?.system_status ?? {});
  const systemHealthy = isChat
    ? Boolean(chatHeader?.modelConnected)
    : systemStatuses.length > 0 && systemStatuses.every(([, value]) => statusIsHealthy(value));
  return (
    <header className="sticky top-0 z-30 flex min-h-16 items-center gap-3 border-b bg-background/92 px-4 backdrop-blur-xl sm:px-6">
      <MobileNavigation />
      <div className="min-w-0 flex-1">
        <p className="truncate text-base font-semibold tracking-tight">
          {title}
        </p>
        <p className="hidden truncate text-xs text-muted-foreground sm:block">
          {description}
        </p>
      </div>
      <div className="hidden items-center gap-2 md:flex">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button className="topbar-status" size="sm" variant="ghost">
              <span
                aria-hidden="true"
                className={cn("topbar-status__dot", systemHealthy && "is-healthy")}
              />
              系统状态
              <ChevronDown className="size-3.5" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-64">
            <DropdownMenuLabel>{auth.workspaceName || "当前工作区"}</DropdownMenuLabel>
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
                <DropdownMenuItem disabled>
                  <Network className="size-4" />
                  {chatHeader?.graphTitle ?? activeGraph?.title ?? "未绑定图谱"}
                </DropdownMenuItem>
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
            <DropdownMenuSeparator />
            <DropdownMenuLabel>信息密度</DropdownMenuLabel>
            <DropdownMenuItem onSelect={() => setDensity("compact")}>
              {density === "compact" ? <Check className="size-4" /> : <span className="size-4" />}
              紧凑
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setDensity("comfortable")}>
              {density === "comfortable" ? <Check className="size-4" /> : <span className="size-4" />}
              舒适
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              aria-label="打开学习活动"
              onClick={onOpenActivity}
              size="icon-sm"
              variant="ghost"
            >
              <CalendarDays className="size-4" />
            </Button>
          </TooltipTrigger>
          <TooltipContent>学习活动</TooltipContent>
        </Tooltip>
        {isChat && sessionId !== "new" ? (
          <Button
            disabled={!chatHeader?.canClose}
            onClick={() =>
              window.dispatchEvent(
                new CustomEvent("learngraph:close-session-requested"),
              )
            }
            size="xs"
            variant="ghost"
          >
            <Check className="size-3" />
            {chatHeader?.sessionClosed ? "已结束" : "结束学习"}
          </Button>
        ) : null}
      </div>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            aria-label="打开页面工具"
            className="shrink-0 md:hidden"
            size="icon-sm"
            variant="ghost"
          >
            <SlidersHorizontal className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-64 md:hidden">
          <DropdownMenuItem onSelect={onOpenActivity}>
            <CalendarDays className="size-4" />
            学习活动
          </DropdownMenuItem>
          {isChat && sessionId !== "new" ? (
            <DropdownMenuItem
              disabled={!chatHeader?.canClose}
              onSelect={() =>
                window.dispatchEvent(
                  new CustomEvent("learngraph:close-session-requested"),
                )
              }
            >
              <Check className="size-4" />
              {chatHeader?.sessionClosed ? "已结束学习" : "结束学习"}
            </DropdownMenuItem>
          ) : null}
          <DropdownMenuSeparator />
          <DropdownMenuLabel>系统状态</DropdownMenuLabel>
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
              <DropdownMenuItem disabled>
                <Network className="size-4" />
                <span className="truncate">
                  {chatHeader?.graphTitle ?? activeGraph?.title ?? "未绑定图谱"}
                </span>
              </DropdownMenuItem>
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
          <DropdownMenuSeparator />
          <DropdownMenuLabel>信息密度</DropdownMenuLabel>
          <DropdownMenuItem onSelect={() => setDensity("compact")}>
            {density === "compact" ? (
              <Check className="size-4" />
            ) : (
              <span className="size-4" />
            )}
            紧凑
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setDensity("comfortable")}>
            {density === "comfortable" ? (
              <Check className="size-4" />
            ) : (
              <span className="size-4" />
            )}
            舒适
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </header>
  );
}

function ContextRail() {
  const { pathname, search } = useLocation();
  const { workspaceId = "" } = useParams();
  const [projectContext, setProjectContext] = useState<{
    project?: SidebarProject;
    sessionId?: string;
  }>();
  const railProjects = useQuery({
    queryKey: ["projects"],
    queryFn: () => listProjects(),
  });
  const railSessions = useQuery({
    queryKey: ["sessions"],
    queryFn: listSessions,
  });
  const isSettings = pathname.includes("/settings/");
  const isGoalClarify = pathname.includes("/goals/new/clarify");
  const isGraph = pathname.includes("/graphs/");
  const isChat = pathname.includes("/chat/");
  const isGoalMode = isChat && new URLSearchParams(search).get("mode") === "goal";
  const sessionId =
    new URLSearchParams(search).get("sidebarSession") ??
    pathname.match(/\/chat\/([^/]+)/)?.[1];
  const showActivity = [
    "/home",
    "/sources",
    "/roadmap",
    "/mastery",
    "/evidence/",
    "/research/",
    "/practice",
    "/memory",
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
              `${railSessions.data?.filter((item) => item.status !== "archived").length ?? 0} 个活跃会话`,
            ],
            [
              "Memory",
              `${railSessions.data?.filter((item) => item.memory_enabled).length ?? 0} 个会话已启用`,
            ],
            ["Scope", sessionId ? "当前会话" : "当前工作区"],
          ],
    [isSettings, railProjects.data, railSessions.data, sessionId],
  );

  useEffect(() => {
    const session = railSessions.data?.find((item) => item.id === sessionId);
    const project = railProjects.data?.find(
      (item) => item.id === session?.project_id,
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
  }, [railProjects.data, railSessions.data, sessionId]);

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

  const activeProject =
    projectContext && projectContext.sessionId === sessionId
      ? projectContext.project
      : undefined;
  return (
    <aside className="context-rail min-h-svh bg-card px-4 py-5">
      {isGoalClarify || isGoalMode ? (
        <GoalGraphPreviewRail />
      ) : isGraph ? (
        <GraphWorkspaceRail />
      ) : isChat ? (
        <ChatGraphRail
          project={activeProject}
          sessionId={sessionId}
          workspaceId={workspaceId}
        />
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
                <dt className="text-[11px] uppercase tracking-wide text-muted-foreground">
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
      )}
    </aside>
  );
}

function ActivityRail() {
  const now = new Date();
  const [selectedDay, setSelectedDay] = useState(now.getDate());
  const sessions = useQuery({ queryKey: ["sessions"], queryFn: listSessions });
  const actions = useQuery({ queryKey: ["actions"], queryFn: listActions });
  const weekdayLabels = ["一", "二", "三", "四", "五", "六", "日"];
  const year = now.getFullYear();
  const month = now.getMonth();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const leadingBlanks = (new Date(year, month, 1).getDay() + 6) % 7;
  const dayActivity = useMemo(() => {
    const result: Record<number, string[]> = {};
    const add = (value: string, label: string) => {
      const date = new Date(value);
      if (date.getFullYear() !== year || date.getMonth() !== month) return;
      result[date.getDate()] = [...(result[date.getDate()] ?? []), label];
    };
    sessions.data?.forEach((session) =>
      add(session.created_at, `创建会话：${session.title}`),
    );
    actions.data?.forEach((action) => {
      if (action.completed_at)
        add(action.completed_at, `完成行动：${action.title}`);
      else if (action.due_at) add(action.due_at, `计划行动：${action.title}`);
    });
    return result;
  }, [actions.data, month, sessions.data, year]);
  const activities = dayActivity[selectedDay] ?? ["当天没有记录的学习活动"];
  return (
    <section className="activity-rail" aria-label="学习活动日历">
      <div className="activity-rail__title">
        <div>
          <p className="text-sm font-semibold">学习活动</p>
          <p>
            {year}年{month + 1}月
          </p>
        </div>
        <CalendarDays className="size-4" />
      </div>
      <div className="activity-rail__weekdays">
        {weekdayLabels.map((label) => (
          <span key={label}>{label}</span>
        ))}
      </div>
      <div className="activity-rail__calendar">
        {Array.from({ length: leadingBlanks }).map((_, index) => (
          <span aria-hidden="true" key={`blank-${index}`} />
        ))}
        {Array.from({ length: daysInMonth }).map((_, index) => {
          const day = index + 1;
          const intensity = Math.min(3, dayActivity[day]?.length ?? 0);
          return (
            <button
              aria-label={`${month + 1}月${day}日`}
              aria-pressed={selectedDay === day}
              className={`activity-rail__day is-level-${intensity}`}
              key={day}
              onClick={() => setSelectedDay(day)}
              type="button"
            >
              {day}
            </button>
          );
        })}
      </div>
      <div className="activity-rail__details">
        <p>
          <strong>
            {month + 1}月{selectedDay}日
          </strong>{" "}
          的活动
        </p>
        <ul>
          {activities.map((activity, index) => (
            <li key={`${activity}-${index}`}>{activity}</li>
          ))}
        </ul>
      </div>
    </section>
  );
}

function ChatGraphRail({
  project,
  sessionId,
  workspaceId,
}: {
  project?: SidebarProject;
  sessionId?: string;
  workspaceId: string;
}) {
  const [view, setView] = useState<"learning" | "capability">("learning");
  const [boundOverride, setBoundOverride] = useState<{
    graphId: string;
    graphTitle: string;
  }>();
  const isLearning = view === "learning";
  const binding =
    boundOverride ??
    (project?.graphId
      ? {
          graphId: project.graphId,
          graphTitle: project.graphTitle || project.title,
        }
      : undefined);

  useEffect(() => setBoundOverride(undefined), [project?.id, project?.graphId]);

  const bindGraph = (graph: GraphSummary) => {
    if (!project) {
      window.dispatchEvent(
        new CustomEvent("learngraph:open-learning-project", {
          detail: { graphId: graph.id, title: graph.title },
        }),
      );
      return;
    }
    setBoundOverride({ graphId: graph.id, graphTitle: graph.title });
    window.dispatchEvent(
      new CustomEvent("learngraph:bind-project-graph", {
        detail: {
          projectId: project.id,
          graphId: graph.id,
          graphTitle: graph.title,
        },
      }),
    );
    window.dispatchEvent(
      new CustomEvent("learngraph:project-context", {
        detail: {
          workspaceId,
          project: { ...project, graphId: graph.id, graphTitle: graph.title },
          sessionId,
        },
      }),
    );
  };

  return (
    <div className="chat-graph-rail">
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
          当前学习
        </Button>
        <Button
          aria-selected={!isLearning}
          onClick={() => setView("capability")}
          role="tab"
          size="xs"
          variant={!isLearning ? "secondary" : "ghost"}
        >
          能力成长
        </Button>
      </div>
      {isLearning ? (
        binding ? (
          <BoundGraphRail
            graphId={binding.graphId}
            title={binding.graphTitle}
          />
        ) : (
          <ProjectBookshelf project={project} onBind={bindGraph} />
        )
      ) : (
        <CapabilityGraphRail />
      )}
    </div>
  );
}

function ProjectBookshelf({
  project,
  onBind,
}: {
  project?: SidebarProject;
  onBind: (graph: GraphSummary) => void;
}) {
  const graphs = useQuery({ queryKey: ["graphs"], queryFn: listGraphs });
  const books = graphs.data ?? [];
  return (
    <section
      aria-label="图谱书架"
      className="project-bookshelf chat-graph-rail__canvas"
    >
      <div className="project-bookshelf__intro">
        <BookOpen className="size-4" />
        <div>
          <p className="text-sm font-semibold">图谱书架</p>
          <p>
            {project
              ? `「${project.title}」尚未绑定图谱`
              : "当前会话尚未归入有图谱的项目"}
          </p>
        </div>
      </div>
      <div className="project-bookshelf__list">
        {books.map((graph) => (
          <button key={graph.id} onClick={() => onBind(graph)} type="button">
            <span className="project-bookshelf__spine">
              <Network className="size-3.5" />
            </span>
            <span>
              <strong>{graph.title}</strong>
              <small>
                {graph.status === "published"
                  ? "已发布 · 点击绑定并预览"
                  : "候选图谱 · 点击绑定并预览"}
              </small>
            </span>
            <ChevronRight className="size-3.5" />
          </button>
        ))}
      </div>
      <p className="project-bookshelf__hint">
        选择一本图谱后，会显示在右侧并成为该项目新会话的默认上下文。
      </p>
    </section>
  );
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
  graphId,
  selectedNodeId: requestedNodeId,
  title,
}: {
  graphId: string;
  selectedNodeId?: string;
  title: string;
}) {
  const queryClient = useQueryClient();
  const graphQuery = useQuery({
    queryKey: ["graph", graphId],
    queryFn: () => getGraph(graphId),
    enabled: Boolean(graphId),
  });
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ label: "", description: "" });
  const [validation, setValidation] = useState<{
    errors: string[];
    suggestions: string[];
  }>();

  useEffect(() => {
    setSelectedNodeId(requestedNodeId);
    setEditing(false);
    setValidation(undefined);
  }, [graphId, requestedNodeId]);

  const graph = graphQuery.data;

  useEffect(() => {
    if (!graph) return;
    if (requestedNodeId) return;
    if (selectedNodeId && graph.nodes.some((node) => node.id === selectedNodeId))
      return;
    const preferred = pickDefaultLearningNode(graph);
    if (!preferred) return;
    setSelectedNodeId(preferred.id);
    publishLearningNodeContext({
      graphId,
      nodeId: preferred.id,
      nodeLabel: preferred.label,
    });
  }, [graph, graphId, requestedNodeId, selectedNodeId]);

  const selectedNode =
    graph?.nodes.find((node) => node.id === selectedNodeId) ??
    (graph ? pickDefaultLearningNode(graph) : undefined);
  const effectiveNodeId = selectedNodeId ?? selectedNode?.id;
  const questionsQuery = useQuery({
    queryKey: ["node-questions", graphId, effectiveNodeId],
    queryFn: () => listNodeQuestions(graphId, effectiveNodeId!),
    enabled: Boolean(effectiveNodeId),
    staleTime: 15_000,
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
  const railGraph = useMemo(
    () => (graph ? toRailKnowledgeGraph(graph, exploreCounts) : undefined),
    [exploreCounts, graph],
  );
  const children =
    selectedNode && graph
      ? graph.edges
          .filter((edge) => edge.source_node_id === selectedNode.id)
          .map((edge) => edge.target_node_id)
      : [];
  const isLeaf = children.length === 0;
  // Same query key as chat completion invalidation so "未深入" flips live.
  const questions = questionsQuery.data ?? [];

  useEffect(() => {
    if (!selectedNode) return;
    setDraft({
      label: selectedNode.label,
      description: selectedNode.description,
    });
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
      queryClient.setQueryData<Graph>(["graph", graphId], (current) =>
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
      void queryClient.invalidateQueries({ queryKey: ["graph", graphId] });
    },
    onError: (error) => {
      toast.error(error.message);
      void queryClient.invalidateQueries({ queryKey: ["graph", graphId] });
    },
  });
  const focus = useMutation({
    mutationFn: (nodeId: string) =>
      updateGraphNode(graphId, nodeId, { attention_state: "focused" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["graph", graphId] });
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
      queryClient.setQueryData<Graph>(["graph", graphId], (current) =>
        current
          ? {
              ...current,
              nodes: current.nodes.map((node) =>
                node.id === updated.id ? { ...node, ...updated } : node,
              ),
            }
          : current,
      );
      void queryClient.invalidateQueries({ queryKey: ["graph", graphId] });
      void queryClient.invalidateQueries({ queryKey: ["mastery"] });
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

  function assessDraft() {
    if (!selectedNode || !graph)
      return { errors: ["请选择一个节点。"], suggestions: [] };
    const label = draft.label.trim();
    const description = draft.description.trim();
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
      label: draft.label.trim(),
      description: draft.description.trim(),
    };
    update.mutate({ nodeId: selectedNode.id, ...payload });
  }

  if (graphQuery.isPending)
    return (
      <div className="chat-graph-rail__canvas grid place-items-center text-xs text-muted-foreground">
        正在读取项目图谱…
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

  const rootLabel =
    graph.nodes.find((node) => node.node_type === "root")?.label ??
    graph.nodes[0]?.label ??
    title;

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

  function askNode(question: string) {
    if (!selectedNode) return;
    rememberLastLearnedNode(graphId, selectedNode.id);
    publishLearningNodeContext({
      graphId,
      nodeId: selectedNode.id,
      nodeLabel: selectedNode.label,
    });
    window.dispatchEvent(
      new CustomEvent("learngraph:compose", { detail: { content: question } }),
    );
  }

  function studyNode() {
    if (!selectedNode) return;
    rememberLastLearnedNode(graphId, selectedNode.id);
    publishLearningNodeContext({
      graphId,
      nodeId: selectedNode.id,
      nodeLabel: selectedNode.label,
    });
    const encyclopediaPrompt =
      `请以百科词条格式讲解「${selectedNode.label}」。` +
      `要求：1) 精确定义与边界；2) 在「${rootLabel}」中的位置；` +
      `3) 1–2 个关键例子；4) 常见误区；5) 可自测的掌握标准。` +
      `用清晰小标题组织，不要只复述节点说明。`;
    window.dispatchEvent(
      new CustomEvent("learngraph:compose", {
        detail: { content: encyclopediaPrompt, autoSend: true },
      }),
    );
    toast.success(`开始学习：${selectedNode.label}`);
  }

  function studyNextNode() {
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
    setSelectedNodeId(next.id);
    rememberLastLearnedNode(graphId, next.id);
    publishLearningNodeContext({
      graphId,
      nodeId: next.id,
      nodeLabel: next.label,
    });
    const encyclopediaPrompt =
      `请以百科词条格式讲解「${next.label}」。` +
      `要求：1) 精确定义与边界；2) 与「${selectedNode.label}」的关系；` +
      `3) 关键例子；4) 常见误区；5) 可自测的掌握标准。`;
    window.dispatchEvent(
      new CustomEvent("learngraph:compose", {
        detail: { content: encyclopediaPrompt, autoSend: true },
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

  function splitNode() {
    if (!selectedNode) return;
    rememberLastLearnedNode(graphId, selectedNode.id);
    publishLearningNodeContext({
      graphId,
      nodeId: selectedNode.id,
      nodeLabel: selectedNode.label,
    });
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
    window.dispatchEvent(
      new CustomEvent("learngraph:compose", {
        detail: {
          content: prompt,
          autoSend: true,
          graphAction: "propose_update",
        },
      }),
    );
    toast.success(`已发起「${selectedNode.label}」拆分变更`);
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
      <KnowledgeGraph
        className="bound-graph-rail__canvas"
        compact
        edges={railGraph.edges}
        interactive
        layout="tree"
        maximumZoom={2.4}
        minimumZoom={0.15}
        nodes={railGraph.nodes}
        onSelect={(node) => {
          setSelectedNodeId(node.id);
          setEditing(false);
          setValidation(undefined);
          rememberLastLearnedNode(graphId, node.id);
          publishLearningNodeContext({
            graphId,
            nodeId: node.id,
            nodeLabel: node.label,
          });
        }}
        selectedId={selectedNode?.id}
        showZoomControls
        title={title || graph.title}
      />
      {selectedNode ? (
        <div className="bound-graph-rail__inspector">
          <div className="bound-graph-rail__node-title">
            <div>
              <span>
                当前节点 ·{" "}
                {isLeaf ? "叶子节点" : `${children.length} 个下级节点`}
              </span>
              <strong>{selectedNode.label}</strong>
            </div>
            {selectedNode.attention_state === "mastered" ? (
              <Badge variant="secondary">已掌握</Badge>
            ) : selectedNode.attention_state === "focused" ? (
              <Badge variant="secondary">重点</Badge>
            ) : null}
          </div>
          {editing ? (
            <div className="graph-rail-editor">
              <label>
                节点名称
                <Input
                  aria-label="节点名称"
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      label: event.currentTarget.value,
                    }))
                  }
                  value={draft.label}
                />
              </label>
              <label>
                学习说明
                <textarea
                  aria-label="节点学习说明"
                  onChange={(event) =>
                    setDraft((current) => ({
                      ...current,
                      description: event.currentTarget.value,
                    }))
                  }
                  rows={3}
                  value={draft.description}
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
              <p className="bound-graph-rail__description">
                {selectedNode.description || "还没有补充学习说明。"}
              </p>
              <div className="bound-graph-rail__actions">
                <Button onClick={studyNode} size="xs">
                  <Play className="size-3.5" />
                  学习此节点
                </Button>
                <Button onClick={studyNextNode} size="xs" variant="outline">
                  <GraduationCap className="size-3.5" />
                  下一节点
                </Button>
                <Button
                  disabled={masteryMark.isPending}
                  onClick={toggleMastered}
                  size="xs"
                  title={
                    selectedNode.attention_state === "mastered"
                      ? "取消已掌握：节点将移出能力成长图谱"
                      : "标记已掌握：节点进入能力成长图谱"
                  }
                  variant={
                    selectedNode.attention_state === "mastered"
                      ? "secondary"
                      : "outline"
                  }
                >
                  <BadgeCheck className="size-3.5" />
                  {masteryMark.isPending
                    ? "更新中…"
                    : selectedNode.attention_state === "mastered"
                      ? "已掌握"
                      : "标为已掌握"}
                </Button>
                <Button
                  onClick={splitNode}
                  size="xs"
                  title="调用智能体生成该节点的图谱变更提案（拆分子节点）"
                  variant="outline"
                >
                  <Split className="size-3.5" />
                  拆分
                </Button>
                <Button
                  onClick={() => focus.mutate(selectedNode.id)}
                  size="xs"
                  variant="outline"
                >
                  <Focus className="size-3.5" />
                  {selectedNode.attention_state === "focused"
                    ? "重点"
                    : "设为重点"}
                </Button>
                <Button onClick={startEditing} size="xs" variant="ghost">
                  <Pencil className="size-3.5" />
                  编辑
                </Button>
              </div>
              {parentNode || childNodes.length ? (
                <div className="bound-graph-rail__relations">
                  {parentNode ? (
                    <button
                      className="bound-graph-rail__chip"
                      onClick={() => {
                        setSelectedNodeId(parentNode.id);
                        rememberLastLearnedNode(graphId, parentNode.id);
                      }}
                      type="button"
                    >
                      上级 · {parentNode.label}
                    </button>
                  ) : null}
                  {childNodes.slice(0, 4).map((child) => (
                    <button
                      className="bound-graph-rail__chip"
                      key={child.id}
                      onClick={() => {
                        setSelectedNodeId(child.id);
                        rememberLastLearnedNode(graphId, child.id);
                      }}
                      type="button"
                    >
                      下级 · {child.label}
                    </button>
                  ))}
                </div>
              ) : null}
            </>
          )}
          <section className="node-question-history">
            <div>
              <p>学习动作</p>
              <span>{questions.length} 条历史提问</span>
            </div>
            {questions.length ? (
              <ol>
                {questions.slice(0, 4).map((question) => (
                  <li key={question.id}>{question.content}</li>
                ))}
              </ol>
            ) : (
              <p className="node-question-history__empty">
                从「学习此节点」开始百科式讲解；提问会自动归档。
              </p>
            )}
            <div className="node-question-history__actions">
              <Button
                onClick={() =>
                  askNode(
                    `请用百科词条格式解释「${selectedNode.label}」的定义、边界与典型例子。`,
                  )
                }
                size="xs"
                variant="secondary"
              >
                百科讲解
              </Button>
              <Button onClick={compareWithParent} size="xs" variant="outline">
                对比上级
              </Button>
              <Button
                onClick={() =>
                  askNode(
                    `围绕「${selectedNode.label}」出 3 道由浅入深的自测题，并给出参考答案要点。`,
                  )
                }
                size="xs"
                variant="outline"
              >
                自测题
              </Button>
              <Button
                onClick={() =>
                  askNode(
                    `「${selectedNode.label}」常见误区有哪些？各给一个纠正方式。`,
                  )
                }
                size="xs"
                variant="outline"
              >
                常见误区
              </Button>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}

function CapabilityGraphRail() {
  const [maxDepth, setMaxDepth] = useState(1);
  const mastery = useQuery({ queryKey: ["mastery"], queryFn: getMastery });
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
      <KnowledgeGraph
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
      />
      {!mastery.isPending && masteredNodes.length === 0 ? (
        <p className="capability-graph-rail__empty">
          在「当前学习」中为节点点击「标为已掌握」后，才会进入能力成长图谱。
        </p>
      ) : null}
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
        <KnowledgeGraph
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
        />
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
  const { graphId: paramGraphId = "" } = useParams();
  const { pathname, search } = useLocation();
  // ContextRail is rendered by the parent workspace route. Derive the child
  // graph id from the URL as well, so the rail never falls back to GET /graphs/.
  const graphId = paramGraphId || graphIdFromPathname(pathname);
  const [view, setView] = useState<"learning" | "capability">("learning");
  const graph = useQuery({
    queryKey: ["graph", graphId],
    queryFn: () => getGraph(graphId),
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
        <CapabilityGraphRail />
      )}
    </div>
  );
}

export function WorkspaceShell() {
  const { pathname, search } = useLocation();
  const settings = useQuery({
    queryKey: ["settings"],
    queryFn: listSettings,
  });
  const [collapsed, setCollapsed] = useState(
    () => window.localStorage.getItem("lg-sidebar-collapsed") !== "false",
  );
  const [railWidth, setRailWidth] = useState(360);
  const [activityOpen, setActivityOpen] = useState(false);
  const isChat = pathname.includes("/chat/");
  const isDocumentReader = pathname.includes("/documents/");
  const isGoalClarify =
    pathname.includes("/goals/new/clarify") ||
    (isChat && new URLSearchParams(search).get("mode") === "goal");
  const showContextRail = isChat || isGoalClarify;
  const hideInspector = !showContextRail;
  useEffect(() => {
    const value = settings.data?.find(
      (item) => item.key === "ui.preferences",
    )?.value;
    if (!value || typeof value !== "object" || !("theme" in value)) return;
    const isDark = (value as { theme?: unknown }).theme === "dark";
    document.documentElement.classList.toggle("dark", isDark);
    try {
      window.localStorage.setItem("lg-theme", isDark ? "dark" : "light");
    } catch {
      // Tab still keeps the applied class when storage is unavailable.
    }
  }, [settings.data]);
  function toggleSidebar() {
    setCollapsed((current) => {
      window.localStorage.setItem("lg-sidebar-collapsed", String(!current));
      return !current;
    });
  }
  function beginRailResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    document.body.classList.add("is-resizing");
    const onMove = (moveEvent: PointerEvent) => {
      const sidebarWidth = collapsed ? 72 : 216;
      const maximum = Math.max(280, window.innerWidth - sidebarWidth - 360);
      setRailWidth(
        Math.min(maximum, Math.max(280, window.innerWidth - moveEvent.clientX)),
      );
    };
    const onEnd = () => {
      document.body.classList.remove("is-resizing");
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onEnd);
  }
  const shellStyle = {
    "--sidebar-width": collapsed ? "72px" : "216px",
    "--rail-width": `${railWidth}px`,
  } as CSSProperties;
  return (
    <div
      className={cn(
        "workspace-shell",
        collapsed && "workspace-shell--collapsed",
        hideInspector && "workspace-shell--no-inspector",
      )}
      style={shellStyle}
    >
      <aside className="workspace-sidebar min-h-svh overflow-hidden bg-sidebar">
        <div className="sticky top-0 h-svh w-full">
          <SidebarNav collapsed={collapsed} onCollapse={toggleSidebar} />
        </div>
      </aside>
      <main
        className={cn(
          "workspace-main relative min-h-svh",
          isChat && "workspace-main--chat",
          isGoalClarify && "workspace-main--goal",
        )}
      >
        {isDocumentReader ? (
          <header className="sticky top-0 z-30 flex min-h-14 items-center gap-2 border-b bg-background/92 px-3 backdrop-blur-xl lg:hidden">
            <MobileNavigation />
            <span className="text-sm font-semibold">LearnGraph</span>
          </header>
        ) : (
          <TopBar onOpenActivity={() => setActivityOpen(true)} />
        )}
        <Outlet />
      </main>
      {!hideInspector ? (
        <>
          <div
            aria-label="调整图谱栏宽度"
            aria-orientation="vertical"
            className="workspace-resizer"
            onPointerDown={beginRailResize}
            role="separator"
          />
          <ContextRail />
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
    </div>
  );
}
