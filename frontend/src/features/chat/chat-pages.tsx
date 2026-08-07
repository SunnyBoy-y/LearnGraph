import {
  memo,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { createUuid } from "@/lib/uuid";
import {
  useMutation,
  useQueries,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ArrowUp,
  Bot,
  CalendarDays,
  Check,
  ChevronLeft,
  ChevronDown,
  ChevronRight,
  Copy,
  FilePlus2,
  FileSearch,
  FileText,
  GitBranch,
  GitCompareArrows,
  LayoutDashboard,
  ImageIcon,
  LoaderCircle,
  MessageSquareQuote,
  Mic,
  Network,
  Pencil,
  RefreshCcw,
  Search,
  Settings2,
  Sparkles,
  Square,
  Target,
  TerminalSquare,
  X,
} from "lucide-react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { SandboxAuthDialog, type SandboxAuthRequest } from "@/components/chat/sandbox-auth-dialog";
import { SandboxBuildProgress } from "@/components/shared/sandbox-build-progress";

import { toast } from "sonner";

import {
  ApiError,
  autoTitleSession,
  branchSession,
  cancelSessionMessage,
  cleanupDictation,
  closeSession,
  confirmCompositeDraft,
  confirmGraphChangeSet,
  createSession,
  createCompositeDraft,
  discoverProviderModels,
  generateSessionSuggestedPrompts,
  getAgentSandboxReadiness,
  getCurrentUser,
  getSandboxBootstrapStatus,
  getMessageSnapshot,
  getSessionContextUsage,
  getSessionSuggestedPrompts,
  listMessageVersions,
  listSessionMessageEvents,
  listProviders,
  listGraphs,
  listMemories,
  listSessionMessages,
  listSessionMessagesPage,
  listSettings,
  listSessions,
  listFiles,
  lookupFile,
  parseFile,
  rejectGraphChangeSet,
  retrySessionMessage,
  startSandboxBootstrap,
  streamSessionMessage,
  undoGraphChangeSet,
  updateSession,
  uploadFile,
  listAudioTranscriptions,
  transcribeAudioFile,
  transcribeDictationSegment,
} from "@/api";
import { hashFileSha256 } from "@/lib/file-hash";
import {
  providerDictationSupported,
  startProviderDictation,
} from "@/lib/provider-dictation";
import type { ProviderDictationHandle } from "@/lib/provider-dictation";
import {
  realtimeDictationSupported,
  startRealtimeDictation,
} from "@/lib/realtime-dictation";
import {
  classifyNonAgentAttachment,
  isAudioNameOrMime,
  isImageNameOrMime,
  isVideoNameOrMime,
  nonAgentAttachmentBlockedMessage,
} from "@/lib/chat-attachment-policy";
import {
  Conversation,
  ConversationContent,
  ConversationEmptyState,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation";
import {
  Message as AiMessage,
  MessageAction,
  MessageActions,
  MessageContent,
} from "@/components/ai-elements/message";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  PromptInput,
  PromptInputActionAddAttachments,
  PromptInputActionMenu,
  PromptInputActionMenuContent,
  PromptInputActionMenuItem,
  PromptInputActionMenuTrigger,
  PromptInputBody,
  PromptInputAttachments,
  PromptInputButton,
  PromptInputSubmit,
  PromptInputTextarea,
  type PromptInputMessage,
} from "@/components/ai-elements/prompt-input";
import { ChatStreamPartRenderer } from "@/components/chat/chat-stream-part-renderer";
import { DeepResearchApprovalFromPart } from "@/components/chat/message-part-renderer";
import {
  groupQuestionParts,
  QuestionSetPager,
} from "@/components/chat/question-set-pager";
import type { TrustedComponentAction } from "@/components/chat/trusted-component-renderer";
import {
  locateSelectionInContent,
  selectionToolbarPoint,
} from "@/features/chat/text-selection";
import {
  createSelectionExplanationId,
  decorateSelectionExplanationMarks,
  inferSelectionAction,
  listSelectionExplanations,
  openSelectionExplanation,
  selectionExplanationRecordsEventName,
  splitTextWithSelectionMarks,
  upsertSelectionExplanation,
  type SelectionExplanationRecord,
} from "@/features/chat/selection-explanation";
import {
  markSessionGenerationFinished,
  markSessionRunning,
  markSessionTouched,
  markSessionViewed,
} from "@/lib/session-activity";
import {
  abortSessionStream,
  clearSessionStream,
  getSessionStream,
  isSessionStreaming,
  listStreamingSessionIds,
  registerSessionStream,
  setSessionStreamMessageId,
} from "@/lib/session-streams";
import {
  PageFrame,
  PageIntro,
  SectionHeading,
  StatePill,
  Surface,
} from "@/components/shared/page-elements";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { InputGroupAddon } from "@/components/ui/input-group";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { FileRecord } from "@/types/files";
import {
  isDeepSeekProvider,
  isModelProviderType,
  type ProviderModel,
} from "@/types/providers";
import type { WorkspaceSetting } from "@/types/settings";
import {
  clearDraftSessionId,
  getDraftSessionId,
  isDefaultDraftTitle,
  setDraftSessionId,
} from "@/lib/draft-session";
import {
  defaultComposerPrefs,
  defaultComposerPrefsForResponseMode,
  getSessionComposerPrefs,
  hasSessionComposerPrefs,
  inheritSessionComposerPrefs,
  isDefaultComposerPrefs,
  prefsFromModelSnapshot,
  setSessionComposerPrefs,
  type GenerationMode,
  type ResponseMode,
  type SearchRoute,
  type ThinkingMode,
} from "@/lib/session-composer-prefs";
import {
  capabilityThinkingModes,
  fuzzyModelMatch,
  isRealtimeTranscriptionModel,
  modelChoiceValue,
  modelProtocolLabel,
  parseModelChoiceValue,
  providerCapabilityString,
  providerModelOptions,
  thinkingLabels,
} from "@/lib/model-choices";
import {
  areChatSuggestedPromptsEnabled,
  isChatContextUsageEnabled,
  isChatDictationCleanupEnabled,
  readChatDefaultResponseMode,
  readChatFeatureModelSetting,
  CHAT_AUTO_TITLE_MODEL_SETTING_KEY,
  CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY,
  CHAT_DICTATION_CLEANUP_SETTING_KEY,
  CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY,
} from "@/lib/workspace-settings";
import { ContextUsageRing } from "@/components/chat/context-usage-ring";
import { shouldShowSuggestedPromptError } from "@/lib/suggested-prompts";
import { cn } from "@/lib/utils";
import {
  workspaceQueryKey,
} from "@/lib/query-keys";
import {
  groupPartsForDisplay,
  isDeepResearchApprovalPart,
  orderedMessageParts,
  shouldShowThinkingPlaceholder,
  thinkingDurationSeconds,
} from "@/features/chat/chat-message-parts";
import { ThinkingChain } from "@/components/chat/thinking-chain";
import {
  GoalSetupConversation,
  useGoalSetupFlow,
} from "@/features/goals/goal-chat-flow";
import type { Goal } from "@/types/goals";
import type { Graph } from "@/types/graphs";
import type {
  Message,
  MessageCreateRequest,
  MessagePart,
  MessageRetryRequest,
  MessageSelectionContext,
  Session,
  SessionMessageStreamData,
  SuggestedPrompt,
} from "@/types/sessions";

type ChatStatus = "ready" | "submitted" | "streaming" | "error";
type GraphAction = NonNullable<MessageCreateRequest["graph_action"]>;
type ImageSize = NonNullable<MessageCreateRequest["image_size"]>;

const IMAGE_EDIT_MIME_TYPES = new Set([
  "image/png",
  "image/jpeg",
  "image/webp",
]);

/** Whether a model can edit/regenerate from reference images. */
function isImageEditModel(
  model: { id: string; capabilities?: ProviderModel["capabilities"] } | undefined,
): boolean {
  if (!model) return false;
  const id = model.id.toLowerCase();
  return (
    model.capabilities?.supports_image_edit === true ||
    id === "gpt-image-2" ||
    id.startsWith("qwen-image-edit")
  );
}

/** Whether a model can serve as a text chat model (not image-only output). */
function isTextChatModel(
  model: { capabilities?: ProviderModel["capabilities"] } | undefined,
): boolean {
  return model?.capabilities?.supports_text_output !== false;
}

const IMAGE_SIZE_OPTIONS: ReadonlyArray<{
  value: ImageSize;
  label: string;
  detail: string;
}> = [
  { value: "auto", label: "自动", detail: "由模型决定" },
  { value: "2048x2048", label: "1:1", detail: "2048 × 2048" },
  { value: "2048x1152", label: "16:9", detail: "2048 × 1152" },
  { value: "1152x2048", label: "9:16", detail: "1152 × 2048" },
  { value: "1536x1152", label: "4:3", detail: "1536 × 1152" },
  { value: "1152x1536", label: "3:4", detail: "1152 × 1536" },
];

type LearningNodeContext = {
  graphId: string;
  nodeId?: string;
  nodeIds?: string[];
  nodeLabel?: string;
};

type TextSelectionMenu = MessageSelectionContext & {
  /** False when the browser selection could not be mapped onto durable content. */
  contentMatched: boolean;
  left: number;
  occurrenceIndex: number;
  top: number;
};

type ConversationJumpItem = {
  id: string;
  label: string;
  branch: boolean;
  active: boolean;
};
type ConversationBranchLink = { id: string; label: string; active: boolean };

/** Matches the phone breakpoint used by the workspace CSS (index.css). */
const PHONE_LAYOUT_QUERY = "(max-width: 780px)";

function usePhoneLayout() {
  const [isPhone, setIsPhone] = useState(
    () => window.matchMedia(PHONE_LAYOUT_QUERY).matches,
  );
  useEffect(() => {
    const query = window.matchMedia(PHONE_LAYOUT_QUERY);
    const update = () => setIsPhone(query.matches);
    update();
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  return isPhone;
}

function SandboxReadinessNotice({ workspaceId }: { workspaceId: string }) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const readiness = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "agent-sandbox-readiness"),
    queryFn: getAgentSandboxReadiness,
    refetchInterval: (query) =>
      query.state.data?.available === false ? 5_000 : false,
  });
  const bootstrap = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "sandbox-bootstrap-status"),
    queryFn: getSandboxBootstrapStatus,
    enabled: readiness.data?.code === "sandbox_backend_unavailable",
    refetchInterval: (query) =>
      query.state.data?.active_job ? 1_500 : false,
  });
  const initialize = useMutation({
    mutationFn: startSandboxBootstrap,
    onSuccess: (result) => {
      setConfirmOpen(false);
      if (!result.accepted && result.error_message) {
        toast.error("沙箱初始化未启动", {
          description: result.error_message,
        });
        return;
      }
      toast.success(
        result.joined_existing
          ? "已加入正在进行的沙箱初始化"
          : "沙箱初始化已开始",
      );
      queryClient.setQueryData(
        workspaceQueryKey(workspaceId, "sandbox-bootstrap-status"),
        result.status,
      );
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "sandbox-bootstrap-status"),
      });
    },
    onError: (error) =>
      toast.error("沙箱初始化失败", {
        description: error instanceof Error ? error.message : "请稍后重试",
      }),
  });
  const currentUser = useQuery({
    queryKey: ["auth-me"],
    queryFn: getCurrentUser,
    staleTime: 5 * 60_000,
  });
  const isAdmin = Boolean(currentUser.data?.is_system_admin);

  useEffect(() => {
    if (!bootstrap.data?.image_ready) return;
    void queryClient.invalidateQueries({
      queryKey: workspaceQueryKey(workspaceId, "agent-sandbox-readiness"),
    });
  }, [bootstrap.data?.image_ready, queryClient, workspaceId]);

  if (
    dismissed ||
    !readiness.data ||
    readiness.data.available ||
    readiness.data.code !== "sandbox_backend_unavailable"
  )
    return null;

  const status = bootstrap.data;
  const dockerMissing = status?.docker_installed === false;
  const dockerStopped =
    status?.docker_installed === true && status.docker_reachable === false;
  const needsInitialization =
    status?.docker_reachable === true && status.image_ready === false;
  const activeJob = status?.active_job;
  const memberGateRestricted = status?.member_bootstrap_allowed === false;
  const canTriggerInit = !memberGateRestricted || isAdmin;
  const title = dockerMissing
    ? "智能体需要 Docker 环境"
    : dockerStopped
      ? "请先启动 Docker"
      : activeJob
        ? "正在初始化智能体沙箱"
        : needsInitialization
          ? "智能体沙箱尚未初始化"
          : "智能体沙箱暂不可用";
  const description = dockerMissing
    ? "当前设备未检测到 Docker。安装并启动 Docker Desktop（Windows/macOS）或 Docker Engine（Linux）后，才能使用文件执行与代码沙箱工具；普通智能体对话仍可继续。"
    : dockerStopped
      ? "已检测到 Docker，但引擎当前不可达。请启动 Docker 后刷新沙箱状态。文件/代码沙箱工具会暂时不可用，对话本身不受阻。"
      : activeJob
        ? ""
        : needsInitialization
          ? canTriggerInit
            ? "已检测到可用的 Docker。是否现在构建并初始化智能体沙箱？首次初始化可能需要几分钟。未初始化前，沙箱工具不可用，但智能体对话仍可发送。"
            : "已检测到可用的 Docker，但当前账号暂未被允许初始化沙箱。请联系工作区管理员在沙箱设置中开启「允许普通成员初始化沙箱」。"
          : readiness.data.message;

  return (
    <>
      <div
        className="mb-2 flex flex-col gap-3 rounded-xl border border-amber-300 bg-amber-50 p-3 text-amber-950 shadow-sm dark:border-amber-800 dark:bg-amber-950/45 dark:text-amber-100 sm:flex-row sm:items-center"
        role="alert"
      >
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-amber-200/70 dark:bg-amber-900">
          <TerminalSquare className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold">{title}</p>
          {description ? (
            <p className="mt-0.5 text-xs leading-5 opacity-85">{description}</p>
          ) : null}
          {activeJob ? (
            <SandboxBuildProgress
              className="mt-2"
              job={activeJob}
              tone="amber"
            />
          ) : null}
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {needsInitialization && !activeJob && canTriggerInit ? (
            <Button
              disabled={initialize.isPending || !status?.can_initialize}
              onClick={() => setConfirmOpen(true)}
              size="xs"
            >
              <TerminalSquare className="size-3.5" />
              立即初始化
            </Button>
          ) : null}
          <Button
            onClick={() =>
              navigate(`/w/${workspaceId}/settings/extensions?tab=sandbox`)
            }
            size="xs"
            variant="outline"
          >
            <Settings2 className="size-3.5" />
            前往沙箱设置
          </Button>
          <Button
            aria-label="关闭沙箱提醒"
            onClick={() => setDismissed(true)}
            size="icon-xs"
            variant="ghost"
          >
            <X className="size-3.5" />
          </Button>
        </div>
      </div>
      <AlertDialog onOpenChange={setConfirmOpen} open={confirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>初始化智能体沙箱？</AlertDialogTitle>
            <AlertDialogDescription>
              LearnGraph 将使用本机 Docker
              构建并登记隔离运行镜像。首次构建需要下载基础镜像，可能需要几分钟。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>暂不初始化</AlertDialogCancel>
            <AlertDialogAction
              disabled={initialize.isPending}
              onClick={() => initialize.mutate()}
            >
              {initialize.isPending ? "正在启动…" : "同意并初始化"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

function ConversationJumpNav({
  items,
  branches = [],
  onJump,
  onBranchJump,
}: {
  items: ConversationJumpItem[];
  branches?: ConversationBranchLink[];
  onJump: (id: string) => void;
  onBranchJump: (id: string) => void;
}) {
  if (!items.length && !branches.length) return null;
  return (
    <nav
      aria-label="对话快速跳转"
      className="conversation-jump-nav"
    >
      <div className="conversation-jump-nav__handle" aria-hidden="true">
        {items.map((item) => (
          <span className={item.active ? "is-active" : undefined} key={item.id} />
        ))}
      </div>
      <div className="conversation-jump-nav__panel">
        <p>本次问答</p>
        <div className="conversation-jump-nav__list">
          {items.map((item, index) => (
            <button
              aria-current={item.active ? "location" : undefined}
              key={item.id}
              onClick={() => onJump(item.id)}
              type="button"
            >
              <span>{index + 1}</span>
              <strong>{item.label}</strong>
              {item.branch ? <GitBranch aria-label="分支问答" /> : null}
            </button>
          ))}
        </div>
        {branches.length ? (
          <div className="conversation-jump-nav__branches">
            {branches.map((branch) => (
              <button
                aria-current={branch.active ? "page" : undefined}
                key={branch.id}
                onClick={() => onBranchJump(branch.id)}
                type="button"
              >
                <strong>{branch.label}</strong>
                <GitBranch aria-label="分支会话" />
              </button>
            ))}
          </div>
        ) : null}
      </div>
    </nav>
  );
}

type ComposerCommandId =
  | "goal"
  | "image"
  | "search"
  | "agentic"
  | "graph"
  | "research"
  | "roadmap"
  | "schedule"
  | "progress"
  | "practice"
  | "usage";

const LEARNING_NODE_CONTEXT_STORAGE_KEY = "learngraph:active-learning-node";
const TERMINAL_MESSAGE_STATUSES = [
  "completed",
  "failed",
  "cancelled",
  // A non-terminal-yet-parked state: the backend process restarted after a
  // checkpoint, the durable partial is preserved, and the task can be resumed
  // (batch 2) or retried. It is treated as terminal for stream polling so a
  // parked `interrupted` message does not trigger phantom 400ms replay, but the
  // UI renders a "已中断（后端重启）" affordance + retry button instead of error.
  "interrupted",
] as const;
const IN_FLIGHT_MESSAGE_STATUSES = ["pending", "streaming"] as const;

/** Sidebar/top-bar title for sessions created by edit/branch. */
function branchSessionTitle(sourceTitle: string | null | undefined): string {
  const base = (sourceTitle ?? "").trim() || "未命名会话";
  const stripped = base.replace(/^(分支\.)+/, "").trim() || "未命名会话";
  return `分支.${stripped}`;
}

/** Default composer draft when a learning node becomes the active context. */
function learningNodeComposerDraft(nodeLabel: string): string {
  return `什么是 ${nodeLabel}？`;
}

/**
 * True when the composer still holds an auto-generated node draft (empty, or
 * the previous “什么是 X？” fill). User-edited text is left alone on node switch.
 */
function isLearningNodeComposerDraft(
  text: string,
  previousNodeLabel?: string,
): boolean {
  const trimmed = text.trim();
  if (!trimmed) return true;
  if (previousNodeLabel && trimmed === learningNodeComposerDraft(previousNodeLabel))
    return true;
  // Also treat any “什么是 …？” single-line draft as replaceable node fill,
  // so switching nodes updates the prompt even if the previous label is unknown.
  return /^什么是\s+.+？$/.test(trimmed) && !trimmed.includes("\n");
}

async function confirmedSessionMessages(
  sessionId: string,
  messageId: string,
  statuses: readonly string[],
  minimumVersion = 0,
) {
  // Snapshot one message instead of re-downloading the entire timeline just to
  // confirm a terminal status — the old path re-materialized multi-MB agent
  // histories on every stream completion.
  const snapshot = await getMessageSnapshot(sessionId, messageId);
  if (
    !statuses.includes(snapshot.status) ||
    snapshot.version < minimumVersion
  )
    throw new Error("持久消息尚未同步到预期终态。");
  return snapshot;
}

function mergeMessageIntoCache(
  current: Message[] | undefined,
  message: Message,
): Message[] {
  if (!current?.length) return [message];
  let found = false;
  const next = current.map((item) => {
    if (item.id !== message.id) return item;
    found = true;
    return message;
  });
  return found ? next : [...next, message];
}

function optimisticAttachmentParts(files: FileRecord[]): MessagePart[] {
  const seen = new Set<string>();
  return files.flatMap((file, index) => {
    if (seen.has(file.id)) return [];
    seen.add(file.id);
    return [
      {
        id: `temp-attachment-${file.id}-${index}`,
        type: "attachment" as const,
        status: "completed" as const,
        content: file.original_name,
        data: {
          file_id: file.id,
          filename: file.original_name,
          media_type: file.mime_type,
          mime_type: file.mime_type,
          parse_status: file.parse_status,
        },
      },
    ];
  });
}

type BrowserSpeechRecognitionResult = {
  0: { transcript: string };
  isFinal: boolean;
};
type BrowserSpeechRecognitionEvent = {
  resultIndex: number;
  results: ArrayLike<BrowserSpeechRecognitionResult>;
};
type BrowserSpeechRecognitionErrorEvent = { error?: string };
type BrowserSpeechRecognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start: () => void;
  stop: () => void;
  abort: () => void;
  onresult: ((event: BrowserSpeechRecognitionEvent) => void) | null;
  onerror: ((event: BrowserSpeechRecognitionErrorEvent) => void) | null;
  onend: (() => void) | null;
};
type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition;

// 语音转写 LLM 整理:为避免打断讲话与中途资费,仅在本次听写结束
// (用户停止,或识别无法恢复)后一次性整理;进行中在输入框上方显示
// 「正在润色」标签,点击标签即跳过整理并保留原始转写。
type DictationCleanupSession = {
  prefix: string;
  cleaned: string; // 已整理文本(不可变,只追加)
  active: string; // 整理请求在途的片段
  pending: string; // 已定稿、等待整理的原始转写
  interim: string; // 浏览器临时识别结果(不发送)
  cleanupEnabled: boolean;
  degraded: boolean; // 用户跳过或连续失败后保留原始转写,停止继续消耗资费
  failures: number;
  inFlight: boolean;
  recognitionEnded: boolean;
  providerId?: string;
  modelId?: string;
  lastRendered: string | null; // 最近一次程序写入输入框的值
};

// 单次请求的片段上限(后端 schema 上限 2000,留余量);超长听写按序分段。
const DICTATION_CLEANUP_MAX_CHUNK_CHARS = 1_800;
// 只携带已整理文本的尾部作为只读语境,token 消耗随语音长度线性增长。
const DICTATION_CLEANUP_CONTEXT_CHARS = 80;
const DICTATION_CLEANUP_MAX_FAILURES = 2;

function dictationCleanupActive(session: DictationCleanupSession): boolean {
  return session.cleanupEnabled && !session.degraded;
}

/** 浏览器本地引擎(webkitSpeechRecognition)不识别标点,转写结果没有
 * 标点符号:本次听写结束时若原始文本仍无标点,用智能整理按语境补标点。
 * 开关已存在(含被管理员显式关闭)时尊重现状不自动开启;仅在从未设置
 * 过时写入默认值启用。云端 ASR 自带标点,不使用该兜底。 */
function shouldFallbackEnableDictationCleanup(
  settings: WorkspaceSetting[] | undefined,
): boolean {
  if (isChatDictationCleanupEnabled(settings)) return true;
  return !(settings ?? []).some(
    (item) => item.key === CHAT_DICTATION_CLEANUP_SETTING_KEY,
  );
}

function readLearningNodeContext(): LearningNodeContext | undefined {
  try {
    const raw = window.sessionStorage.getItem(
      LEARNING_NODE_CONTEXT_STORAGE_KEY,
    );
    const value = raw ? (JSON.parse(raw) as LearningNodeContext) : undefined;
    return value && typeof value.graphId === "string" ? value : undefined;
  } catch {
    return undefined;
  }
}

function storeLearningNodeContext(context: LearningNodeContext) {
  try {
    window.sessionStorage.setItem(
      LEARNING_NODE_CONTEXT_STORAGE_KEY,
      JSON.stringify(context),
    );
  } catch {
    // Current-page state still carries the selected learning node.
  }
}

function clearLearningNodeContext() {
  try {
    window.sessionStorage.removeItem(LEARNING_NODE_CONTEXT_STORAGE_KEY);
  } catch {
    // A new session can still clear its in-memory context.
  }
}

function appendPart(
  parts: MessagePart[],
  incoming: MessagePart,
): MessagePart[] {
  const visibleParts =
    incoming.type === "image"
      ? parts.filter((part) => part.data?.optimistic !== true)
      : parts;
  const index = visibleParts.findIndex((part) => part.id === incoming.id);
  // Prefer an explicit full content replace (part.replaced / part.completed)
  // over delta-append so agent-tool-round rewinds and timeout retries work.
  const nextContent =
    typeof incoming.content === "string"
      ? incoming.content
      : `${visibleParts[index]?.content ?? ""}${incoming.content_delta ?? ""}`;
  if (index === -1)
    return [
      ...visibleParts,
      {
        ...incoming,
        content: nextContent,
        sequence:
          typeof incoming.sequence === "number"
            ? incoming.sequence
            : visibleParts.length,
      },
    ];
  return visibleParts.map((part, partIndex) =>
    partIndex === index
      ? {
          ...part,
          ...incoming,
          data: { ...(part.data ?? {}), ...(incoming.data ?? {}) },
          content: nextContent,
          sequence:
            typeof incoming.sequence === "number"
              ? incoming.sequence
              : (part.sequence ?? partIndex),
        }
      : part,
  );
}

function isMessagePart(value: unknown): value is MessagePart {
  return (
    typeof value === "object" &&
    value !== null &&
    typeof (value as MessagePart).id === "string" &&
    typeof (value as MessagePart).type === "string" &&
    typeof (value as MessagePart).status === "string"
  );
}

function streamData(value: SessionMessageStreamData): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function streamEventType(data: Record<string, unknown>): string {
  return typeof data.type === "string"
    ? data.type
    : typeof data.event === "string"
      ? data.event
      : "";
}


const STREAM_EVENTS_PER_FRAME = 3;
// Larger chunks cut per-frame string/array clones during long agent streams.
// Typing animation still looks smooth; 28-char micro-slices were mostly RAM churn.
const STREAM_DELTA_CHARS = 180;
const DEFAULT_STREAM_RECONNECTS = 5;
/** Newest-window size for the initial chat hydrate. Older turns load on scroll-up. */
const INITIAL_MESSAGE_PAGE = 50;
const OLDER_MESSAGE_PAGE = 40;
/** Keep the active session plus a short recent window; drop the rest on switch. */
const MESSAGE_CACHE_KEEP_RECENT = 1;
const STREAM_RECONNECT_DELAYS_MS = [500, 1_000, 2_000, 4_000, 8_000] as const;

type StreamConnectionNotice = {
  phase: "reconnecting" | "failed";
  attempt: number;
  maxAttempts: number;
  detail: string;
};

function streamErrorDetail(error: unknown): string {
  if (error instanceof ApiError) {
    return `HTTP ${error.status} · ${error.code} · ${error.message}`;
  }
  if (error instanceof Error) return error.message;
  return "连接意外中断";
}

function streamEventFailure(data: Record<string, unknown>): string {
  const payload =
    typeof data.payload === "object" && data.payload !== null
      ? (data.payload as Record<string, unknown>)
      : {};
  const error =
    typeof payload.error === "object" && payload.error !== null
      ? (payload.error as Record<string, unknown>)
      : typeof data.error === "object" && data.error !== null
        ? (data.error as Record<string, unknown>)
        : {};
  const code = typeof error.code === "string" ? error.code : "provider_error";
  const message =
    typeof error.message === "string" ? error.message : "模型提供商返回失败";
  const status =
    typeof error.status === "number"
      ? `HTTP ${error.status} · `
      : typeof error.status_code === "number"
        ? `HTTP ${error.status_code} · `
        : "";
  return `${status}${code} · ${message}`;
}

/** Read a PromptInput data URL without issuing a CSP-controlled fetch. */
function dataUrlToBlob(url: string): Blob | null {
  if (!url.startsWith("data:")) return null;
  const comma = url.indexOf(",");
  if (comma < 0) return null;
  const metadata = url.slice(5, comma);
  const encoded = url.slice(comma + 1);
  const mime = metadata.split(";")[0] || "application/octet-stream";
  try {
    if (metadata.split(";").includes("base64")) {
      const binary = atob(encoded);
      const bytes = Uint8Array.from(binary, (char) => char.charCodeAt(0));
      return new Blob([bytes], { type: mime });
    }
    return new Blob([decodeURIComponent(encoded)], { type: mime });
  } catch {
    return null;
  }
}

async function attachmentUrlToBlob(url: string): Promise<Blob> {
  const local = dataUrlToBlob(url);
  if (local) return local;
  const response = await fetch(url);
  if (!response.ok) throw new Error(`附件读取失败（HTTP ${response.status}）`);
  return response.blob();
}

async function waitForStreamReconnect(
  reconnectAttempt: number,
  signal: AbortSignal,
): Promise<void> {
  if (signal.aborted) throw signal.reason;
  const delay =
    STREAM_RECONNECT_DELAYS_MS[
      Math.min(reconnectAttempt, STREAM_RECONNECT_DELAYS_MS.length - 1)
    ];
  await new Promise<void>((resolve, reject) => {
    let timer: number | undefined;
    const cleanup = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      window.removeEventListener("online", finish);
      signal.removeEventListener("abort", abort);
    };
    const finish = () => {
      cleanup();
      resolve();
    };
    const abort = () => {
      cleanup();
      reject(signal.reason);
    };
    signal.addEventListener("abort", abort, { once: true });
    if (!window.navigator.onLine) {
      window.addEventListener("online", finish, { once: true });
      return;
    }
    timer = window.setTimeout(finish, delay);
  });
}

function StreamConnectionFeedback({
  notice,
}: {
  notice: StreamConnectionNotice;
}) {
  const reconnecting = notice.phase === "reconnecting";
  return (
    <div
      aria-live={reconnecting ? "polite" : "assertive"}
      className={`chat-stream-connection chat-stream-connection--${notice.phase}`}
      role={reconnecting ? "status" : "alert"}
    >
      <LoaderCircle
        aria-hidden="true"
        className={reconnecting ? "animate-spin" : undefined}
      />
      <div>
        <strong>
          {reconnecting
            ? notice.attempt > 0
              ? `连接中断，正在重连（${notice.attempt}/${notice.maxAttempts}）`
              : "正在续接生成任务"
            : "连接或模型服务异常"}
        </strong>
        <span>{notice.detail}</span>
      </div>
    </div>
  );
}

function expandStreamUpdate(
  data: Record<string, unknown>,
  options: { animate?: boolean } = {},
) {
  const part = isMessagePart(data.part) ? data.part : undefined;
  const eventType = streamEventType(data);
  const delta = part?.content_delta;
  // Skip character micro-slicing for off-screen / background streams — those
  // intermediate clones only inflate RAM and never paint.
  if (options.animate === false) return [data];
  if (
    !part ||
    typeof delta !== "string" ||
    !["part.delta", "message.part.delta"].includes(eventType)
  )
    return [data];
  const characters = Array.from(delta);
  if (characters.length <= STREAM_DELTA_CHARS) return [data];
  const updates: Record<string, unknown>[] = [];
  for (let index = 0; index < characters.length; index += STREAM_DELTA_CHARS) {
    updates.push({
      ...data,
      part: {
        ...part,
        content: undefined,
        content_delta: characters
          .slice(index, index + STREAM_DELTA_CHARS)
          .join(""),
      },
    });
  }
  return updates;
}

function applyStreamUpdates(
  message: Message,
  updates: Record<string, unknown>[],
): Message {
  return updates.reduce<Message>((current, data) => {
    const eventType = streamEventType(data);
    if (eventType === "message.completed")
      return {
        ...current,
        status: "completed",
        provider_trace: (data.provider_trace ?? {}) as Record<string, unknown>,
      };
    if (eventType === "message.failed" || eventType === "message.cancelled")
      return {
        ...current,
        status: eventType === "message.cancelled" ? "cancelled" : "failed",
        parts: current.parts.map((part) =>
          part.data?.optimistic === true
            ? { ...part, status: "failed" as const }
            : part,
        ),
      };
    if (isMessagePart(data.part))
      return {
        ...current,
        parts: appendPart(current.parts, data.part),
      };
    return current;
  }, message);
}

function createAnimationFrameQueue<T>(onBatch: (batch: T[]) => void) {
  let pending: T[] = [];
  let frameId: number | null = null;
  let scheduledWithAnimationFrame = false;
  let drainResolvers: Array<() => void> = [];

  const resolveDrains = () => {
    if (pending.length || frameId !== null) return;
    const resolvers = drainResolvers;
    drainResolvers = [];
    resolvers.forEach((resolve) => resolve());
  };
  const schedule = () => {
    if (frameId !== null) return;
    const run = () => {
      frameId = null;
      const batchSize =
        pending.length > 90
          ? STREAM_EVENTS_PER_FRAME * 4
          : pending.length > 30
            ? STREAM_EVENTS_PER_FRAME * 2
            : STREAM_EVENTS_PER_FRAME;
      const batch = pending.splice(0, batchSize);
      if (batch.length) onBatch(batch);
      if (pending.length) schedule();
      else resolveDrains();
    };
    scheduledWithAnimationFrame =
      document.visibilityState === "visible" &&
      typeof window.requestAnimationFrame === "function";
    frameId = scheduledWithAnimationFrame
      ? window.requestAnimationFrame(run)
      : window.setTimeout(run, 16);
  };

  return {
    push(item: T) {
      pending.push(item);
      schedule();
    },
    drain() {
      if (!pending.length && frameId === null) return Promise.resolve();
      return new Promise<void>((resolve) => drainResolvers.push(resolve));
    },
    clear() {
      pending = [];
      if (frameId !== null) {
        if (scheduledWithAnimationFrame)
          window.cancelAnimationFrame(frameId);
        else window.clearTimeout(frameId);
        frameId = null;
      }
      resolveDrains();
    },
  };
}

function readTextSelection(): TextSelectionMenu | null {
  const selection = window.getSelection();
  if (!selection || selection.rangeCount === 0 || selection.isCollapsed)
    return null;
  const selectedText = selection.toString().trim().slice(0, 500);
  if (selectedText.length < 3) return null;
  const range = selection.getRangeAt(0);
  const startElement =
    range.startContainer instanceof HTMLElement
      ? range.startContainer
      : range.startContainer.parentElement;
  const endElement =
    range.endContainer instanceof HTMLElement
      ? range.endContainer
      : range.endContainer.parentElement;
  const selectableStart = startElement?.closest<HTMLElement>(
    "[data-message-selectable-text]",
  );
  const selectableEnd = endElement?.closest<HTMLElement>(
    "[data-message-selectable-text]",
  );
  // Cross-message selections stay unsupported; multi-block text inside one
  // selectable root (tables, lists, multiple markdown paragraphs) is fine.
  if (!selectableStart || selectableStart !== selectableEnd) return null;
  const messageElement = selectableStart.closest<HTMLElement>("[data-message-id]");
  const sourceMessageId = messageElement?.dataset.messageId;
  if (
    !messageElement ||
    !sourceMessageId ||
    sourceMessageId.startsWith("temp") ||
    sourceMessageId === "welcome-local" ||
    messageElement.dataset.selectionDisabled === "true"
  )
    return null;
  const contentElement = selectableStart;
  let prefix = "";
  let suffix = "";
  let occurrenceIndex = 0;
  try {
    const before = range.cloneRange();
    before.selectNodeContents(contentElement);
    before.setEnd(range.startContainer, range.startOffset);
    prefix = before.toString().slice(-500);
    const precedingText = before.toString();
    // Count prior exact occurrences of the *raw* browser selection; multi-line
    // selections rarely collide exactly, so this is only a soft preference.
    let precedingOffset = 0;
    while (precedingOffset <= precedingText.length - selectedText.length) {
      const occurrence = precedingText.indexOf(selectedText, precedingOffset);
      if (occurrence < 0) break;
      occurrenceIndex += 1;
      precedingOffset = occurrence + Math.max(1, selectedText.length);
    }
    const after = range.cloneRange();
    after.selectNodeContents(contentElement);
    after.setStart(range.endContainer, range.endOffset);
    suffix = after.toString().slice(0, 500);
  } catch {
    const allText = contentElement.textContent ?? "";
    const selectedIndex = allText.indexOf(selectedText);
    if (selectedIndex >= 0) {
      prefix = allText.slice(Math.max(0, selectedIndex - 500), selectedIndex);
      suffix = allText.slice(
        selectedIndex + selectedText.length,
        selectedIndex + selectedText.length + 500,
      );
    }
  }
  const point = selectionToolbarPoint(range);
  if (!point) return null;
  return {
    source_message_id: sourceMessageId,
    selected_text: selectedText,
    prefix,
    suffix,
    occurrenceIndex,
    contentMatched: false,
    left: point.left,
    top: point.top,
  };
}

function selectionRequestContext(
  selection: TextSelectionMenu,
): MessageSelectionContext | null {
  if (!selection.contentMatched) return null;
  return {
    source_message_id: selection.source_message_id,
    selected_text: selection.selected_text,
    prefix: selection.prefix,
    suffix: selection.suffix,
  };
}

type MessageVersionNavigation = {
  currentIndex: number;
  total: number;
  onNext: () => void;
  onPrevious: () => void;
};

function MessageVersionNavigator({
  navigation,
}: {
  navigation?: MessageVersionNavigation;
}) {
  if (!navigation || navigation.total <= 1) return null;
  return (
    <MessageActions className="min-h-7 justify-end text-xs text-muted-foreground">
      <MessageAction
        disabled={navigation.currentIndex === 0}
        label="上一版本"
        onClick={navigation.onPrevious}
        tooltip="上一版本"
      >
        <ChevronLeft className="size-3.5" />
      </MessageAction>
      <span aria-live="polite" className="min-w-8 text-center tabular-nums">
        {navigation.currentIndex + 1}/{navigation.total}
      </span>
      <MessageAction
        disabled={navigation.currentIndex === navigation.total - 1}
        label="下一版本"
        onClick={navigation.onNext}
        tooltip="下一版本"
      >
        <ChevronRight className="size-3.5" />
      </MessageAction>
    </MessageActions>
  );
}

function UserMessage({
  message,
  editing,
  editValue,
  disabled,
  versionNavigation,
  selectionMarks = [],
  onOpenSelectionExplanation,
  onCancelEdit,
  onEditValueChange,
  onSaveEdit,
  onStartEdit,
}: {
  message: Message;
  editing: boolean;
  editValue: string;
  disabled: boolean;
  versionNavigation?: MessageVersionNavigation;
  selectionMarks?: SelectionExplanationRecord[];
  onOpenSelectionExplanation?: (recordId: string) => void;
  onCancelEdit: () => void;
  onEditValueChange: (value: string) => void;
  onSaveEdit: () => void;
  onStartEdit: () => void;
}) {
  const copyMessage = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      toast.success("已复制消息");
    } catch {
      toast.error("无法复制消息");
    }
  };

  const markedSegments = useMemo(() => {
    const marks = selectionMarks
      .filter((item) => item.sourceMessageId === message.id)
      .map((item) => ({
        id: item.id,
        selectedText: item.selectedText,
        prefix: item.prefix,
        suffix: item.suffix,
      }));
    return splitTextWithSelectionMarks(message.content, marks);
  }, [message.content, message.id, selectionMarks]);

  return (
    <AiMessage
      className="relative"
      data-message-id={message.id}
      from="user"
      id={`conversation-jump-${message.id}`}
    >
      <MessageContent
        className={editing ? "w-full sm:max-w-xl" : undefined}
        data-message-content
      >
        {editing ? (
          <div className="space-y-2">
            <Textarea
              aria-label="编辑消息"
              autoFocus
              className="min-h-24 bg-background/80 text-sm"
              disabled={disabled}
              onChange={(event) => onEditValueChange(event.currentTarget.value)}
              onKeyDown={(event) => {
                if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                  event.preventDefault();
                  onSaveEdit();
                }
              }}
              value={editValue}
            />
            <div className="flex justify-end gap-2">
              <Button
                disabled={disabled}
                onClick={onCancelEdit}
                size="xs"
                type="button"
                variant="ghost"
              >
                取消
              </Button>
              <Button
                disabled={disabled || !editValue.trim()}
                onClick={onSaveEdit}
                size="xs"
                type="button"
              >
                <Check className="size-3.5" />
                保存并重新回答
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            {message.parts
              .filter((part) =>
                ["attachment", "document_selection", "selection_quote"].includes(
                  part.type,
                ),
              )
              .map((part) => <ChatStreamPartRenderer key={part.id} part={part} />)}
            <p
              className="whitespace-pre-wrap leading-6"
              data-message-selectable-text
            >
              {markedSegments.map((segment, index) =>
                segment.type === "mark" ? (
                  <button
                    aria-label={`打开划词解释：${segment.value.slice(0, 40)}`}
                    className="selection-explain-mark"
                    data-selection-explain-id={segment.id}
                    key={`${segment.id}-${index}`}
                    onClick={(event) => {
                      event.preventDefault();
                      event.stopPropagation();
                      onOpenSelectionExplanation?.(segment.id);
                    }}
                    title="打开历史划词解释"
                    type="button"
                  >
                    {segment.value}
                  </button>
                ) : (
                  <span key={`text-${index}`}>{segment.value}</span>
                ),
              )}
            </p>
          </div>
        )}
      </MessageContent>
      {!editing ? (
        <MessageActions className="chat-message-actions--user min-h-8 justify-end opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
          <MessageAction
            label="复制消息"
            onClick={() => void copyMessage()}
            tooltip="复制消息"
          >
            <Copy className="size-3.5" />
          </MessageAction>
          <MessageAction
            disabled={disabled}
            label="编辑消息"
            onClick={onStartEdit}
            tooltip="编辑消息"
          >
            <Pencil className="size-3.5" />
          </MessageAction>
        </MessageActions>
      ) : null}
      <MessageVersionNavigator navigation={versionNavigation} />
    </AiMessage>
  );
}

/**
 * Defer heavy Streamdown/Shiki trees until the row nears the viewport, and
 * release them again once the user scrolls far away. Once-visible-always-mounted
 * was the previous behaviour and kept every revealed agent answer resident for
 * the rest of the session.
 */
function LazyMessageMount({
  children,
  eager = false,
  minHeight = 96,
}: {
  children: ReactNode;
  eager?: boolean;
  minHeight?: number;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [visible, setVisible] = useState(eager);
  const measuredHeightRef = useRef(minHeight);

  useEffect(() => {
    if (eager) {
      setVisible(true);
      return;
    }
    const node = hostRef.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        const entry = entries[0];
        if (!entry) return;
        if (entry.isIntersecting) {
          measuredHeightRef.current = Math.max(
            minHeight,
            Math.round(node.getBoundingClientRect().height) || minHeight,
          );
          setVisible(true);
        } else {
          // Keep a stable placeholder height so stick-to-bottom / scroll
          // position does not jump when far-above rows unmount.
          measuredHeightRef.current = Math.max(
            minHeight,
            Math.round(node.getBoundingClientRect().height) ||
              measuredHeightRef.current,
          );
          setVisible(false);
        }
      },
      {
        // Prefetch a screenful above/below; unmount well outside that band.
        rootMargin: "900px 0px",
        threshold: 0,
      },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [eager, minHeight]);

  return (
    <div
      ref={hostRef}
      className="chat-message-mount"
      style={
        visible
          ? undefined
          : {
              minHeight: measuredHeightRef.current,
              contentVisibility: "auto",
              containIntrinsicSize: `auto ${measuredHeightRef.current}px`,
            }
      }
    >
      {visible ? children : null}
    </div>
  );
}

function AssistantMessageInner({
  message,
  sessionId,
  workspaceId,
  onRetry,
  onBranch,
  onComponentAction,
  selectionMarks = [],
  onOpenSelectionExplanation,
  retryDisabled = false,
  retryDisabledReason,
  branchDisabled = false,
  branchDisabledReason,
  componentsInteractive = true,
}: {
  message: Message;
  sessionId: string;
  workspaceId: string;
  onRetry: () => void;
  onBranch: () => void;
  onComponentAction: (action: TrustedComponentAction) => void | Promise<void>;
  selectionMarks?: SelectionExplanationRecord[];
  onOpenSelectionExplanation?: (recordId: string) => void;
  retryDisabled?: boolean;
  retryDisabledReason?: string;
  branchDisabled?: boolean;
  branchDisabledReason?: string;
  /** False while the assistant turn is still streaming so review cards stay locked. */
  componentsInteractive?: boolean;
}) {
  const messageContentRef = useRef<HTMLDivElement | null>(null);
  const persisted =
    !message.id.startsWith("temp") && message.id !== "welcome-local";
  // Lazily fetch message versions only after the user shows interest in the
  // message's action area (hover/focus), instead of firing a query for every
  // persisted assistant message on mount. Most messages have a single version
  // (the version row never renders), so eager fetching was pure overhead: N
  // messages ⇒ N subscriptions + N effects. The snapshot query below was
  // already gated on `selectedVersionId`; this gates its prerequisite too.
  const [versionsRequested, setVersionsRequested] = useState(false);
  const versions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "message-versions", sessionId, message.id),
    queryFn: () => listMessageVersions(sessionId, message.id),
    enabled: persisted && versionsRequested,
  });
  const [selectedVersionId, setSelectedVersionId] = useState<
    string | undefined
  >();
  useEffect(() => {
    setSelectedVersionId(undefined);
  }, [message.id, message.version]);
  const snapshot = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "message-snapshot", sessionId, message.id, selectedVersionId),
    queryFn: () => getMessageSnapshot(sessionId, message.id, selectedVersionId),
    enabled: persisted && Boolean(selectedVersionId),
  });
  const shown = snapshot.data ?? message;
  const imageInputTrace =
    shown.provider_trace?.image_input &&
    typeof shown.provider_trace.image_input === "object" &&
    !Array.isArray(shown.provider_trace.image_input)
      ? shown.provider_trace.image_input as Record<string, unknown>
      : null;
  const visionModelId =
    imageInputTrace?.image_input_mode === "external_vision" &&
    typeof imageInputTrace.model_id === "string" &&
    imageInputTrace.model_id.trim()
      ? imageInputTrace.model_id.trim()
      : null;
  const orderedParts = orderedMessageParts(shown.parts);
  // Thinking chain (reasoning + tools) is always rendered above the final body.
  const displaySegments = groupPartsForDisplay(shown.parts);
  // Budget approval needs a clickable card outside the collapsed thinking fold.
  const deepResearchApprovalParts = orderedParts.filter(isDeepResearchApprovalPart);
  const fullText = shown.parts
    .filter((part) => part.type === "text" || part.type === "acknowledgement")
    .map((part) => part.content ?? "")
    .filter((content) => content && content.trim() !== "正在思考")
    .join("\n");
  // Empty stream before any chain/answer part arrives.
  const isThinkingPlaceholder = shouldShowThinkingPlaceholder(
    shown.status,
    orderedParts,
  );
  const renderPart = (part: MessagePart) => (
    <ChatStreamPartRenderer
      interactive={componentsInteractive}
      key={part.id}
      onAction={onComponentAction}
      part={part}
      siblingParts={orderedParts}
      streaming={
        shown.status === "streaming" &&
        (part.status === "streaming" || part.status === "pending")
      }
    />
  );
  const renderAnswerParts = (parts: MessagePart[]) =>
    groupQuestionParts(parts).map((group) => {
      if (group.kind === "question_set") {
        return (
          <QuestionSetPager
            key={`question-set-${group.parts.map((part) => part.id).join("-")}`}
            onSubmit={(submission) =>
              void onComponentAction?.({
                componentId: group.questions[0]?.componentId ?? group.parts[0].id,
                componentType:
                  group.questions.length > 1
                    ? "question_set"
                    : group.questions[0]?.componentType ?? "question_set",
                event: "submit",
                payload: {
                  answer: submission.summaryText,
                  summaryText: submission.summaryText,
                  results: submission.results,
                  labels: submission.results.flatMap((item) => item.labels),
                  graded_count: submission.gradedCount,
                  correct_count: submission.correctCount,
                },
              })
            }
            questions={group.questions}
          />
        );
      }
      return renderPart(group.part);
    });

  useEffect(() => {
    if (selectedVersionId || !messageContentRef.current) return;
    const marks = selectionMarks
      .filter((item) => item.sourceMessageId === message.id)
      .map((item) => ({
        id: item.id,
        selectedText: item.selectedText,
        prefix: item.prefix,
        suffix: item.suffix,
      }));
    if (!marks.length) return;
    let dispose: (() => void) | undefined;
    // Streamdown can replace text nodes after paint; decorate on the next frame
    // and once more shortly after so marks survive late markdown hydration.
    const apply = () => {
      dispose?.();
      if (!messageContentRef.current) return;
      dispose = decorateSelectionExplanationMarks(
        messageContentRef.current,
        marks,
        (recordId) => onOpenSelectionExplanation?.(recordId),
      );
    };
    const frame = window.requestAnimationFrame(apply);
    const timer = window.setTimeout(apply, 120);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(timer);
      dispose?.();
    };
  }, [
    message.id,
    message.content,
    message.parts,
    onOpenSelectionExplanation,
    selectedVersionId,
    selectionMarks,
    shown.status,
  ]);

  return (
    <AiMessage
      className="max-w-none"
      data-message-id={message.id}
      data-selection-disabled={selectedVersionId ? "true" : undefined}
      from="assistant"
      // Lazy versions trigger — see `versionsRequested` above.
      onMouseEnter={() => setVersionsRequested(true)}
      onFocus={() => setVersionsRequested(true)}
    >
      {versions.data && versions.data.length > 1 ? (
        <div className="mb-2 flex items-center gap-1 text-[10px] text-muted-foreground">
          版本：
          {versions.data.map((version) => (
            <Button
              key={version.id}
              onClick={() => setSelectedVersionId(version.id)}
              size="xs"
              variant={
                selectedVersionId === version.id ||
                (!selectedVersionId && version.version === message.version)
                  ? "secondary"
                  : "ghost"
              }
            >
              v{version.version}
            </Button>
          ))}
        </div>
      ) : null}
      <MessageContent className="w-full gap-1" data-message-content>
        {visionModelId ? (
          <p className="text-xs text-muted-foreground">
            图像由 {visionModelId} 模型提供
          </p>
        ) : null}
        <div ref={messageContentRef} className="contents">
        {isThinkingPlaceholder ? (
          <div className="message-thinking" role="status" aria-live="polite">
            <span className="message-thinking__dot" />
            <span>正在思考</span>
          </div>
        ) : null}
        {displaySegments.map((segment, index) =>
          segment.kind === "chain" ? (
            <div className="space-y-2" key={`chain-wrap-${message.id}-${index}`}>
              <ThinkingChain
                chainParts={segment.parts}
                completedDurationSec={thinkingDurationSeconds(
                  shown.provider_trace,
                )}
                messageStatus={shown.status}
                startedAt={
                  typeof shown.provider_trace.generation_started_at === "string"
                    ? shown.provider_trace.generation_started_at
                    : shown.created_at
                }
              >
                {segment.parts.map(renderPart)}
              </ThinkingChain>
              {index === 0 && deepResearchApprovalParts.length ? (
                <div className="message-deep-research-approvals space-y-3">
                  {deepResearchApprovalParts.map((part) => (
                    <DeepResearchApprovalFromPart
                      key={`approval-${part.id}`}
                      part={part}
                    />
                  ))}
                </div>
              ) : null}
            </div>
          ) : (
            <div
              className="message-answer-segment"
              key={`parts-${message.id}-${index}`}
            >
              {renderAnswerParts(segment.parts)}
            </div>
          ),
        )}
        {!displaySegments.some((segment) => segment.kind === "chain") &&
        deepResearchApprovalParts.length ? (
          <div className="message-deep-research-approvals space-y-3 pt-1">
            {deepResearchApprovalParts.map((part) => (
              <DeepResearchApprovalFromPart key={`approval-${part.id}`} part={part} />
            ))}
          </div>
        ) : null}
        </div>
      </MessageContent>
      <MessageActions className="opacity-60 transition-opacity focus-within:opacity-100 hover:opacity-100">
        <MessageAction
          label="复制全文"
          onClick={() =>
           void navigator.clipboard
              .writeText(fullText)
              .then(() => toast.success("已复制回答"))
              .catch(() => toast.error("无法复制回答"))
          }
          tooltip="复制全文"
        >
          <Copy className="size-3.5" />
        </MessageAction>
        <MessageAction
          disabled={retryDisabled}
          label="重试并保留版本"
          onClick={onRetry}
          tooltip={retryDisabledReason ?? "重试并保留版本"}
        >
          <RefreshCcw className="size-3.5" />
        </MessageAction>
        <MessageAction
          disabled={branchDisabled}
          label="从此创建分支"
          onClick={onBranch}
          tooltip={branchDisabledReason ?? "从此创建分支"}
        >
          <GitBranch className="size-3.5" />
        </MessageAction>
        {sessionId ? (
          <Badge className="ml-1 font-mono text-[10px]" variant="secondary">
            {message.id} · v{shown.version}
          </Badge>
        ) : null}
      </MessageActions>
    </AiMessage>
  );
}

/**
 * Memoized AssistantMessage.
 *
 * Why a custom comparator: the call site passes `onRetry` / `onBranch` as
 * inline closures (intentionally — they capture the latest volatile state such
 * as selected model/response mode at click time, avoiding stale closures).
 * Those closures change identity every render, which would defeat a default
 * shallow `memo`. The comparator deliberately ignores the callback identities
 * and compares only the props that drive the rendered output: the `message`
 * object reference, `sessionId`, and the disabled/state-reason flags. When a
 * streaming frame mutates only the in-flight message, every other assistant
 * message keeps a stable `message` reference and (during streaming) stable
 * disabled flags, so they short-circuit and skip re-parsing Streamdown
 * markdown — the dominant per-frame cost in long multi-agent threads.
 *
 * Inline callbacks are fine here: a skipped render never calls them, and a
 * rendered render always re-creates them with the freshest captured state.
 */
const areEqualAssistantMessage = (
  prev: AssistantMessageMemoProps,
  next: AssistantMessageMemoProps,
): boolean =>
  prev.message === next.message &&
  prev.sessionId === next.sessionId &&
  prev.workspaceId === next.workspaceId &&
  prev.retryDisabled === next.retryDisabled &&
  prev.branchDisabled === next.branchDisabled &&
  prev.retryDisabledReason === next.retryDisabledReason &&
  prev.branchDisabledReason === next.branchDisabledReason &&
  prev.componentsInteractive === next.componentsInteractive &&
  prev.selectionMarks === next.selectionMarks &&
  prev.onOpenSelectionExplanation === next.onOpenSelectionExplanation;

type AssistantMessageMemoProps = Parameters<typeof AssistantMessageInner>[0];

const AssistantMessage = memo(
  AssistantMessageInner,
  areEqualAssistantMessage,
);

function EmptySessionPrompts({
  prompts,
  onSelect,
  disabled,
  isError,
  isPending,
  isUnavailable,
  onConfigureProvider,
  onRetry,
}: {
  prompts: SuggestedPrompt[];
  onSelect: (content: string) => void;
  disabled: boolean;
  isError: boolean;
  isPending: boolean;
  isUnavailable: boolean;
  onConfigureProvider: () => void;
  onRetry: () => void;
}) {
  return (
    <ConversationEmptyState className="chat-empty-state">
      <div className="chat-empty-state__content">
        <p className="chat-empty-state__eyebrow">新会话</p>
        <h2>从一个问题开始</h2>
        <SuggestedPromptContent
          disabled={disabled}
          isError={isError}
          isPending={isPending}
          isUnavailable={isUnavailable}
          onConfigureProvider={onConfigureProvider}
          onRetry={onRetry}
          onSelect={onSelect}
          prompts={prompts}
        />
      </div>
    </ConversationEmptyState>
  );
}

/** 空会话默认问题：优先来自记忆，否则使用中文通用开场提示。 */
function buildMemoryBackedEmptyPrompts(
  memories: Array<{ id: string; title: string; content: string | null }>,
): SuggestedPrompt[] {
  const active = memories.filter((item) => (item.title || item.content)?.trim());
  if (!active.length) {
    return [
      {
        id: "empty-zh-1",
        content: "我现在最应该先学什么？",
      },
      {
        id: "empty-zh-2",
        content: "请根据我当前的学习目标，帮我梳理可学的主题。",
      },
    ];
  }
  return active.slice(0, 2).map((item, index) => {
    const title = (item.title || "").trim();
    const snippet = (item.content || "").trim().replace(/\s+/g, " ").slice(0, 48);
    const topic = title || snippet || "这段记忆";
    return {
      id: `memory-prompt-${item.id || index}`,
      content:
        index === 0
          ? `结合我记住的「${topic}」，接下来该怎么学？`
          : `请基于「${topic}」帮我回顾关键点，并给出下一步练习。`,
    };
  });
}

function SuggestedPromptContent({
  prompts,
  onSelect,
  disabled,
  isError,
  isPending,
  isUnavailable,
  onConfigureProvider,
  onRetry,
}: {
  prompts: SuggestedPrompt[];
  onSelect: (content: string) => void;
  disabled: boolean;
  isError: boolean;
  isPending: boolean;
  isUnavailable: boolean;
  onConfigureProvider: () => void;
  onRetry: () => void;
}) {
  // 即便模型/Provider 不可用，也展示记忆或中文默认提示，避免空白或英文占位。
  if (!isPending && !isError && !isUnavailable && !prompts.length) return null;

  return (
    <div
      aria-busy={isPending}
      aria-live="polite"
      className="chat-suggestions"
    >
      {isPending && !prompts.length ? (
        <div className="chat-suggestions__status">
          <LoaderCircle
            aria-hidden="true"
            className="chat-suggestions__spinner size-3.5 shrink-0"
          />
          <span>正在生成问题提示…</span>
        </div>
      ) : isUnavailable && !prompts.length ? (
        <div className="chat-suggestions__status chat-suggestions__status--unavailable">
          <span>需配置真实模型 Provider</span>
          <Button onClick={onConfigureProvider} size="xs" variant="outline">
            前往设置
          </Button>
        </div>
      ) : isError && !prompts.length ? (
        <div className="chat-suggestions__status chat-suggestions__status--error">
          <span>问题提示生成失败</span>
          <Button onClick={onRetry} size="xs" variant="ghost">
            <RefreshCcw aria-hidden="true" className="size-3" />
            重试
          </Button>
        </div>
      ) : (
        <div aria-label="推荐问题" className="chat-empty-state__prompts">
          {prompts.map((prompt) => (
            <button
              className="chat-empty-state__prompt"
              disabled={disabled}
              key={prompt.id}
              onClick={() => onSelect(prompt.content)}
              type="button"
            >
              {prompt.content}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function FollowUpPrompts({
  prompts,
  onSelect,
  isError,
  isPending,
  isUnavailable,
  onConfigureProvider,
  onRetry,
}: {
  prompts: SuggestedPrompt[];
  onSelect: (content: string) => void;
  isError: boolean;
  isPending: boolean;
  isUnavailable: boolean;
  onConfigureProvider: () => void;
  onRetry: () => void;
}) {
  if (!prompts.length && !isError && !isPending && !isUnavailable) return null;

  return (
    <section aria-label="对话问题提示" className="chat-follow-up-prompts">
      <div className="chat-follow-up-prompts__label">
        <Sparkles aria-hidden="true" className="size-3.5" />
        <span>接下来可以问</span>
      </div>
      <SuggestedPromptContent
        disabled={isPending}
        isError={isError}
        isPending={isPending}
        isUnavailable={isUnavailable}
        onConfigureProvider={onConfigureProvider}
        onRetry={onRetry}
        onSelect={onSelect}
        prompts={prompts}
      />
    </section>
  );
}

function ConversationContextBar({
  goalBound,
  graphTitle,
  learningNode,
  onClearLearningNode,
}: {
  goalBound: boolean;
  graphTitle?: string;
  learningNode?: LearningNodeContext;
  onClearLearningNode: () => void;
}) {
  if (!goalBound && !graphTitle && !learningNode) return null;

  const itemCount =
    (goalBound ? 1 : 0) + (graphTitle ? 1 : 0) + (learningNode ? 1 : 0);
  const primaryLabel = learningNode
    ? (learningNode.nodeLabel ?? "学习节点")
    : graphTitle
      ? graphTitle
      : "已绑定目标";
  const triggerTitle = learningNode
    ? `节点 · ${learningNode.nodeLabel ?? "已选择学习节点"}`
    : graphTitle
      ? `图谱 · ${graphTitle}`
      : "已绑定目标";

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={`本轮对话上下文，共 ${itemCount} 项`}
          className="chat-context-menu-trigger"
          size="sm"
          title={triggerTitle}
          variant="ghost"
        >
          <span className="chat-context-menu-trigger__label">上下文</span>
          <span className="chat-context-menu-trigger__value" title={primaryLabel}>
            {primaryLabel}
          </span>
          {itemCount > 1 ? (
            <span className="chat-context-menu-trigger__count">+{itemCount - 1}</span>
          ) : null}
          <ChevronDown aria-hidden="true" className="chat-context-menu-trigger__chevron" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align="end"
        className="chat-context-menu w-64"
        sideOffset={6}
      >
        <DropdownMenuLabel>本轮对话上下文</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {goalBound ? (
          <div className="chat-context-menu__row">
            <Target className="size-3.5" />
            <span className="min-w-0 flex-1 truncate">已绑定目标</span>
          </div>
        ) : null}
        {graphTitle ? (
          <div className="chat-context-menu__row" title={graphTitle}>
            <Network className="size-3.5" />
            <span className="min-w-0 flex-1 truncate">图谱 · {graphTitle}</span>
          </div>
        ) : null}
        {learningNode ? (
          <div className="chat-context-menu__row chat-context-menu__row--node">
            <GitBranch className="size-3.5" />
            <span
              className="min-w-0 flex-1 truncate"
              title={learningNode.nodeLabel ?? "已选择学习节点"}
            >
              节点 · {learningNode.nodeLabel ?? "已选择学习节点"}
            </span>
            <button
              aria-label="移除当前学习节点上下文"
              className="chat-context-menu__clear"
              onClick={onClearLearningNode}
              title="本轮后续消息不再绑定此节点"
              type="button"
            >
              <X aria-hidden="true" className="size-3" />
            </button>
          </div>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ConversationQuickActions({
  agentActive,
  agentDisabled,
  attachDisabled,
  deepResearchDisabled,
  goalActive,
  goalDisabled,
  graphActive,
  graphDisabled,
  imageActive,
  imageDisabled,
  onAttach,
  onDeepResearch,
  onGoal,
  onGraph,
  onImage,
  onPractice,
  onSearch,
  onAgent,
  practiceDisabled,
  searchActive,
  searchDisabled,
}: {
  agentActive: boolean;
  agentDisabled: boolean;
  attachDisabled: boolean;
  deepResearchDisabled: boolean;
  goalActive: boolean;
  goalDisabled: boolean;
  graphActive: boolean;
  graphDisabled: boolean;
  imageActive: boolean;
  imageDisabled: boolean;
  onAttach: () => void;
  onDeepResearch: () => void;
  onGoal: () => void;
  onGraph: () => void;
  onImage: () => void;
  onPractice: () => void;
  onSearch: () => void;
  onAgent: () => void;
  practiceDisabled: boolean;
  searchActive: boolean;
  searchDisabled: boolean;
}) {
  return (
    <section aria-label="对话工作台功能" className="chat-workbench-toolbar">
      <span className="chat-workbench-toolbar__label">本轮能力</span>
      <div className="chat-workbench-toolbar__actions">
        <button
          aria-label="添加资料到本轮对话"
          className="chat-workbench-toolbar__action"
          disabled={attachDisabled}
          onClick={onAttach}
          title="添加文件或图片，发送时按文件权限和解析状态处理"
          type="button"
        >
          <FilePlus2 aria-hidden="true" />
          资料
        </button>
        <button
          aria-pressed={goalActive}
          className="chat-workbench-toolbar__action"
          disabled={goalDisabled}
          onClick={onGoal}
          title={goalActive ? "退出目标设定" : "在当前对话中设定学习目标"}
          type="button"
        >
          <Target aria-hidden="true" />
          {goalActive ? "目标中" : "目标"}
        </button>
        <button
          aria-pressed={searchActive}
          className="chat-workbench-toolbar__action"
          disabled={searchDisabled}
          onClick={onSearch}
          title={
            searchActive
              ? "关闭本轮联网搜索"
              : searchDisabled
                ? "请先启用 SearchProvider，或确认模型托管联网能力"
                : "下一条消息使用已授权的联网搜索"
          }
          type="button"
        >
          <Search aria-hidden="true" />
          {searchActive ? "联网中" : "联网"}
        </button>
        <button
          aria-pressed={agentActive}
          className="chat-workbench-toolbar__action"
          disabled={agentDisabled}
          onClick={onAgent}
          title={agentActive ? "切回普通对话模式" : "允许本轮调用已授权工具"}
          type="button"
        >
          <Bot aria-hidden="true" />
          智能体
        </button>
        <button
          className="chat-workbench-toolbar__action"
          disabled={deepResearchDisabled}
          onClick={onDeepResearch}
          title={
            deepResearchDisabled
              ? "请先启用 Deep Research Provider，并使用支持工具调用的模型"
              : "快捷启动深度研究：启用智能体并预填 start_deep_research 任务"
          }
          type="button"
        >
          <FileSearch aria-hidden="true" />
          深度研究
        </button>
        <button
          aria-pressed={graphActive}
          className="chat-workbench-toolbar__action"
          disabled={graphDisabled}
          onClick={onGraph}
          title={
            graphActive
              ? "取消图谱变更：下一条消息将不再生成增量提案"
              : "图谱变更：围绕当前节点细化/增补子节点（去重后生成需审核的变更提案）"
          }
          type="button"
        >
          <Network aria-hidden="true" />
          {graphActive ? "图谱变更" : "图谱"}
        </button>
        <button
          aria-pressed={imageActive}
          className="chat-workbench-toolbar__action"
          disabled={imageDisabled}
          onClick={onImage}
          title={imageActive ? "退出绘图模式" : "使用已配置的图片生成模型"}
          type="button"
        >
          <ImageIcon aria-hidden="true" />
          {imageActive ? "绘图中" : "绘图"}
        </button>
        <button
          className="chat-workbench-toolbar__action"
          disabled={practiceDisabled}
          onClick={onPractice}
          title="基于当前目标、节点和资料，在对话框中预填练习请求"
          type="button"
        >
          <Sparkles aria-hidden="true" />
          生成练习
        </button>
      </div>
    </section>
  );
}

export function ChatCanvasPage() {
  const { workspaceId = "", sessionId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const goalMode = new URLSearchParams(location.search).get("mode") === "goal";
  const conversationResetKey = `${workspaceId}:${sessionId}`;
  const mentionMenuId = useId();
  const isPhoneLayout = usePhoneLayout();
  // On phones the model picker renders inside the top bar (slot owned by the
  // workspace shell) instead of the cramped composer end addon.
  const [topbarModelSlot, setTopbarModelSlot] = useState<HTMLElement | null>(
    null,
  );
  // Bound goal/graph/node chips live in the page header, not above the composer.
  const [topbarContextSlot, setTopbarContextSlot] = useState<HTMLElement | null>(
    null,
  );
  useEffect(() => {
    setTopbarModelSlot(document.getElementById("topbar-model-slot"));
    setTopbarContextSlot(document.getElementById("topbar-context-slot"));
  }, []);
  const [localMessages, setLocalMessages] = useState<Message[]>([]);
  const [status, setStatus] = useState<ChatStatus>("ready");
  const [streamConnectionNotice, setStreamConnectionNotice] =
    useState<StreamConnectionNotice | null>(null);
  const [selectionMenu, setSelectionMenu] = useState<TextSelectionMenu | null>(null);
  const [selectionExplanationMarks, setSelectionExplanationMarks] = useState<
    SelectionExplanationRecord[]
  >(() => listSelectionExplanations(sessionId));
  const [activeConversationQuestionId, setActiveConversationQuestionId] =
    useState<string | null>(null);
  const [longPaste, setLongPaste] = useState<string | null>(null);

  useEffect(() => {
    setSelectionExplanationMarks(listSelectionExplanations(sessionId));
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent<{ parentSessionId?: string }>).detail;
      if (detail?.parentSessionId && detail.parentSessionId !== sessionId) return;
      setSelectionExplanationMarks(listSelectionExplanations(sessionId));
    };
    window.addEventListener(selectionExplanationRecordsEventName(), refresh);
    return () =>
      window.removeEventListener(selectionExplanationRecordsEventName(), refresh);
  }, [sessionId]);

  const handleOpenSelectionExplanation = useCallback(
    (recordId: string) => {
      const record = selectionExplanationMarks.find((item) => item.id === recordId);
      if (!record) return;
      openSelectionExplanation({
        parentSessionId: sessionId,
        sourceMessageId: record.sourceMessageId,
        selectedText: record.selectedText,
        prefix: record.prefix,
        suffix: record.suffix,
        contentMatched: record.contentMatched,
        action: record.action,
        recordId: record.id,
        explanationSessionId: record.explanationSessionId,
      });
    },
    [selectionExplanationMarks, sessionId],
  );
  const [pendingFiles, setPendingFiles] = useState<FileRecord[]>([]);
  const [composerText, setComposerText] = useState("");
  const [graphAction, setGraphAction] = useState<GraphAction>("none");
  // Settings load after mount; initial seed uses product defaults, then
  // the session restore effect below adopts the workspace default.
  const initialComposerPrefs = useMemo(
    () =>
      sessionId && sessionId !== "new"
        ? getSessionComposerPrefs(sessionId)
        : defaultComposerPrefs(),
    // Only seed from storage on first mount of this session route.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [conversationResetKey],
  );
  const [selectedProviderId, setSelectedProviderId] = useState(
    initialComposerPrefs.providerId ?? "",
  );
  const [selectedModelId, setSelectedModelId] = useState(
    initialComposerPrefs.modelId ?? "",
  );
  const [selectedImageProviderId, setSelectedImageProviderId] = useState(
    initialComposerPrefs.imageProviderId ?? "",
  );
  const [selectedImageModelId, setSelectedImageModelId] = useState(
    initialComposerPrefs.imageModelId ?? "",
  );
  const [responseMode, setResponseMode] = useState<ResponseMode>(
    initialComposerPrefs.responseMode,
  );
  const [thinkingMode, setThinkingMode] = useState<ThinkingMode>(
    initialComposerPrefs.thinkingMode,
  );
  const [searchRoute, setSearchRoute] = useState<SearchRoute>(
    initialComposerPrefs.searchRoute,
  );
  const [generationMode, setGenerationMode] = useState<GenerationMode>(
    initialComposerPrefs.generationMode,
  );
  const [imageSize, setImageSize] = useState<ImageSize>("auto");
  useEffect(() => {
    setStreamConnectionNotice(null);
  }, [conversationResetKey]);
  const [modelSearch, setModelSearch] = useState("");
  const [composerInstanceKey, setComposerInstanceKey] =
    useState(conversationResetKey);
  const resumeInFlightRef = useRef<string | null>(null);
  const [rejectProposalId, setRejectProposalId] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [closeDialogOpen, setCloseDialogOpen] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null);
  const [editingMessageContent, setEditingMessageContent] = useState("");
  const [dismissedMention, setDismissedMention] = useState("");
  const [mentionIndex, setMentionIndex] = useState(0);
  const [retryTarget, setRetryTarget] = useState<{
    messageId: string;
    sourceSessionId: string;
  } | null>(null);
  const [retryProviderId, setRetryProviderId] = useState("");
  const [retryModelId, setRetryModelId] = useState("");
  const [retryResponseMode, setRetryResponseMode] =
    useState<ResponseMode>("thinking");
  const [retryThinkingMode, setRetryThinkingMode] =
    useState<ThinkingMode>("medium");
  const [retryWebSearch, setRetryWebSearch] = useState(false);
  const [retryAllowedDomains, setRetryAllowedDomains] = useState("");
  // Image answers retry by resending the original prompt with a freely
  // chosen image model (the backend has no versioned retry for images).
  const [imageRetryTarget, setImageRetryTarget] = useState<{
    messageId: string;
    prompt: string;
  } | null>(null);
  const [imageRetryChoice, setImageRetryChoice] = useState("");
  const [sandboxAuthRequest, setSandboxAuthRequest] = useState<SandboxAuthRequest | null>(null);
  const initialLearningNode = useRef<LearningNodeContext | undefined>(
    readLearningNodeContext(),
  );
  const [learningNode, setLearningNode] = useState<
    LearningNodeContext | undefined
  >(initialLearningNode.current);
  const learningNodeRef = useRef<LearningNodeContext | undefined>(
    initialLearningNode.current,
  );
  const abortRef = useRef<AbortController | null>(null);
  const activeCancellationRef = useRef<Promise<void> | null>(null);
  const activeMessageId = useRef<string | null>(null);
  const activeStreamSessionId = useRef<string | null>(null);
  /** Always the session currently on screen — background streams must not mutate UI. */
  const viewingSessionIdRef = useRef(sessionId);
  viewingSessionIdRef.current = sessionId;
  const latestOperationId = useRef(0);
  const optimisticSessionId = useRef<string | null>(null);
  const retryExpectedVersionRef = useRef(0);
  const composerTextareaRef = useRef<HTMLTextAreaElement | null>(null);
  const [composerExpanded, setComposerExpanded] = useState(false);
  const openFileDialogRef = useRef<() => void>(() => undefined);
  const pendingHandled = useRef(false);
  const draftSessionCreationRef = useRef<{
    locationKey: string;
    promise: Promise<Session>;
  } | null>(null);
  const preserveDraftForSessionRef = useRef<string | null>(null);
  const speechRecognitionRef = useRef<BrowserSpeechRecognition | null>(null);
  const dictationStopRequestedRef = useRef(false);
  const dictationCleanupSessionRef = useRef<DictationCleanupSession | null>(
    null,
  );
  // 听写结束后的收尾阶段:云端 ASR 剩余段转写中,或 LLM 润色中。
  // 两个阶段都在输入框上方显示可点击跳过的标签。
  const [dictationFinalizing, setDictationFinalizing] = useState<
    null | "transcribing" | "polishing"
  >(null);
  const providerDictationRef = useRef<ProviderDictationHandle | null>(null);
  const registerFileDialog = useCallback((openFileDialog: () => void) => {
    openFileDialogRef.current = openFileDialog;
  }, []);

  // Grow the composer with soft wraps (not only hard newlines), ChatGPT-style.
  // Cap height; once capped, allow overflow so the caret stays reachable.
  useLayoutEffect(() => {
    const el = composerTextareaRef.current;
    if (!el) return;

    const syncHeight = () => {
      const maxHeight = 210;
      const minHeight = 52;
      // Collapse first so scrollHeight reflects full content, including soft wraps.
      el.style.height = "0px";
      const contentHeight = el.scrollHeight;
      const next = Math.min(maxHeight, Math.max(minHeight, contentHeight));
      el.style.height = `${next}px`;
      el.style.overflowY = contentHeight > maxHeight + 1 ? "auto" : "hidden";
      setComposerExpanded(contentHeight > minHeight + 1);
    };

    syncHeight();
    window.addEventListener("resize", syncHeight);
    return () => window.removeEventListener("resize", syncHeight);
  }, [composerText, composerInstanceKey]);

  useEffect(() => {
    if (!selectionMenu) return;
    const dismiss = () => setSelectionMenu(null);
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") dismiss();
    };
    window.addEventListener("resize", dismiss);
    window.addEventListener("scroll", dismiss, true);
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("resize", dismiss);
      window.removeEventListener("scroll", dismiss, true);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [selectionMenu]);

  useEffect(() => {
    if (!goalMode) return;
    setGraphAction("none");
    setSearchRoute("disabled");
    setGenerationMode("text");
  }, [goalMode]);

  const [historyHasMoreBefore, setHistoryHasMoreBefore] = useState(false);
  const [historyTotalCount, setHistoryTotalCount] = useState(0);
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false);
  const loadingOlderRef = useRef(false);

  const history = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "messages", sessionId),
    queryFn: async () => {
      const page = await listSessionMessagesPage(sessionId, {
        limit: INITIAL_MESSAGE_PAGE,
      });
      setHistoryTotalCount(page.total_count);
      const previous =
        queryClient.getQueryData<Message[]>(workspaceQueryKey(workspaceId, "messages", sessionId)) ?? [];
      const pageIds = new Set(page.items.map((item) => item.id));
      const oldestPageId = page.items[0]?.id;
      const oldestIndex = oldestPageId
        ? previous.findIndex((item) => item.id === oldestPageId)
        : -1;
      // Preserve any older turns the user already scrolled into view. A plain
      // replace would drop them whenever React Query refetches the newest window.
      const retainedOlder =
        oldestIndex > 0
          ? previous
              .slice(0, oldestIndex)
              .filter((item) => !pageIds.has(item.id))
          : [];
      if (retainedOlder.length === 0) {
        setHistoryHasMoreBefore(page.has_more_before);
      }
      return retainedOlder.length
        ? [...retainedOlder, ...page.items]
        : page.items;
    },
    enabled: sessionId !== "new",
    // Incomplete streams need a fresh read after refresh so we can resume.
    refetchOnMount: "always",
    // Evict inactive session histories quickly; active eviction below is the
    // primary bound, this is the safety net for unobserved caches.
    gcTime: 15_000,
  });
  const sessions = useQuery({ queryKey: workspaceQueryKey(workspaceId, "sessions"), queryFn: listSessions });

  // Reset older-page cursor state when the active session changes.
  useEffect(() => {
    setHistoryHasMoreBefore(false);
    setHistoryTotalCount(0);
    setLoadingOlderMessages(false);
    loadingOlderRef.current = false;
  }, [sessionId]);

  const loadOlderMessages = useCallback(async () => {
    if (
      !sessionId ||
      sessionId === "new" ||
      !historyHasMoreBefore ||
      loadingOlderRef.current
    ) {
      return;
    }
    const oldestId = history.data?.[0]?.id;
    if (!oldestId) return;
    loadingOlderRef.current = true;
    setLoadingOlderMessages(true);
    try {
      const page = await listSessionMessagesPage(sessionId, {
        limit: OLDER_MESSAGE_PAGE,
        beforeId: oldestId,
      });
      setHistoryHasMoreBefore(page.has_more_before);
      setHistoryTotalCount(page.total_count);
      if (page.items.length) {
        // Preserve scroll position when prepending older turns.
        const scroller =
          document.querySelector<HTMLElement>(
            ".chat-canvas-page [role='log'] > div",
          ) ?? null;
        const previousHeight = scroller?.scrollHeight ?? 0;
        const previousTop = scroller?.scrollTop ?? 0;
        queryClient.setQueryData<Message[]>(
          workspaceQueryKey(workspaceId, "messages", sessionId),
          (current) => {
            const existing = current ?? [];
            const seen = new Set(existing.map((item) => item.id));
            const older = page.items.filter((item) => !seen.has(item.id));
            return older.length ? [...older, ...existing] : existing;
          },
        );
        if (scroller) {
          requestAnimationFrame(() => {
            const delta = scroller.scrollHeight - previousHeight;
            scroller.scrollTop = previousTop + delta;
          });
        }
      }
    } catch (error) {
      toast.error(
        error instanceof Error ? error.message : "加载更早消息失败",
      );
    } finally {
      loadingOlderRef.current = false;
      setLoadingOlderMessages(false);
    }
  }, [history.data, historyHasMoreBefore, queryClient, sessionId, workspaceId]);

  // Near the top of the conversation scroller, pull older turns automatically.
  useEffect(() => {
    if (!historyHasMoreBefore) return;
    const scroller = document.querySelector<HTMLElement>(
      ".chat-canvas-page [role='log'] > div",
    );
    if (!scroller) return;
    const onScroll = () => {
      if (scroller.scrollTop < 120) void loadOlderMessages();
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => scroller.removeEventListener("scroll", onScroll);
  }, [historyHasMoreBefore, loadOlderMessages, sessionId]);

  // Track recently viewed sessions so we keep a tiny warm cache window without
  // retaining every visited agent thread's full message history.
  const recentMessageSessionIdsRef = useRef<string[]>([]);
  useEffect(() => {
    if (!sessionId || sessionId === "new") return;
    const previous = recentMessageSessionIdsRef.current.filter(
      (id) => id !== sessionId,
    );
    recentMessageSessionIdsRef.current = [sessionId, ...previous].slice(
      0,
      MESSAGE_CACHE_KEEP_RECENT + 1,
    );
    const keep = new Set([
      ...recentMessageSessionIdsRef.current,
      ...listStreamingSessionIds(),
    ]);
    // Drop full histories for sessions the user left and that are not still
    // generating in the background — the dominant multi-session RAM cost.
    queryClient.removeQueries({
      predicate: (query) => {
        const key = query.queryKey;
        if (!Array.isArray(key) || key.length < 4) return false;
        const [namespace, cachedWorkspaceId, resource, cachedSessionId] = key;
        if (namespace !== "workspace" || cachedWorkspaceId !== workspaceId) {
          return false;
        }
        if (
          resource !== "messages" &&
          resource !== "message-versions" &&
          resource !== "message-snapshot"
        ) {
          return false;
        }
        return (
          typeof cachedSessionId === "string" &&
          cachedSessionId !== "new" &&
          !keep.has(cachedSessionId)
        );
      },
    });
    // Keep only optimistic/streaming rows for active + background streams.
    setLocalMessages((current) => {
      const next = current.filter((message) => {
        const owner = message.session_id;
        if (!owner || owner === sessionId) return true;
        return isSessionStreaming(owner);
      });
      return next.length === current.length ? current : next;
    });
  }, [queryClient, sessionId, workspaceId]);

  const settings = useQuery({ queryKey: workspaceQueryKey(workspaceId, "settings"), queryFn: listSettings });
  const workspaceDefaultResponseMode = useMemo(
    () => readChatDefaultResponseMode(settings.data),
    [settings.data],
  );
  // Gate persist on a *state* marker, not a ref. When sessionId flips, this
  // render still holds the previous session's responseMode; a ref flipped at
  // the end of the restore effect would let the same-cycle persist effect write
  // that stale mode into the new session's localStorage (e.g. 极速 onto a draft
  // that should inherit the workspace default 智能体).
  const [composerPrefsReadySessionId, setComposerPrefsReadySessionId] =
    useState<string | null>(null);

  // Restore per-session composer prefs whenever the active session changes.
  useEffect(() => {
    // Workspace default only matters when this session has no local prefs yet.
    // Existing sessions can restore immediately; drafts wait for settings so we
    // do not seed/persist the product fallback before the admin default arrives.
    const hasStoredPrefs =
      Boolean(sessionId) &&
      sessionId !== "new" &&
      hasSessionComposerPrefs(sessionId);
    if (!hasStoredPrefs && settings.isPending) return;
    if (!sessionId || sessionId === "new") {
      const defaults = defaultComposerPrefsForResponseMode(
        workspaceDefaultResponseMode,
      );
      setResponseMode(defaults.responseMode);
      setThinkingMode(defaults.thinkingMode);
      setSearchRoute(defaults.searchRoute);
      setGenerationMode(defaults.generationMode);
      // Batched with the mode updates above; persist sees them on the next render.
      setComposerPrefsReadySessionId(sessionId || "new");
      return;
    }
    const stored = hasStoredPrefs
      ? getSessionComposerPrefs(sessionId)
      : defaultComposerPrefsForResponseMode(workspaceDefaultResponseMode);
    const session = sessions.data?.find((item) => item.id === sessionId);
    const fromSnapshot = prefsFromModelSnapshot(
      session?.model_snapshot as Record<string, unknown> | undefined,
    );
    // Local storage wins; snapshot only fills gaps on first visit.
    const merged = {
      ...stored,
      ...(!stored.providerId && fromSnapshot.providerId
        ? { providerId: fromSnapshot.providerId }
        : {}),
      ...(!stored.modelId && fromSnapshot.modelId
        ? { modelId: fromSnapshot.modelId }
        : {}),
      // If prefs are still workspace/product defaults and snapshot has a
      // different mode, adopt the durable snapshot (e.g. first open after refresh).
      ...(isDefaultComposerPrefs(stored, workspaceDefaultResponseMode) &&
      fromSnapshot.responseMode &&
      fromSnapshot.responseMode !== stored.responseMode
        ? {
            responseMode: fromSnapshot.responseMode,
            thinkingMode: fromSnapshot.thinkingMode ?? stored.thinkingMode,
          }
        : {}),
    };
    setResponseMode(merged.responseMode);
    setThinkingMode(merged.thinkingMode);
    setSearchRoute(merged.searchRoute);
    setGenerationMode(merged.generationMode);
    setSelectedProviderId(merged.providerId ?? "");
    setSelectedModelId(merged.modelId ?? "");
    setSelectedImageProviderId(merged.imageProviderId ?? "");
    setSelectedImageModelId(merged.imageModelId ?? "");
    setComposerPrefsReadySessionId(sessionId);
  }, [
    sessionId,
    sessions.data,
    settings.isPending,
    workspaceDefaultResponseMode,
  ]);

  // Clear the in-flight resume latch when switching sessions so a new session
  // can auto-resume independently. Do NOT abort background streams — concurrent
  // sessions keep generating until the user stops that session explicitly.
  useEffect(() => {
    resumeInFlightRef.current = null;
    if (sessionId && sessionId !== "new") {
      markSessionViewed(sessionId);
    }
  }, [sessionId]);

  // Persist composer mode changes against the active session id.
  useEffect(() => {
    if (!sessionId || sessionId === "new") return;
    // Wait until the restore effect's mode updates have committed for this id.
    if (composerPrefsReadySessionId !== sessionId) return;
    setSessionComposerPrefs(sessionId, {
      responseMode,
      thinkingMode,
      searchRoute,
      generationMode,
      providerId: selectedProviderId || undefined,
      modelId: selectedModelId || undefined,
      imageProviderId: selectedImageProviderId || undefined,
      imageModelId: selectedImageModelId || undefined,
    });
  }, [
    composerPrefsReadySessionId,
    generationMode,
    responseMode,
    searchRoute,
    selectedModelId,
    selectedProviderId,
    selectedImageModelId,
    selectedImageProviderId,
    sessionId,
    thinkingMode,
  ]);
  const providers = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "providers"),
    queryFn: listProviders,
  });
  const graphs = useQuery({ queryKey: workspaceQueryKey(workspaceId, "graphs"), queryFn: listGraphs });
  // 空会话提示优先参考工作区记忆；无记忆时使用中文默认问题。
  const emptySessionMemories = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "memories", "empty-session-prompts"),
    queryFn: () => listMemories({ state: "active", zone: "hot" }),
    staleTime: 60_000,
  });
  const memoryFallbackPrompts = useMemo(
    () =>
      buildMemoryBackedEmptyPrompts(
        (emptySessionMemories.data ?? []).map((item) => ({
          id: item.id,
          title: item.title,
          content: item.content,
        })),
      ),
    [emptySessionMemories.data],
  );
  const modelProviders = useMemo(
    () =>
      (providers.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          isModelProviderType(provider.provider_type),
      ),
    [providers.data],
  );
  const activeModelProvider = useMemo(
    () =>
      modelProviders.find((provider) => provider.id === selectedProviderId) ??
      modelProviders[0],
    [modelProviders, selectedProviderId],
  );
  const imageProviders = useMemo(
    () =>
      (providers.data ?? []).filter(
        (provider) =>
          provider.enabled &&
          provider.remote_capability &&
          (provider.provider_type === "openai_images" ||
            providerCapabilityString(provider, "provider_role") ===
              "image_generation") &&
          Boolean(
            providerCapabilityString(
              provider,
              "default_image_generation_model_id",
            ),
          ),
      ),
    [providers.data],
  );
  const activeImageProvider = useMemo(
    () =>
      imageProviders.find(
        (provider) => provider.id === selectedImageProviderId,
      ) ?? imageProviders[0],
    [imageProviders, selectedImageProviderId],
  );
  const transcriptionProviders = useMemo(
    () =>
      (providers.data ?? []).filter((provider) => {
        if (!provider.enabled || !provider.remote_capability) return false;
        const role = providerCapabilityString(provider, "provider_role");
        return (
          role === "transcription" ||
          provider.provider_type === "openai_compatible_transcription"
        );
      }),
    [providers.data],
  );
  const asrAvailable = transcriptionProviders.some((provider) => {
    const storedModel = providerCapabilityString(
      provider,
      "default_transcription_model_id",
    );
    const realtimeModel = providerCapabilityString(
      provider,
      "default_realtime_transcription_model_id",
    );
    return Boolean(storedModel || realtimeModel);
  });
  const storedAudioTranscriptionProvider = transcriptionProviders.find(
    (provider) => {
      const modelId = providerCapabilityString(
        provider,
        "default_transcription_model_id",
      );
      return Boolean(modelId && !isRealtimeTranscriptionModel(modelId));
    },
  );
  const storedAudioAsrAvailable = Boolean(storedAudioTranscriptionProvider);
  // realtime 系列 ASR 模型只提供 WebSocket 接口,听写须走实时长连接;
  // 非 realtime 模型走 HTTP 分段上传。
  const realtimeAudioTranscriptionProvider = transcriptionProviders.find(
    (provider) => {
      const configuredRealtime = providerCapabilityString(
        provider,
        "default_realtime_transcription_model_id",
      );
      if (isRealtimeTranscriptionModel(configuredRealtime)) return true;
      // 兼容旧配置：旧 key 中的 realtime 型号仅用于实时听写。
      return isRealtimeTranscriptionModel(
        providerCapabilityString(provider, "default_transcription_model_id"),
      );
    },
  );
  const realtimeAudioTranscriptionModel = realtimeAudioTranscriptionProvider
    ? providerCapabilityString(
        realtimeAudioTranscriptionProvider,
        "default_realtime_transcription_model_id",
      ) ||
      providerCapabilityString(
        realtimeAudioTranscriptionProvider,
        "default_transcription_model_id",
      )
    : "";
  const asrRealtimeConfigured = Boolean(
    realtimeAudioTranscriptionProvider && realtimeAudioTranscriptionModel,
  );
  const imageProviderModelQueries = useQueries({
    queries: imageProviders.map((provider) => ({
      queryKey: workspaceQueryKey(workspaceId, "provider-models", provider.id),
      queryFn: () => discoverProviderModels(provider.id),
      retry: false,
    })),
  });
  const activeImageProviderIndex = imageProviders.findIndex(
    (provider) => provider.id === activeImageProvider?.id,
  );
  const discoveredImageModels =
    activeImageProviderIndex >= 0
      ? imageProviderModelQueries[activeImageProviderIndex]
      : undefined;
  const imageModelOptions = useMemo(
    () =>
      providerModelOptions(
        activeImageProvider,
        discoveredImageModels?.data?.models,
        "default_image_generation_model_id",
      ),
    [activeImageProvider, discoveredImageModels?.data?.models],
  );
  const availableImageModelChoices = useMemo(
    () =>
      imageProviders.flatMap((provider, index) =>
        providerModelOptions(
          provider,
          imageProviderModelQueries[index]?.data?.models,
          "default_image_generation_model_id",
        ).map((model) => ({ provider, model })),
      ),
    [imageProviderModelQueries, imageProviders],
  );
  const filteredAvailableImageModelChoices = useMemo(
    () =>
      availableImageModelChoices.filter(({ provider, model }) =>
        fuzzyModelMatch(
          `${model.id} ${provider.display_name} ${modelProtocolLabel(provider.provider_type)}`,
          modelSearch,
        ),
      ),
    [availableImageModelChoices, modelSearch],
  );
  const selectedImageModel =
    imageModelOptions.find((model) => model.id === selectedImageModelId) ??
    imageModelOptions[0];
  const imageEditEnabled = isImageEditModel(selectedImageModel);
  const imageSizeOption =
    IMAGE_SIZE_OPTIONS.find((option) => option.value === imageSize) ??
    IMAGE_SIZE_OPTIONS[0];
  useEffect(() => {
    if (generationMode !== "image") return;
    setPendingFiles((current) => {
      const retained = imageEditEnabled
        ? current
            .filter((file) =>
              IMAGE_EDIT_MIME_TYPES.has(file.mime_type.toLowerCase()),
            )
            .slice(0, 4)
        : [];
      if (retained.length !== current.length) {
        toast.message(
          imageEditEnabled
            ? "已移除绘图模式不支持的附件。"
            : "已移除参考图：当前绘图模型不支持参考图。",
        );
      }
      return retained;
    });
  }, [generationMode, imageEditEnabled]);
  const composerFileAccept = useMemo(() => {
    if (generationMode === "image")
      return imageEditEnabled ? "image/png,image/jpeg,image/webp" : "";
    return undefined;
  }, [generationMode, imageEditEnabled]);
  const defaultImageModelId = providerCapabilityString(
    activeImageProvider,
    "default_image_generation_model_id",
  );
  const providerModelQueries = useQueries({
    queries: modelProviders.map((provider) => ({
      queryKey: workspaceQueryKey(workspaceId, "provider-models", provider.id),
      queryFn: () => discoverProviderModels(provider.id),
      retry: false,
    })),
  });
  const activeModelProviderIndex = modelProviders.findIndex(
    (provider) => provider.id === activeModelProvider?.id,
  );
  const discoveredModels =
    activeModelProviderIndex >= 0
      ? providerModelQueries[activeModelProviderIndex]
      : undefined;
  const modelOptions = useMemo(
    () =>
      providerModelOptions(
        activeModelProvider,
        discoveredModels?.data?.models,
      ).filter((model) => isTextChatModel(model)),
    [activeModelProvider, discoveredModels?.data?.models],
  );
  const availableModelChoices = useMemo(
    () =>
      modelProviders
        .flatMap((provider, index) =>
          providerModelOptions(
            provider,
            providerModelQueries[index]?.data?.models,
          ).map((model) => ({ provider, model })),
        )
        .filter(({ model }) => isTextChatModel(model)),
    [modelProviders, providerModelQueries],
  );
  const filteredAvailableModelChoices = useMemo(
    () =>
      availableModelChoices.filter(({ provider, model }) =>
        fuzzyModelMatch(
          `${model.id} ${provider.display_name} ${modelProtocolLabel(provider.provider_type)}`,
          modelSearch,
        ),
      ),
    [availableModelChoices, modelSearch],
  );
  const selectedModel = modelOptions.find(
    (model) => model.id === selectedModelId,
  );
  const retryProvider = modelProviders.find(
    (provider) => provider.id === retryProviderId,
  );
  const retryDiscoveredModels = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "provider-models", retryProviderId),
    queryFn: () => discoverProviderModels(retryProviderId),
    enabled: Boolean(retryTarget && retryProviderId),
    retry: false,
  });
  const retryModelOptions = useMemo(
    () =>
      providerModelOptions(retryProvider, retryDiscoveredModels.data?.models),
    [retryDiscoveredModels.data?.models, retryProvider],
  );
  const retrySelectedModel = retryModelOptions.find(
    (model) => model.id === retryModelId,
  );
  const retrySupportsAgentMode = Boolean(
    retryProvider &&
      isModelProviderType(retryProvider.provider_type) &&
      retryProvider.capabilities.supports_agent_tools !== false &&
      retrySelectedModel?.remote,
  );
  const activeProviderSupportsStructuredAgent = Boolean(
    activeModelProvider &&
      isModelProviderType(activeModelProvider.provider_type) &&
      activeModelProvider.capabilities.supports_agent_tools !== false,
  );
  const activeProviderIsDeepSeek = Boolean(
    activeModelProvider && isDeepSeekProvider(activeModelProvider),
  );
  const hasAuthorizedAgentSearchProvider = Boolean(
    providers.data?.some(
      (provider) =>
        provider.enabled &&
        provider.remote_capability &&
        ([
            "anysearch",
            "searxng",
            "tavily",
            "exa",
            "brave_search",
            "firecrawl_search",
            "ollama_cloud_search",
          ].includes(provider.provider_type) ||
          (provider.provider_type === "qwen" &&
            Array.isArray(provider.capabilities.companion_capabilities) &&
            provider.capabilities.companion_capabilities.includes("web_search"))),
    ),
  );
  const hasDeepResearchProvider = Boolean(
    providers.data?.some((provider) => {
      if (!provider.enabled || !provider.remote_capability) return false;
      const role = providerCapabilityString(provider, "provider_role");
      return (
        role === "deep_research" ||
        provider.provider_type.includes("deep_research")
      );
    }),
  );
  const hasQwenCompanionSearchProvider = Boolean(
    providers.data?.some(
      (provider) =>
        provider.provider_type === "qwen" &&
        provider.enabled &&
        provider.remote_capability &&
        Array.isArray(provider.capabilities.companion_capabilities) &&
        provider.capabilities.companion_capabilities.includes("web_search"),
    ),
  );
  const thinkingModes = useMemo(
    () =>
      capabilityThinkingModes(
        selectedModel?.capabilities?.reasoning_efforts ??
          activeModelProvider?.capabilities.reasoning_efforts ??
          (activeProviderIsDeepSeek
            ? ["low", "medium", "high", "xhigh"]
            : undefined),
      ),
    [
      activeProviderIsDeepSeek,
      activeModelProvider?.capabilities.reasoning_efforts,
      selectedModel?.capabilities?.reasoning_efforts,
    ],
  );
  const retryThinkingModes = useMemo(
    () =>
      capabilityThinkingModes(
        retrySelectedModel?.capabilities?.reasoning_efforts ??
          retryProvider?.capabilities.reasoning_efforts ??
          (retryProvider && isDeepSeekProvider(retryProvider)
            ? ["low", "medium", "high", "xhigh"]
            : undefined),
      ),
    [
      retryProvider,
      retrySelectedModel?.capabilities?.reasoning_efforts,
    ],
  );
  useEffect(() => {
    setSelectedProviderId((current) =>
      modelProviders.some((provider) => provider.id === current)
        ? current
        : (modelProviders[0]?.id ?? ""),
    );
  }, [modelProviders]);
  useEffect(() => {
    setSelectedImageProviderId((current) =>
      imageProviders.some((provider) => provider.id === current)
        ? current
        : (imageProviders[0]?.id ?? ""),
    );
  }, [imageProviders]);
  useEffect(() => {
    setSelectedImageModelId((current) =>
      imageModelOptions.some((model) => model.id === current)
        ? current
        : (imageModelOptions.find((model) => model.id === defaultImageModelId)
            ?.id ??
          imageModelOptions[0]?.id ??
          ""),
    );
  }, [defaultImageModelId, imageModelOptions]);
  useEffect(() => {
    setSelectedModelId((current) =>
      modelOptions.some((model) => model.id === current)
        ? current
        : (modelOptions[0]?.id ?? ""),
    );
  }, [modelOptions]);
  useEffect(() => {
    if (!thinkingModes.length) return;
    setThinkingMode((current) =>
      thinkingModes.includes(current) ? current : thinkingModes[0],
    );
  }, [thinkingModes]);
  useEffect(() => {
    setRetryModelId((current) =>
      retryModelOptions.some((model) => model.id === current && model.remote)
        ? current
        : (retryModelOptions.find((model) => model.remote)?.id ?? ""),
    );
  }, [retryModelOptions]);
  useEffect(() => {
    if (retryTarget && retryDiscoveredModels.isPending) return;
    if (!retryThinkingModes.length) {
      setRetryResponseMode("fast");
      setRetryThinkingMode("off");
      return;
    }
    setRetryThinkingMode((current) =>
      retryThinkingModes.includes(current)
        ? current
        : retryThinkingModes.includes("medium")
          ? "medium"
          : retryThinkingModes[0],
    );
  }, [retryDiscoveredModels.isPending, retryTarget, retryThinkingModes]);
  const supportsThinkingMode = thinkingModes.length > 0;
  const thinkingRequired =
    selectedModel?.capabilities?.thinking_required === true;
  const supportsAgentMode =
    activeProviderSupportsStructuredAgent &&
    Boolean(selectedModel?.remote);
  const agentModeUnavailableLabel = !activeProviderSupportsStructuredAgent
    ? "（当前接口未声明结构化工具能力）"
    : "";
  // Response mode is an explicit user preference. Capability changes may make
  // the selected mode temporarily unavailable, but must never silently rewrite
  // 智能体 to 思考/极速; the disabled send path explains the unavailable model.
  useEffect(() => {
    if (providers.isPending || discoveredModels?.isPending) return;
    if (
      responseMode === "thinking" &&
      activeModelProvider &&
      !supportsThinkingMode
    ) {
      setResponseMode("fast");
    }
  }, [
    activeModelProvider,
    discoveredModels?.isPending,
    providers.isPending,
    responseMode,
    supportsThinkingMode,
  ]);
  useEffect(() => {
    if (!thinkingRequired || !supportsThinkingMode || responseMode !== "fast") {
      return;
    }
    setResponseMode("thinking");
    setThinkingMode((current) =>
      thinkingModes.includes(current)
        ? current
        : thinkingModes.includes("medium")
          ? "medium"
          : thinkingModes[0],
    );
  }, [responseMode, supportsThinkingMode, thinkingModes, thinkingRequired]);
  // External SearchProvider is authorized only when enabled + remote_capable.
  // Drop a stale "auto/external/local" preference if the user disables search
  // (or the SearchProvider itself) so agentic send cannot request web_search
  // against an unavailable SearchProvider.
  useEffect(() => {
    if (providers.isPending || providers.isError) return;
    if (searchRoute === "disabled" || searchRoute === "model_native") return;
    if (!hasAuthorizedAgentSearchProvider) {
      setSearchRoute("disabled");
    }
  }, [
    hasAuthorizedAgentSearchProvider,
    providers.isError,
    providers.isPending,
    searchRoute,
  ]);
  const effectiveThinkingMode: ThinkingMode =
    (responseMode === "fast" && !thinkingRequired) || !supportsThinkingMode
      ? "off"
      : thinkingMode;
  const responseModeLabel =
    responseMode === "fast" ? "极速" : responseMode === "agentic" ? "智能体" : "思考";
  const activeGenerationProvider =
    generationMode === "image" ? activeImageProvider : activeModelProvider;
  const activeGenerationModelId =
    generationMode === "image" ? selectedImageModel?.id ?? "" : selectedModel?.id ?? "";
  const messages = useMemo(() => {
    const persisted =
      optimisticSessionId.current === sessionId ? [] : history.data ?? [];
    const retryOverlays = new Map<string, Message>();
    const normalOverlays = new Map<string, Message>();
    const appended: Message[] = [];
    const persistedById = new Map(
      persisted.map((message) => [message.id, message]),
    );
    // Concurrent streams may still push into localMessages; only show the
    // active session so session B's tokens never paint on session C.
    localMessages.forEach((message) => {
      if (
        message.session_id &&
        message.session_id !== sessionId &&
        optimisticSessionId.current !== sessionId
      ) {
        return;
      }
      const target = message.provider_trace?.optimistic_target_message_id;
      if (typeof target === "string") retryOverlays.set(target, message);
      else {
        const persistedId = message.provider_trace
          ?.optimistic_persisted_message_id;
        const confirmed =
          persistedById.get(message.id) ??
          (typeof persistedId === "string"
            ? persistedById.get(persistedId)
            : undefined);
        if (!confirmed)
          appended.push(message);
        else if (
          !TERMINAL_MESSAGE_STATUSES.includes(
            confirmed.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
          )
        )
          normalOverlays.set(confirmed.id, message);
      }
    });
    return [
      ...persisted.map(
        (message) =>
          retryOverlays.get(message.id) ??
          normalOverlays.get(message.id) ??
          message,
      ),
      ...appended,
    ];
  }, [history.data, localMessages, sessionId]);
  const conversationJumpItems = useMemo(() => {
    const activeSession = (sessions.data ?? []).find(
      (session) => session.id === sessionId,
    );
    const branchSourceIds = new Set(
      (sessions.data ?? [])
        .filter((session) => session.parent_session_id === sessionId)
        .map((session) => session.source_message_id)
        .filter((id): id is string => Boolean(id)),
    );
    const firstLocalUserId = messages.find(
      (message) => message.role === "user" && message.session_id === sessionId,
    )?.id;
    return messages
      .filter(
        (message) =>
          message.role === "user" &&
          !message.id.startsWith("temp") &&
          message.id !== "welcome-local",
      )
      .map((message) => ({
        id: message.id,
        label: message.content.replace(/\s+/gu, " ").trim() || "未命名问题",
        branch:
          branchSourceIds.has(message.id) ||
          (Boolean(activeSession?.parent_session_id) &&
            message.id === firstLocalUserId),
        active: message.id === activeConversationQuestionId,
      }));
  }, [activeConversationQuestionId, messages, sessionId, sessions.data]);
  useEffect(() => {
    if (!conversationJumpItems.length) {
      setActiveConversationQuestionId(null);
      return;
    }
    const updateActiveQuestion = () => {
      const anchor = Math.min(window.innerHeight * 0.42, 320);
      let closest = conversationJumpItems[0]!;
      let closestDistance = Number.POSITIVE_INFINITY;
      for (const item of conversationJumpItems) {
        const element = document.getElementById(`conversation-jump-${item.id}`);
        if (!element) continue;
        const distance = Math.abs(element.getBoundingClientRect().top - anchor);
        if (distance < closestDistance) {
          closest = item;
          closestDistance = distance;
        }
      }
      setActiveConversationQuestionId((current) =>
        current === closest.id ? current : closest.id,
      );
    };
    updateActiveQuestion();
    window.addEventListener("scroll", updateActiveQuestion, true);
    window.addEventListener("resize", updateActiveQuestion);
    return () => {
      window.removeEventListener("scroll", updateActiveQuestion, true);
      window.removeEventListener("resize", updateActiveQuestion);
    };
  }, [conversationJumpItems]);

  useEffect(() => {
    // Promote sandbox_auth_required status parts into an authorization dialog.
    const parts = messages.flatMap((message) => message.parts ?? []);
    for (const part of parts) {
      if (part.type !== "sandbox_status" || part.data?.auth_required !== true) continue;
      const paths = Array.isArray(part.data?.paths)
        ? part.data.paths.filter((item): item is string => typeof item === "string")
        : [];
      if (!paths.length) continue;
      const chatSessionId =
        (typeof part.data?.chat_session_id === "string" && part.data.chat_session_id) ||
        sessionId;
      if (!chatSessionId || chatSessionId === "new") continue;
      const sandboxSessionId =
        typeof part.data?.sandbox_session_id === "string"
          ? part.data.sandbox_session_id
          : null;
      const commandIntentDigest =
        typeof part.data?.command_intent_digest === "string"
          ? part.data.command_intent_digest
          : null;
      if (!sandboxSessionId || !commandIntentDigest) continue;
      setSandboxAuthRequest((current) => {
        if (current) return current;
        return {
          chatSessionId,
          paths,
          action: typeof part.data?.action === "string" ? part.data.action : "delete_path",
          message:
            typeof part.data?.message_zh === "string" ? part.data.message_zh : undefined,
          sandboxSessionId,
          commandIntentDigest,
        };
      });
      break;
    }
  }, [messages, sessionId]);

  // After refresh (or remount), detect in-flight assistant messages and resume
  // durable event replay so the user can keep receiving the answer.
  useEffect(() => {
    if (
      sessionId === "new" ||
      !history.isSuccess ||
      status === "streaming" ||
      status === "submitted"
    ) {
      return;
    }
    // A concurrent send for this session is still live — reattach UI controls.
    if (isSessionStreaming(sessionId)) {
      const handle = getSessionStream(sessionId);
      if (handle) {
        abortRef.current = handle.controller;
        activeStreamSessionId.current = sessionId;
        activeMessageId.current = handle.messageId;
      }
      setStatus("streaming");
      return;
    }
    const inFlight = [...(history.data ?? [])]
      .reverse()
      .find(
        (message) =>
          message.role === "assistant" &&
          IN_FLIGHT_MESSAGE_STATUSES.includes(
            message.status as (typeof IN_FLIGHT_MESSAGE_STATUSES)[number],
          ),
      );
    if (!inFlight) return;
    if (resumeInFlightRef.current === inFlight.id) return;
    resumeInFlightRef.current = inFlight.id;

    const controller = new AbortController();
    abortRef.current = controller;
    activeMessageId.current = inFlight.id;
    activeStreamSessionId.current = sessionId;
    registerSessionStream(sessionId, controller, inFlight.id);
    markSessionRunning(sessionId, true);
    setStatus("streaming");
    setStreamConnectionNotice({
      phase: "reconnecting",
      attempt: 0,
      maxAttempts: DEFAULT_STREAM_RECONNECTS,
      detail: "检测到仍在生成的回答，正在从持久事件续接",
    });

    void (async () => {
      latestOperationId.current += 1;
      const streamSessionId = sessionId;
      let completed = false;
      let terminalFailure = "";
      let lastEventId = "";
      const seenEventIds = new Set<string>();
      const isViewing = () => viewingSessionIdRef.current === streamSessionId;
      const frameQueue = createAnimationFrameQueue<Record<string, unknown>>(
        (updates) => {
          setLocalMessages((current) => {
            const existing = current.find((item) => item.id === inFlight.id);
            if (existing) {
              return current.map((message) =>
                message.id === inFlight.id
                  ? applyStreamUpdates(message, updates)
                  : message,
              );
            }
            return [
              ...current,
              applyStreamUpdates(
                {
                  ...inFlight,
                  status: "streaming",
                },
                updates,
              ),
            ];
          });
        },
      );
      const consume = (data: Record<string, unknown>) => {
        const eventId =
          typeof data.event_id === "string" ? data.event_id : "";
        if (eventId && seenEventIds.has(eventId)) return;
        if (eventId) {
          seenEventIds.add(eventId);
          lastEventId = eventId;
        }
        const type = streamEventType(data);
        if (type === "message.completed") completed = true;
        if (type === "message.failed")
          terminalFailure = streamEventFailure(data);
        if (type === "message.cancelled") terminalFailure = "生成已取消。";
        // A resumable interruption parks the message as `interrupted`. Treat it
        // as a finished generation (stop polling, refresh persisted state) so
        // the durable message reflected from the server can render its
        // "已中断（可重试）" affordance; it is not an error.
        if (type === "message.interrupted") completed = true;
        if ((type || isMessagePart(data.part)) && isViewing()) {
          setStatus("streaming");
          setStreamConnectionNotice(null);
        }
        expandStreamUpdate(data, { animate: isViewing() }).forEach((update) =>
          frameQueue.push(update),
        );
      };
      try {
        let consecutiveFailures = 0;
        // Exponential back-off for the replay poll: a steady in-flight stream
        // whose generator is momentarily idle (between agent steps) should not
        // hammer the GET events endpoint at a flat 400ms cadence. Each round
        // that does NOT advance the durable cursor doubles the wait up to 4s;
        // any new replay event resets it, restoring near-instant delivery when
        // the stream is productive.
        let pollDelayMs = 400;
        while (!controller.signal.aborted) {
          if (controller.signal.aborted) break;
          const lastEventIdBefore = lastEventId;
          try {
            const replay = await listSessionMessageEvents(
              streamSessionId,
              inFlight.id,
              {
                afterEventId: lastEventId || undefined,
              },
            );
            replay.forEach((event) =>
              consume(event as Record<string, unknown>),
            );
            consecutiveFailures = 0;
            pollDelayMs =
              lastEventId !== lastEventIdBefore
                ? 400
                : Math.min(pollDelayMs * 2, 4_000);
          } catch (error) {
            consecutiveFailures += 1;
            pollDelayMs = Math.min(pollDelayMs * 2, 4_000);
            if (isViewing()) {
              setStreamConnectionNotice({
                phase:
                  consecutiveFailures >= DEFAULT_STREAM_RECONNECTS
                    ? "failed"
                    : "reconnecting",
                attempt: consecutiveFailures,
                maxAttempts: DEFAULT_STREAM_RECONNECTS,
                detail: streamErrorDetail(error),
              });
            }
            if (consecutiveFailures >= DEFAULT_STREAM_RECONNECTS) {
              throw error;
            }
          }
          if (completed || terminalFailure) break;
          // Also re-check persisted status in case the generator finished
          // without more events (e.g. process restarted mid-stream).
          // Snapshot only the in-flight message — re-downloading the entire
          // history on every poll tick was a major multi-session RAM spike.
          try {
            const refreshed = await getMessageSnapshot(
              streamSessionId,
              inFlight.id,
            );
            if (
              refreshed &&
              TERMINAL_MESSAGE_STATUSES.includes(
                refreshed.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
              )
            ) {
              queryClient.setQueryData<Message[]>(
                workspaceQueryKey(workspaceId, "messages", streamSessionId),
                (current) => {
                  if (!current?.length) return [refreshed];
                  let found = false;
                  const next = current.map((item) => {
                    if (item.id !== refreshed.id) return item;
                    found = true;
                    return refreshed;
                  });
                  return found ? next : [...next, refreshed];
                },
              );
              completed = true;
              break;
            }
          } catch {
            // ignore and keep polling events
          }
          await new Promise((resolve) => window.setTimeout(resolve, pollDelayMs));
        }
        await frameQueue.drain();
        if (terminalFailure) {
          markSessionRunning(streamSessionId, false);
          if (isViewing()) {
            setStatus("error");
            setStreamConnectionNotice({
              phase: "failed",
              attempt: 0,
              maxAttempts: DEFAULT_STREAM_RECONNECTS,
              detail: terminalFailure,
            });
          } else {
            markSessionGenerationFinished(streamSessionId, { viewing: false });
          }
          return;
        }
        if (completed) {
          await queryClient.invalidateQueries({
            queryKey: workspaceQueryKey(workspaceId, "messages", streamSessionId),
          });
          void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "sessions") });
          // Always drop the optimistic row once the durable message is terminal,
          // even if the user is currently looking at another session.
          setLocalMessages((current) =>
            current.filter((message) => message.id !== inFlight.id),
          );
          if (isViewing()) {
            setStatus("ready");
            setStreamConnectionNotice(null);
            markSessionGenerationFinished(streamSessionId, { viewing: true });
          } else {
            markSessionGenerationFinished(streamSessionId, { viewing: false });
          }
          return;
        }
      } catch (error) {
        markSessionRunning(streamSessionId, false);
        if (controller.signal.aborted) {
          if (isViewing()) setStatus("ready");
          return;
        }
        if (isViewing()) {
          setStatus("error");
          setStreamConnectionNotice({
            phase: "failed",
            attempt: DEFAULT_STREAM_RECONNECTS,
            maxAttempts: DEFAULT_STREAM_RECONNECTS,
            detail: streamErrorDetail(error),
          });
        }
      } finally {
        clearSessionStream(streamSessionId, controller);
        if (abortRef.current === controller) abortRef.current = null;
        if (activeMessageId.current === inFlight.id)
          activeMessageId.current = null;
        if (activeStreamSessionId.current === streamSessionId)
          activeStreamSessionId.current = null;
      }
    })();

    // Intentionally no abort on unmount/session switch — concurrent sessions
    // keep replaying until the user stops this session or it finishes.
    return undefined;
  }, [history.data, history.isSuccess, queryClient, sessionId, status]);


  useEffect(() => {
    if (!history.data?.length) return;
    const persistedById = new Map(
      history.data.map((message) => [message.id, message]),
    );
    setLocalMessages((current) => {
      const normalOptimistic = current.filter(
        (message) =>
          typeof message.provider_trace?.optimistic_persisted_message_id ===
          "string",
      );
      const allNormalConfirmed = normalOptimistic.every((message) => {
        const persistedId = message.provider_trace
          ?.optimistic_persisted_message_id as string;
        const persisted = persistedById.get(persistedId);
        return Boolean(
          persisted && TERMINAL_MESSAGE_STATUSES.includes(
            persisted.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
          ),
        );
      });
      const holdNewSessionOptimistic =
        optimisticSessionId.current === sessionId && !allNormalConfirmed;
      const next = current.filter((message) => {
        const retryTarget = message.provider_trace?.optimistic_target_message_id;
        if (typeof retryTarget === "string") {
          const persisted = persistedById.get(retryTarget);
          return !(
            persisted &&
            persisted.version >= message.version &&
            TERMINAL_MESSAGE_STATUSES.includes(
              persisted.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
            )
          );
        }
        const persistedId = message.provider_trace
          ?.optimistic_persisted_message_id;
        if (typeof persistedId !== "string" || holdNewSessionOptimistic)
          return true;
        const persisted = persistedById.get(persistedId);
        return !(
          persisted &&
          TERMINAL_MESSAGE_STATUSES.includes(
            persisted.status as (typeof TERMINAL_MESSAGE_STATUSES)[number],
          )
        );
      });
      if (
        optimisticSessionId.current === sessionId &&
        allNormalConfirmed &&
        normalOptimistic.length
      )
        optimisticSessionId.current = null;
      return next.length === current.length ? current : next;
    });
  }, [history.data, sessionId]);
  const currentSession = sessions.data?.find(
    (session) => session.id === sessionId,
  );
  const conversationBranchLinks = useMemo(() => {
    if (!currentSession?.parent_session_id) return [];
    const parent = (sessions.data ?? []).find(
      (session) => session.id === currentSession.parent_session_id,
    );
    return parent
      ? [{ id: parent.id, label: "原会话", active: false }]
      : [];
  }, [currentSession, sessionId, sessions.data]);
  const sessionIsClosed = currentSession?.status === "closed";
  const isEmptySession = messages.length === 0;
  const latestAssistantMessage = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index].role === "assistant") return messages[index];
    }
    return undefined;
  }, [messages]);
  const suggestionAnchor = isEmptySession
    ? null
    : latestAssistantMessage?.status === "completed"
      ? latestAssistantMessage
      : undefined;
  const completedMessageFingerprint = useMemo(
    () =>
      messages
        .filter((message) => message.status === "completed")
        .map(
          (message) =>
            `${message.id}:${message.version}:${message.status}`,
        )
        .join("|"),
    [messages],
  );
  const suggestedPromptsEnabled = areChatSuggestedPromptsEnabled(settings.data);
  const contextUsageEnabled = isChatContextUsageEnabled(settings.data);
  // 仅在持久化时间线变化时重取上下文估算，流式增量不触发请求。
  const persistedTimelineStamp = useMemo(() => {
    const persisted = history.data ?? [];
    if (!persisted.length) return "0";
    const last = persisted[persisted.length - 1];
    return `${persisted.length}:${last.id}:${last.status}`;
  }, [history.data]);
  const contextUsage = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "context-usage",
      sessionId,
      activeModelProvider?.id ?? "",
      selectedModel?.id ?? "",
      responseMode === "agentic",
      persistedTimelineStamp,
    ),
    queryFn: () =>
      getSessionContextUsage(sessionId, {
        provider_id: activeModelProvider?.id,
        model_id: selectedModel?.id,
        agent_mode: responseMode === "agentic",
      }),
    enabled:
      contextUsageEnabled &&
      sessionId !== "new" &&
      generationMode === "text" &&
      Boolean(activeModelProvider),
    staleTime: 30_000,
  });
  const autoTitleModel = useMemo(
    () =>
      readChatFeatureModelSetting(
        settings.data,
        CHAT_AUTO_TITLE_MODEL_SETTING_KEY,
      ),
    [settings.data],
  );
  const suggestedPromptsModel = useMemo(
    () =>
      readChatFeatureModelSetting(
        settings.data,
        CHAT_SUGGESTED_PROMPTS_MODEL_SETTING_KEY,
      ),
    [settings.data],
  );
  const dictationCleanupModel = useMemo(
    () =>
      readChatFeatureModelSetting(
        settings.data,
        CHAT_DICTATION_CLEANUP_MODEL_SETTING_KEY,
      ),
    [settings.data],
  );
  const canPrepareSuggestedPrompts = Boolean(
    !goalMode &&
      settings.isSuccess &&
      suggestedPromptsEnabled &&
      sessions.isSuccess &&
      currentSession &&
      history.isSuccess &&
      sessionId !== "new" &&
      !sessionIsClosed &&
      status === "ready" &&
      suggestionAnchor !== undefined,
  );
  const suggestionAnchorVersions = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "message-versions",
      suggestionAnchor?.session_id ?? sessionId,
      suggestionAnchor?.id ?? null,
    ),
    queryFn: () =>
      listMessageVersions(suggestionAnchor!.session_id, suggestionAnchor!.id),
    enabled: Boolean(canPrepareSuggestedPrompts && suggestionAnchor),
    retry: false,
  });
  const suggestionAnchorVersion =
    suggestionAnchor === null
      ? null
      : suggestionAnchorVersions.data?.find(
          (version) =>
            version.version === suggestionAnchor?.version &&
            version.status === "completed",
        );
  const expectedSuggestionAnchorId = suggestionAnchor?.id ?? null;
  const expectedSuggestionAnchorVersionId = suggestionAnchorVersion?.id ?? null;
  const suggestionAnchorReady = Boolean(
    suggestionAnchor === null || suggestionAnchorVersion,
  );
  const suggestionAnchorError = Boolean(
    suggestionAnchor &&
      (suggestionAnchorVersions.isError ||
        (suggestionAnchorVersions.isSuccess && !suggestionAnchorVersion)),
  );
  const canReadSuggestedPrompts = Boolean(
    canPrepareSuggestedPrompts && suggestionAnchorReady,
  );
  const suggestionQueryContext = [
    sessionId,
    completedMessageFingerprint,
    suggestionAnchor?.id ?? null,
    suggestionAnchor?.version ?? 0,
    expectedSuggestionAnchorVersionId,
    activeModelProvider?.id ?? null,
    selectedModel?.id ?? null,
  ] as const;
  const persistedSuggestedPrompts = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "suggested-prompts",
      "persisted",
      ...suggestionQueryContext,
    ),
    queryFn: async ({ signal }) =>
      (await getSessionSuggestedPrompts(sessionId, { signal })) ?? null,
    enabled: canReadSuggestedPrompts,
    retry: false,
  });
  const persistedSuggestionBatch =
    persistedSuggestedPrompts.data?.session_id === sessionId &&
    persistedSuggestedPrompts.data.anchor_message_id ===
      expectedSuggestionAnchorId &&
    persistedSuggestedPrompts.data.anchor_message_version_id ===
      expectedSuggestionAnchorVersionId
      ? persistedSuggestedPrompts.data
      : undefined;
  const remoteSuggestionProviderAvailable = Boolean(
    activeModelProvider?.id && selectedModel?.id && selectedModel.remote,
  );
  const suggestionProviderPending = Boolean(
    providers.isPending ||
      (activeModelProvider &&
        (discoveredModels?.isPending ||
          (modelOptions.length > 0 && !selectedModel))),
  );
  const suggestionProviderError = Boolean(
    providers.isError ||
      (!remoteSuggestionProviderAvailable && discoveredModels?.isError),
  );
  const canGenerateSuggestedPrompts = Boolean(
    canReadSuggestedPrompts &&
      persistedSuggestedPrompts.isSuccess &&
      !persistedSuggestionBatch &&
      remoteSuggestionProviderAvailable,
  );
  const generatedSuggestedPrompts = useQuery({
    queryKey: workspaceQueryKey(
      workspaceId,
      "suggested-prompts",
      "generated",
      ...suggestionQueryContext,
      suggestedPromptsModel.provider_id,
      suggestedPromptsModel.model_id,
    ),
    queryFn: ({ signal }) =>
      generateSessionSuggestedPrompts(
        sessionId,
        {
          anchor_message_id: suggestionAnchor?.id ?? null,
          anchor_message_version_id: expectedSuggestionAnchorVersionId,
          provider_id:
            suggestedPromptsModel.provider_id ?? activeModelProvider!.id,
          model_id: suggestedPromptsModel.model_id ?? selectedModel!.id,
          count: 2,
        },
        { signal },
      ),
    enabled: canGenerateSuggestedPrompts,
    retry: false,
    staleTime: Number.POSITIVE_INFINITY,
  });
  const generatedSuggestionBatch =
    generatedSuggestedPrompts.data?.session_id === sessionId &&
    generatedSuggestedPrompts.data.anchor_message_id ===
      expectedSuggestionAnchorId &&
    generatedSuggestedPrompts.data.anchor_message_version_id ===
      expectedSuggestionAnchorVersionId
      ? generatedSuggestedPrompts.data
      : undefined;
  const promptSuggestions =
    (generatedSuggestionBatch ?? persistedSuggestionBatch)?.prompts ?? [];
  const emptySessionDisplayPrompts =
    promptSuggestions.length > 0
      ? promptSuggestions
      : memoryFallbackPrompts;
  const suggestionAnchorPending = Boolean(
    suggestionAnchor !== null &&
      (suggestionAnchorVersions.isPending || suggestionAnchorVersions.isFetching),
  );
  const persistedSuggestionPending = Boolean(
    canReadSuggestedPrompts &&
      (persistedSuggestedPrompts.isPending ||
        persistedSuggestedPrompts.isFetching ||
        (!persistedSuggestionBatch &&
          !persistedSuggestedPrompts.isError &&
          (suggestionProviderPending ||
            (canGenerateSuggestedPrompts &&
              (generatedSuggestedPrompts.isPending ||
                generatedSuggestedPrompts.isFetching))))),
  );
  const suggestedPromptsPending = Boolean(
    canPrepareSuggestedPrompts &&
      (suggestionAnchorPending || persistedSuggestionPending),
  );
  const suggestedPromptsError = shouldShowSuggestedPromptError({
    canPrepare: canPrepareSuggestedPrompts,
    canRead: canReadSuggestedPrompts,
    anchorError: suggestionAnchorError,
    persistedReadError: persistedSuggestedPrompts.isError,
    hasPersistedBatch: Boolean(persistedSuggestionBatch),
    providerError: suggestionProviderError,
    canGenerate: canGenerateSuggestedPrompts,
    generationError: generatedSuggestedPrompts.isError,
  });
  const suggestedPromptsUnavailable = Boolean(
    canReadSuggestedPrompts &&
      persistedSuggestedPrompts.isSuccess &&
      !persistedSuggestionBatch &&
      !suggestionProviderPending &&
      !suggestionProviderError &&
      !remoteSuggestionProviderAvailable,
  );
  const retrySuggestedPrompts = () => {
    if (suggestionAnchorError) {
      void suggestionAnchorVersions.refetch();
    } else if (persistedSuggestedPrompts.isError) {
      void persistedSuggestedPrompts.refetch();
    } else if (providers.isError) {
      void providers.refetch();
    } else if (
      !remoteSuggestionProviderAvailable &&
      discoveredModels?.isError
    ) {
      void discoveredModels?.refetch();
    } else {
      void generatedSuggestedPrompts.refetch();
    }
  };
  const showSuggestedPromptState =
    canPrepareSuggestedPrompts &&
    (suggestedPromptsPending ||
      suggestedPromptsError ||
      suggestedPromptsUnavailable ||
      promptSuggestions.length > 0);
  const graphTitle = currentSession?.graph_id
    ? graphs.data?.find((graph) => graph.id === currentSession.graph_id)?.title
    : undefined;
  const firstLocalUserMessageId = useMemo(
    () =>
      messages.find(
        (message) =>
          message.session_id === sessionId && message.role === "user",
      )?.id,
    [messages, sessionId],
  );
  const rememberCreatedSession = useCallback(
    (session: Session) => {
      // Seed workspace default response mode before the chat route hydrates so a
      // brand-new draft never inherits a previous session's "极速" via localStorage.
      if (!hasSessionComposerPrefs(session.id)) {
        setSessionComposerPrefs(
          session.id,
          defaultComposerPrefsForResponseMode(workspaceDefaultResponseMode),
        );
      }
      queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) => [
        session,
        ...(current ?? []).filter((item) => item.id !== session.id),
      ]);
      window.dispatchEvent(
        new CustomEvent("learngraph:session-created", { detail: { session } }),
      );
    },
    [queryClient, workspaceDefaultResponseMode],
  );
  useEffect(() => {
    if (sessionId !== "new" || goalMode) return;
    let cancelled = false;

    // Prefer reusing the single unused empty draft so /chat/new never multiplies.
    const existingDraftId = getDraftSessionId();
    if (existingDraftId) {
      const cached = (
        queryClient.getQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions")) ?? []
      ).find((session) => session.id === existingDraftId);
      if (cached) {
        // Blank drafts re-opened via /chat/new adopt the current workspace
        // default, clearing any mode stamped by a previous session switch race.
        setSessionComposerPrefs(
          existingDraftId,
          defaultComposerPrefsForResponseMode(workspaceDefaultResponseMode),
        );
        preserveDraftForSessionRef.current = existingDraftId;
        navigate(`/w/${workspaceId}/chat/${existingDraftId}`, {
          replace: true,
        });
        return;
      }
      const reusePromise = listSessions()
        .then((sessions) => {
          queryClient.setQueryData(workspaceQueryKey(workspaceId, "sessions"), sessions);
          return sessions.find((session) => session.id === existingDraftId);
        })
        .then((session) => {
          if (cancelled) return;
          if (session) {
            setSessionComposerPrefs(
              session.id,
              defaultComposerPrefsForResponseMode(workspaceDefaultResponseMode),
            );
            preserveDraftForSessionRef.current = session.id;
            navigate(`/w/${workspaceId}/chat/${session.id}`, {
              replace: true,
            });
            return;
          }
          clearDraftSessionId(existingDraftId);
          return createSession({ memory_enabled: true, title: "新会话" }).then(
            (created) => {
              if (cancelled) return;
              rememberCreatedSession(created);
              setDraftSessionId(created.id);
              preserveDraftForSessionRef.current = created.id;
              navigate(`/w/${workspaceId}/chat/${created.id}`, {
                replace: true,
              });
            },
          );
        });
      void reusePromise.catch((error: unknown) => {
        if (!cancelled) {
          toast.error(
            error instanceof Error ? error.message : "无法创建新会话",
          );
        }
      });
      return () => {
        cancelled = true;
      };
    }

    let attempt = draftSessionCreationRef.current;
    if (!attempt || attempt.locationKey !== location.key) {
      attempt = {
        locationKey: location.key,
        promise: createSession({ memory_enabled: true, title: "新会话" }),
      };
      draftSessionCreationRef.current = attempt;
    }
    const currentAttempt = attempt;
    void currentAttempt.promise
      .then((session) => {
        if (cancelled) return;
        rememberCreatedSession(session);
        setDraftSessionId(session.id);
        preserveDraftForSessionRef.current = session.id;
        navigate(`/w/${workspaceId}/chat/${session.id}`, { replace: true });
      })
      .catch((error: unknown) => {
        if (draftSessionCreationRef.current === currentAttempt) {
          draftSessionCreationRef.current = null;
        }
        if (!cancelled) {
          toast.error(
            error instanceof Error ? error.message : "无法创建新会话",
          );
        }
      });
    return () => {
      cancelled = true;
    };
  }, [
    goalMode,
    location.key,
    navigate,
    queryClient,
    rememberCreatedSession,
    sessionId,
    workspaceDefaultResponseMode,
    workspaceId,
  ]);
  const completeGoalSetup = useCallback(
    async ({ goal, graph }: { goal: Goal; graph: Graph }) => {
      try {
        const canReuseCurrentSession = Boolean(
          sessionId !== "new" &&
            currentSession?.status === "active" &&
            !currentSession.goal_id &&
            !currentSession.graph_id,
        );
        const learningSession = canReuseCurrentSession
          ? await updateSession(sessionId, {
              goal_id: goal.id,
              graph_id: graph.id,
              title: goal.title,
            })
          : await createSession({
              goal_id: goal.id,
              graph_id: graph.id,
              memory_enabled: true,
              title: goal.title,
            });
        if (canReuseCurrentSession) {
          queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) =>
            current
              ? current.map((item) =>
                  item.id === learningSession.id ? learningSession : item,
                )
              : [learningSession],
          );
        } else {
          rememberCreatedSession(learningSession);
        }
        setLocalMessages([]);
        setPendingFiles([]);
        setComposerText("");
        navigate(`/w/${workspaceId}/chat/${learningSession.id}`, {
          replace: true,
        });
        toast.success("目标图谱已发布，已进入学习会话。");
      } catch (error) {
        toast.error(
          `图谱已发布，但学习会话创建失败：${
            error instanceof Error ? error.message : "未知错误"
          }`,
        );
        navigate(`/w/${workspaceId}/graphs/${graph.id}`, { replace: true });
      }
    },
    [
      currentSession,
      navigate,
      queryClient,
      rememberCreatedSession,
      sessionId,
      workspaceId,
    ],
  );
  const goalFlow = useGoalSetupFlow({
    enabled: goalMode,
    onPublished: completeGoalSetup,
    scopeKey: conversationResetKey,
    workspaceId,
  });
  useEffect(() => {
    if (!goalMode) return;
    window.dispatchEvent(
      new CustomEvent("learngraph:goal-graph-preview", {
        detail: { composerText },
      }),
    );
  }, [composerText, goalMode]);
  const closeSessionMutation = useMutation({
    mutationFn: () => {
      if (!currentSession || sessionId === "new") {
        throw new Error("请先创建学习会话后再结束并复盘。");
      }
      return closeSession(currentSession.id);
    },
    onSuccess: async (closedSession) => {
      queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) =>
        current
          ? current.map((item) =>
              item.id === closedSession.id ? closedSession : item,
            )
          : [closedSession],
      );
      setCloseDialogOpen(false);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "sessions") }),
        // The session is closed — evict its message history and per-message
        // derived caches rather than just marking them stale. invalidate keeps
        // the (potentially large) cached message array resident; remove drops
        // it so a closed session stops holding memory, while re-opening simply
        // re-fetches. removeQueries returns void; wrap so Promise.all is uniform.
        Promise.resolve(
          queryClient.removeQueries({ queryKey: workspaceQueryKey(workspaceId, "messages", closedSession.id) }),
        ),
        Promise.resolve(
          queryClient.removeQueries({
            queryKey: workspaceQueryKey(workspaceId, "message-versions", closedSession.id),
          }),
        ),
        Promise.resolve(
          queryClient.removeQueries({
            queryKey: workspaceQueryKey(workspaceId, "message-snapshot", closedSession.id),
          }),
        ),
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "evidence") }),
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "mastery") }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "mastery-review-jobs"),
        }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "mastery-session-states"),
        }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "mastery-schedules"),
        }),
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "dashboard") }),
        closedSession.goal_id
          ? queryClient.invalidateQueries({
              queryKey: workspaceQueryKey(workspaceId, "roadmap", closedSession.goal_id),
            })
          : Promise.resolve(),
      ]);
      toast.success("学习会话已结束，复盘已触发。");
    },
    onError: (error) =>
      toast.error(
        error instanceof Error ? error.message : "结束学习失败，请稍后重试。",
      ),
  });
  useEffect(() => {
    window.dispatchEvent(
      new CustomEvent("learngraph:chat-header", {
        detail: {
          canClose:
            sessionId !== "new" &&
            Boolean(currentSession) &&
            !sessionIsClosed &&
            status !== "streaming" &&
            status !== "submitted" &&
            !closeSessionMutation.isPending,
          graphTitle,
          modelConnected: Boolean(activeGenerationProvider),
          sessionClosed: sessionIsClosed,
          sessionTitle: currentSession?.title ?? "新会话",
        },
      }),
    );
  }, [
    activeGenerationProvider,
    closeSessionMutation.isPending,
    currentSession,
    graphTitle,
    sessionId,
    sessionIsClosed,
    status,
  ]);
  useEffect(() => {
    const requestClose = () => {
      if (
        sessionId !== "new" &&
        currentSession &&
        !sessionIsClosed &&
        status !== "streaming" &&
        status !== "submitted" &&
        !closeSessionMutation.isPending
      ) {
        setCloseDialogOpen(true);
      }
    };
    window.addEventListener("learngraph:close-session-requested", requestClose);
    return () =>
      window.removeEventListener(
        "learngraph:close-session-requested",
        requestClose,
      );
  }, [
    closeSessionMutation.isPending,
    currentSession,
    sessionId,
    sessionIsClosed,
    status,
  ]);
  const userVersionNavigation = useCallback(
    (message: Message): MessageVersionNavigation | undefined => {
      const branchParentSessionId = currentSession?.parent_session_id;
      const branchSourceMessageId = currentSession?.source_message_id;
      const isEditedBranchRoot = Boolean(
        branchParentSessionId &&
        branchSourceMessageId &&
        message.session_id === sessionId &&
        message.id === firstLocalUserMessageId,
      );
      const sourceSessionId = isEditedBranchRoot
        ? branchParentSessionId!
        : message.session_id === sessionId
          ? sessionId
          : undefined;
      const sourceMessageId = isEditedBranchRoot
        ? branchSourceMessageId!
        : message.session_id === sessionId
          ? message.id
          : undefined;
      if (!sourceSessionId || !sourceMessageId) return undefined;
      const branchSessionIds = (sessions.data ?? [])
        .filter(
          (session) =>
            session.parent_session_id === sourceSessionId &&
            session.source_message_id === sourceMessageId,
        )
        .sort((left, right) => left.created_at.localeCompare(right.created_at))
        .map((session) => session.id);
      const sessionIds = [sourceSessionId, ...branchSessionIds];
      const currentIndex = sessionIds.indexOf(sessionId);
      if (sessionIds.length <= 1 || currentIndex < 0) return undefined;
      return {
        currentIndex,
        total: sessionIds.length,
        onPrevious: () =>
          navigate(`/w/${workspaceId}/chat/${sessionIds[currentIndex - 1]}`),
        onNext: () =>
          navigate(`/w/${workspaceId}/chat/${sessionIds[currentIndex + 1]}`),
      };
    },
    [
      currentSession?.parent_session_id,
      currentSession?.source_message_id,
      firstLocalUserMessageId,
      navigate,
      sessionId,
      sessions.data,
      workspaceId,
    ],
  );

  const ensureAgentSandboxReady = useCallback(async (): Promise<boolean> => {
    // R-007: agentic chat must still run without Docker. Sandbox tools degrade
    // server-side; only warn here so non-sandbox agent work is not blocked.
    try {
      const readiness = await getAgentSandboxReadiness();
      if (readiness.available) return true;
      toast.message("沙箱工具暂不可用", {
        description: [
          readiness.message || "本机 Docker/沙箱未就绪。",
          "智能体对话仍可继续；文件执行与代码沙箱工具会暂时不可用。",
          readiness.remediation_steps[0],
        ]
          .filter(Boolean)
          .join(" "),
      });
      return true;
    } catch (error) {
      toast.message("无法检查智能体沙箱状态", {
        description:
          error instanceof Error
            ? `${error.message} 将继续发送；沙箱工具可能不可用。`
            : "将继续发送；沙箱工具可能不可用。",
      });
      return true;
    }
  }, []);

  const send = useCallback(
    async (
      contentValue: string,
      options: {
        fileIds?: string[];
        attachmentFiles?: FileRecord[];
        graphAction?: GraphAction;
        generationMode?: GenerationMode;
        imageProviderId?: string;
        imageModelId?: string;
        imageSize?: ImageSize;
        sourceFileIds?: string[];
        sandboxPreflighted?: boolean;
        selectionContext?: MessageSelectionContext | null;
      } = {},
    ) => {
      const content = contentValue.trim();
      if (!content) return;
      // Concurrent sessions: only block while THIS session is generating.
      if (isSessionStreaming(sessionId)) return;
      if (
        (status === "streaming" || status === "submitted") &&
        activeStreamSessionId.current === sessionId
      )
        return;
      if (closeSessionMutation.isPending) {
        toast.error("正在结束学习会话，请稍候。");
        return;
      }
      if (sessionIsClosed) {
        toast.error("该学习会话已结束。请创建分支或新会话继续学习。");
        return;
      }
      const requestedGenerationMode = options.generationMode ?? generationMode;
      if (
        requestedGenerationMode === "text" &&
        responseMode === "agentic" &&
        !options.sandboxPreflighted
      ) {
        if (!(await ensureAgentSandboxReady())) return;
      }
      // Image retries can override the composer selection with an explicit
      // provider/model so switching models does not race React state updates.
      const overrideImageProvider =
        requestedGenerationMode === "image" && options.imageProviderId
          ? imageProviders.find(
              (provider) => provider.id === options.imageProviderId,
            )
          : undefined;
      const requestProvider =
        requestedGenerationMode === "image"
          ? overrideImageProvider ?? activeImageProvider
          : activeModelProvider;
      const requestModelId =
        requestedGenerationMode === "image"
          ? options.imageModelId ?? selectedImageModel?.id ?? ""
          : selectedModel?.id ?? "";
      if (!requestProvider || !requestModelId) {
        toast.error(
          requestedGenerationMode === "image"
            ? "当前工作区未启用带默认模型的真实绘图 Provider。"
            : "当前工作区未启用真实模型 Provider，请先到设置中完成配置。",
        );
        return;
      }
      if (requestedGenerationMode === "text" && !selectedModel?.remote) {
        toast.error("当前选择的模型没有可验证的远程能力。");
        return;
      }
      const requestedGraphAction =
        requestedGenerationMode === "image"
          ? "none"
          : options.graphAction ?? graphAction;
      if (
        requestedGraphAction === "propose_create" &&
        !currentSession?.goal_id
      ) {
        toast.error("当前会话尚未绑定已确认的学习目标，不能生成图谱提案。");
        return;
      }
      if (
        requestedGraphAction === "propose_update" &&
        !currentSession?.graph_id
      ) {
        toast.error("当前会话尚未绑定图谱，不能生成增量提案。");
        return;
      }
      const stamp = Date.now();
      const operationId = latestOperationId.current + 1;
      latestOperationId.current = operationId;
      const user: Message = {
        id: `temp-user-${stamp}`,
        workspace_id: workspaceId,
        session_id: sessionId,
        parent_message_id: null,
        role: "user",
        version: 1,
        status: "completed",
        content,
        parts: [
          ...optimisticAttachmentParts(options.attachmentFiles ?? []),
          {
            id: `temp-user-part-${stamp}`,
            type: "text",
            status: "completed",
            content,
          },
        ],
        provider_trace: {},
        created_at: new Date().toISOString(),
      };
      const assistant: Message = {
        id: `temp-assistant-${stamp}`,
        workspace_id: workspaceId,
        session_id: sessionId,
        parent_message_id: user.id,
        role: "assistant",
        version: 1,
        status: "streaming",
        content: "",
        parts:
          requestedGenerationMode === "image"
            ? [
                {
                  id: `temp-image-${stamp}`,
                  type: "image",
                  status: "pending",
                  data: {
                    optimistic: true,
                    title: options.sourceFileIds?.length
                      ? "正在编辑图片"
                      : "正在生成图片",
                    aspect_ratio:
                      (options.imageSize ?? imageSize) === "auto"
                        ? "4 / 3"
                        : (options.imageSize ?? imageSize).replace("x", " / "),
                  },
                },
              ]
            : [
                {
                  id: `temp-acknowledgement-${stamp}`,
                  type: "acknowledgement",
                  status: "pending",
                  content: "",
                },
              ],
        provider_trace: {},
        created_at: new Date().toISOString(),
      };
      setLocalMessages((current) => [...current, user, assistant]);
      setSelectionMenu(null);
      setStreamConnectionNotice(null);
      setStatus("submitted");
      let targetSessionId = sessionId;
      let targetSession = currentSession;
      if (sessionId === "new") {
        try {
          if (goalMode) {
            targetSession = await createSession({
              memory_enabled: true,
              title: "目标调研",
            });
          } else {
            let draftAttempt = draftSessionCreationRef.current;
            if (!draftAttempt || draftAttempt.locationKey !== location.key) {
              draftAttempt = {
                locationKey: location.key,
                promise: createSession({
                  memory_enabled: true,
                  title: "新会话",
                }),
              };
              draftSessionCreationRef.current = draftAttempt;
            }
            targetSession = await draftAttempt.promise;
          }
          targetSessionId = targetSession.id;
          optimisticSessionId.current = targetSessionId;
          setLocalMessages((current) =>
            current.map((message) =>
              message.id === user.id || message.id === assistant.id
                ? { ...message, session_id: targetSessionId }
                : message,
            ),
          );
          rememberCreatedSession(targetSession);
          navigate(
            `/w/${workspaceId}/chat/${targetSessionId}${goalMode ? "?mode=goal" : ""}`,
            { replace: true },
          );
        } catch (error) {
          setStatus("error");
          setLocalMessages((current) =>
            current.map((message) =>
              message.id === assistant.id
                ? {
                    ...message,
                    status: "failed",
                    parts: [
                      ...message.parts.map((part) => ({
                        ...part,
                        status: "failed" as const,
                      })),
                      {
                        id: `error-${stamp}`,
                        type: "error",
                        status: "failed",
                        content:
                          error instanceof Error
                            ? error.message
                            : "创建会话失败",
                      },
                    ],
                  }
                : message,
            ),
          );
          toast.error(error instanceof Error ? error.message : "创建会话失败");
          return;
        }
      }
      // Goal mode keeps its explicit "目标调研" workflow label.
      const shouldAutoTitle = Boolean(
        targetSession &&
          (targetSession.title === "新会话" ||
            targetSession.title === "新学习会话") &&
          (sessionId === "new" ||
            (history.isSuccess &&
              !messages.some(
                (message) =>
                  message.session_id === targetSessionId &&
                  message.role === "user",
              ))),
      );
      const activeLearningNode =
        requestedGenerationMode === "text" ? learningNodeRef.current : undefined;
      // First user message graduates the empty draft into a normal sidebar session.
      if (
        isDefaultDraftTitle(targetSession?.title) &&
        getDraftSessionId() === targetSessionId
      ) {
        clearDraftSessionId(targetSessionId);
      }
      window.dispatchEvent(
        new CustomEvent("learngraph:session-started", {
          detail: { sessionId: targetSessionId },
        }),
      );
      markSessionTouched(targetSessionId);
      const controller = new AbortController();
      abortRef.current = controller;
      activeStreamSessionId.current = targetSessionId;
      registerSessionStream(targetSessionId, controller, null);
      markSessionRunning(targetSessionId, true);
      const isViewingStream = () =>
        viewingSessionIdRef.current === targetSessionId;
      const selectionContext = options.selectionContext
        ? {
            ...options.selectionContext,
            selected_text: options.selectionContext.selected_text.slice(0, 500),
            prefix: options.selectionContext.prefix.slice(-500),
            suffix: options.selectionContext.suffix.slice(0, 500),
          }
        : undefined;
      const request: MessageCreateRequest = {
        content,
        generation_mode: requestedGenerationMode,
        image_size:
          requestedGenerationMode === "image"
            ? options.imageSize ?? imageSize
            : "auto",
        source_file_ids:
          requestedGenerationMode === "image" ? options.sourceFileIds ?? [] : [],
        file_ids:
          requestedGenerationMode === "image" ? [] : options.fileIds ?? [],
        node_ids:
          requestedGenerationMode === "text"
            ? activeLearningNode?.nodeIds?.slice(0, 8) ??
              (activeLearningNode?.nodeId ? [activeLearningNode.nodeId] : [])
            : [],
        provider_id: requestProvider.id,
        model_id: requestModelId,
        thinking_mode:
          requestedGenerationMode === "image" ? "off" : effectiveThinkingMode,
        agent_mode:
          requestedGenerationMode === "text" && responseMode === "agentic",
        goal_mode:
          requestedGenerationMode === "text" &&
          responseMode === "agentic" &&
          goalMode,
        // Agent mode no longer forces web_search just because a SearchProvider
        // exists. Network search follows the explicit searchRoute (and the
        // composer "联网" toggle), while agent tools still run without search.
        search_route:
          requestedGenerationMode === "image"
            ? "disabled"
            : responseMode === "agentic" &&
                !hasAuthorizedAgentSearchProvider &&
                searchRoute !== "model_native"
              ? "disabled"
              : searchRoute === "auto" ||
                  searchRoute === "external" ||
                  searchRoute === "local"
                ? hasAuthorizedAgentSearchProvider
                  ? searchRoute
                  : "disabled"
                : searchRoute,
        web_search:
          requestedGenerationMode === "text" &&
          searchRoute !== "disabled" &&
          (searchRoute === "model_native" || hasAuthorizedAgentSearchProvider),
        graph_action: requestedGraphAction,
        graph_id:
          requestedGraphAction === "propose_update"
            ? targetSession?.graph_id
            : undefined,
        selection_context:
          requestedGenerationMode === "text" ? selectionContext : undefined,
      };
      const idempotencyKey = `chat-${createUuid()}`;
      const seenEventIds = new Set<string>();
      let lastEventId: string | undefined;
      let completed = false;
      let persistedAssistantId = "";
      let persistedUserId = "";
      let autoTitleRequested = false;
      let autoTitlePromise: Promise<void> | null = null;
      const markPersistedId = (localId: string, persistedId: string) => {
        setLocalMessages((current) =>
          current.map((message) =>
            message.id === localId
              ? {
                  ...message,
                  provider_trace: {
                    ...message.provider_trace,
                    optimistic_persisted_message_id: persistedId,
                  },
                }
              : message,
          ),
        );
      };
      const reconcilePersisted = async (statuses: readonly string[]) => {
        if (!persistedUserId || !persistedAssistantId)
          throw new Error("服务端未返回完整的持久消息标识。");
        const [persistedUser, persistedAssistant] = await Promise.all([
          confirmedSessionMessages(targetSessionId, persistedUserId, ["completed"]),
          confirmedSessionMessages(
            targetSessionId,
            persistedAssistantId,
            statuses,
          ),
        ]);
        queryClient.setQueryData<Message[]>(
          workspaceQueryKey(workspaceId, "messages", targetSessionId),
          (current) => [
            ...(current ?? []).filter(
              (message) =>
                message.id !== persistedUser.id &&
                message.id !== persistedAssistant.id,
            ),
            persistedUser,
            persistedAssistant,
          ],
        );
        if (isViewingStream()) {
          optimisticSessionId.current = null;
        }
        setLocalMessages((current) =>
          current.filter(
            (message) => message.id !== user.id && message.id !== assistant.id,
          ),
        );
      };
      const retryPersistedReconciliation = () => {
        void (async () => {
          for (const delay of [250, 1_000, 3_000]) {
            await new Promise((resolve) => window.setTimeout(resolve, delay));
            try {
              await reconcilePersisted(TERMINAL_MESSAGE_STATUSES);
              if (isViewingStream()) setStatus("ready");
              return;
            } catch {
              // A later retry or normal query refresh can still reconcile it.
            }
          }
          void queryClient.invalidateQueries({
            queryKey: workspaceQueryKey(workspaceId, "messages", targetSessionId),
          });
        })();
      };
      // Always apply stream tokens into localMessages so returning to this
      // session mid-generation still shows the partial answer.
      const frameQueue = createAnimationFrameQueue<Record<string, unknown>>(
        (updates) =>
          setLocalMessages((current) =>
            current.map((message) =>
              message.id === assistant.id
                ? applyStreamUpdates(message, updates)
                : message,
            ),
          ),
      );
      try {
        for (
          let reconnectAttempt = 0;
          reconnectAttempt <= DEFAULT_STREAM_RECONNECTS;
          reconnectAttempt += 1
        ) {
          let transientError: unknown;
          let terminalFailure = "";
          try {
            for await (const event of streamSessionMessage(targetSessionId, request, {
              signal: controller.signal,
              seenEventIds,
              headers: {
                "Idempotency-Key": idempotencyKey,
                ...(lastEventId ? { "Last-Event-ID": lastEventId } : {}),
              },
            })) {
              if (event.id) lastEventId = event.id;
              if (isViewingStream()) {
                setStatus("streaming");
                setStreamConnectionNotice(null);
              }
              const data = streamData(event.data);
              const type = streamEventType(data);
              if (
                typeof data.message_id === "string" &&
                !persistedAssistantId
              ) {
                persistedAssistantId = data.message_id;
                if (isViewingStream()) {
                  activeMessageId.current = data.message_id;
                }
                setSessionStreamMessageId(targetSessionId, data.message_id);
                markPersistedId(assistant.id, persistedAssistantId);
              }
              const eventPayload =
                typeof data.payload === "object" && data.payload !== null
                  ? (data.payload as Record<string, unknown>)
                  : {};
              if (
                type === "message.accepted" &&
                typeof eventPayload.user_message_id === "string" &&
                !persistedUserId
              ) {
                persistedUserId = eventPayload.user_message_id;
                markPersistedId(user.id, persistedUserId);
              }
              if (
                type === "message.accepted" &&
                shouldAutoTitle &&
                requestedGenerationMode === "text" &&
                !autoTitleRequested
              ) {
                autoTitleRequested = true;
                const sourceMessageId = eventPayload.user_message_id;
                if (typeof sourceMessageId !== "string" || !sourceMessageId) {
                  toast.error(
                    "会话自动命名失败，服务端未返回首条消息标识。",
                  );
                } else {
                  autoTitlePromise = autoTitleSession(targetSessionId, {
                    source_message_id: sourceMessageId,
                    expected_title: targetSession!.title,
                    provider_id:
                      autoTitleModel.provider_id ?? requestProvider.id,
                    model_id: autoTitleModel.model_id ?? requestModelId,
                  })
                    .then(async (updatedSession) => {
                      queryClient.setQueryData<Session[]>(
                        workspaceQueryKey(workspaceId, "sessions"),
                        (current) => {
                          if (!current) return [updatedSession];
                          if (
                            !current.some(
                              (item) => item.id === updatedSession.id,
                            )
                          ) {
                            return [updatedSession, ...current];
                          }
                          return current.map((item) =>
                            item.id === updatedSession.id
                              ? { ...item, title: updatedSession.title }
                              : item,
                          );
                        },
                      );
                      await queryClient.invalidateQueries({
                        queryKey: workspaceQueryKey(workspaceId, "sessions"),
                      });
                    })
                    .catch(async (error: unknown) => {
                      if (
                        error instanceof ApiError &&
                        error.code === "session_title_changed"
                      ) {
                        await queryClient.invalidateQueries({
                          queryKey: workspaceQueryKey(workspaceId, "sessions"),
                        });
                        return;
                      }
                      toast.error(
                        "会话自动命名失败，已保留当前标题。",
                        {
                          description:
                            error instanceof Error
                              ? error.message
                              : "自动命名服务暂时不可用。",
                        },
                      );
                    });
                }
              }
              if (type === "message.completed") completed = true;
              if (type === "message.failed") {
                const errorPayload =
                  typeof eventPayload.error === "object" &&
                  eventPayload.error !== null
                    ? (eventPayload.error as Record<string, unknown>)
                    : typeof data.error === "object" && data.error !== null
                      ? (data.error as Record<string, unknown>)
                      : null;
                const errorCode =
                  typeof errorPayload?.code === "string"
                    ? errorPayload.code
                    : null;
                const errorMessage =
                  typeof errorPayload?.message === "string"
                    ? errorPayload.message
                    : null;
                if (errorCode === "document_context_too_large") {
                  terminalFailure =
                    errorMessage ??
                    "该文件全文超过极速/思考模式可安全读取的上下文。请切换到智能体模式，以通过沙箱分段读取完整文件。";
                  setResponseMode("agentic");
                  toast.message("已切换到智能体模式", {
                    description: "附件已保留，请再次发送问题以让智能体完整读取文件。",
                  });
                } else if (errorCode === "agent_invocation_limit_reached") {
                  terminalFailure =
                    errorMessage ??
                    "智能体工具调用轮次已达上限，请缩小问题范围或重试。";
                } else if (errorMessage) {
                  terminalFailure = `模型流在服务端失败：${errorMessage}`;
                } else {
                  terminalFailure =
                    "模型流在服务端失败，已保留失败状态与事件记录。";
                }
              }
              if (type === "message.cancelled")
                terminalFailure = "生成已取消。";
              expandStreamUpdate(data, {
                animate: isViewingStream(),
              }).forEach((update) => frameQueue.push(update));
            }
          } catch (error) {
            if (controller.signal.aborted) throw error;
            transientError = error;
          }

          if (completed) break;
          if (terminalFailure) {
            await frameQueue.drain();
            throw new Error(terminalFailure);
          }
          if (reconnectAttempt === DEFAULT_STREAM_RECONNECTS)
            throw transientError instanceof Error
              ? transientError
              : new Error("消息流意外中断，五次恢复均未完成。");
          if (isViewingStream()) {
            setStreamConnectionNotice({
              phase: "reconnecting",
              attempt: reconnectAttempt + 1,
              maxAttempts: DEFAULT_STREAM_RECONNECTS,
              detail: streamErrorDetail(transientError),
            });
          }
          await waitForStreamReconnect(reconnectAttempt, controller.signal);
        }
        await frameQueue.drain();
        if (!completed) throw new Error("消息流结束但没有收到完成事件。");
        if (autoTitlePromise) void autoTitlePromise;
        if (isViewingStream()) setGraphAction("none");
        try {
          await reconcilePersisted(["completed"]);
        } catch {
          if (isViewingStream()) {
            setStatus("ready");
            toast.message("回答已完成，消息记录正在后台同步。");
          }
          markSessionGenerationFinished(targetSessionId, {
            viewing: isViewingStream(),
          });
          retryPersistedReconciliation();
          return;
        }
        if (activeLearningNode?.nodeId) {
          await queryClient.invalidateQueries({
            queryKey: workspaceQueryKey(
              workspaceId,
              "node-questions",
              activeLearningNode.graphId,
              activeLearningNode.nodeId,
            ),
          });
          // Mastery stars / evidence may have moved; refresh rail graph cards.
          await queryClient.invalidateQueries({
            queryKey: workspaceQueryKey(workspaceId, "graph", activeLearningNode.graphId),
          });
        }
        markSessionGenerationFinished(targetSessionId, {
          viewing: isViewingStream(),
        });
        if (isViewingStream()) setStatus("ready");
        if (isViewingStream()) setStreamConnectionNotice(null);
        void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "sessions") });
      } catch (error) {
        if (controller.signal.aborted) {
          const cancellation = activeCancellationRef.current;
          if (cancellation) await cancellation;
          frameQueue.clear();
          if (isViewingStream()) {
            setLocalMessages((current) =>
              current.map((message) =>
                message.id === assistant.id
                  ? {
                      ...message,
                      status: "cancelled",
                      parts: message.parts.map((part) => ({
                        ...part,
                        status: "failed" as const,
                      })),
                    }
                  : message,
              ),
            );
          }
          try {
            await reconcilePersisted(["cancelled"]);
          } catch {
            retryPersistedReconciliation();
          }
          markSessionRunning(targetSessionId, false);
          if (isViewingStream()) setStatus("ready");
        }
        else {
          await frameQueue.drain();
          if (persistedAssistantId) {
            try {
              await reconcilePersisted(TERMINAL_MESSAGE_STATUSES);
              markSessionGenerationFinished(targetSessionId, {
                viewing: isViewingStream(),
              });
              if (isViewingStream()) {
                setStatus("ready");
                setStreamConnectionNotice({
                  phase: "failed",
                  attempt: DEFAULT_STREAM_RECONNECTS,
                  maxAttempts: DEFAULT_STREAM_RECONNECTS,
                  detail: streamErrorDetail(error),
                });
              }
              return;
            } catch {
              // Preserve the streamed failure below until persistence catches up.
            }
          }
          markSessionGenerationFinished(targetSessionId, {
            viewing: isViewingStream(),
          });
          if (isViewingStream()) {
            setStatus("error");
            setStreamConnectionNotice({
              phase: "failed",
              attempt: DEFAULT_STREAM_RECONNECTS,
              maxAttempts: DEFAULT_STREAM_RECONNECTS,
              detail: streamErrorDetail(error),
            });
            setLocalMessages((current) =>
              current.map((message) =>
                message.id === assistant.id
                  ? {
                      ...message,
                      status: "failed",
                      parts: [
                        ...message.parts.map((part) =>
                          part.data?.optimistic === true
                            ? { ...part, status: "failed" as const }
                            : part,
                        ),
                        {
                          id: `error-${stamp}`,
                          type: "error",
                          status: "failed",
                          content:
                            error instanceof Error ? error.message : "消息流失败",
                        },
                      ],
                    }
                  : message,
              ),
            );
          }
          retryPersistedReconciliation();
        }
      } finally {
        clearSessionStream(targetSessionId, controller);
        if (abortRef.current === controller) {
          abortRef.current = null;
          activeMessageId.current = null;
          activeStreamSessionId.current = null;
        }
      }
    },
    [
      queryClient,
      activeModelProvider,
      activeImageProvider,
      autoTitleModel,
      currentSession,
      generationMode,
      graphAction,
      goalMode,
      hasAuthorizedAgentSearchProvider,
      history.isSuccess,
      imageProviders,
      imageSize,
      location.key,
      messages,
      selectedModel?.id,
      selectedModel?.remote,
      selectedImageModel?.id,
      responseMode,
      effectiveThinkingMode,
      ensureAgentSandboxReady,
      searchRoute,
      sessionId,
      navigate,
      rememberCreatedSession,
      closeSessionMutation.isPending,
      sessionIsClosed,
      status,
      workspaceId,
    ],
  );

  const prepareStoredFile = useCallback(
    async (file: FileRecord, agentMode: boolean) => {
      if (
        isImageNameOrMime(file.original_name, file.mime_type) ||
        isVideoNameOrMime(file.original_name, file.mime_type)
      )
        return file;
      if (isAudioNameOrMime(file.original_name, file.mime_type)) {
        if (agentMode) return file;
        const prior = await listAudioTranscriptions(file.id);
        const completed = prior.find(
          (item) =>
            item.status === "completed" && item.transcript.trim().length > 0,
        );
        if (completed) return file;
        toast.message(`正在为「${file.original_name}」自动转写…`);
        const transcription = await transcribeAudioFile(file.id, {
          provider_id: storedAudioTranscriptionProvider?.id,
          model_id: providerCapabilityString(
            storedAudioTranscriptionProvider,
            "default_transcription_model_id",
          ),
        });
        if (
          transcription.status !== "completed" ||
          !transcription.transcript.trim()
        ) {
          throw new Error(
            `音频「${file.original_name}」自动转写失败：${transcription.error_message ?? transcription.status}`,
          );
        }
        toast.success(`「${file.original_name}」转写完成，将引用转写文本`);
        return file;
      }
      if (file.parse_status === "indexed") return file;
      if (
        file.parse_capability === "attachment_only" ||
        file.original_name.toLowerCase().endsWith(".ppt")
      ) {
        if (!agentMode) {
          throw new Error(
            `「${file.original_name}」当前无法建立文本索引。请切换到智能体模式，或者删除不受支持的文件后再发送。`,
          );
        }
        return file;
      }
      try {
        return await parseFile(file.id);
      } catch (error) {
        if (agentMode && error instanceof ApiError) {
          toast.message(
            `“${file.original_name}”已安全存储，但解析失败：${error.message}。将作为原始文件附到本轮。`,
          );
          return file;
        }
        throw new Error(
          `“${file.original_name}”解析失败或为旧格式：${error instanceof Error ? error.message : "未知错误"}。请切换到智能体模式，或者删除该文件后再发送。`,
        );
      } finally {
        await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "files") });
      }
    },
    [queryClient, storedAudioTranscriptionProvider],
  );

  const uploadAndIndex = useCallback(
    async (file: File, options?: { agentMode?: boolean }) => {
      const agentMode = options?.agentMode ?? responseMode === "agentic";
      let stored: FileRecord | null = null;
      try {
        const digest = await hashFileSha256(file);
        if (digest) {
          stored = await lookupFile({ name: file.name, sha256: digest });
          toast.message(`已复用资料库文件「${stored.original_name}」`);
        }
      } catch (error) {
        if (!(error instanceof ApiError && error.status === 404)) {
          // Lookup and browser-digest failures fall back to normal upload.
        }
      }
      if (!stored) stored = await uploadFile(file);
      await queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "files") });
      return prepareStoredFile(stored, agentMode);
    },
    [prepareStoredFile, queryClient, responseMode],
  );

  const convertLongPaste = useMutation({
    mutationFn: (content: string) => {
      const timestamp = new Date().toISOString().replaceAll(/[:.]/g, "-");
      return uploadAndIndex(
        new File([content], `chat-note-${timestamp}.md`, {
          type: "text/markdown",
        }),
      );
    },
    onSuccess: (file) => {
      setPendingFiles((current) => [
        ...current.filter((item) => item.id !== file.id),
        file,
      ]);
      setComposerText((current) =>
        longPaste ? current.replace(longPaste, "").trim() : current,
      );
      setLongPaste(null);
      toast.success(`“${file.original_name}”已解析，将随下一条消息发送`);
    },
    onError: (error) => toast.error(error.message),
  });

  const submitPrompt = useCallback(
    async (message: PromptInputMessage) => {
      try {
        const hasImageMention = /^\s*@绘图(?=\s|$)/u.test(message.text);
        const requestedGenerationMode: GenerationMode =
          hasImageMention || generationMode === "image" ? "image" : "text";
        if (
          requestedGenerationMode === "text" &&
          responseMode !== "agentic"
        ) {
          const blockedNames = [
            ...message.files
              .filter(
                (part) =>
                  !classifyNonAgentAttachment({
                    name: part.filename ?? "未命名附件",
                    mime: part.mediaType,
                    asrAvailable: storedAudioAsrAvailable,
                  }).ok,
              )
              .map((part) => part.filename ?? "未命名附件"),
            ...pendingFiles
              .filter(
                (file) =>
                  !classifyNonAgentAttachment({
                    name: file.original_name,
                    mime: file.mime_type,
                    asrAvailable: storedAudioAsrAvailable,
                  }).ok,
              )
              .map((file) => file.original_name),
          ];
          if (blockedNames.length) {
            toast.error(nonAgentAttachmentBlockedMessage(blockedNames));
            return false;
          }
        }
        if (
          requestedGenerationMode === "text" &&
          responseMode === "agentic" &&
          !goalMode &&
          !(await ensureAgentSandboxReady())
        ) {
          return false;
        }
        const contentText =
          requestedGenerationMode === "image"
            ? message.text.replace(/^\s*@绘图(?=\s|$)/u, "").trim()
            : message.text.trim();
        if (goalMode && requestedGenerationMode === "text" && !contentText) {
          toast.message(
            "请先补充目标描述。附件可以关联到 Goal，但当前澄清不会读取附件正文。",
          );
          return false;
        }
        if (
          requestedGenerationMode === "image" &&
          (!activeImageProvider || !selectedImageModel)
        ) {
          toast.error("请先启用绘图 Provider 并选择已配置的文生图模型。");
          return false;
        }
        if (
          requestedGenerationMode === "image" &&
          pendingFiles.some(
            (file) => !IMAGE_EDIT_MIME_TYPES.has(file.mime_type.toLowerCase()),
          )
        ) {
          toast.error("绘图模式的参考附件只能是图片。");
          return false;
        }
        if (
          requestedGenerationMode === "image" &&
          (message.files.length > 0 || pendingFiles.length > 0) &&
          !isImageEditModel(selectedImageModel)
        ) {
          toast.error("图生图和图片编辑仅支持支持参考图的绘图模型。");
          return false;
        }
        if (
          requestedGenerationMode === "image" &&
          message.files.some(
            (part) =>
              !IMAGE_EDIT_MIME_TYPES.has(
                (part.mediaType ?? "").toLowerCase(),
              ),
          )
        ) {
          toast.error("绘图模式的参考附件只能是图片。");
          return false;
        }
        const preparedPendingFiles =
          requestedGenerationMode === "text"
            ? await Promise.all(
                pendingFiles.map((file) =>
                  prepareStoredFile(file, responseMode === "agentic"),
                ),
              )
            : pendingFiles;
        const uploaded = await Promise.all(
          message.files.map(async (part, index) => {
            if (!part.url && !part.localFile)
              throw new Error(
                `附件 ${part.filename ?? index + 1} 缺少可读取内容`,
              );
            const blob = part.localFile
              ? part.localFile
              : await attachmentUrlToBlob(part.url!).catch((error) => {
              throw new Error(
                `无法读取附件 ${part.filename ?? index + 1}：${error instanceof Error ? error.message : "未知错误"}`,
              );
            });
            return uploadAndIndex(
              new File([blob], part.filename ?? `attachment-${index + 1}`, {
                type: part.mediaType || blob.type || "application/octet-stream",
              }),
              { agentMode: responseMode === "agentic" },
            );
          }),
        );
        const attachmentFiles = [...preparedPendingFiles, ...uploaded].filter(
          (file, index, files) =>
            files.findIndex((candidate) => candidate.id === file.id) === index,
        );
        const attachmentFileIds = attachmentFiles.map((file) => file.id);
        if (requestedGenerationMode === "image" && attachmentFileIds.length > 4) {
          toast.error("当前绘图模型最多支持 4 张参考图片。");
          return false;
        }
        const fileIds =
          requestedGenerationMode === "text" ? attachmentFileIds : [];
        const sourceFileIds =
          requestedGenerationMode === "image" ? attachmentFileIds : [];
        const content =
          contentText ||
          (fileIds.length ? "请阅读并结合附件内容回答。" : "");
        if (!content) {
          if (requestedGenerationMode === "image")
            toast.message("请补充要生成的画面描述。");
          return false;
        }
        if (goalMode && requestedGenerationMode === "text") {
          if (goalFlow.stage !== "capture" || goalFlow.busy) {
            toast.message("请先完成上方的目标审核。");
            return false;
          }
          if (fileIds.length) {
            toast.message(
              "附件将作为 Goal 关联资料保存；本轮澄清只依据你的文字描述。",
            );
          }
          await goalFlow.submit(content, fileIds);
          setPendingFiles([]);
          setComposerText("");
          return true;
        } else {
          const sending = send(content, {
            fileIds,
            attachmentFiles:
              requestedGenerationMode === "text" ? attachmentFiles : [],
            generationMode: requestedGenerationMode,
            imageSize,
            sourceFileIds,
            sandboxPreflighted:
              requestedGenerationMode === "text" && responseMode === "agentic",
          });
          setPendingFiles([]);
          setComposerText("");
          setGenerationMode("text");
          void sending.catch(() => undefined);
          return true;
        }
      } catch (error) {
        toast.error(error instanceof Error ? error.message : "附件处理失败");
        throw error;
      }
    },
    [
      activeImageProvider,
      ensureAgentSandboxReady,
      generationMode,
      goalFlow,
      goalMode,
      imageSize,
      pendingFiles,
      prepareStoredFile,
      responseMode,
      selectedImageModel,
      send,
      storedAudioAsrAvailable,
      uploadAndIndex,
    ],
  );

  const reviewGraphChange = useMutation({
    mutationFn: ({
      decision,
      proposalId,
      reason,
    }: {
      decision: "confirm" | "reject" | "undo";
      proposalId: string;
      reason?: string;
    }) => {
      if (decision === "confirm") {
        return confirmGraphChangeSet(sessionId, proposalId);
      }
      if (decision === "undo") {
        return undoGraphChangeSet(sessionId, proposalId);
      }
      return rejectGraphChangeSet(sessionId, proposalId, reason);
    },
    onSuccess: async (changeSet) => {
      // The server persists the resolved component snapshot, but updating the
      // active message cache first keeps the proposal controls from remaining
      // actionable while the refetch is in flight.
      const allowedEvents =
        changeSet.status === "confirmed"
          ? (["undo"] as string[])
          : ([] as string[]);
      queryClient.setQueryData<Message[]>(workspaceQueryKey(workspaceId, "messages", sessionId), (current) =>
        current?.map((message) => ({
          ...message,
          parts: message.parts.map((part) => {
            const component = part.data;
            const props = component?.props as Record<string, unknown> | undefined;
            if (
              part.type !== "component" ||
              component?.component_type !== "graph_update_proposal" ||
              !props ||
              props.proposal_id !== changeSet.id
            ) {
              return part;
            }
            return {
              ...part,
              data: {
                ...component,
                allowed_events: allowedEvents,
                props: {
                  ...props,
                  graph_id: changeSet.graph_id,
                  status: changeSet.status,
                  confirmed_revision: changeSet.confirmed_revision,
                  confirmation_required: false,
                  can_undo: changeSet.status === "confirmed",
                  rejection_reason: changeSet.rejection_reason,
                },
              },
            };
          }),
        })),
      );
      queryClient.setQueryData<Session[]>(workspaceQueryKey(workspaceId, "sessions"), (current) =>
        current?.map((item) =>
          item.id === sessionId
            ? {
                ...item,
                // Undo of a create proposal may clear session.graph_id; keep the
                // cache consistent with the change-set payload either way.
                graph_id:
                  changeSet.status === "undone" && !changeSet.graph_id
                    ? null
                    : (changeSet.graph_id ?? item.graph_id),
              }
            : item,
        ),
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "messages", sessionId) }),
        queryClient.invalidateQueries({
          queryKey: workspaceQueryKey(workspaceId, "graph-change-sets", sessionId),
        }),
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "sessions") }),
        queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "graphs") }),
        changeSet.graph_id
          ? queryClient.invalidateQueries({
              queryKey: workspaceQueryKey(workspaceId, "graph", changeSet.graph_id),
            })
          : Promise.resolve(),
      ]);
      setLocalMessages([]);
      setRejectProposalId(null);
      setRejectReason("");
      toast.success(
        changeSet.status === "confirmed"
          ? `图谱提案已写入修订 v${changeSet.confirmed_revision}`
          : changeSet.status === "undone"
            ? "图谱提案写入已撤销"
            : "图谱提案已拒绝，正式图谱未被修改",
      );
    },
    onError: (error) => toast.error(error.message),
  });

  const handleComponentAction = useCallback(
    (action: TrustedComponentAction) => {
      if (action.componentType === "graph_update_proposal") {
        const proposalId = action.payload.proposal_id;
        if (typeof proposalId !== "string" || !proposalId) {
          toast.error("提案缺少有效标识，未执行任何图谱修改。");
          return;
        }
        if (action.event === "confirm") {
          reviewGraphChange.mutate({ decision: "confirm", proposalId });
        } else if (action.event === "reject") {
          setRejectProposalId(proposalId);
          setRejectReason("");
        } else if (action.event === "undo") {
          reviewGraphChange.mutate({ decision: "undo", proposalId });
        }
        return;
      }

      if (action.event !== "submit" && action.event !== "create_plan" && action.event !== "open_plan") {
        return;
      }
      // Multi-question pager already formats a full summary with per-item grading.
      if (
        action.event === "submit" &&
        typeof action.payload.summaryText === "string" &&
        action.payload.summaryText.trim()
      ) {
        void send(action.payload.summaryText.trim(), { graphAction: "none" });
        return;
      }

      const labels = Array.isArray(action.payload.labels)
        ? action.payload.labels.filter(
            (item): item is string =>
              typeof item === "string" && Boolean(item.trim()),
          )
        : [];
      let answer =
        labels.join("、") ||
        (typeof action.payload.answer === "string"
          ? action.payload.answer
          : typeof action.payload.value === "string"
            ? action.payload.value
            : "");
      if (!answer) {
        if (action.event === "create_plan") {
          const location =
            typeof action.payload.location === "string"
              ? action.payload.location
              : "当前城市";
          const condition =
            typeof action.payload.condition === "string"
              ? action.payload.condition
              : "";
          const temperature =
            typeof action.payload.temperature_c === "number"
              ? `${action.payload.temperature_c}°C`
              : "";
          answer = `请根据${location}的天气（${condition} ${temperature}）生成明日学习计划`;
        } else if (action.event === "open_plan") {
          answer = "请根据卡片中的指标继续分析并给出下一步学习建议";
        } else {
          answer = "跳过该问题";
        }
      } else if (action.event === "submit") {
        answer = `我的回答：${answer}`;
      }
      void send(answer, { graphAction: "none" });
    },
    [reviewGraphChange, send],
  );

  useEffect(() => {
    if (preserveDraftForSessionRef.current === sessionId) {
      preserveDraftForSessionRef.current = null;
      setLocalMessages([]);
      return;
    }
    pendingHandled.current = false;
    if (optimisticSessionId.current !== sessionId) {
      optimisticSessionId.current = null;
    }
    // Keep concurrent-session optimistic rows in localMessages so background
    // streams continue painting; the messages memo only shows this session.
    setEditingMessageId(null);
    setEditingMessageContent("");
    setComposerInstanceKey(conversationResetKey);
    setComposerText("");
    setPendingFiles([]);
    setLongPaste(null);
    setGraphAction("none");
    // Keep workspace defaults for a fresh /chat/new canvas; existing sessions
    // restore their own prefs via the sessionId effect above.
    if (sessionId === "new") {
      const defaults = defaultComposerPrefsForResponseMode(
        workspaceDefaultResponseMode,
      );
      setResponseMode(defaults.responseMode);
      setThinkingMode(defaults.thinkingMode);
      setSearchRoute(defaults.searchRoute);
      setGenerationMode(defaults.generationMode);
    } else {
      // Do not force searchRoute to disabled when switching existing sessions —
      // the prefs restore effect owns that state.
    }
    setSelectionMenu(null);
    if (isSessionStreaming(sessionId)) {
      const handle = getSessionStream(sessionId);
      if (handle) {
        abortRef.current = handle.controller;
        activeStreamSessionId.current = sessionId;
        activeMessageId.current = handle.messageId;
      }
      setStatus("streaming");
    } else {
      // Detach UI control refs from any other session's background stream.
      abortRef.current = null;
      activeStreamSessionId.current = null;
      activeMessageId.current = null;
      setStatus("ready");
    }
    setRejectProposalId(null);
    setRejectReason("");
    setCloseDialogOpen(false);
    learningNodeRef.current = undefined;
    setLearningNode(undefined);
    clearLearningNodeContext();
  }, [conversationResetKey, sessionId, workspaceDefaultResponseMode]);

  useEffect(() => {
    const routeState = location.state as {
      pendingPrompt?: string;
      pendingFileIds?: string[];
      pendingGraphAction?: GraphAction;
      learningNode?: LearningNodeContext;
    } | null;
    if (routeState?.learningNode?.graphId) {
      learningNodeRef.current = routeState.learningNode;
      setLearningNode(routeState.learningNode);
      storeLearningNodeContext(routeState.learningNode);
    }
    const pendingPrompt = routeState?.pendingPrompt;
    if (
      pendingPrompt &&
      activeModelProvider &&
      !pendingHandled.current &&
      !history.isPending
    ) {
      pendingHandled.current = true;
      navigate(location.pathname, { replace: true });
      void send(pendingPrompt, {
        fileIds: routeState.pendingFileIds ?? [],
        graphAction: routeState.pendingGraphAction,
      });
    }
  }, [
    activeModelProvider,
    history.isPending,
    location.pathname,
    location.state,
    navigate,
    send,
  ]);

  useEffect(() => {
    const listener = (event: Event) => {
      const detail = (
        event as CustomEvent<{
          content?: string;
          autoSend?: boolean;
          graphAction?: GraphAction;
        }>
      ).detail;
      const content = detail?.content ?? "";
      if (!content.trim()) return;
      if (detail?.graphAction && detail.graphAction !== "none") {
        setGraphAction(detail.graphAction);
      }
      if (detail?.autoSend) {
        void send(content, {
          graphAction: detail.graphAction,
        });
        return;
      }
      // Fill composer only when autoSend is not requested.
      window.dispatchEvent(
        new CustomEvent("learngraph:composer-fill", {
          detail: { content },
        }),
      );
      void send(content, {
        graphAction: detail?.graphAction,
      });
    };
    window.addEventListener("learngraph:compose", listener);
    return () => window.removeEventListener("learngraph:compose", listener);
  }, [send]);

  useEffect(() => {
    const listener = () => {
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "messages", sessionId),
      });
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "sessions"),
      });
    };
    window.addEventListener("learngraph:refresh-messages", listener);
    return () => window.removeEventListener("learngraph:refresh-messages", listener);
  }, [queryClient, sessionId, workspaceId]);

  useEffect(() => {
    const selectLearningNode = (event: Event) => {
      const detail = (event as CustomEvent<LearningNodeContext>).detail;
      if (!detail || typeof detail.graphId !== "string") return;
      const previousLabel = learningNodeRef.current?.nodeLabel;
      learningNodeRef.current = detail;
      setLearningNode(detail);
      storeLearningNodeContext(detail);
      // Keep the composer in sync with the selected node: fill when empty, or
      // replace the previous auto draft. Leave real user edits untouched.
      if (detail.nodeLabel) {
        const nextDraft = learningNodeComposerDraft(detail.nodeLabel);
        setComposerText((current) =>
          isLearningNodeComposerDraft(current, previousLabel)
            ? nextDraft
            : current,
        );
        window.requestAnimationFrame(() =>
          composerTextareaRef.current?.focus(),
        );
      }
    };
    window.addEventListener(
      "learngraph:learning-node-selected",
      selectLearningNode,
    );
    return () =>
      window.removeEventListener(
        "learngraph:learning-node-selected",
        selectLearningNode,
      );
  }, []);

  useEffect(
    () => () => {
      dictationStopRequestedRef.current = true;
      const session = dictationCleanupSessionRef.current;
      if (session) session.degraded = true;
      speechRecognitionRef.current?.abort();
      providerDictationRef.current?.abort();
      providerDictationRef.current = null;
    },
    [],
  );

  const dictationEngine = useMemo(() => {
    const apply = (session: DictationCleanupSession) => {
      // 新一轮听写开始后,旧会话的迟到整理结果不再写入输入框。
      if (dictationCleanupSessionRef.current !== session) return;
      const transcript = (
        session.cleaned +
        session.active +
        session.pending +
        session.interim
      ).trim();
      const nextText = [session.prefix, transcript]
        .filter(Boolean)
        .join(session.prefix ? " " : "");
      setComposerText((current) => {
        // 停止监听后,如果用户已手动编辑输入框,迟到的整理结果不再覆盖。
        if (session.recognitionEnded && current !== session.lastRendered)
          return current;
        session.lastRendered = nextText;
        return nextText;
      });
    };

    const finish = (session: DictationCleanupSession) => {
      if (dictationCleanupSessionRef.current === session)
        setDictationFinalizing(null);
    };

    async function flush(session: DictationCleanupSession): Promise<void> {
      if (session.inFlight) return;
      if (!dictationCleanupActive(session) || !session.pending.trim()) {
        if (session.pending) {
          session.cleaned += session.pending;
          session.pending = "";
          apply(session);
        }
        finish(session);
        return;
      }
      const chunk = session.pending.slice(0, DICTATION_CLEANUP_MAX_CHUNK_CHARS);
      session.pending = session.pending.slice(chunk.length);
      session.active = chunk;
      session.inFlight = true;
      const contextTail = (
        (session.prefix ? `${session.prefix} ` : "") + session.cleaned
      ).slice(-DICTATION_CLEANUP_CONTEXT_CHARS);
      try {
        const result = await cleanupDictation({
          text: chunk,
          context: contextTail,
          provider_id: session.providerId,
          model_id: session.modelId,
        });
        session.failures = 0;
        // 等待期间用户点击了跳过:废弃迟到的整理结果,保留原始转写。
        // 整段均为语气词时,整理结果可以为空字符串。
        session.cleaned += session.degraded ? chunk : result.text;
      } catch {
        // 失败时保留原始转写,内容永不丢失;连续失败则本次听写降级,
        // 停止继续发起计费调用。
        session.cleaned += chunk;
        session.failures += 1;
        if (
          session.failures >= DICTATION_CLEANUP_MAX_FAILURES &&
          !session.degraded
        ) {
          session.degraded = true;
          toast.message("语音智能整理暂不可用", {
            description: "本次听写将保留原始转写。",
          });
        }
      } finally {
        session.active = "";
        session.inFlight = false;
      }
      apply(session);
      if (session.pending) void flush(session);
      else finish(session);
    }

    return {
      apply,
      // 仅在听写结束后调用:一次性整理全部原始转写(超长时按序分段)。
      polish: (session: DictationCleanupSession) => void flush(session),
    };
  }, [setComposerText]);

  // 听写结束后的共享收尾:有待整理文本且未跳过则进入润色阶段,否则直接
  // 采用原始转写并收起标签。
  const finishDictationSession = useCallback(
    (session: DictationCleanupSession) => {
      session.recognitionEnded = true;
      // 实时听写可能残留未定稿的 partial 文本,并入待整理内容以免丢失。
      if (session.interim) {
        session.pending += session.interim;
        session.interim = "";
      }
      if (dictationCleanupActive(session) && session.pending.trim()) {
        setDictationFinalizing("polishing");
        dictationEngine.polish(session);
      } else {
        if (session.pending) {
          session.cleaned += session.pending;
          session.pending = "";
          dictationEngine.apply(session);
        }
        setDictationFinalizing(null);
      }
    },
    [dictationEngine],
  );

  const toggleDictation = useCallback(() => {
    if (isListening) {
      dictationStopRequestedRef.current = true;
      const providerDictation = providerDictationRef.current;
      if (providerDictation) {
        const session = dictationCleanupSessionRef.current;
        setIsListening(false);
        // 云端听写收尾:先等剩余语音段转写返回,再进入(可选的)润色。
        setDictationFinalizing("transcribing");
        void providerDictation.stop().then(() => {
          if (providerDictationRef.current === providerDictation)
            providerDictationRef.current = null;
          const current = dictationCleanupSessionRef.current;
          if (current && current === session) finishDictationSession(current);
          else setDictationFinalizing(null);
        });
        return;
      }
      speechRecognitionRef.current?.stop();
      return;
    }

    // 已配置云端 ASR 时优先使用:麦克风持续采集(长会话,不因识别轮次
    // 中断),保留 ASR 服务原生的标点推断。realtime 模型走 WebSocket
    // 实时长连接(partial 逐字上屏);非 realtime 模型在自然停顿处切段上传。
    if (asrAvailable && providerDictationSupported()) {
      const session: DictationCleanupSession = {
        prefix: composerText.trimEnd(),
        cleaned: "",
        active: "",
        pending: "",
        interim: "",
        cleanupEnabled: isChatDictationCleanupEnabled(settings.data),
        degraded: false,
        failures: 0,
        inFlight: false,
        recognitionEnded: false,
        providerId: dictationCleanupModel.provider_id ?? activeModelProvider?.id,
        modelId: dictationCleanupModel.model_id ?? (selectedModelId || undefined),
        lastRendered: null,
      };
      dictationStopRequestedRef.current = false;
      setDictationFinalizing(null);
      dictationCleanupSessionRef.current = session;
      const appendFinalText = (text: string) => {
        // 中英文混排:两侧都是字母数字时补一个空格再拼接。
        const joiner =
          session.pending &&
          /[A-Za-z0-9]$/.test(session.pending) &&
          /^[A-Za-z0-9]/.test(text)
            ? " "
            : "";
        session.pending += joiner + text;
      };
      const handleFatal = (message: string) => {
        toast.error(message);
        providerDictationRef.current = null;
        setIsListening(false);
        if (dictationCleanupSessionRef.current === session)
          finishDictationSession(session);
      };
      const beginSegmentedDictation = () => {
        if (!storedAudioTranscriptionProvider) {
          handleFatal("尚未配置文件/分段转写模型");
          return Promise.resolve();
        }
        return startProviderDictation({
          transcribe: async (segment) =>
            (
              await transcribeDictationSegment(segment, {
                provider_id: storedAudioTranscriptionProvider.id,
                model_id: providerCapabilityString(
                  storedAudioTranscriptionProvider,
                  "default_transcription_model_id",
                ),
              })
            ).text,
          onSegmentText: (text) => {
            if (dictationCleanupSessionRef.current !== session) return;
            appendFinalText(text);
            dictationEngine.apply(session);
          },
          onFatal: handleFatal,
        })
          .then((handle) => {
            providerDictationRef.current = handle;
            setIsListening(true);
          })
          .catch(() => {
            if (dictationCleanupSessionRef.current === session)
              dictationCleanupSessionRef.current = null;
            toast.error("未获得麦克风权限");
          });
      };
      if (asrRealtimeConfigured && realtimeDictationSupported()) {
        startRealtimeDictation({
          providerId: realtimeAudioTranscriptionProvider!.id,
          modelId: realtimeAudioTranscriptionModel,
          onPartial: (text) => {
            if (dictationCleanupSessionRef.current !== session) return;
            session.interim = text;
            dictationEngine.apply(session);
          },
          onFinal: (text) => {
            if (dictationCleanupSessionRef.current !== session) return;
            session.interim = "";
            appendFinalText(text);
            dictationEngine.apply(session);
          },
          onFatal: handleFatal,
        })
          .then((handle) => {
            providerDictationRef.current = handle;
            setIsListening(true);
          })
          .catch((error: Error & { code?: string }) => {
            // 后端判定当前模型并非 realtime 时,退回分段上传路径。
            if (error.code === "realtime_model_required") {
              void beginSegmentedDictation();
              return;
            }
            if (dictationCleanupSessionRef.current === session)
              dictationCleanupSessionRef.current = null;
            toast.error(error.message || "无法启动实时语音转写");
          });
        return;
      }
      void beginSegmentedDictation();
      return;
    }

    const speechWindow = window as Window & {
      SpeechRecognition?: BrowserSpeechRecognitionConstructor;
      webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor;
    };
    const SpeechRecognition =
      speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
    if (!SpeechRecognition) {
      toast.message("当前浏览器不支持语音转写", {
        description:
          "语音输入需要 Chrome 或 Edge。也可以在工作区设置中配置转写模型，改由云端 ASR 提供语音输入。",
        duration: 8000,
        action: {
          label: "前往设置",
          onClick: () => void navigate(`/w/${workspaceId}/settings/providers`),
        },
      });
      return;
    }

    const recognition = new SpeechRecognition();
    // 会话状态跨浏览器自动重启保留:静音超时结束一轮识别后 event.results
    // 会清空,已定稿/已整理的文本都保存在这里。
    const session: DictationCleanupSession = {
      prefix: composerText.trimEnd(),
      cleaned: "",
      active: "",
      pending: "",
      interim: "",
      cleanupEnabled: shouldFallbackEnableDictationCleanup(settings.data),
      degraded: false,
      failures: 0,
      inFlight: false,
      recognitionEnded: false,
      providerId: dictationCleanupModel.provider_id ?? activeModelProvider?.id,
      modelId: dictationCleanupModel.model_id ?? (selectedModelId || undefined),
      lastRendered: null,
    };
    recognition.lang = navigator.language.startsWith("zh")
      ? "zh-CN"
      : navigator.language || "zh-CN";
    recognition.continuous = true;
    recognition.interimResults = true;
    recognition.onresult = (event) => {
      let finalText = "";
      let interimText = "";
      for (
        let index = event.resultIndex;
        index < event.results.length;
        index += 1
      ) {
        const result = event.results[index];
        const text = result[0]?.transcript ?? "";
        if (result.isFinal) finalText += text;
        else interimText += text;
      }
      session.interim = interimText;
      // 讲话过程中只累计原始转写,LLM 整理推迟到本次听写结束后一次完成。
      if (finalText) session.pending += finalText;
      dictationEngine.apply(session);
    };
    recognition.onerror = (event) => {
      if (
        event.error === "not-allowed" ||
        event.error === "service-not-allowed"
      ) {
        dictationStopRequestedRef.current = true;
        toast.error("未获得麦克风权限");
      } else if (event.error === "aborted") {
        dictationStopRequestedRef.current = true;
      }
      // 其他错误(如 no-speech 静音超时、network 抖动)不终止,
      // 交给 onend 自动重启,直到用户手动关闭。
    };
    recognition.onend = () => {
      // 一轮识别结束后,未定稿的临时结果不会再收到 final 事件,
      // 并入待整理文本以免内容丢失。
      if (session.interim) {
        session.pending += session.interim;
        session.interim = "";
      }
      const finishDictation = () => {
        setIsListening(false);
        speechRecognitionRef.current = null;
        // 语音结束才触发 LLM 整理;进行中可点击「正在润色」标签跳过。
        finishDictationSession(session);
      };
      if (dictationStopRequestedRef.current) {
        finishDictation();
        return;
      }
      // 静音超时等轮次结束:仅累计原始转写并自动重启,不在中途整理。
      try {
        recognition.start();
      } catch {
        finishDictation();
        toast.message("语音转写已停止", { description: "请稍后再试。" });
      }
    };

    dictationStopRequestedRef.current = false;
    speechRecognitionRef.current = recognition;
    // 新一轮听写取代旧会话:未完成的润色不再写回输入框,同步收起标签。
    setDictationFinalizing(null);
    dictationCleanupSessionRef.current = session;
    try {
      recognition.start();
      setIsListening(true);
    } catch {
      speechRecognitionRef.current = null;
      dictationCleanupSessionRef.current = null;
      toast.error("无法启动语音转写");
    }
  }, [
    activeModelProvider?.id,
    asrAvailable,
    asrRealtimeConfigured,
    composerText,
    dictationCleanupModel,
    dictationEngine,
    finishDictationSession,
    isListening,
    navigate,
    realtimeAudioTranscriptionModel,
    realtimeAudioTranscriptionProvider,
    selectedModelId,
    settings.data,
    storedAudioTranscriptionProvider,
  ]);

  const skipDictationFinalizing = useCallback(() => {
    // 跳过收尾:转写阶段丢弃未完成的语音段,润色阶段废弃在途整理结果,
    // 两种情况都立即采用已有的原始转写。
    providerDictationRef.current?.abort();
    providerDictationRef.current = null;
    setDictationFinalizing(null);
    const session = dictationCleanupSessionRef.current;
    if (!session) return;
    session.degraded = true;
    if (session.interim) {
      session.pending += session.interim;
      session.interim = "";
    }
    if (!session.inFlight && session.pending) {
      session.cleaned += session.pending;
      session.pending = "";
      dictationEngine.apply(session);
    }
  }, [dictationEngine]);

  const branch = useMutation({
    mutationFn: (messageId: string) => {
      const sourceTitle =
        currentSession?.title ??
        sessions.data?.find((item) => item.id === sessionId)?.title;
      return branchSession(sessionId, messageId, {
        title: branchSessionTitle(sourceTitle),
      });
    },
    onSuccess: (session) => {
      inheritSessionComposerPrefs(sessionId, session.id);
      rememberCreatedSession(session);
      navigate(`/w/${workspaceId}/chat/${session.id}`);
    },
    onError: (error) => toast.error(error.message),
  });
  const branchEdit = useMutation<
    Session,
    Error,
    { content: string; messageId: string; sourceSessionId: string }
  >({
    mutationFn: ({ messageId, sourceSessionId }) => {
      const sourceTitle =
        sessions.data?.find((item) => item.id === sourceSessionId)?.title ??
        currentSession?.title;
      return branchSession(sourceSessionId, messageId, {
        title: branchSessionTitle(sourceTitle),
      });
    },
    onSuccess: (session, variables) => {
      inheritSessionComposerPrefs(variables.sourceSessionId, session.id);
      rememberCreatedSession(session);
      setEditingMessageId(null);
      setEditingMessageContent("");
      navigate(`/w/${workspaceId}/chat/${session.id}`, {
        state: { pendingPrompt: variables.content },
      });
    },
    onError: (error) => toast.error(error.message),
  });
  const reconcileRetryHistory = async (
    sourceSessionId: string,
    messageId: string,
    minimumVersion: number,
    statuses: readonly string[],
  ) => {
    const sourceMessage = await confirmedSessionMessages(
      sourceSessionId,
      messageId,
      statuses,
      minimumVersion,
    );
    queryClient.setQueryData<Message[]>(
      workspaceQueryKey(workspaceId, "messages", sourceSessionId),
      (current) => mergeMessageIntoCache(current, sourceMessage),
    );
    if (sessionId !== sourceSessionId) {
      // Retry versions live on the source session; just invalidate the viewing
      // session cache rather than re-fetching a full timeline.
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "messages", sessionId) });
    }
  };
  const retry = useMutation<
    { expectedVersion: number; status: "cancelled" | "completed"; tempId: string },
    Error,
    {
      messageId: string;
      sourceSessionId: string;
      payload: MessageRetryRequest;
    }
  >({
    mutationFn: async ({
      messageId,
      sourceSessionId,
      payload,
    }) => {
      setStatus("submitted");
      latestOperationId.current += 1;
      const stamp = Date.now();
      const tempId = `temp-retry-${stamp}`;
      const currentVersion =
        queryClient
          .getQueryData<Message[]>(workspaceQueryKey(workspaceId, "messages", sourceSessionId))
          ?.find((message) => message.id === messageId)?.version ?? 0;
      const expectedVersion = currentVersion + 1;
      retryExpectedVersionRef.current = expectedVersion;
      const retryMessage: Message = {
        id: tempId,
        workspace_id: workspaceId,
        session_id: sourceSessionId,
        parent_message_id: messageId,
        role: "assistant",
        version: expectedVersion,
        status: "streaming",
        content: "",
        parts: [],
        provider_trace: { optimistic_target_message_id: messageId },
        created_at: new Date().toISOString(),
      };
      setLocalMessages((current) => [...current, retryMessage]);
      const controller = new AbortController();
      abortRef.current = controller;
      activeMessageId.current = messageId;
      activeStreamSessionId.current = sourceSessionId;
      registerSessionStream(sourceSessionId, controller, messageId);
      markSessionRunning(sourceSessionId, true);
      markSessionTouched(sourceSessionId);
      const isViewingRetry = () =>
        viewingSessionIdRef.current === sourceSessionId;
      let completed = false;
      let terminalFailure = "";
      let lastEventId = "";
      let messageVersionId = "";
      const seenEventIds = new Set<string>();
      const frameQueue = createAnimationFrameQueue<Record<string, unknown>>(
        (updates) =>
          setLocalMessages((current) =>
            current.map((message) =>
              message.id === tempId
                ? applyStreamUpdates(message, updates)
                : message,
            ),
          ),
      );
      const consumeRetryEvent = (data: Record<string, unknown>) => {
        const eventId =
          typeof data.event_id === "string" ? data.event_id : "";
        if (eventId && seenEventIds.has(eventId)) return;
        if (eventId) {
          seenEventIds.add(eventId);
          lastEventId = eventId;
        }
        if (
          typeof data.message_version_id === "string" &&
          data.message_version_id
        )
          messageVersionId = data.message_version_id;
        if (typeof data.message_id === "string") {
          if (isViewingRetry()) activeMessageId.current = data.message_id;
          setSessionStreamMessageId(sourceSessionId, data.message_id);
        }
        const type = streamEventType(data);
        if (type === "message.completed") completed = true;
        if (type === "message.failed")
          terminalFailure = "重试版本生成失败，失败记录已保留。";
        if (type === "message.cancelled") terminalFailure = "生成已取消。";
        if ((type || isMessagePart(data.part)) && isViewingRetry())
          setStatus("streaming");
        expandStreamUpdate(data, { animate: isViewingRetry() }).forEach(
          (update) => frameQueue.push(update),
        );
      };
      try {
        let streamError: unknown;
        try {
          for await (const event of retrySessionMessage(
            sourceSessionId,
            messageId,
            payload,
            { signal: controller.signal },
          )) {
            consumeRetryEvent(streamData(event.data));
          }
        } catch (error) {
          if (controller.signal.aborted) throw error;
          streamError = error;
        }
        if (!completed && !terminalFailure && streamError && messageVersionId) {
          toast.message("重试连接中断，正在续接持久事件…");
          for (let attempt = 0; attempt < 20; attempt += 1) {
            if (controller.signal.aborted) throw streamError;
            try {
              const replay = await listSessionMessageEvents(
                sourceSessionId,
                messageId,
                {
                  afterEventId: lastEventId || undefined,
                  messageVersionId,
                },
              );
              replay.forEach((event) =>
                consumeRetryEvent(event as Record<string, unknown>),
              );
            } catch {
              // A transient GET failure is retried without starting a new version.
            }
            if (completed || terminalFailure) break;
            await new Promise((resolve) => window.setTimeout(resolve, 250));
          }
        }
        await frameQueue.drain();
        if (terminalFailure) throw new Error(terminalFailure);
        if (!controller.signal.aborted && !completed)
          throw (
            streamError instanceof Error
              ? streamError
              : new Error("重试消息流结束，但没有收到完成事件。")
          );
      } catch (error) {
        if (controller.signal.aborted) {
          const cancellation = activeCancellationRef.current;
          if (cancellation) await cancellation;
          frameQueue.clear();
          if (isViewingRetry()) {
            setLocalMessages((current) =>
              current.map((message) =>
                message.id === tempId
                  ? {
                      ...message,
                      status: "cancelled",
                      parts: message.parts.map((part) => ({
                        ...part,
                        status: "failed" as const,
                      })),
                    }
                  : message,
              ),
            );
          }
        }
        else {
          await frameQueue.drain();
          if (isViewingRetry()) {
            setLocalMessages((current) =>
              current.map((message) =>
                message.id === tempId
                  ? {
                      ...message,
                      status: "failed",
                      parts: [
                        ...message.parts,
                        {
                          id: `retry-error-${stamp}`,
                          type: "error",
                          status: "failed",
                          content:
                            error instanceof Error ? error.message : "重试失败",
                        },
                      ],
                    }
                  : message,
              ),
            );
          }
          throw error;
        }
      } finally {
        clearSessionStream(sourceSessionId, controller);
        if (abortRef.current === controller) {
          abortRef.current = null;
          activeMessageId.current = null;
          activeStreamSessionId.current = null;
        }
      }
      return {
        expectedVersion,
        status: controller.signal.aborted ? "cancelled" : "completed",
        tempId,
      };
    },
    onSuccess: async (result, variables) => {
      markSessionGenerationFinished(variables.sourceSessionId, {
        viewing: viewingSessionIdRef.current === variables.sourceSessionId,
      });
      try {
        await reconcileRetryHistory(
          variables.sourceSessionId,
          variables.messageId,
          result.expectedVersion,
          [result.status],
        );
      } catch {
        retryExpectedVersionRef.current = 0;
        if (viewingSessionIdRef.current === variables.sourceSessionId) {
          setStatus("ready");
          toast.message("新版本已生成，消息记录正在后台同步。");
        }
        void Promise.all([
          queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "messages", sessionId) }),
          queryClient.invalidateQueries({
            queryKey: workspaceQueryKey(workspaceId, "messages", variables.sourceSessionId),
          }),
          queryClient.invalidateQueries({
            queryKey: workspaceQueryKey(workspaceId, "message-versions", variables.sourceSessionId),
          }),
        ]);
        return;
      }
      if (viewingSessionIdRef.current === variables.sourceSessionId) {
        setRetryTarget(null);
        setStatus("ready");
      }
      // Drop the optimistic retry row whether or not the user is still viewing
      // this session — otherwise concurrent retries accumulate in localMessages.
      setLocalMessages((current) =>
        current.filter((message) => message.id !== result.tempId),
      );
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "message-versions", variables.sourceSessionId),
      });
      retryExpectedVersionRef.current = 0;
    },
    onError: async (error, variables) => {
      markSessionGenerationFinished(variables.sourceSessionId, {
        viewing: viewingSessionIdRef.current === variables.sourceSessionId,
      });
      try {
        await reconcileRetryHistory(
          variables.sourceSessionId,
          variables.messageId,
          retryExpectedVersionRef.current,
          TERMINAL_MESSAGE_STATUSES,
        );
        setLocalMessages((current) =>
          current.filter(
            (message) =>
              message.provider_trace?.optimistic_target_message_id !==
              variables.messageId,
          ),
        );
      } catch {
        void Promise.all([
          queryClient.invalidateQueries({
            queryKey: workspaceQueryKey(workspaceId, "messages", variables.sourceSessionId),
          }),
          queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "messages", sessionId) }),
        ]);
      }
      void queryClient.invalidateQueries({
        queryKey: workspaceQueryKey(workspaceId, "message-versions", variables.sourceSessionId),
      });
      retryExpectedVersionRef.current = 0;
      setStatus("ready");
      toast.error(error.message);
    },
  });

  const mentionMatch = composerText.match(/(^|\s)@([^\s@]*)$/u);
  const mentionQuery = mentionMatch?.[2]?.toLocaleLowerCase() ?? "";
  const libraryFileMentions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "files", "mention", mentionQuery),
    queryFn: () => listFiles({ q: mentionQuery || undefined, limit: 8 }),
    enabled: Boolean(mentionMatch && !goalMode && !sessionIsClosed),
    staleTime: 15_000,
  });
  const goalComposerLocked = Boolean(
    goalMode && goalFlow.stage !== "capture",
  );
  const goalStageLabel =
    goalFlow.stage === "capture"
      ? "描述目标"
      : goalFlow.stage === "clarifying"
        ? "关键澄清"
        : goalFlow.stage === "goal_review" || goalFlow.stage === "graph_building"
          ? "确认 Goal"
          : goalFlow.stage === "graph_review"
            ? "审核图谱"
            : "已完成";

  const enterGoalMode = useCallback(() => {
    setComposerText((current) =>
      current.replace(/(^|\s)@[^\s@]*$/u, "$1").trimEnd(),
    );
    setDismissedMention("");
    setGraphAction("none");
    setSearchRoute("disabled");
    setGenerationMode("text");
    const params = new URLSearchParams(location.search);
    params.set("mode", "goal");
    navigate(`${location.pathname}?${params.toString()}`);
    window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
  }, [location.pathname, location.search, navigate]);

  const leaveGoalMode = useCallback(() => {
    const params = new URLSearchParams(location.search);
    params.delete("mode");
    const query = params.toString();
    navigate(`${location.pathname}${query ? `?${query}` : ""}`, {
      replace: true,
    });
  }, [location.pathname, location.search, navigate]);

  const canUseNetworkSearch = Boolean(
    hasAuthorizedAgentSearchProvider ||
      hasQwenCompanionSearchProvider ||
      selectedModel?.capabilities?.hosted_web_search ||
      activeModelProvider?.capabilities.hosted_web_search,
  );
  const retryCanUseNetworkSearch = Boolean(
    hasAuthorizedAgentSearchProvider ||
      hasQwenCompanionSearchProvider ||
      retrySelectedModel?.capabilities?.hosted_web_search ||
      retryProvider?.capabilities.hosted_web_search,
  );
  const preferredAgentThinkingMode = useMemo(
    () =>
      (["xhigh", "high", "medium", "low"] as ThinkingMode[]).find((mode) =>
        thinkingModes.includes(mode),
      ) ?? "off",
    [thinkingModes],
  );
  const graphCommandAvailable = Boolean(
    currentSession?.goal_id || currentSession?.graph_id,
  );
  const graphCommandLabel = currentSession?.graph_id
    ? "图谱创建和更新"
    : "图谱创建和更新";
  const focusComposer = useCallback(() => {
    window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
  }, []);
  const enableAgentMode = useCallback(
    ({ enableSearch = false }: { enableSearch?: boolean } = {}) => {
      if (!supportsAgentMode) return;
      setGenerationMode("text");
      setResponseMode("agentic");
      setThinkingMode(preferredAgentThinkingMode);
      if (enableSearch) {
        setSearchRoute(
          hasAuthorizedAgentSearchProvider
            ? "auto"
            : canUseNetworkSearch
              ? "model_native"
              : "disabled",
        );
      }
    },
    [
      canUseNetworkSearch,
      hasAuthorizedAgentSearchProvider,
      preferredAgentThinkingMode,
      supportsAgentMode,
    ],
  );
  const prepareTaskPrompt = useCallback(
    (
      prompt: string,
      {
        agent = false,
        enableSearch = false,
      }: { agent?: boolean; enableSearch?: boolean } = {},
    ) => {
      setComposerText((current) =>
        current.trim() ? current.trimEnd() + "\n\n" + prompt : prompt,
      );
      setGenerationMode("text");
      setGraphAction("none");
      if (agent) enableAgentMode({ enableSearch });
      focusComposer();
    },
    [enableAgentMode, focusComposer],
  );
  const setGraphProposal = useCallback(() => {
    if (!graphCommandAvailable) return;
    prepareTaskPrompt(
      currentSession?.graph_id
        ? "请使用图谱提案工具读取并更新当前目标图谱，生成需要我审核的变更提案，不要直接发布。"
        : "请使用图谱提案工具为当前已确认目标创建候选图谱，生成需要我审核的提案，不要直接发布。",
      { agent: true },
    );
  }, [currentSession?.graph_id, graphCommandAvailable, prepareTaskPrompt]);
  const startDeepResearch = useCallback(() => {
    if (!supportsAgentMode || !hasDeepResearchProvider) return;
    prepareTaskPrompt(
      "请使用 start_deep_research 工具启动深度研究。研究问题请根据当前对话上下文提炼；若用户已给出预算则使用该预算，否则先询问预算（单位：元 / budget_cny）。提交后若返回 user_approval_required=true，请停止并等待用户在界面上确认预算，不要重复调用 get_deep_research。",
      { agent: true },
    );
  }, [hasDeepResearchProvider, prepareTaskPrompt, supportsAgentMode]);
  const toggleNetworkSearch = useCallback(() => {
    setGenerationMode("text");
    setSearchRoute((current) => {
      if (current !== "disabled") return "disabled";
      if (!canUseNetworkSearch) return "disabled";
      // Prefer external SearchProvider when authorized; model_native only when
      // the active model explicitly hosts web search and no SearchProvider is on.
      if (hasAuthorizedAgentSearchProvider) return "auto";
      if (
        selectedModel?.capabilities?.hosted_web_search ||
        activeModelProvider?.capabilities.hosted_web_search
      ) {
        return "model_native";
      }
      return "disabled";
    });
    if (!canUseNetworkSearch) {
      toast.message("无法开启联网搜索", {
        description:
          "当前没有已启用的 SearchProvider，且模型不托管联网能力。请到 Provider 管理中启用搜索服务后重试。",
      });
    }
    focusComposer();
  }, [
    activeModelProvider?.capabilities.hosted_web_search,
    canUseNetworkSearch,
    focusComposer,
    hasAuthorizedAgentSearchProvider,
    selectedModel?.capabilities?.hosted_web_search,
  ]);
  const toggleAgentMode = useCallback(() => {
    if (responseMode === "agentic") {
      setResponseMode(supportsThinkingMode ? "thinking" : "fast");
      focusComposer();
      return;
    }
    enableAgentMode();
    focusComposer();
  }, [enableAgentMode, focusComposer, responseMode, supportsThinkingMode]);
  const toggleImageMode = useCallback(() => {
    setGenerationMode((current) => (current === "image" ? "text" : "image"));
    setGraphAction("none");
    setSearchRoute("disabled");
    focusComposer();
  }, [focusComposer]);
  const composerCommands = [
    {
      id: "goal" as const,
      label: "设定学习目标",
      description: "进入 Goal 澄清流程",
      keywords: "目标 goal",
      disabled: sessionIsClosed && !goalMode,
      Icon: Target,
    },
    {
      id: "image" as const,
      label: "绘图",
      description: activeImageProvider
        ? `使用 ${selectedImageModel?.id ?? defaultImageModelId}`
        : "需启用绘图 Provider 与默认模型",
      keywords: "绘图 图片 image",
      disabled:
        sessionIsClosed || !activeImageProvider || !selectedImageModel,
      Icon: ImageIcon,
    },
    {
      id: "search" as const,
      label: "网络搜索",
      description: canUseNetworkSearch
        ? hasAuthorizedAgentSearchProvider
          ? "通过已启用的 SearchProvider 检索公开网页"
          : "使用模型托管网页搜索"
        : "请先在 Provider 管理中点「启用」SearchProvider，或确认模型托管联网能力",
      keywords: "网络 搜索 search",
      disabled: sessionIsClosed || !canUseNetworkSearch,
      Icon: Search,
    },
    {
      id: "agentic" as const,
      label: "智能体",
      description: supportsAgentMode
        ? hasAuthorizedAgentSearchProvider
          ? "启用工具循环；联网需打开「联网」开关"
          : "启用工具循环；SearchProvider 探测通过后仍须「启用」才能联网"
        : "需支持结构化工具调用的远程模型",
      keywords: "智能体 agent",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: Bot,
    },
    {
      id: "graph" as const,
      label: graphCommandLabel,
      description: currentSession?.graph_id
        ? "围绕当前绑定节点增补/修改子节点，自动去重后生成需审核提案"
        : "下一条消息生成需人工审核的新图谱提案",
      keywords: "图谱 节点 变更 graph",
      disabled: sessionIsClosed || !graphCommandAvailable,
      Icon: Network,
    },
    {
      id: "research" as const,
      label: "深度研究",
      description: hasDeepResearchProvider
        ? "预填 start_deep_research 任务，确认预算后启动"
        : "请先在 Provider 管理中启用 Deep Research Provider",
      keywords: "研究 调研 深度研究 deep research",
      disabled:
        sessionIsClosed || !supportsAgentMode || !hasDeepResearchProvider,
      Icon: FileSearch,
    },
    {
      id: "roadmap" as const,
      label: "学习路线",
      description: "由智能体读取路线或创建待审核的重规划草稿",
      keywords: "路线 计划 roadmap action",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: GitBranch,
    },
    {
      id: "schedule" as const,
      label: "日程规划",
      description: "由智能体读取、创建或调整带时间的学习行动",
      keywords: "日程 计划 安排 calendar schedule action",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: CalendarDays,
    },
    {
      id: "progress" as const,
      label: "学习进度",
      description: "读取掌握度、证据与复习状态",
      keywords: "进度 掌握 复习 mastery evidence",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: Check,
    },
    {
      id: "practice" as const,
      label: "生成练习",
      description: "预填基于当前上下文的练习请求",
      keywords: "练习 测验 quiz exercise",
      disabled: sessionIsClosed || !activeModelProvider,
      Icon: Sparkles,
    },
    {
      id: "usage" as const,
      label: "用量与预算",
      description: "读取用量、成本和预算状态",
      keywords: "用量 预算 成本 token usage budget",
      disabled: sessionIsClosed || !supportsAgentMode,
      Icon: LayoutDashboard,
    },
  ];
  const mentionCandidates = composerCommands.filter(
    (item) =>
      !mentionQuery ||
      `${item.label} ${item.keywords}`.toLocaleLowerCase().includes(mentionQuery),
  );
  type MentionEntry =
    | { kind: "command"; command: (typeof composerCommands)[number] }
    | { kind: "file"; file: FileRecord };
  const mentionEntries = useMemo<MentionEntry[]>(() => {
    const commands: MentionEntry[] = mentionCandidates.map((command) => ({
      kind: "command",
      command,
    }));
    const files: MentionEntry[] = (libraryFileMentions.data ?? [])
      .filter((file) =>
        !mentionQuery
          ? true
          : file.original_name.toLocaleLowerCase().includes(mentionQuery),
      )
      .slice(0, 8)
      .map((file) => ({ kind: "file", file }));
    return [...commands, ...files];
  }, [libraryFileMentions.data, mentionCandidates, mentionQuery]);
  const showMentionMenu = Boolean(
    !goalMode &&
      mentionMatch &&
      dismissedMention !== composerText &&
      !sessionIsClosed &&
      mentionEntries.length,
  );
  const mentionCandidateSignature = mentionEntries
    .map((item) =>
      item.kind === "command"
        ? `c:${item.command.id}:${item.command.disabled}`
        : `f:${item.file.id}`,
    )
    .join("|");
  const firstEnabledMentionIndex = mentionEntries.findIndex((item) =>
    item.kind === "command" ? !item.command.disabled : true,
  );

  useEffect(() => {
    setMentionIndex(
      firstEnabledMentionIndex >= 0 ? firstEnabledMentionIndex : 0,
    );
  }, [composerText, firstEnabledMentionIndex, mentionCandidateSignature]);

  // The menu scrolls on small screens; keep the keyboard-selected row visible.
  useEffect(() => {
    if (!showMentionMenu) return;
    document
      .getElementById(mentionMenuId)
      ?.querySelector("[aria-selected='true']")
      ?.scrollIntoView({ block: "nearest" });
  }, [mentionIndex, mentionMenuId, showMentionMenu]);

  const attachLibraryFile = useCallback(
    (file: FileRecord) => {
      setComposerText((current) =>
        current.replace(/(^|\s)@[^\s@]*$/u, "$1").trimEnd(),
      );
      setDismissedMention("");
      setPendingFiles((current) => {
        if (current.some((item) => item.id === file.id)) return current;
        if (generationMode === "image") {
          if (!imageEditEnabled) {
            toast.error("当前绘图模型不支持图生图。");
            return current;
          }
          if (!IMAGE_EDIT_MIME_TYPES.has(file.mime_type.toLowerCase())) {
            toast.error("绘图模式的参考附件只能是图片。");
            return current;
          }
          if (current.length >= 4) {
            toast.error("当前绘图模型最多支持 4 张参考图片。");
            return current;
          }
        }
        return [...current, file];
      });
      toast.message(`已引用资料「${file.original_name}」`);
      focusComposer();
    },
    [focusComposer, generationMode, imageEditEnabled],
  );
  const activateComposerCommand = useCallback(
    (action: ComposerCommandId) => {
      if (action === "goal") {
        enterGoalMode();
        return;
      }
      setComposerText((current) =>
        current.replace(/(^|\s)@[^\s@]*$/u, "$1").trimEnd(),
      );
      setDismissedMention("");
      if (action === "image") {
        setGenerationMode("image");
        setGraphAction("none");
        setSearchRoute("disabled");
        focusComposer();
        return;
      }
      if (action === "search") {
        setGenerationMode("text");
        setSearchRoute("auto");
        focusComposer();
        return;
      }
      if (action === "agentic") {
        enableAgentMode({ enableSearch: hasAuthorizedAgentSearchProvider });
        focusComposer();
        return;
      }
      if (action === "graph") {
        setGraphProposal();
        return;
      }
      if (action === "research") {
        startDeepResearch();
        return;
      }
      if (action === "roadmap") {
        prepareTaskPrompt(
          "请读取当前学习目标的路线，说明下一步行动、前置阻塞和可执行建议；如需调整，只创建待审核的路线草稿。",
          { agent: true },
        );
        return;
      }
      if (action === "schedule") {
        prepareTaskPrompt(
          "请使用日程规划工具读取当前学习行动，并结合目标期限和可用时间创建或调整具体日程；任何路线重规划只生成待审核草稿。",
          { agent: true },
        );
        return;
      }
      if (action === "progress") {
        prepareTaskPrompt(
          "请读取当前学习节点的掌握度、证据与复习状态，并给出下一步学习建议。",
          { agent: true },
        );
        return;
      }
      if (action === "usage") {
        prepareTaskPrompt(
          "请读取当前工作区的模型用量与预算，分别说明美元和人民币成本以及剩余额度。",
          { agent: true },
        );
        return;
      }
      prepareTaskPrompt(
        "请基于当前学习目标、节点和已附资料生成一道可验证的练习，并先说明覆盖的知识点。",
      );
    },
    [
      enableAgentMode,
      enterGoalMode,
      focusComposer,
      hasAuthorizedAgentSearchProvider,
      prepareTaskPrompt,
      setGraphProposal,
      startDeepResearch,
    ],
  );
  const clearSelectedLearningNode = useCallback(() => {
    const previousLabel = learningNodeRef.current?.nodeLabel;
    learningNodeRef.current = undefined;
    setLearningNode(undefined);
    clearLearningNodeContext();
    // Drop the auto-filled “什么是 X？” draft when the user unbinds the node.
    setComposerText((current) =>
      isLearningNodeComposerDraft(current, previousLabel) ? "" : current,
    );
    focusComposer();
  }, [focusComposer]);
  const toggleGoalMode = useCallback(() => {
    if (goalMode) {
      leaveGoalMode();
      return;
    }
    enterGoalMode();
  }, [enterGoalMode, goalMode, leaveGoalMode]);
  const openAttachmentPicker = useCallback(() => {
    openFileDialogRef.current();
  }, []);
  const selectComposerMenuCommand = useCallback(
    (command: ComposerCommandId) => {
      if (command === "goal") {
        toggleGoalMode();
        return;
      }
      if (command === "image") {
        toggleImageMode();
        return;
      }
      if (command === "search") {
        toggleNetworkSearch();
        return;
      }
      if (command === "agentic") {
        toggleAgentMode();
        return;
      }
      activateComposerCommand(command);
    },
    [
      activateComposerCommand,
      toggleAgentMode,
      toggleGoalMode,
      toggleImageMode,
      toggleNetworkSearch,
    ],
  );

  const submitRetry = () => {
    if (!retryTarget || !retryProvider || !retrySelectedModel?.remote) return;
    const useWebSearch = retryWebSearch && retryCanUseNetworkSearch;
    const allowedDomains = [
      ...new Set(
        retryAllowedDomains
          .split(/[\s,，]+/u)
          .map((domain) => domain.trim())
          .filter(Boolean),
      ),
    ];
    const variables = {
      messageId: retryTarget.messageId,
      sourceSessionId: retryTarget.sourceSessionId,
      payload: {
        provider_id: retryProvider.id,
        model_id: retrySelectedModel.id,
        thinking_mode:
          retryResponseMode === "fast" ? "off" : retryThinkingMode,
        agent_mode: retryResponseMode === "agentic",
        search_route: useWebSearch
          ? hasAuthorizedAgentSearchProvider
            ? "auto"
            : "model_native"
          : "disabled",
        web_search: useWebSearch,
        allowed_domains: useWebSearch ? allowedDomains : [],
      },
    } satisfies {
      messageId: string;
      sourceSessionId: string;
      payload: MessageRetryRequest;
    };
    setRetryTarget(null);
    retry.mutate(variables);
  };

  const submitImageRetry = () => {
    if (!imageRetryTarget) return;
    const choice = parseModelChoiceValue(imageRetryChoice);
    if (!choice) return;
    const { prompt } = imageRetryTarget;
    setImageRetryTarget(null);
    // Keep the composer chip in sync with the newly chosen image model.
    setSelectedImageProviderId(choice.providerId);
    setSelectedImageModelId(choice.modelId);
    void send(prompt, {
      generationMode: "image",
      imageProviderId: choice.providerId,
      imageModelId: choice.modelId,
    });
  };

  return (
    <div className="chat-canvas-page relative flex h-full min-h-0 flex-col bg-background">
      <ConversationJumpNav
        branches={conversationBranchLinks}
        items={conversationJumpItems}
        onBranchJump={(targetSessionId) => {
          if (targetSessionId !== sessionId)
            navigate(`/w/${workspaceId}/chat/${targetSessionId}`);
        }}
        onJump={(messageId) => {
          setActiveConversationQuestionId(messageId);
          document
            .getElementById(`conversation-jump-${messageId}`)
            ?.scrollIntoView({ behavior: "smooth", block: "center" });
        }}
      />
      <Conversation className="min-h-0 flex-1">
        <ConversationContent
          className="mx-auto w-full max-w-4xl gap-7 px-4 py-6 pb-36 sm:px-7 sm:py-7"
          onMouseUp={() => {
            const selected = readTextSelection();
            if (!selected) {
              setSelectionMenu(null);
              return;
            }
            const source = messages.find(
              (message) => message.id === selected.source_message_id,
            );
            if (!source) {
              setSelectionMenu(null);
              return;
            }
            // Multi-line markdown selections rarely match `message.content`
            // byte-for-byte. Always show the toolbar; only mark contentMatched
            // when we can re-anchor onto the durable body for selection_context.
            const located = locateSelectionInContent(
              source.content,
              selected.selected_text,
              {
                occurrenceIndex: selected.occurrenceIndex,
                prefixHint: selected.prefix,
                suffixHint: selected.suffix,
              },
            );
            setSelectionMenu({
              ...selected,
              selected_text: located.selected_text,
              prefix: located.prefix,
              suffix: located.suffix,
              contentMatched: located.contentMatched,
            });
          }}
        >
          {isEmptySession && !goalMode ? (
            <EmptySessionPrompts
              disabled={
                history.isPending || status !== "ready" || sessionIsClosed
              }
              isError={
                showSuggestedPromptState &&
                suggestedPromptsError &&
                emptySessionDisplayPrompts.length === 0
              }
              isPending={
                (showSuggestedPromptState && suggestedPromptsPending) ||
                (emptySessionMemories.isPending &&
                  emptySessionDisplayPrompts.length === 0)
              }
              isUnavailable={
                showSuggestedPromptState &&
                suggestedPromptsUnavailable &&
                emptySessionDisplayPrompts.length === 0
              }
              onConfigureProvider={() =>
                navigate(`/w/${workspaceId}/settings/providers`)
              }
              onRetry={retrySuggestedPrompts}
              onSelect={(content) => void send(content)}
              prompts={emptySessionDisplayPrompts}
            />
          ) : (
            <>
              {historyHasMoreBefore || loadingOlderMessages ? (
                <div className="flex flex-col items-center gap-1 pb-2">
                  <Button
                    disabled={loadingOlderMessages}
                    onClick={() => void loadOlderMessages()}
                    size="sm"
                    type="button"
                    variant="ghost"
                  >
                    {loadingOlderMessages ? (
                      <>
                        <LoaderCircle className="size-3.5 animate-spin" />
                        正在加载更早消息…
                      </>
                    ) : (
                      <>
                        加载更早消息
                        {historyTotalCount > 0
                          ? `（已显示 ${messages.length}/${historyTotalCount}）`
                          : null}
                      </>
                    )}
                  </Button>
                </div>
              ) : null}
              {messages.map((message, messageIndex) => {
                const persisted =
                  !message.id.startsWith("temp") &&
                  message.id !== "welcome-local";
                const imageAnswer = message.parts.some(
                  (part) => part.type === "image",
                );
                // Always mount the latest few turns (composer context) and the
                // in-flight answer; older history mounts only near the viewport.
                // User messages stay eager so conversation-jump anchors remain
                // addressable for the left question rail.
                const eagerMount =
                  message.role === "user" ||
                  messageIndex >= messages.length - 6 ||
                  message.status === "streaming" ||
                  message.status === "pending" ||
                  editingMessageId === message.id;
                if (message.role === "user")
                  return (
                    <LazyMessageMount
                      eager={eagerMount}
                      key={message.id}
                      minHeight={72}
                    >
                      <UserMessage
                        disabled={
                          status !== "ready" ||
                          branchEdit.isPending ||
                          !persisted
                        }
                        editing={editingMessageId === message.id}
                        editValue={
                          editingMessageId === message.id
                            ? editingMessageContent
                            : message.content
                        }
                        message={message}
                        onCancelEdit={() => {
                          setEditingMessageId(null);
                          setEditingMessageContent("");
                        }}
                        onEditValueChange={setEditingMessageContent}
                        onOpenSelectionExplanation={handleOpenSelectionExplanation}
                        onSaveEdit={() => {
                          const content = editingMessageContent.trim();
                          if (!content) return;
                          branchEdit.mutate({
                            content,
                            messageId: message.id,
                            sourceSessionId: message.session_id,
                          });
                        }}
                        onStartEdit={() => {
                          setEditingMessageId(message.id);
                          setEditingMessageContent(message.content);
                        }}
                        selectionMarks={selectionExplanationMarks}
                        versionNavigation={userVersionNavigation(message)}
                      />
                    </LazyMessageMount>
                  );
                return (
                  <LazyMessageMount
                    eager={eagerMount}
                    key={message.id}
                    minHeight={140}
                  >
                    <AssistantMessage
                      branchDisabled={
                        !persisted ||
                        message.session_id !== sessionId ||
                        status !== "ready" ||
                        branch.isPending
                      }
                      branchDisabledReason={
                        !persisted
                          ? "回答持久化后才能创建分支"
                          : message.session_id !== sessionId
                            ? "请先切换到该回答所属的会话版本"
                            : status !== "ready" || branch.isPending
                              ? "当前操作完成后才能创建分支"
                              : undefined
                      }
                      componentsInteractive={
                        message.status !== "streaming" &&
                        message.status !== "pending" &&
                        status !== "streaming" &&
                        status !== "submitted"
                      }
                      message={message}
                      onComponentAction={handleComponentAction}
                      onBranch={() => {
                        if (persisted && message.session_id === sessionId)
                          branch.mutate(message.id);
                      }}
                      onOpenSelectionExplanation={handleOpenSelectionExplanation}
                      selectionMarks={selectionExplanationMarks}
                      onRetry={() => {
                        if (!persisted) return;
                        if (imageAnswer) {
                          const prompt =
                            messages
                              .find(
                                (item) =>
                                  item.id === message.parent_message_id &&
                                  item.role === "user",
                              )
                              ?.content?.trim() ?? "";
                          if (!prompt) {
                            toast.error("未找到原始绘图提示词，无法重试。");
                            return;
                          }
                          setImageRetryChoice(
                            activeImageProvider && selectedImageModel
                              ? modelChoiceValue(
                                  activeImageProvider.id,
                                  selectedImageModel.id,
                                )
                              : "",
                          );
                          setImageRetryTarget({
                            messageId: message.id,
                            prompt,
                          });
                          return;
                        }
                        setRetryProviderId(
                          activeModelProvider?.id ?? modelProviders[0]?.id ?? "",
                        );
                        setRetryModelId(selectedModelId);
                        setRetryResponseMode(responseMode);
                        setRetryThinkingMode(effectiveThinkingMode);
                        setRetryWebSearch(searchRoute !== "disabled");
                        setRetryAllowedDomains("");
                        setRetryTarget({
                          messageId: message.id,
                          sourceSessionId: message.session_id,
                        });
                      }}
                      retryDisabled={
                        !persisted ||
                        sessionIsClosed ||
                        status !== "ready" ||
                        retry.isPending ||
                        (imageAnswer &&
                          (message.session_id !== sessionId ||
                            !imageProviders.length))
                      }
                      retryDisabledReason={
                        !persisted
                          ? "回答持久化后才能重试"
                          : sessionIsClosed
                            ? "会话已结束；请创建分支或新会话继续学习"
                            : status !== "ready" || retry.isPending
                              ? "当前操作完成后才能重试"
                              : imageAnswer && message.session_id !== sessionId
                                ? "请先切换到该回答所属的会话版本"
                                : imageAnswer && !imageProviders.length
                                  ? "当前工作区没有已启用的绘图 Provider"
                                  : imageAnswer
                                    ? "重试绘图（可切换绘图模型）"
                                    : undefined
                      }
                      sessionId={message.session_id}
                      workspaceId={workspaceId}
                    />
                  </LazyMessageMount>
                );
              })}
              {streamConnectionNotice ? (
                <StreamConnectionFeedback notice={streamConnectionNotice} />
              ) : null}
              {goalMode ? (
                <GoalSetupConversation
                  flow={goalFlow}
                  hasConversationMessages={!isEmptySession}
                />
              ) : null}
              {!goalMode && showSuggestedPromptState ? (
                <FollowUpPrompts
                  isError={suggestedPromptsError}
                  isPending={suggestedPromptsPending}
                  isUnavailable={suggestedPromptsUnavailable}
                  onConfigureProvider={() =>
                    navigate(`/w/${workspaceId}/settings/providers`)
                  }
                  onRetry={retrySuggestedPrompts}
                  onSelect={(content) => void send(content)}
                  prompts={promptSuggestions}
                />
              ) : null}
            </>
          )}
        </ConversationContent>
        <ConversationScrollButton />
      </Conversation>

      {selectionMenu ? (
        <div
          aria-label="划词操作"
          className="chat-selection-menu"
          role="toolbar"
          style={{ left: selectionMenu.left, top: selectionMenu.top }}
        >
          <span title={selectionMenu.selected_text}>
            “{selectionMenu.selected_text}”
          </span>
          <Button
            onClick={() => {
              const quote = `> ${selectionMenu.selected_text.replaceAll("\n", "\n> ")}\n\n`;
              setComposerText((current) => `${current}${current ? "\n\n" : ""}${quote}`);
              setSelectionMenu(null);
              window.requestAnimationFrame(() => composerTextareaRef.current?.focus());
            }}
            size="xs"
            variant="ghost"
          >
            <MessageSquareQuote className="size-3.5" />
            引用
          </Button>
          {["解释", "举例"].map((action) => (
            <Button
              disabled={
                selectionMenu.source_message_id.startsWith("temp") ||
                selectionMenu.source_message_id === "welcome-local" ||
                status !== "ready"
              }
              key={action}
              onClick={() => {
                const context = selectionRequestContext(selectionMenu);
                const quote = selectionMenu.selected_text;
                setSelectionMenu(null);
                // Unmatched multi-line selections still fire the prompt; they
                // just omit selection_context so the backend never 409s.
                void send(`请${action}这段内容：${quote}`, {
                  generationMode: "text",
                  selectionContext: context,
                });
              }}
              size="xs"
              variant="ghost"
            >
              {action}
            </Button>
          ))}
          <Button
            disabled={
              selectionMenu.source_message_id.startsWith("temp") ||
              selectionMenu.source_message_id === "welcome-local"
            }
            onClick={() => {
              const action = inferSelectionAction(selectionMenu.selected_text);
              const record = upsertSelectionExplanation({
                id: createSelectionExplanationId(),
                parentSessionId: sessionId,
                sourceMessageId: selectionMenu.source_message_id,
                selectedText: selectionMenu.selected_text,
                prefix: selectionMenu.prefix,
                suffix: selectionMenu.suffix,
                contentMatched: selectionMenu.contentMatched,
                action,
                createdAt: new Date().toISOString(),
              });
              setSelectionExplanationMarks(listSelectionExplanations(sessionId));
              openSelectionExplanation({
                parentSessionId: sessionId,
                sourceMessageId: record.sourceMessageId,
                selectedText: record.selectedText,
                prefix: record.prefix,
                suffix: record.suffix,
                contentMatched: record.contentMatched,
                action: record.action,
                recordId: record.id,
                explanationSessionId: record.explanationSessionId,
              });
              setSelectionMenu(null);
            }}
            size="xs"
            variant="ghost"
          >
            单独解释
          </Button>
          <Button
            aria-label="关闭划词操作"
            onClick={() => setSelectionMenu(null)}
            size="icon-xs"
            variant="ghost"
          >
            <X className="size-3" />
          </Button>
        </div>
      ) : null}

      <div className="chat-composer-dock relative z-10 mx-auto w-full max-w-4xl px-3 pb-3 pt-2.5 sm:px-4">
        {responseMode === "agentic" ? (
          <SandboxReadinessNotice workspaceId={workspaceId} />
        ) : null}
        {topbarContextSlot
          ? createPortal(
              <ConversationContextBar
                goalBound={Boolean(currentSession?.goal_id)}
                graphTitle={graphTitle}
                learningNode={learningNode}
                onClearLearningNode={clearSelectedLearningNode}
              />,
              topbarContextSlot,
            )
          : null}
        <ConversationQuickActions
          agentActive={responseMode === "agentic"}
          agentDisabled={
            sessionIsClosed || goalFlow.busy || !supportsAgentMode
          }
          attachDisabled={sessionIsClosed || goalFlow.busy}
          deepResearchDisabled={
            sessionIsClosed ||
            goalMode ||
            goalFlow.busy ||
            !supportsAgentMode ||
            !hasDeepResearchProvider
          }
          goalActive={goalMode}
          goalDisabled={goalFlow.busy || (!goalMode && sessionIsClosed)}
          graphActive={graphAction !== "none"}
          graphDisabled={
            sessionIsClosed ||
            goalMode ||
            goalFlow.busy ||
            !graphCommandAvailable
          }
          imageActive={generationMode === "image"}
          imageDisabled={
            sessionIsClosed ||
            goalMode ||
            goalFlow.busy ||
            !activeImageProvider ||
            !selectedImageModel
          }
          onAgent={toggleAgentMode}
          onAttach={openAttachmentPicker}
          onDeepResearch={startDeepResearch}
          onGoal={toggleGoalMode}
          onGraph={setGraphProposal}
          onImage={toggleImageMode}
          onPractice={() => activateComposerCommand("practice")}
          onSearch={toggleNetworkSearch}
          practiceDisabled={
            sessionIsClosed || goalMode || goalFlow.busy || !activeModelProvider
          }
          searchActive={searchRoute !== "disabled"}
          searchDisabled={
            sessionIsClosed ||
            goalFlow.busy ||
            !canUseNetworkSearch
          }
        />
        <SandboxAuthDialog
          onClose={() => setSandboxAuthRequest(null)}
          onGranted={() => {
            void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "messages", sessionId) });
          }}
          request={sandboxAuthRequest}
        />

      {longPaste ? (
          <div className="mb-2 flex flex-wrap items-center gap-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 shadow-sm dark:border-amber-900 dark:bg-amber-950/45 dark:text-amber-100">
            <FilePlus2 className="size-4" />
            <span className="min-w-0 flex-1">
              <strong>检测到单次粘贴超过 10,000 字符。</strong>
              原文仍保留，可选择转换为私有 Markdown 附件。
            </span>
            <Button
              onClick={() => setLongPaste(null)}
              size="xs"
              variant="outline"
            >
              保留文本
            </Button>
            <Button
              disabled={convertLongPaste.isPending}
              onClick={() => convertLongPaste.mutate(longPaste)}
              size="xs"
            >
              {convertLongPaste.isPending ? "上传并解析中…" : "转换附件"}
            </Button>
          </div>
        ) : null}
        {pendingFiles.length ? (
          <div className="mb-2 flex flex-wrap gap-2" aria-label="待发送附件">
            {pendingFiles.map((file) => (
              <Badge className="gap-1" key={file.id} variant="secondary">
                {file.mime_type.toLowerCase().startsWith("image/") ? (
                  <ImageIcon className="size-3" />
                ) : (
                  <FilePlus2 className="size-3" />
                )}
                <span className="max-w-52 truncate">{file.original_name}</span>
                <button
                  aria-label={`移除附件 ${file.original_name}`}
                  className="ml-1 rounded-sm hover:text-destructive"
                  onClick={() =>
                    setPendingFiles((current) =>
                      current.filter((item) => item.id !== file.id),
                    )
                  }
                  type="button"
                >
                  <X className="size-3" />
                </button>
              </Badge>
            ))}
          </div>
        ) : null}
        {!providers.isPending && !activeModelProvider ? (
          <div className="mb-2 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-amber-200 bg-amber-50/70 p-3 text-xs text-amber-900 dark:border-amber-900 dark:bg-amber-950/35 dark:text-amber-100">
            <span>没有可用的真实模型 Provider，发送已暂停。</span>
            <Button
              onClick={() => navigate(`/w/${workspaceId}/settings/providers`)}
              size="xs"
              variant="outline"
            >
              配置 Provider
            </Button>
          </div>
        ) : null}
        {graphAction !== "none" ? (
          <div className="chat-graph-action" role="status">
            <Network className="size-3.5" />
            <span>
              {graphAction === "propose_create"
                ? "下一条消息将由真实模型生成新图谱提案"
                : "图谱变更已开启：将围绕当前节点细化/增补子节点，并与现有概念去重"}
            </span>
            <Button
              aria-label="取消图谱变更操作"
              onClick={() => setGraphAction("none")}
              size="icon-xs"
              variant="ghost"
            >
              <X className="size-3" />
            </Button>
          </div>
        ) : null}
        {goalMode ? (
          <div className="chat-goal-mode" role="status">
            <Target className="size-3.5" />
            <span>
              <strong>目标设定</strong>
              {goalStageLabel}
            </span>
            <Button
              aria-label="退出目标设定模式"
              disabled={goalFlow.busy}
              onClick={leaveGoalMode}
              size="icon-xs"
              title="退出目标设定"
              variant="ghost"
            >
              <X className="size-3" />
            </Button>
          </div>
        ) : null}
        {!goalMode && generationMode === "image" ? (
          <div className="chat-image-controls" role="group" aria-label="绘图设置">
            <div className="chat-image-action" role="status">
              <ImageIcon className="size-3.5" />
              <span>
                绘图 · {activeGenerationProvider?.display_name} / {activeGenerationModelId}
              </span>
              <Button
                aria-label="退出绘图模式"
                onClick={() => setGenerationMode("text")}
                size="icon-xs"
                variant="ghost"
              >
                <X className="size-3" />
              </Button>
            </div>
            <Select
              onValueChange={(value) => setImageSize(value as ImageSize)}
              value={imageSize}
            >
              <SelectTrigger
                aria-label="选择图片比例"
                className="chat-image-size-select"
              >
                <SelectValue>{imageSizeOption.label}</SelectValue>
              </SelectTrigger>
              <SelectContent>
                {IMAGE_SIZE_OPTIONS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    <span className="flex items-center gap-2">
                      <strong>{option.label}</strong>
                      <span className="text-muted-foreground">{option.detail}</span>
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Button
              disabled={!imageEditEnabled}
              onClick={() => openFileDialogRef.current()}
              size="xs"
              title={
                imageEditEnabled
                  ? "添加参考图片，使用当前绘图模型编辑"
                  : "当前绘图模型不支持图生图"
              }
              variant="outline"
            >
              <FilePlus2 className="size-3.5" />
              {pendingFiles.length ? `参考图 ${pendingFiles.length}` : "添加参考图"}
            </Button>
          </div>
        ) : null}
        {dictationFinalizing ? (
          <button
            aria-label={
              dictationFinalizing === "transcribing"
                ? "正在转写剩余语音，点击跳过并保留已有文本"
                : "正在润色语音转写，点击跳过并保留原始转写"
            }
            className="chat-dictation-polish"
            onClick={skipDictationFinalizing}
            title="点击跳过收尾，立即保留已有文本"
            type="button"
          >
            <LoaderCircle className="size-3.5 animate-spin" />
            <span>
              {dictationFinalizing === "transcribing"
                ? "正在转写剩余语音…"
                : "正在润色语音转写…"}
            </span>
            <em>点击跳过</em>
          </button>
        ) : null}
        <div className="chat-composer-anchor">
        {showMentionMenu ? (
          <div
            aria-label="@ 命令与资料文件"
            className="chat-mention-menu"
            id={mentionMenuId}
            role="listbox"
          >
            {mentionEntries.map((item, index) => {
              if (item.kind === "file") {
                const file = item.file;
                return (
                  <button
                    aria-selected={mentionIndex === index}
                    id={`${mentionMenuId}-file-${file.id}`}
                    key={`file-${file.id}`}
                    onClick={() => attachLibraryFile(file)}
                    onMouseEnter={() => setMentionIndex(index)}
                    onMouseDown={(event) => event.preventDefault()}
                    role="option"
                    type="button"
                  >
                    <span className="chat-mention-menu__icon">
                      <FileText className="size-4" />
                    </span>
                    <span>
                      <strong>@{file.original_name}</strong>
                      <small>
                        资料库复用 · {file.parse_status}
                        {file.mime_type ? ` · ${file.mime_type}` : ""}
                      </small>
                    </span>
                    {mentionIndex === index ? (
                      <span className="chat-mention-menu__key">Enter</span>
                    ) : null}
                  </button>
                );
              }
              const command = item.command;
              return (
                <button
                  aria-selected={mentionIndex === index}
                  disabled={command.disabled}
                  id={`${mentionMenuId}-${command.id}`}
                  key={command.id}
                  onClick={() => activateComposerCommand(command.id)}
                  onMouseEnter={() => setMentionIndex(index)}
                  onMouseDown={(event) => event.preventDefault()}
                  role="option"
                  type="button"
                >
                  <span className="chat-mention-menu__icon">
                    <command.Icon className="size-4" />
                  </span>
                  <span>
                    <strong>@{command.label}</strong>
                    <small>{command.description}</small>
                  </span>
                  {mentionIndex === index && !command.disabled ? (
                    <span className="chat-mention-menu__key">Enter</span>
                  ) : null}
                </button>
              );
            })}
          </div>
        ) : null}
        <PromptInput
          key={composerInstanceKey}
          accept={composerFileAccept}
          maxFiles={generationMode === "image" ? 4 : undefined}
          maxFileSize={
            generationMode === "image" ? 50 * 1024 * 1024 : undefined
          }
          className={
            composerExpanded ? "chat-composer is-expanded" : "chat-composer"
          }
          multiple
          onError={(error) => {
            if (error.code === "accept") {
              toast.error(
                generationMode === "image"
                  ? imageEditEnabled
                    ? "参考图仅支持 PNG、JPEG、WEBP 等图片格式。"
                    : "当前绘图模型不支持参考图，请先切换绘图模型。"
                  : error.message,
              );
              return;
            }
            toast.error(error.message);
          }}
          onFileDialogReady={registerFileDialog}
          onSubmit={submitPrompt}
        >
          <PromptInputAttachments />
          <InputGroupAddon
            align="inline-start"
            className="chat-composer__start"
          >
            <PromptInputActionMenu>
              <PromptInputActionMenuTrigger
                aria-label="打开对话功能菜单"
                className="chat-composer__add"
                tooltip="打开功能菜单"
              />
              <PromptInputActionMenuContent className="chat-plus-menu">
                <DropdownMenuLabel>上下文</DropdownMenuLabel>
                <PromptInputActionAddAttachments
                  disabled={
                    sessionIsClosed ||
                    goalFlow.busy ||
                    (generationMode === "image" && !imageEditEnabled)
                  }
                  label={generationMode === "image" ? "添加参考图片" : "添加资料"}
                />
                {(["goal", "graph"] as const).map((id) => {
                  const command = composerCommands.find(
                    (item) => item.id === id,
                  );
                  if (!command) return null;
                  const Icon = command.Icon;
                  return (
                    <PromptInputActionMenuItem
                      disabled={command.disabled || goalFlow.busy}
                      key={command.id}
                      onSelect={() => selectComposerMenuCommand(command.id)}
                    >
                      <Icon className="size-4" />
                      {command.id === "goal" && goalMode
                        ? "退出目标设定"
                        : command.label}
                    </PromptInputActionMenuItem>
                  );
                })}
                <DropdownMenuSeparator />
                <DropdownMenuLabel>模式与工具</DropdownMenuLabel>
                {(["search", "agentic", "image"] as const).map((id) => {
                  const command = composerCommands.find(
                    (item) => item.id === id,
                  );
                  if (!command) return null;
                  const Icon = command.Icon;
                  return (
                    <PromptInputActionMenuItem
                      disabled={command.disabled || goalFlow.busy}
                      key={command.id}
                      onSelect={() => selectComposerMenuCommand(command.id)}
                    >
                      <Icon className="size-4" />
                      {command.id === "search" && searchRoute !== "disabled"
                        ? "关闭网络搜索"
                        : command.label}
                    </PromptInputActionMenuItem>
                  );
                })}
                <DropdownMenuSeparator />
                <DropdownMenuLabel>学习工作流</DropdownMenuLabel>
                {(["research", "roadmap", "schedule", "progress", "practice", "usage"] as const).map(
                  (id) => {
                    const command = composerCommands.find(
                      (item) => item.id === id,
                    );
                    if (!command) return null;
                    const Icon = command.Icon;
                    return (
                      <PromptInputActionMenuItem
                        disabled={command.disabled || goalFlow.busy}
                        key={command.id}
                        onSelect={() => selectComposerMenuCommand(command.id)}
                      >
                        <Icon className="size-4" />
                        {command.label}
                      </PromptInputActionMenuItem>
                    );
                  },
                )}
                <DropdownMenuSeparator />
                <PromptInputActionMenuItem
                  onSelect={() =>
                    navigate(`/w/${workspaceId}/research/tasks/new`, {
                      state: { pendingPrompt: composerText },
                    })
                  }
                >
                  <Sparkles className="size-4" />
                  打开深度研究任务
                </PromptInputActionMenuItem>
              </PromptInputActionMenuContent>
            </PromptInputActionMenu>
          </InputGroupAddon>
          <PromptInputBody>
            <PromptInputTextarea
              aria-activedescendant={
                showMentionMenu
                  ? (() => {
                      const entry = mentionEntries[mentionIndex];
                      if (!entry) return undefined;
                      if (entry.kind === "file") {
                        return `${mentionMenuId}-file-${entry.file.id}`;
                      }
                      if (entry.command.disabled) return undefined;
                      return `${mentionMenuId}-${entry.command.id}`;
                    })()
                  : undefined
              }
              aria-autocomplete="list"
              aria-controls={showMentionMenu ? mentionMenuId : undefined}
              aria-expanded={showMentionMenu}
              aria-label="输入消息"
              className="chat-composer__textarea"
              disabled={
                (goalMode
                  ? !activeModelProvider
                  : !activeModelProvider && !activeImageProvider) ||
                sessionIsClosed ||
                closeSessionMutation.isPending ||
                goalFlow.busy
              }
              onChange={(event) => setComposerText(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (
                  showMentionMenu &&
                  (event.key === "ArrowDown" || event.key === "ArrowUp")
                ) {
                  event.preventDefault();
                  const direction = event.key === "ArrowDown" ? 1 : -1;
                  let nextIndex = mentionIndex;
                  for (
                    let offset = 0;
                    offset < mentionEntries.length;
                    offset += 1
                  ) {
                    nextIndex =
                      (nextIndex + direction + mentionEntries.length) %
                      mentionEntries.length;
                    const entry = mentionEntries[nextIndex];
                    if (
                      entry &&
                      (entry.kind === "file" || !entry.command.disabled)
                    ) {
                      break;
                    }
                  }
                  setMentionIndex(nextIndex);
                } else if (
                  showMentionMenu &&
                  (event.key === "Enter" || event.key === "Tab")
                ) {
                  const entry = mentionEntries[mentionIndex];
                  event.preventDefault();
                  if (!entry) return;
                  if (entry.kind === "file") {
                    attachLibraryFile(entry.file);
                    return;
                  }
                  if (entry.command.disabled) return;
                  activateComposerCommand(entry.command.id);
                } else if (showMentionMenu && event.key === "Escape") {
                  event.preventDefault();
                  setDismissedMention(composerText);
                }
              }}
              onPaste={(event) => {
                const text = event.clipboardData.getData("text");
                if (Array.from(text).length > 10_000) setLongPaste(text);
              }}
              placeholder={
                goalMode
                  ? searchRoute !== "disabled"
                    ? "搜索待确认的信息…"
                    : goalComposerLocked
                      ? "完成审核，或开启搜索"
                      : "描述你的学习目标…"
                  : generationMode === "image"
                    ? pendingFiles.length
                      ? "描述你想如何修改参考图片…"
                      : "描述你想生成的画面…"
                    : "输入消息，或输入 @ 选择模式 / 文件…"
              }
              ref={composerTextareaRef}
              role="combobox"
              value={composerText}
            />
          </PromptInputBody>
           <InputGroupAddon align="inline-end" className="chat-composer__end">
            {contextUsageEnabled &&
            generationMode === "text" &&
            sessionId !== "new" &&
            contextUsage.data ? (
              <ContextUsageRing usage={contextUsage.data} />
            ) : null}
             {(() => {
               const modelTriggerDisabled =
                 !activeGenerationProvider ||
                 sessionIsClosed ||
                 closeSessionMutation.isPending ||
                 goalFlow.busy ||
                 status !== "ready";
               const modelTriggerAriaLabel =
                 generationMode === "image"
                   ? "选择绘图模型"
                   : "选择响应模式、思考力度和模型";
               const modelTriggerLabel =
                 generationMode === "image"
                   ? `绘图 · ${selectedImageModel?.id ?? "未选择"}`
                   : `${responseModeLabel} · ${activeModelProvider?.display_name ?? "模型"} / ${selectedModel?.id ?? "未选择"}`;
               const thinkingChoices = thinkingModes.length ? (
                 <DropdownMenuRadioGroup
                   onValueChange={(value) => setThinkingMode(value as ThinkingMode)}
                   value={thinkingMode}
                 >
                   {thinkingModes.map((mode) => (
                     <DropdownMenuRadioItem key={mode} value={mode}>
                       {thinkingLabels[mode]}
                     </DropdownMenuRadioItem>
                   ))}
                 </DropdownMenuRadioGroup>
               ) : (
                 <DropdownMenuItem disabled>由当前服务商决定</DropdownMenuItem>
               );
               const modelChoices = (
                 <>
                   <div className="relative px-1 pb-1">
                     <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                     <Input
                       aria-label="搜索模型"
                       className="h-8 pl-8 font-mono text-xs"
                       onChange={(event) => setModelSearch(event.target.value)}
                       onKeyDown={(event) => event.stopPropagation()}
                       onPointerDown={(event) => event.stopPropagation()}
                       placeholder="搜索模型名称或 Provider…"
                       value={modelSearch}
                     />
                   </div>
                   {filteredAvailableModelChoices.length ? (
                     <DropdownMenuRadioGroup
                       onValueChange={(value) => {
                         const choice = parseModelChoiceValue(value);
                         if (!choice) return;
                         setSelectedProviderId(choice.providerId);
                         setSelectedModelId(choice.modelId);
                       }}
                       value={
                         activeModelProvider && selectedModelId
                           ? modelChoiceValue(
                               activeModelProvider.id,
                               selectedModelId,
                             )
                           : ""
                       }
                     >
                       {filteredAvailableModelChoices.map(({ provider, model }) => (
                         <DropdownMenuRadioItem
                           key={`${provider.id}:${model.id}`}
                           value={modelChoiceValue(provider.id, model.id)}
                         >
                           <span className="flex min-w-0 flex-col">
                             <span className="truncate font-mono">{model.id}</span>
                             <span className="text-[10px] text-muted-foreground">
                               {provider.display_name} ·{" "}
                               {modelProtocolLabel(provider.provider_type)}
                             </span>
                           </span>
                           {model.capabilities?.supports_image_input
                             ? " · 图片"
                             : ""}
                         </DropdownMenuRadioItem>
                       ))}
                     </DropdownMenuRadioGroup>
                   ) : (
                     <DropdownMenuItem disabled>
                       {discoveredModels?.isPending ? "正在载入模型…" : "暂无可用模型"}
                     </DropdownMenuItem>
                   )}
                 </>
               );
               const modelMenu = (
             <DropdownMenu
               onOpenChange={(open) => {
                 if (!open) setModelSearch("");
               }}
             >
              <DropdownMenuTrigger asChild>
                {isPhoneLayout ? (
                  <Button
                    aria-label={modelTriggerAriaLabel}
                    className="topbar-model-trigger"
                    disabled={modelTriggerDisabled}
                    size="xs"
                    type="button"
                    variant="outline"
                  >
                    <span>{modelTriggerLabel}</span>
                    <ChevronDown className="size-3.5" />
                  </Button>
                ) : (
                  <PromptInputButton
                    aria-label={modelTriggerAriaLabel}
                    className="chat-composer__mode"
                    disabled={modelTriggerDisabled}
                    tooltip={
                      generationMode === "image" ? "绘图模型" : "响应模式与模型"
                    }
                  >
                    <span>{modelTriggerLabel}</span>
                    <ChevronDown className="size-3.5" />
                  </PromptInputButton>
                )}
              </DropdownMenuTrigger>
              <DropdownMenuContent
                align="end"
                className="chat-model-menu w-60"
                collisionPadding={12}
                side={isPhoneLayout ? "bottom" : "top"}
              >
                {generationMode === "image" ? (
                  <>
                    <DropdownMenuLabel>绘图模型</DropdownMenuLabel>
                    <div className="relative px-1 pb-1">
                      <Search className="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        aria-label="搜索绘图模型"
                        className="h-8 pl-8 font-mono text-xs"
                        onChange={(event) => setModelSearch(event.target.value)}
                        onKeyDown={(event) => event.stopPropagation()}
                        onPointerDown={(event) => event.stopPropagation()}
                        placeholder="搜索绘图模型或 Provider…"
                        value={modelSearch}
                      />
                    </div>
                    {filteredAvailableImageModelChoices.length ? (
                      <DropdownMenuRadioGroup
                        onValueChange={(value) => {
                          const choice = parseModelChoiceValue(value);
                          if (!choice) return;
                          setSelectedImageProviderId(choice.providerId);
                          setSelectedImageModelId(choice.modelId);
                        }}
                        value={
                          activeImageProvider && selectedImageModel
                            ? modelChoiceValue(
                                activeImageProvider.id,
                                selectedImageModel.id,
                              )
                            : ""
                        }
                      >
                        {filteredAvailableImageModelChoices.map(
                          ({ provider, model }) => (
                            <DropdownMenuRadioItem
                              key={`${provider.id}:${model.id}`}
                              value={modelChoiceValue(provider.id, model.id)}
                            >
                              <span className="flex min-w-0 flex-col">
                                <span className="truncate font-mono">{model.id}</span>
                                <span className="text-[10px] text-muted-foreground">
                                  {provider.display_name} · 绘图
                                </span>
                              </span>
                            </DropdownMenuRadioItem>
                          ),
                        )}
                      </DropdownMenuRadioGroup>
                    ) : (
                      <DropdownMenuItem disabled>
                        {discoveredImageModels?.isPending
                          ? "正在载入绘图模型…"
                          : "暂无已配置的绘图模型"}
                      </DropdownMenuItem>
                    )}
                  </>
                ) : (
                  <>
                <DropdownMenuLabel>响应模式</DropdownMenuLabel>
                <DropdownMenuRadioGroup
                  onValueChange={(value) => {
                    const nextMode = value as ResponseMode;
                    setGenerationMode("text");
                    setResponseMode(nextMode);
                    if (nextMode === "fast") {
                      setThinkingMode("off");
                    } else if (nextMode === "agentic") {
                      // Prefer medium thinking intensity for agent mode (product default).
                      const preferred = ["medium", "high", "low", "xhigh"].find(
                        (mode) => thinkingModes.includes(mode as ThinkingMode),
                      );
                      setThinkingMode((preferred as ThinkingMode | undefined) ?? thinkingMode);
                      // Only auto-enable search when a SearchProvider is already
                      // enabled; otherwise leave search off so agent tools work
                      // without forcing web_search against an unavailable provider.
                      if (hasAuthorizedAgentSearchProvider) {
                        setSearchRoute((current) =>
                          current === "disabled" ? "auto" : current,
                        );
                      }
                    } else if (thinkingMode === "off") {
                      setThinkingMode(
                        thinkingModes.includes("medium") ? "medium" : thinkingModes[0] ?? "medium",
                      );
                    }
                  }}
                  value={responseMode}
                >
                  <DropdownMenuRadioItem
                    disabled={thinkingRequired}
                    value="fast"
                  >
                    极速{thinkingRequired ? "（该模型仅支持思考）" : ""}
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem
                    disabled={!supportsThinkingMode}
                    value="thinking"
                  >
                    思考{supportsThinkingMode ? "" : "（模型未声明推理能力）"}
                  </DropdownMenuRadioItem>
                  <DropdownMenuRadioItem
                    disabled={!supportsAgentMode}
                    value="agentic"
                  >
                    智能体{supportsAgentMode ? "" : agentModeUnavailableLabel}
                  </DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
                <DropdownMenuSeparator />
                {isPhoneLayout ? (
                  // Sub-menus open sideways and overflow narrow screens, so
                  // phones get every section inline in one scrollable menu.
                  <>
                    <DropdownMenuLabel>
                      思考力度
                      {responseMode === "fast" ? (
                        <span className="ml-2 text-xs font-normal text-muted-foreground">
                          极速模式下已关闭
                        </span>
                      ) : null}
                    </DropdownMenuLabel>
                    {responseMode === "fast" ? null : thinkingChoices}
                    <DropdownMenuSeparator />
                    <DropdownMenuLabel>模型</DropdownMenuLabel>
                    {modelChoices}
                  </>
                ) : (
                  <>
                    <DropdownMenuSub>
                      <DropdownMenuSubTrigger disabled={responseMode === "fast"}>
                        思考力度
                        <span className="ml-auto text-xs text-muted-foreground">
                          {responseMode === "fast"
                            ? "已关闭"
                            : thinkingModes.length
                            ? thinkingLabels[thinkingMode]
                            : "当前模型未声明"}
                        </span>
                      </DropdownMenuSubTrigger>
                      <DropdownMenuSubContent>
                        {thinkingChoices}
                      </DropdownMenuSubContent>
                    </DropdownMenuSub>
                    <DropdownMenuSub>
                      <DropdownMenuSubTrigger>模型</DropdownMenuSubTrigger>
                      <DropdownMenuSubContent className="max-h-[min(60vh,32rem)] overflow-y-auto">
                        {modelChoices}
                      </DropdownMenuSubContent>
                    </DropdownMenuSub>
                  </>
                )}
                  </>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
               );
               // Portal keeps composer state/context while the trigger lives
               // in the top bar on phone widths.
               return isPhoneLayout && topbarModelSlot
                 ? createPortal(modelMenu, topbarModelSlot)
                 : modelMenu;
             })()}
            <PromptInputButton
              aria-label={isListening ? "停止语音输入" : "开始语音输入"}
              aria-pressed={isListening}
              className="chat-composer__microphone"
              disabled={
                !activeGenerationProvider ||
                sessionIsClosed ||
                closeSessionMutation.isPending ||
                goalFlow.busy
              }
              onClick={toggleDictation}
              tooltip={isListening ? "停止语音输入" : "语音输入"}
            >
              <Mic className="size-4" />
            </PromptInputButton>
            <PromptInputSubmit
              aria-label={
                status === "streaming" || status === "submitted" || goalFlow.busy
                  ? "停止生成"
                  : "发送消息"
              }
              className={cn(
                "chat-composer__submit",
                status === "streaming" && "chat-composer__submit--stop",
              )}
              disabled={
                !activeGenerationProvider ||
                !activeGenerationModelId ||
                sessionIsClosed ||
                closeSessionMutation.isPending ||
                goalFlow.busy ||
                goalComposerLocked
              }
              onStop={() => {
                const messageId = activeMessageId.current;
                const streamSessionId = activeStreamSessionId.current;
                const cancellation =
                  messageId && streamSessionId
                    ? cancelSessionMessage(streamSessionId, messageId)
                        .catch((error: Error) => toast.error(error.message))
                        .then(() => undefined)
                    : Promise.resolve();
                activeCancellationRef.current = cancellation;
                void cancellation.finally(() => {
                  if (activeCancellationRef.current === cancellation)
                    activeCancellationRef.current = null;
                });
                // Only abort the currently viewed session's stream.
                abortSessionStream(streamSessionId ?? sessionId);
                abortRef.current?.abort();
                markSessionRunning(streamSessionId ?? sessionId, false);
                setStatus("submitted");
              }}
              status={goalFlow.busy ? "submitted" : status}
            >
              {status === "streaming" && !goalFlow.busy ? (
                <Square className="size-3.5" color="#111" fill="#111" strokeWidth={0} />
              ) : status === "submitted" || goalFlow.busy ? (
                <LoaderCircle className="size-3.5 animate-spin" />
              ) : (
                <ArrowUp className="size-4" />
              )}
            </PromptInputSubmit>
          </InputGroupAddon>
         </PromptInput>
        </div>
      </div>
      <Dialog
        onOpenChange={(open) => {
          if (!open && !retry.isPending) setRetryTarget(null);
        }}
        open={Boolean(retryTarget)}
      >
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>重试回答并保留版本</DialogTitle>
            <DialogDescription>
              为这次重试显式选择真实 Provider、模型和调用模式。新回答会保存为可追溯版本，原回答与 Provider Trace 不会被覆盖。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="retry-provider">模型 Provider</Label>
              <Select
                disabled={retry.isPending || !modelProviders.length}
                onValueChange={(value) => {
                  setRetryProviderId(value);
                  setRetryModelId("");
                }}
                value={retryProviderId}
              >
                <SelectTrigger className="w-full" id="retry-provider">
                  <SelectValue placeholder="选择真实模型 Provider" />
                </SelectTrigger>
                <SelectContent>
                  {modelProviders.map((provider) => (
                    <SelectItem key={provider.id} value={provider.id}>
                      {provider.display_name} · {provider.provider_type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!modelProviders.length && !providers.isPending ? (
                <div className="flex items-center justify-between gap-3 text-xs text-amber-700 dark:text-amber-300">
                  <span>当前工作区没有已启用的真实模型 Provider。</span>
                  <Button
                    onClick={() =>
                      navigate(`/w/${workspaceId}/settings/providers`)
                    }
                    size="xs"
                    variant="outline"
                  >
                    前往设置
                  </Button>
                </div>
              ) : null}
            </div>

            <div className="grid gap-2">
              <Label htmlFor="retry-model">模型</Label>
              <Select
                disabled={
                  retry.isPending ||
                  !retryProvider ||
                  retryDiscoveredModels.isPending ||
                  !retryModelOptions.some((model) => model.remote)
                }
                onValueChange={setRetryModelId}
                value={retryModelId}
              >
                <SelectTrigger className="w-full" id="retry-model">
                  <SelectValue
                    placeholder={
                      retryDiscoveredModels.isPending
                        ? "正在载入模型…"
                        : "选择远程模型"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {retryModelOptions.map((model) => (
                    <SelectItem
                      disabled={!model.remote}
                      key={model.id}
                      value={model.id}
                    >
                      {model.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {retryDiscoveredModels.isError ? (
                <div className="flex items-center justify-between gap-3 text-xs text-destructive">
                  <span>模型发现失败，尚未发起重试。</span>
                  <Button
                    disabled={retryDiscoveredModels.isFetching}
                    onClick={() => void retryDiscoveredModels.refetch()}
                    size="xs"
                    variant="ghost"
                  >
                    <RefreshCcw className="size-3" />
                    重试发现
                  </Button>
                </div>
              ) : null}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-2">
                <Label htmlFor="retry-response-mode">响应模式</Label>
                <Select
                  disabled={retry.isPending}
                  onValueChange={(value) => {
                    const mode = value as ResponseMode;
                    setRetryResponseMode(mode);
                    if (mode === "fast") setRetryThinkingMode("off");
                    else if (retryThinkingMode === "off")
                      setRetryThinkingMode(
                        retryThinkingModes.includes("medium")
                          ? "medium"
                          : (retryThinkingModes[0] ?? "off"),
                      );
                  }}
                  value={retryResponseMode}
                >
                  <SelectTrigger className="w-full" id="retry-response-mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="fast">极速</SelectItem>
                    <SelectItem
                      disabled={!retryThinkingModes.length}
                      value="thinking"
                    >
                      思考
                    </SelectItem>
                    <SelectItem
                      disabled={!retrySupportsAgentMode}
                      value="agentic"
                    >
                      智能体
                      {retrySupportsAgentMode
                        ? ""
                        : "（所选模型未声明结构化工具能力）"}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="retry-thinking-mode">思考力度</Label>
                <Select
                  disabled={
                    retry.isPending ||
                    retryResponseMode === "fast" ||
                    !retryThinkingModes.length
                  }
                  onValueChange={(value) =>
                    setRetryThinkingMode(value as ThinkingMode)
                  }
                  value={retryThinkingMode}
                >
                  <SelectTrigger className="w-full" id="retry-thinking-mode">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {retryThinkingModes.map((mode) => (
                      <SelectItem key={mode} value={mode}>
                        {thinkingLabels[mode]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="grid gap-3 rounded-lg border p-3">
              <div className="flex items-start gap-3">
                <Checkbox
                  checked={retryWebSearch}
                  disabled={retry.isPending || !retryCanUseNetworkSearch}
                  id="retry-web-search"
                  onCheckedChange={(checked) =>
                    setRetryWebSearch(checked === true)
                  }
                />
                <Label
                  className="grid cursor-pointer gap-1 font-normal"
                  htmlFor="retry-web-search"
                >
                  <span className="font-medium">启用联网搜索</span>
                  <span className="text-xs text-muted-foreground">
                    {retryCanUseNetworkSearch
                      ? "由后端按模型能力与已授权 Search Provider 选择真实搜索路由。"
                      : "当前模型与工作区没有可用的真实搜索能力。"}
                  </span>
                </Label>
              </div>
              {retryWebSearch && retryCanUseNetworkSearch ? (
                <div className="grid gap-2">
                  <Label htmlFor="retry-allowed-domains">限定来源域名（可选）</Label>
                  <Input
                    disabled={retry.isPending}
                    id="retry-allowed-domains"
                    onChange={(event) =>
                      setRetryAllowedDomains(event.currentTarget.value)
                    }
                    placeholder="例如：docs.python.org, react.dev"
                    value={retryAllowedDomains}
                  />
                  <p className="text-xs text-muted-foreground">
                    使用逗号或空格分隔；只允许这些域名及其子域的结果。
                  </p>
                </div>
              ) : null}
            </div>
          </div>
          <DialogFooter>
            <Button
              disabled={retry.isPending}
              onClick={() => setRetryTarget(null)}
              variant="outline"
            >
              取消
            </Button>
            <Button
              disabled={
                retry.isPending ||
                !retryTarget ||
                !retryProvider ||
                !retrySelectedModel?.remote ||
                (retryResponseMode === "thinking" &&
                  !retryThinkingModes.includes(retryThinkingMode)) ||
                (retryResponseMode === "agentic" && !retrySupportsAgentMode)
              }
              onClick={submitRetry}
            >
              {retry.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <RefreshCcw className="size-4" />
              )}
              {retry.isPending ? "正在生成新版本…" : "开始重试"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        onOpenChange={(open) => {
          if (!open) setImageRetryTarget(null);
        }}
        open={Boolean(imageRetryTarget)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>重试绘图</DialogTitle>
            <DialogDescription>
              选择绘图模型后，将使用原始提示词重新生成一张图片，结果会作为一条新回答发送。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="image-retry-model">绘图模型</Label>
              <Select
                disabled={!availableImageModelChoices.length}
                onValueChange={setImageRetryChoice}
                value={imageRetryChoice}
              >
                <SelectTrigger className="w-full" id="image-retry-model">
                  <SelectValue
                    placeholder={
                      availableImageModelChoices.length
                        ? "选择绘图模型"
                        : "正在载入绘图模型…"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {availableImageModelChoices.map(({ provider, model }) => (
                    <SelectItem
                      key={`${provider.id}:${model.id}`}
                      value={modelChoiceValue(provider.id, model.id)}
                    >
                      {model.id} · {provider.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {imageRetryTarget ? (
              <p className="line-clamp-3 text-xs text-muted-foreground">
                提示词：{imageRetryTarget.prompt}
              </p>
            ) : null}
          </div>
          <DialogFooter>
            <Button onClick={() => setImageRetryTarget(null)} variant="outline">
              取消
            </Button>
            <Button
              disabled={
                !imageRetryTarget ||
                !parseModelChoiceValue(imageRetryChoice) ||
                status !== "ready"
              }
              onClick={submitImageRetry}
            >
              <ImageIcon className="size-4" />
              重新生成
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        onOpenChange={(open) => {
          if (!closeSessionMutation.isPending) setCloseDialogOpen(open);
        }}
        open={closeDialogOpen}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>结束本次学习并复盘？</DialogTitle>
            <DialogDescription>
              结束后将停止向这个会话提交新消息，并立即按同一掌握度规则复盘已关联的学习证据。既有消息、版本、附件和证据会保留；如需继续探索，可从现有回答创建分支或新会话。
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              disabled={closeSessionMutation.isPending}
              onClick={() => setCloseDialogOpen(false)}
              variant="outline"
            >
              继续学习
            </Button>
            <Button
              disabled={closeSessionMutation.isPending}
              onClick={() => closeSessionMutation.mutate()}
            >
              {closeSessionMutation.isPending ? (
                <LoaderCircle className="size-4 animate-spin" />
              ) : (
                <Check className="size-4" />
              )}
              确认结束并复盘
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <Dialog
        onOpenChange={(open) => {
          if (!open && !reviewGraphChange.isPending) {
            setRejectProposalId(null);
            setRejectReason("");
          }
        }}
        open={Boolean(rejectProposalId)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>拒绝这份图谱提案？</DialogTitle>
            <DialogDescription>
              拒绝只会结束本次待审提案，不会修改正式图谱。理由可选，便于后续对话继续调整。
            </DialogDescription>
          </DialogHeader>
          <Textarea
            aria-label="拒绝图谱提案的理由"
            disabled={reviewGraphChange.isPending}
            onChange={(event) => setRejectReason(event.currentTarget.value)}
            placeholder="例如：节点粒度太粗，请拆分后重新生成（可不填）"
            value={rejectReason}
          />
          <DialogFooter>
            <Button
              disabled={reviewGraphChange.isPending}
              onClick={() => {
                setRejectProposalId(null);
                setRejectReason("");
              }}
              variant="outline"
            >
              取消
            </Button>
            <Button
              disabled={!rejectProposalId || reviewGraphChange.isPending}
              onClick={() => {
                if (!rejectProposalId) return;
                reviewGraphChange.mutate({
                  decision: "reject",
                  proposalId: rejectProposalId,
                  reason: rejectReason.trim(),
                });
              }}
              variant="destructive"
            >
              {reviewGraphChange.isPending ? "正在拒绝…" : "确认拒绝"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export function VersionsPage() {
  const { sessionId = "", workspaceId = "" } = useParams();
  const queryClient = useQueryClient();
  const [messageId, setMessageId] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [draftId, setDraftId] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const messages = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "messages", sessionId),
    queryFn: () => listSessionMessages(sessionId, { limit: 100 }),
    enabled: Boolean(sessionId),
    gcTime: 15_000,
  });
  const assistants = (messages.data ?? []).filter(
    (item) => item.role === "assistant",
  );
  const targetId = messageId || assistants.at(-1)?.id || "";
  const versions = useQuery({
    queryKey: workspaceQueryKey(workspaceId, "message-versions", sessionId, targetId),
    queryFn: () => listMessageVersions(sessionId, targetId),
    enabled: Boolean(targetId),
  });
  const create = useMutation({
    mutationFn: () => createCompositeDraft(targetId, selected),
    onSuccess: (draft) => {
      setDraftId(draft.id);
      setDraftContent(draft.content);
    },
    onError: (error) => toast.error(error.message),
  });
  const confirm = useMutation({
    mutationFn: () => confirmCompositeDraft(draftId),
    onSuccess: () => {
      toast.success("合并版本已保存并设为当前版本");
      setDraftId("");
      setSelected([]);
      void queryClient.invalidateQueries({ queryKey: workspaceQueryKey(workspaceId, "messages", sessionId) });
      void versions.refetch();
    },
    onError: (error) => toast.error(error.message),
  });
  return (
    <PageFrame>
      <PageIntro
        description="重试、换模型和创建分支都产生新版本或新 Session，原回答与 Provider Trace 不会被覆盖。"
        eyebrow="Session history"
        title="会话分支与版本对比"
      />
      <Surface className="p-5">
        <SectionHeading title="选择助手消息" />
        <Select
          onValueChange={(value) => {
            setMessageId(value);
            setSelected([]);
          }}
          value={targetId}
        >
          <SelectTrigger className="mt-4">
            <SelectValue placeholder="选择消息" />
          </SelectTrigger>
          <SelectContent>
            {assistants.map((item) => (
              <SelectItem key={item.id} value={item.id}>
                {item.content.slice(0, 80) || item.id}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </Surface>
      <Surface className="p-5">
        <SectionHeading
          action={
            <Button
              disabled={selected.length < 2 || create.isPending}
              onClick={() => create.mutate()}
              size="sm"
            >
              <GitCompareArrows className="size-4" />
              合并所选版本
            </Button>
          }
          title="真实版本"
        />
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          {versions.data?.map((version) => (
            <label className="rounded-xl border p-4" key={version.id}>
              <div className="flex items-center gap-3">
                <input
                  checked={selected.includes(version.id)}
                  onChange={(event) =>
                    setSelected((current) =>
                      event.target.checked
                        ? [...current, version.id]
                        : current.filter((id) => id !== version.id),
                    )
                  }
                  type="checkbox"
                />
                <p className="text-sm font-semibold">v{version.version}</p>
                <StatePill label={version.status} status={version.status} />
              </div>
              <div className="mt-4 font-mono text-[11px] text-muted-foreground">
                {String(version.provider_trace.provider_id ?? "unknown")} ·{" "}
                {version.id}
              </div>
            </label>
          ))}
        </div>
        {!versions.data?.length ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            该消息还没有可比较版本。
          </p>
        ) : null}
      </Surface>
      <Dialog
        onOpenChange={(open) => {
          if (!open) setDraftId("");
        }}
        open={Boolean(draftId)}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>确认合并后的新版本</DialogTitle>
            <DialogDescription>
              来源版本会永久保留；确认后新版本自动成为当前展示版本。
            </DialogDescription>
          </DialogHeader>
          <Textarea className="min-h-64" readOnly value={draftContent} />
          <div className="flex justify-end gap-2">
            <Button onClick={() => setDraftId("")} variant="outline">
              取消
            </Button>
            <Button
              disabled={confirm.isPending}
              onClick={() => confirm.mutate()}
            >
              {confirm.isPending ? "保存中…" : "确认并设为当前版本"}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </PageFrame>
  );
}
