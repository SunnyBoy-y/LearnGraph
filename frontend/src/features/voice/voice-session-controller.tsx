import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { apiClient, ApiError } from "@/api/client";
import { createUuid } from "@/lib/uuid";
import {
  cancelVoiceTask as requestVoiceTaskCancel,
  getVoiceIceServers,
  getVoiceSession as requestVoiceSession,
  getVoiceTask as requestVoiceTask,
  startVoiceTask as requestVoiceTaskStart,
} from "@/api/voice";
import { clearVoiceSessionResumable, markVoiceSessionResumable, setVoiceSessionActive } from "./voice-session-markers";
import {
  degradedNotice,
  deriveDegradedStages,
  deriveModelPin,
  emptyTranscriptState,
  requiresTextFallback,
  orderedEntries,
  reduceVoiceEvent,
  seedFromServerTurns,
  userEntryId,
  type TranscriptEntry,
  type TranscriptState,
  type VoiceEventLike,
} from "./voice-transcript";
import {
  emptyVoiceTaskState,
  mergeVoiceTaskPatches,
  mergeVoiceTaskSnapshots,
  mergeVoiceTaskResults,
  reduceVoiceTaskEvent,
  voiceTaskIsActive,
  type VoiceTask,
  type VoiceTaskActivity,
  type VoiceTaskPatch,
  type VoiceTaskState,
} from "./voice-tasks";
import type { VoiceBotOutputPart } from "./voice-bot-output";
import { markVoiceLatency, resetVoiceLatency } from "./voice-latency-store";
import { VoicePlaybackTranscript } from "./voice-playback";
import { VoiceUserTranscript } from "./voice-user-transcript";

export type VoiceTransportState = "idle" | "connecting" | "connected" | "reconnecting" | "error";
export type VoiceSessionState = "closed" | "ready" | "listening" | "thinking" | "speaking" | "error";
export type ThinkingLimit = "off" | "low" | "medium" | "high" | "xhigh";
export type VoiceTranscriptRole = TranscriptEntry["role"];
export type VoiceAudioSource = "idle" | "user" | "assistant";
export type VoiceTranscript = TranscriptEntry;
export type VoiceTaskEvent = VoiceTaskPatch;

/**
 * 字幕段落类型与它的状态机都在 `./voice-bot-output`：数据源是**官方**的
 * `bot-output`（协议 2.x 句级路径），前端不再自己排期、也不算任何时钟。
 */
export type { VoiceBotOutputPart, VoiceCaptionMode } from "./voice-bot-output";

export interface VoiceRenderUpdate extends Omit<Partial<TranscriptEntry>, "role"> {
  id: string;
  role: "user" | "assistant";
  text: string;
  final: boolean;
  createdAt: string;
  /** Full LLM draft currently available for the canvas typewriter. */
  streamingText?: string;
  /**
   * Row ids this update retires from the conversation.
   *
   * A user turn is rendered as one bubble per ASR segment while it is still
   * open (the pauses are visible) and collapses into the single authoritative
   * bubble the server persisted for that turn. Nothing in the canvas can retire
   * a row on its own, so the collapse has to be stated explicitly here.
   */
  removes?: string[];
  /**
   * Turn whose ephemeral per-segment user rows must go.
   *
   * `removes` is a snapshot of the rows this client happens to remember, which
   * makes the collapse depend on the *next* turn boundary arriving at all: a
   * `user.final` re-delivered after a flush (the RTVI push channel and the HTTP
   * event catch-up both feed the same handler) allocates a fresh row key nobody
   * has on their retire list, and that row then sat next to the authoritative one
   * for as long as the turn stayed open. Stating the *turn* instead lets the
   * canvas resolve ownership itself -- every per-segment row of that turn is
   * retired, whenever it was created and in whatever order the events arrived.
   */
  retireTurnSegments?: string;
  /**
   * 该上屏的段落（已点亮的 + 已经开始播放的当前句），顺序与官方通道宣布的一致。
   *
   * 每一段带自己的"已读游标"：`new`/`in-progress` 的段整段未读（灰），`completed` 的段
   * 整段已读。未读部分只活在这份内存里——刷新或被打断后就撤下，账本与转录永远只认真的
   * 播过的句子。这里已经裁过一刀（见 `revealedParts`）：预读句在真实播放 marker 到达前
   * 不会出现在画布上。
   */
  captionParts?: VoiceBotOutputPart[];
  /** Marks an ephemeral per-segment user row (see `removes`). */
  voiceSegment?: boolean;
}
/**
 * What the call is actually using, versus what the user asked for.
 *
 * A model switch is pinned as "next turn takes effect", so between the request
 * and the next turn (or the next connection) the two genuinely differ. Keeping
 * both lets the UI state which one is in force instead of showing the requested
 * model as if it were live.
 */
export interface VoiceModelPin {
  requestedModelId: string | null;
  /** The model this call is using right now. */
  effectiveModelId: string | null;
  /** True once the running pipeline confirmed it re-pointed its LLM service. */
  repointed: boolean;
  /** "repointed": next turn uses it. "next_connection": only a reconnect does. */
  certainty: "repointed" | "next_connection";
  epoch: number;
  at: string;
}

export interface VoiceEventEnvelope { event_id: string; session_id: string; session_epoch: number; turn_id?: string | null; event_seq: number; request_id?: string | null; phase: string; causality?: Record<string, unknown>; timestamp?: string | null; audio_cursor_ms?: number | null; type?: string; payload: Record<string, unknown> }
/** 与 Pipecat `SmallWebRTCPatchRequest.candidates[].IceCandidate` 一一对应（snake_case）。 */
export interface VoiceIceCandidate { candidate: string; sdp_mid: string; sdp_mline_index: number }
export interface VoiceSessionSnapshot {
  workspaceId: string; sessionId: string; transport: VoiceTransportState; state: VoiceSessionState;
  thinkingLimit: ThinkingLimit; transcript: VoiceTranscript[]; tasks: VoiceTask[]; error: string | null;
  modelId: string | null; providerId: string | null;
  sessionIdRemote: string | null;
  signalingUrl: string | null;
  /** Local-only mute: the mic track is disabled without telling the pipeline. */
  muted: boolean;
  /**
   * Live caption state fed by the pipeline's RTVI channel: the in-flight user
   * hypothesis plus the assistant text as it is generated. Both are cleared when
   * their turn finalizes into `transcript`.
   */
  interimUserText: string;
  streamingAssistantText: string;
  audioLevel: number;
  audioSource: VoiceAudioSource;
  lastEventSeq: number;
  sessionEpoch: number;
  /** The model this call is actually using (differs from modelId while a switch is pending). */
  effectiveModelId: string | null;
  /** Null until the user changes the model during a live call. */
  modelPin: VoiceModelPin | null;
  /**
   * Pipeline stages that exhausted their retry budget. Non-empty means the call
   * is running in a reduced mode; `asr` in particular means the user can no
   * longer be understood by speaking and text input must be allowed.
   */
  degradedStages: string[];
  /** Human-readable explanation for the degraded banner. */
  degradedNotice: string | null;
  /**
   * 最近一条**需要用户知道代价**的 pipeline 提示（durable `processor.notice`）。
   *
   * 它不是降级：通话照常、不强制文字输入、麦克风不静音。目前唯一来源是"前台实时
   * 回合要求关闭思考，但这个模型/方言表达不出来"——那种情况下模型会按厂商默认继续
   * 思考，每轮首字明显变慢。用户不知道就会以为服务坏了，所以必须说出来。
   *
   * 只认白名单里的 code：ASR 自适应重连、VAD 兜底之类的 notice 是排障信息，留在
   * durable 事件里即可，不该占用界面。
   */
  pipelineNotice: string | null;
  /** True when ordinary text chat must be allowed even though voice mode is on. */
  textFallback: boolean;
  /**
   * The ICE path this call settled on, as reported by the server once the
   * candidate pair is nominated. Null until that event arrives.
   */
  icePath: VoiceIcePath | null;
  /**
   * True when the browser refused to start playback of the tutor's audio (the
   * autoplay policy blocks sound until the page has been interacted with).
   *
   * The call itself is healthy in every other respect -- the pipeline is
   * talking, the captions advance, the mic still works -- so this is not an
   * `error`: it needs its own flag plus a one-click fix (a gesture-triggered
   * `play()`), otherwise the page looks connected and stays silent.
   */
  playbackBlocked: boolean;
}

/** Which ICE candidate pair a live call is using (from the ``session.ice`` event). */
export interface VoiceIcePath {
  /** Local and remote candidate types, e.g. "host↔prflx" or "relay↔relay". */
  label: string;
  /** True when either end of the pair is a TURN relay (i.e. it costs relay traffic). */
  relayed: boolean;
  protocol: string;
}

const STORAGE_KEY = "learngraph.voice.preferences.v1";
const REMOTE_SESSION_STORAGE_KEY = "learngraph.voice.remote-sessions.v1";

/**
 * `processor.notice` 里唯一会被提到界面上的 code：前台实时回合要求关闭思考，但当前
 * 模型/方言表达不出关闭字段（服务端仍照常服务，模型会按厂商默认继续思考）——这条
 * 提示的价值就是告诉用户"每轮首字变慢是已知原因，不是服务坏了"。
 */
const THINKING_OFF_UNAVAILABLE_NOTICE_CODE = "thinking_off_unavailable";

/**
 * Deployment-wide ICE servers (STUN/TURN) configured by the instance
 * administrator.
 *
 * Cached for a few minutes rather than per dial: the credential the server hands
 * out lives for hours, and a call must not wait on a settings round-trip. Any
 * failure -- offline, expired session, relay misconfigured -- degrades to an
 * empty list, i.e. exactly the behaviour of a deployment that never configured a
 * relay, so a broken relay can never make calling impossible.
 *
 * Degrading is not the same as failing silently, though. The empty result is
 * cached for a much shorter time than a real one, and the error is reported: a
 * single transient failure used to pin `[]` for the full five minutes, which for
 * a remote caller meant five minutes of relay-less calls that could not connect,
 * with nothing anywhere saying why.
 */
const ICE_SERVERS_TTL_MS = 5 * 60_000;
const ICE_SERVERS_FAILURE_TTL_MS = 15_000;
let iceServersCache: { at: number; servers: RTCIceServer[]; ttlMs: number } | null = null;

async function loadIceServers(): Promise<RTCIceServer[]> {
  const now = Date.now();
  if (iceServersCache && now - iceServersCache.at < iceServersCache.ttlMs) {
    return iceServersCache.servers;
  }
  try {
    const config = await getVoiceIceServers();
    const servers = (config.iceServers ?? [])
      .filter((item) =>
        Array.isArray(item.urls) ? item.urls.length > 0 : Boolean(item.urls),
      )
      .map((item) => ({
        urls: item.urls,
        username: item.username ?? undefined,
        credential: item.credential ?? undefined,
      })) as RTCIceServer[];
    iceServersCache = { at: now, servers, ttlMs: ICE_SERVERS_TTL_MS };
    return servers;
  } catch (error) {
    console.warn(
      "[voice] ICE servers unavailable; dialing without STUN/TURN (a direct path can still connect, a remote one cannot)",
      error,
    );
    iceServersCache = { at: now, servers: [], ttlMs: ICE_SERVERS_FAILURE_TTL_MS };
    return [];
  }
}

function readPersistedRemoteSession(workspaceId: string, sessionId: string): string | null {
  if (!workspaceId || !sessionId || typeof window === "undefined") return null;
  try {
    const raw = JSON.parse(window.localStorage.getItem(REMOTE_SESSION_STORAGE_KEY) || "{}");
    const value = raw?.[`${workspaceId}:${sessionId}`];
    return typeof value === "string" && value ? value : null;
  } catch {
    return null;
  }
}

function persistRemoteSession(workspaceId: string, sessionId: string, remoteId: string) {
  if (!workspaceId || !sessionId || !remoteId || typeof window === "undefined") return;
  try {
    const stored = JSON.parse(window.localStorage.getItem(REMOTE_SESSION_STORAGE_KEY) || "{}");
    const next = stored && typeof stored === "object" && !Array.isArray(stored) ? { ...stored } : {};
    next[`${workspaceId}:${sessionId}`] = remoteId;
    window.localStorage.setItem(REMOTE_SESSION_STORAGE_KEY, JSON.stringify(next));
  } catch { /* storage is optional */ }
}

function forgetRemoteSession(workspaceId: string, sessionId: string) {
  if (!workspaceId || !sessionId || typeof window === "undefined") return;
  try {
    const stored = JSON.parse(window.localStorage.getItem(REMOTE_SESSION_STORAGE_KEY) || "{}");
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) return;
    delete stored[`${workspaceId}:${sessionId}`];
    window.localStorage.setItem(REMOTE_SESSION_STORAGE_KEY, JSON.stringify(stored));
  } catch { /* storage is optional */ }
}
const defaultSnapshot: VoiceSessionSnapshot = { workspaceId: "", sessionId: "", transport: "idle", state: "closed", thinkingLimit: "high", transcript: [], tasks: [], error: null, modelId: null, providerId: null, sessionIdRemote: null, signalingUrl: null, muted: false, interimUserText: "", streamingAssistantText: "", audioLevel: 0, audioSource: "idle", lastEventSeq: 0, sessionEpoch: 0, effectiveModelId: null, modelPin: null, degradedStages: [], degradedNotice: null, pipelineNotice: null, textFallback: false, icePath: null, playbackBlocked: false };
let snapshot: VoiceSessionSnapshot = { ...defaultSnapshot };
const sessionCache = new Map<string, VoiceSessionSnapshot>();
const listeners = new Set<() => void>();
let abortController: AbortController | null = null;
let peerConnection: RTCPeerConnection | null = null;
let localStream: MediaStream | null = null;
let dataChannel: RTCDataChannel | null = null;

/** Pipecat's RTVI wire constants (label is fixed by the protocol). */
const RTVI_LABEL = "rtvi-ai";
const RTVI_VERSION = "2.1.0";
/** Custom client message the embedded pipeline turns into a barge-in. */
export const VOICE_INTERRUPT_MESSAGE = "learngraph-interrupt";
/**
 * Custom client message announcing a typed turn's idempotency key.
 *
 * `send-text` cannot carry it (Pipecat's payload has only content/options, and
 * the append frame it produces only holds role/content), yet the worker needs it
 * to attach to the turn the control plane already created instead of opening a
 * second one. Sent on the same ordered data channel immediately before
 * `send-text`, and consumed by the journal (`expect_typed_turn`).
 */
export const VOICE_TYPED_TURN_MESSAGE = "learngraph-typed-turn";

function sendRtvi(message: { type: string; data?: unknown }): boolean {
  if (dataChannel?.readyState !== "open") return false;
  dataChannel.send(JSON.stringify({ label: RTVI_LABEL, id: createUuid(), ...message }));
  return true;
}

function dispatchVoiceRender(update: VoiceRenderUpdate) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("learngraph:voice-render", { detail: update }));
  }
}

let pendingUserFinal = "";
let activeUserTurnId = "";
const playbackTranscript = new VoicePlaybackTranscript();
const userTranscript = new VoiceUserTranscript();
let reconnectTimer: number | null = null;
let reconnectAttempt = 0;
let iceRestartAttempted = false;
const seenEventIds = new Set<string>();
const renderedLiveEventIds = new Set<string>();
const pendingTyped = new Map<string, { text: string; turnId?: string }>();
const turnAudioCursors = new Map<string, number>();
/**
 * LLM text arrives before the sentence/audio ledger. Keep the streamed
 * fragments per server turn so the conversation canvas can render a live
 * typewriter bubble instead of waiting for TTS playback markers.
 */
const assistantDraftText = new Map<string, string>();
const assistantDraftTerminalTurns = new Set<string>();

/**
 * Authoritative + speculative transcript state, reduced from durable events.
 *
 * `snapshot.transcript` is a projection of this state, so ordering and
 * de-duplication are decided by `event_id` / `turn_id` / `sentence_seq` rather
 * than by whichever RTVI channel happened to deliver last.
 */
let transcriptState: TranscriptState = emptyTranscriptState();
let taskState: VoiceTaskState = emptyVoiceTaskState();
let taskPollTimer: number | null = null;
let taskPollDelayMs = 2000;
const cancellingTaskIds = new Set<string>();
/** Catch-up polling for the durable cursor (keeps `lastEventSeq` advancing). */
let eventPollTimer: number | null = null;
let eventPollDelayMs = 1500;
/** Retained remote audio element so it can be torn down deterministically. */
let remoteAudio: HTMLAudioElement | null = null;
/** Mirrors `snapshot.playbackBlocked` so the `ontrack` closure can compare. */
let remoteAudioBlocked = false;
/** Idempotency guard: DELETE runs at most once per remote session. */
let closedRemoteSession: string | null = null;
/** Pending typed bubbles awaiting their `turn.accepted` echo. */
const typedAcceptTimers = new Map<string, number>();

function syncTranscriptFromState() {
  update({ transcript: orderedEntries(transcriptState).slice(-50) });
}

function resetTaskState() {
  taskState = emptyVoiceTaskState();
  cancellingTaskIds.clear();
}

function syncTaskState() {
  const tasks = [...taskState.tasks]
    .sort((a, b) => {
      const activeDifference = Number(voiceTaskIsActive(b)) - Number(voiceTaskIsActive(a));
      return activeDifference || b.updatedAt.localeCompare(a.updatedAt);
    })
    .slice(0, 20);
  update({ tasks });
}

function appendTaskActivity(activity: VoiceTaskActivity) {
  const activityKey =
    activity.eventId ??
    `${activity.taskId}:${activity.eventSeq ?? activity.createdAt}:${activity.text}`;
  transcriptState = reduceVoiceEvent(transcriptState, {
    event_id: `task-activity:${activityKey}`,
    event_seq: activity.eventSeq,
    type: "task.activity",
    payload: { text: activity.text, task_id: activity.taskId },
  });
  syncTranscriptFromState();
}

function applyTaskEvent(raw: Record<string, unknown>, fallbackTaskId?: string): boolean {
  const reduction = reduceVoiceTaskEvent(taskState, raw, fallbackTaskId);
  if (!reduction.changed && !reduction.activity) return false;
  taskState = reduction.state;
  syncTaskState();
  if (reduction.activity) appendTaskActivity(reduction.activity);
  return reduction.changed || Boolean(reduction.activity);
}

function resetTranscriptState() {
  transcriptState = emptyTranscriptState();
  seenEventIds.clear();
  renderedLiveEventIds.clear();
  turnAudioCursors.clear();
  assistantDraftText.clear();
  assistantDraftTerminalTurns.clear();
}

/** Fold a durable event into the transcript projection. */
function applyDurableEvent(event: VoiceEventLike) {
  durableEventCount += 1;
  transcriptState = reduceVoiceEvent(transcriptState, event);
  syncTranscriptFromState();
}

/** How many durable events this page has folded in (0 = journal unavailable). */
let durableEventCount = 0;

/**
 * Ask the control plane to open the durable turn for a typed utterance.
 *
 * 应答里的 `turn_id` 就是这一轮的**权威身份**，必须用上：控制面写的
 * `user.final` / `turn.accepted` 只能靠 1.5s 轮询补投，而数据通道上的输出侧事件
 * （worker 写的 `assistant.*`）会先把游标推到它们前面 —— 游标只前进不回补，那两个
 * 事件就永远不会再被取回。真机证据：本机日志的轮询游标 `… 27 → 32`，而这一轮的
 * `user.final`/`turn.accepted` 正是 29/30；同一通话里 4 条打字回合的身份全都没到。
 * 拿不到身份，延迟面板就只能把这一轮的 LLM/TTS 信号按"无主"计数（面板：只有两行
 * 实时通道 + 未测得 + 永远"进行中"）。
 *
 * 身份对不上也没关系：这一轮在服务端可能由 worker 先建（那时应答给回的就是 worker
 * 那个 id）—— 这里用的始终是服务端应答里的真值，不是客户端猜的 id。
 */
function persistAcceptedTurn(turnId: string, userText: string, clientMessageId?: string) {
  const remote = snapshot.sessionIdRemote;
  if (!remote || !userText.trim()) return;
  void apiClient
    .post<{ turn_id?: string; turnId?: string }>(`/voice/sessions/${remote}/turns/accept`, {
      turn_id: turnId,
      text: userText.trim(),
      client_message_id: clientMessageId,
    })
    .then((accepted) => {
      const confirmed = String(accepted?.turn_id ?? accepted?.turnId ?? "").trim();
      if (!confirmed) return;
      // 认领身份：这一轮随后的账本记号（含已经先到的）都会归到它自己名下。
      markVoiceLatency({ stage: "turnAccepted", source: "typed", turnId: confirmed });
    })
    .catch(() => undefined);
}

/**
 * Bound the wait for a `turn.accepted` echo.
 *
 * A typed message is shown as pending until the server confirms the idempotency
 * key. Without a bound, a dropped channel would leave a bubble that looks sent
 * forever; after the window it is marked failed so the UI can offer a retry
 * that reuses the same `client_message_id`.
 */
function armTypedAcceptTimer(clientMessageId: string) {
  clearTypedAcceptTimer(clientMessageId);
  const handle = window.setTimeout(() => {
    typedAcceptTimers.delete(clientMessageId);
    if (!pendingTyped.has(clientMessageId)) return;
    const pending = pendingTyped.get(clientMessageId)!;
    dispatchVoiceRender({
      id: userEntryId(undefined, clientMessageId), role: "user", text: pending.text,
      final: false, pending: true, deliveryStatus: "failed",
      clientMessageId, createdAt: new Date().toISOString(),
    });
  }, 12_000);
  typedAcceptTimers.set(clientMessageId, handle);
}

function clearTypedAcceptTimer(clientMessageId: string) {
  const handle = typedAcceptTimers.get(clientMessageId);
  if (handle !== undefined) {
    window.clearTimeout(handle);
    typedAcceptTimers.delete(clientMessageId);
  }
}

function scheduleReconnect() {
  if (reconnectTimer !== null || !snapshot.sessionId || snapshot.transport === "idle") return;
  reconnectAttempt += 1;
  if (reconnectAttempt > 5) {    update({ transport: "error", state: "error", error: "语音连接无法恢复，已降级为文字模式。请重试或退出语音模式。" });
    return;
  }
  const base = Math.min(30_000, 500 * 2 ** Math.min(reconnectAttempt - 1, 6));
  const delay = Math.round(base * (0.7 + Math.random() * 0.6));
  update({ transport: "reconnecting", state: "ready", error: "语音连接中断，正在重连…" });
  reconnectTimer = window.setTimeout(() => {
    reconnectTimer = null;
    void voiceSessionController.connect();
  }, delay);
}

/**
 * 数据通道上的账本事件该怎么推进轮询游标（纯函数，单测覆盖）。
 *
 * 数据通道**只带 worker 写的事件**：`journal._emit → _publish` 才下发。控制面（API
 * 进程）写的那些根本不在上面 —— 打字回合的 `user.final` / `turn.accepted`（`accept_turn`
 * 只写库）、HTTP 触发的 `turn.interrupted`、`session.closed`、部分 `context.updated`。
 * 所以"事件序号不连续"是**常态**，不是异常。
 *
 * 正因为如此，按序号直接推进游标是错的：轮询是 `after_event_seq=<游标>`，游标一旦被
 * 数据通道推过那些没送达的序号，它们就**再也取不回来**。真机证据：会话
 * `vs_c03d8b92…` 的访问日志游标 `…27 → 32`，而那条打字回合的身份事件正是 29/30 ——
 * 客户端因此永远认领不到这一轮，转录里的打字气泡也永远等不到「回合受理」（12s 后
 * 被判发送失败），延迟面板则把这一轮的 LLM/TTS 信号全当"无主"。
 *
 * 规则：只在 `seq == 游标 + 1` 时推进（游标为 0 = 本次连接还没有游标，允许直接采纳，
 * 否则一个中途加入的客户端永远等不到开头）；发现缺口就**不动游标**并要求补投一轮轮询，
 * 由它把缺口补齐、并把游标推进到页内最大值（页内序号是连续的）。
 */
export function advanceDurableCursor(
  cursor: number,
  seq: number,
): { cursor: number; backfill: boolean } {
  if (!Number.isFinite(seq) || seq <= cursor) return { cursor, backfill: false };
  if (cursor === 0 || seq === cursor + 1) return { cursor: seq, backfill: false };
  return { cursor, backfill: true };
}

/**
 * 发现缺口后立刻补一轮轮询（不等定时器）。
 *
 * 与定时轮询并发也无害：`pollVoiceEvents` 按 `event_id` 去重、游标单调前进，两条
 * 请求最多各取一次同一页。
 */
let eventCatchUpInFlight = false;
function requestEventCatchUp() {
  if (eventCatchUpInFlight) return;
  if (!snapshot.sessionIdRemote) return;
  eventCatchUpInFlight = true;
  void pollVoiceEvents().finally(() => {
    eventCatchUpInFlight = false;
  });
}

/** Fetch and fold durable events newer than the local cursor. */
async function pollVoiceEvents(): Promise<boolean> {
  const remote = snapshot.sessionIdRemote;
  if (!remote) return false;
  try {
    const replay = await apiClient.get<{ events?: Array<Record<string, unknown>> }>(
      `/voice/sessions/${remote}/events?after_event_seq=${snapshot.lastEventSeq}`,
    );
    for (const rawEvent of replay.events ?? []) {
      const normalized = normalizeVoiceEvent({
        ...rawEvent,
        event_seq: rawEvent.event_seq ?? rawEvent.seq,
        type: rawEvent.type ?? rawEvent.event_type,
        payload: rawEvent.payload ?? {},
      });
      if (!normalized || seenEventIds.has(normalized.event_id)) continue;
      seenEventIds.add(normalized.event_id);
      // 轮询页里的序号是连续的，所以这里照旧按最大值推进游标（它同时也是"补投完成"的
      // 收口：页内的缺口由这一页填上了）。
      if (normalized.event_seq > snapshot.lastEventSeq) {
        update({ lastEventSeq: normalized.event_seq, sessionEpoch: normalized.session_epoch });
      }
      processVoiceEvent(normalized);
    }
    return true;
  } catch {
    return false;
  }
}

/**
 * Keep the durable cursor alive while the call is up.
 *
 * The RTVI data channel carries live events, but a dropped frame, a suspended
 * tab or a reconnect would otherwise freeze `lastEventSeq` and leave the client
 * permanently behind. Polling with backoff is the catch-up path; it is cheap
 * (an empty page when nothing happened) and idempotent thanks to `seenEventIds`.
 */
function startEventPolling() {
  stopEventPolling();
  stopTaskPolling();
  const tick = async () => {
    if (snapshot.transport !== "connected" || !snapshot.sessionIdRemote) {
      eventPollTimer = null;
      return;
    }
    const ok = await pollVoiceEvents();
    eventPollDelayMs = ok ? 1500 : Math.min(15_000, Math.round(eventPollDelayMs * 1.8));
    if (snapshot.transport === "connected" && snapshot.sessionIdRemote) {
      eventPollTimer = window.setTimeout(() => { void tick(); }, eventPollDelayMs);
    } else {
      eventPollTimer = null;
    }
  };
  eventPollTimer = window.setTimeout(() => { void tick(); }, eventPollDelayMs);
}

function stopEventPolling() {
  if (eventPollTimer !== null) {
    window.clearTimeout(eventPollTimer);
    eventPollTimer = null;
  }
  eventPollDelayMs = 1500;
}

/**
 * Rebuild the visible conversation and background task shelf after a refresh or
 * reconnect. This only restores UI state: it never replays old task audio.
 */
async function recoverVoiceSessionState(): Promise<void> {
  const remote = snapshot.sessionIdRemote;
  if (!remote) return;
  const [transcriptResult, sessionResult] = await Promise.all([
    apiClient
      .get<{
        turns?: Array<{ turn_id: string; user_text?: string; assistant_text?: string; client_message_id?: string | null; finalized_at?: string | null; status?: string; failure_reason?: string | null }>;
      }>(`/voice/sessions/${remote}/transcript?limit=50`)
      .catch(() => null),
    requestVoiceSession(remote).catch(() => null),
  ]);
  if (transcriptResult?.turns?.length) {
    transcriptState = seedFromServerTurns(transcriptState, transcriptResult.turns);
    syncTranscriptFromState();
  }
  const tasks = sessionResult?.tasks;
  if (Array.isArray(tasks)) {
    taskState = mergeVoiceTaskSnapshots(
      taskState,
      tasks.filter((task): task is Record<string, unknown> => Boolean(task && typeof task === "object")),
    );
    syncTaskState();
  }
  if (Array.isArray(sessionResult?.results)) {
    taskState = mergeVoiceTaskResults(
      taskState,
      sessionResult.results.filter(
        (result): result is Record<string, unknown> => Boolean(result && typeof result === "object"),
      ),
    );
    syncTaskState();
  }
}

async function pollVoiceTasks(): Promise<boolean> {
  const remote = snapshot.sessionIdRemote;
  if (!remote) return false;
  const activeTasks = taskState.tasks.filter(voiceTaskIsActive).slice(0, 4);
  if (!activeTasks.length) return true;
  const results = await Promise.all(
    activeTasks.map(async (task) => {
      try {
        return {
          taskId: task.taskId,
          snapshot: await requestVoiceTask(
            remote,
            task.taskId,
            taskState.cursorByTask[task.taskId] ?? 0,
          ),
        };
      } catch {
        return null;
      }
    }),
  );
  let allSucceeded = true;
  for (const result of results) {
    if (!result) {
      allSucceeded = false;
      continue;
    }
    taskState = mergeVoiceTaskSnapshots(taskState, [result.snapshot]);
    if (Array.isArray(result.snapshot.results)) {
      taskState = mergeVoiceTaskResults(
        taskState,
        result.snapshot.results.filter(
          (item): item is Record<string, unknown> => Boolean(item && typeof item === "object"),
        ),
      );
    }
    const events = Array.isArray(result.snapshot.events) ? result.snapshot.events : [];
    for (const event of events) {
      if (!event || typeof event !== "object") continue;
      applyTaskEvent(event as Record<string, unknown>, result.taskId);
    }
  }
  syncTaskState();
  return allSucceeded;
}

function startTaskPolling() {
  stopTaskPolling();
  const tick = async () => {
    if (snapshot.transport !== "connected" || !snapshot.sessionIdRemote) {
      taskPollTimer = null;
      return;
    }
    const ok = await pollVoiceTasks();
    taskPollDelayMs = ok
      ? 1800
      : Math.min(12_000, Math.round(taskPollDelayMs * 1.8));
    if (snapshot.transport === "connected" && snapshot.sessionIdRemote) {
      taskPollTimer = window.setTimeout(() => {
        void tick();
      }, taskPollDelayMs);
    } else {
      taskPollTimer = null;
    }
  };
  taskPollTimer = window.setTimeout(() => {
    void tick();
  }, 800);
}

function stopTaskPolling() {
  if (taskPollTimer !== null) {
    window.clearTimeout(taskPollTimer);
    taskPollTimer = null;
  }
  taskPollDelayMs = 2000;
}

function ensureUserTurnId() {
  if (!activeUserTurnId) activeUserTurnId = createUuid();
  try { localStorage.setItem(`learngraph.voice.turn.${snapshot.sessionId}`, activeUserTurnId); } catch { /* optional */ }
  return activeUserTurnId;
}

function renderAuthoritativeUserTurn(
  turnId: string, text: string,
  options: { clientMessageId?: string; createdAt?: string } = {},
) {
  const rendered = userTranscript.final(turnId, text, options.clientMessageId);
  if (rendered) dispatchVoiceRender(rendered);
}

function flushPendingUserFinal() {
  if (pendingUserFinal.trim()) {
    renderAuthoritativeUserTurn(ensureUserTurnId(), pendingUserFinal);
    pendingUserFinal = "";
  }
}

function renderPlaybackEvent(event: Parameters<VoicePlaybackTranscript["apply"]>[0]) {
  const rendered = playbackTranscript.apply(event);
  if (!rendered) return;
  if (!rendered.final) {
    if (rendered.captionParts?.length === 1 && rendered.captionParts[0].status !== "completed") {
      userTranscript.answerStarted();
    }
    update({ streamingAssistantText: rendered.text });
  } else {
    update({ streamingAssistantText: "" });
  }
  const draftText = rendered.turnId ? assistantDraftText.get(rendered.turnId) : undefined;
  const removes = [
    ...(rendered.removes ?? []),
    ...(rendered.final && !rendered.text.trim() && rendered.turnId
      ? [`assistant-stream-${rendered.turnId}`]
      : []),
  ];
  dispatchVoiceRender({
    ...rendered,
    ...(removes.length ? { removes } : {}),
    ...(draftText ? { streamingText: draftText } : {}),
  });
}

function normalizeVoiceEvent(input: Record<string, unknown>): VoiceEventEnvelope | null {
  const eventId = String(input.event_id ?? input.eventId ?? "");
  const seq = Number(input.event_seq ?? input.eventSeq ?? 0);
  if (!eventId || !Number.isFinite(seq)) return null;
  return {
    event_id: eventId,
    session_id: String(input.session_id ?? input.sessionId ?? snapshot.sessionIdRemote ?? ""),
    session_epoch: Number(input.session_epoch ?? input.sessionEpoch ?? snapshot.sessionEpoch ?? 0),
    turn_id: input.turn_id == null ? (input.turnId as string | undefined) : String(input.turn_id),
    event_seq: seq,
    request_id: input.request_id == null ? undefined : String(input.request_id),
    phase: String(input.phase ?? input.type ?? ""),
    causality: (input.causality as Record<string, unknown>) ?? {},
    timestamp: input.timestamp == null ? null : String(input.timestamp),
    audio_cursor_ms: input.audio_cursor_ms == null ? null : Number(input.audio_cursor_ms),
    type: String(input.type ?? input.event_type ?? input.phase ?? ""),
    payload: (input.payload as Record<string, unknown>) ?? input,
  };
}

function isVoiceTaskEventType(type: string): boolean {
  return (
    type.startsWith("task.") ||
    type.startsWith("result.") ||
    type.startsWith("speech.") ||
    type.startsWith("requirement.")
  );
}

function processVoiceEvent(event: VoiceEventEnvelope, live = false) {
  const payload = event.payload ?? {};
  const type = event.type || event.phase;
  const turnId = event.turn_id || String(payload.turn_id ?? payload.turnId ?? "") || undefined;
  const text = String(payload.text ?? payload.content ?? payload.delta ?? "");
  // Single ordering authority: every durable event is folded into the pure
  // reducer, which decides identity and monotonicity from server ids.
  if (!live) applyDurableEvent({
    event_id: event.event_id,
    event_seq: event.event_seq,
    type,
    phase: event.phase,
    turn_id: turnId ?? null,
    audio_cursor_ms: event.audio_cursor_ms,
    causality: event.causality,
    payload,
  });
  const liveId = String(payload.live_event_id || "");
  if (liveId) {
    if (renderedLiveEventIds.has(liveId)) return;
    renderedLiveEventIds.add(liveId);
    if (renderedLiveEventIds.size > 2000) renderedLiveEventIds.delete(renderedLiveEventIds.values().next().value!);
  }
  if (isVoiceTaskEventType(type)) {
    applyTaskEvent({
      ...payload,
      event_id: event.event_id,
      event_seq: event.event_seq,
      type,
    });
    return;
  }
  if (event.audio_cursor_ms != null && turnId) {
    const previousCursor = turnAudioCursors.get(turnId) ?? -1;
    if (event.audio_cursor_ms < previousCursor) return;
    turnAudioCursors.set(turnId, event.audio_cursor_ms);
  }
  switch (type) {
    case "session.created":
      // 新一通电话：上一通的提示（例如"这个模型关不掉思考"）是旧 Provider 解析的
      // 产物，必须清掉，否则会挂在新通话上误导用户。
      update({ state: "ready", error: null, pipelineNotice: null });
      return;
    case "session.ready":
      // A rebuilt pipeline re-resolves every stage, so a previous degradation is
      // no longer a statement about the current one.  The ICE path belongs to the
      // *connection*, not the pipeline, so it survives a pipeline rebuild.
      update({
        transport: "connected", state: "listening", error: null,
        degradedStages: [], degradedNotice: null, textFallback: false,
      });
      return;
    case "session.ice": {
      // Which candidate pair the connection settled on. Kept in the snapshot so
      // the call can say "relayed" or "direct" out loud -- otherwise a deployment
      // that configured a relay has no way to confirm it is actually in use.
      const localType = String(payload.local_type ?? "");
      const remoteType = String(payload.remote_type ?? "");
      update({
        icePath: {
          label: String(payload.label ?? `${localType || "?"}↔${remoteType || "?"}`),
          relayed: payload.relayed === true,
          protocol: String(payload.protocol ?? ""),
        },
      });
      return;
    }
    case "session.reconnecting":
      update({ transport: "reconnecting", error: null });
      return;
    case "session.closed":
      forgetRemoteSession(snapshot.workspaceId, snapshot.sessionId);
      update({ transport: "idle", state: "closed", sessionIdRemote: null, pipelineNotice: null });
      return;
    case "user.started":
      // The journal may still be pointing at the previous turn when speech
      // starts. Never adopt that id for the new utterance.
      activeUserTurnId = createUuid();
      update({ state: "listening", interimUserText: "" });
      // 刻意不传 `turnId`：账本这条带的是**上一轮**的 id（journal 先发事件、后开新轮），
      // 照身份归位会把用户这次真实开口吞掉。起音永远是新一轮的开始，身份等
      // `user.final` / `turn.accepted` 到达时认领（见 voice-latency.ts 的归轮规则）。
      markVoiceLatency({ stage: "userStarted", source: "ledger" });
      return;
    case "user.interim":
      activeUserTurnId = activeUserTurnId || createUuid();
      update({ interimUserText: text });
      // The live hypothesis of the segment being spoken. Its row keeps the id it
      // will have once this segment is finalized, so finalizing replaces the text
      // in place rather than adding a second bubble. Before the worker opens the
      // turn there is no durable turn id yet, and the row is deliberately left
      // without one instead of borrowing the client key (a fake id would make the
      // row look like the durable twin of some other message).
      const userLive = userTranscript.interim(text, turnId);
      if (userLive) dispatchVoiceRender(userLive);
      return;
    case "user.final": {
      markVoiceLatency({ stage: "asrFinal", source: "ledger", detail: text, turnId });
      const typedClientId = String(
        payload.client_message_id ?? payload.clientMessageId ?? "",
      ).trim();
      activeUserTurnId = turnId || activeUserTurnId || createUuid();
      update({ interimUserText: "" });
      if (typedClientId) {
        // A typed utterance already owns its authoritative row: `sendText`
        // rendered it under `user-typed-<client_message_id>` and the durable
        // `turn.accepted` promotes it in place. Rendering a per-segment row here
        // was the duplicate -- the typed branch of `turn.accepted` never retires
        // one, so the fragment sat beside the settled bubble for as long as the
        // turn stayed open (i.e. permanently on a hung turn).
        renderAuthoritativeUserTurn(
          turnId || activeUserTurnId,
          text || pendingTyped.get(typedClientId)?.text || "",
          { clientMessageId: typedClientId },
        );
        return;
      }
      // `payload.text` is already the merged turn text, so it replaces rather
      // than accumulates; joining it would duplicate every earlier segment.
      pendingUserFinal = "";
      const userFinal = userTranscript.final(
        turnId || activeUserTurnId,
        text,
        typedClientId || undefined,
      );
      if (userFinal) dispatchVoiceRender(userFinal);
      return;
    }
    case "turn.accepted": {
      markVoiceLatency({ stage: "turnAccepted", source: "ledger", turnId });
      const clientId = String(payload.client_message_id ?? payload.clientMessageId ?? "");
      if (clientId && pendingTyped.has(clientId)) {
        const pending = pendingTyped.get(clientId)!;
        pendingTyped.set(clientId, { ...pending, turnId });
        clearTypedAcceptTimer(clientId);
        pendingTyped.delete(clientId);
        // The transcript entry is produced by the reducer from this same event,
        // so no manual append is needed (and would double the bubble).
        renderAuthoritativeUserTurn(turnId || pending.turnId || "", pending.text, {
          clientMessageId: clientId,
        });
      } else {
        // Voice turn accepted: flush the user's question into the authoritative
        // bubble immediately and retire the interim segment bubbles so they
        // cannot linger or stack a second copy bar.
        flushPendingUserFinal();
      }
      return;
    }
    case "turn.finalized":
      if (String(payload.role ?? "") === "user") {
        renderAuthoritativeUserTurn(turnId || ensureUserTurnId(), text);
      } else {
        if (turnId) {
          assistantDraftTerminalTurns.add(turnId);
          assistantDraftText.delete(turnId);
        }
        if (turnId && typeof payload.user_text === "string" && payload.user_text.trim()) {
          renderAuthoritativeUserTurn(turnId, payload.user_text);
        }
        renderPlaybackEvent(event);
        markVoiceLatency({ stage: "answerDone", source: "ledger", turnId });
      }
      return;
    case "turn.interrupted":
      if (turnId) {
        assistantDraftTerminalTurns.add(turnId);
        assistantDraftText.delete(turnId);
      }
      renderPlaybackEvent(event);
      markVoiceLatency({ stage: "answerDone", source: "barge-in", turnId });
      return;
    case "assistant.llm.delta":
      markVoiceLatency({ stage: "llmFirst", source: "ledger", turnId });
      if (snapshot.state !== "speaking") update({ state: "thinking" });
      if (turnId && text && !assistantDraftTerminalTurns.has(turnId)) {
        // VoiceJournal coalesces adjacent Pipecat TextFrame chunks into each
        // payload.text while preserving their order. Append each emitted batch;
        // event ids are de-duplicated before this switch, so replay cannot
        // duplicate a batch.
        const nextText = `${assistantDraftText.get(turnId) ?? ""}${text}`;
        assistantDraftText.set(turnId, nextText);
        update({ streamingAssistantText: nextText });
        dispatchVoiceRender({
          id: `assistant-stream-${turnId}`,
          role: "assistant",
          turnId,
          text: nextText,
          streamingText: nextText,
          final: false,
          createdAt: event.timestamp || new Date().toISOString(),
          eventId: event.event_id,
        });
      }
      return;
    case "assistant.sentence.queued":
      flushPendingUserFinal();
      renderPlaybackEvent(event);
      markVoiceLatency({ stage: "sentenceQueued", source: "ledger", turnId });
      return;
    case "assistant.sentence.ended":
      renderPlaybackEvent(event);
      markVoiceLatency({ stage: "sentenceEnded", source: "ledger", detail: text, turnId });
      return;
    case "assistant.sentence.playback_started":
      markVoiceLatency({ stage: "botSpeaking", source: "ledger", turnId });
      return;
    case "assistant.sentence.playback_ended":
      // Only turn.finalized/interrupted settles a bubble; silence between
      // segments and an unscoped BotStoppedSpeaking never complete an answer.
      if (payload.turn_final === true) markVoiceLatency({ stage: "answerDone", source: "ledger", turnId });
      return;
    case "processor.notice": {
      // 不是错误：不设置 error、不进降级、不强制文字输入，只在状态区提醒代价。
      // 只认白名单 code——其它 notice 是排障信息（ASR 自适应重连、VAD 兜底帧缺失），
      // 不该占用界面。
      if (String(payload.code ?? "") !== THINKING_OFF_UNAVAILABLE_NOTICE_CODE) return;
      const message = String(payload.message ?? "").trim();
      if (!message) return;
      update({ pipelineNotice: message });
      return;
    }
    case "processor.error": {
      const stages = deriveDegradedStages(snapshot.degradedStages, payload);
      const textFallback = requiresTextFallback(stages);
      update({
        error: String(payload.message ?? payload.error ?? "语音处理暂时失败"),
        degradedStages: stages,
        degradedNotice: degradedNotice(stages),
        textFallback,
      });
      if (textFallback && !snapshot.muted) {
        // Stop streaming the microphone into an ASR that cannot answer: left
        // running, the user keeps talking into a void with no feedback at all.
        voiceSessionController.setMuted(true);
      }
      const failedClientId = String(payload.client_message_id ?? payload.clientMessageId ?? "");
      const pending = failedClientId ? pendingTyped.get(failedClientId) : undefined;
      if (pending) dispatchVoiceRender({ id: userEntryId(undefined, failedClientId), role: "user", text: pending.text, final: false, pending: true, deliveryStatus: "failed", clientMessageId: failedClientId, createdAt: new Date().toISOString(), eventId: event.event_id });
      return;
    }
    case "processor.retry_scheduled":
      update({ transport: "reconnecting", error: String(payload.message ?? "正在重试语音处理…") });
      return;
    case "context.updated": {
      // Never optimistically show the requested model as live: the pin is a
      // statement about the next turn, and only an explicit confirmation may
      // move the model the UI presents as current (see deriveModelPin).
      // ``null`` means the event was not about the model at all (a context
      // snapshot, or a report with nothing to report): the existing pin -- which
      // may be absent -- stays exactly as it is.
      const pin = deriveModelPin(
        payload,
        event,
        snapshot.effectiveModelId ?? snapshot.modelId,
        snapshot.modelPin,
      );
      if (!pin) return;
      update({
        modelPin: pin,
        ...(pin.repointed && pin.effectiveModelId
          ? {
              effectiveModelId: pin.effectiveModelId,
              // 换模型会重新解析"能否关闭思考"：旧模型的"关不掉"提示对新模型不成立。
              // 服务端在 context.updated 之后才会补发新模型对应的 notice（若能表达关闭
              // 就不发），所以先清后设不会互相覆盖。
              pipelineNotice: null,
            }
          : {}),
      });
      return;
    }
    default:
      return;
  }
}

/** Row id of the live (still-speaking) assistant bubble of one turn. */
function handleRtviMessage(raw: string) {
  type RtviEnvelope = { label?: string; type?: string; data?: Record<string, unknown> };
  let payload: RtviEnvelope | null = null;
  try { payload = JSON.parse(raw) as RtviEnvelope; } catch { return; }
  if (!payload || payload.label !== RTVI_LABEL) return;
  const data = payload.data ?? {};
  // New durable event envelopes may arrive wrapped in an RTVI server-message
  // or directly as `data.event`. Apply them before legacy observer messages.
  const candidate = (data.event ?? data.payload ?? data.data ?? data) as Record<string, unknown>;
  const livePlayback = candidate &&
    String(candidate.delivery ?? "") === "live" &&
    typeof candidate.type === "string" &&
    (String(candidate.type).startsWith("assistant.") || String(candidate.type).startsWith("user.") || candidate.type === "turn.finalized" || candidate.type === "turn.interrupted");
  if (candidate && ((candidate.event_id || candidate.eventId) && (candidate.event_seq !== undefined || candidate.eventSeq !== undefined) || livePlayback)) {
    const event = normalizeVoiceEvent({
      ...candidate,
      event_id: candidate.event_id ?? candidate.eventId ?? `live:${candidate.type}:${candidate.turn_id}:${String((candidate.payload as Record<string, unknown> | undefined)?.sentence_seq ?? "")}`,
      event_seq: candidate.event_seq ?? candidate.eventSeq ?? snapshot.lastEventSeq,
    });
    if (livePlayback && event) {
      processVoiceEvent(event, true);
      return;
    }
    if (event && !seenEventIds.has(event.event_id)) {
      seenEventIds.add(event.event_id);
      // 见 `advanceDurableCursor`：数据通道只带 worker 写的事件，控制面写的那些不在
      // 这条通道上，游标因此只能推进到**连续**的位置；有缺口就交给轮询补投。
      const advanced = advanceDurableCursor(snapshot.lastEventSeq, event.event_seq);
      if (advanced.cursor !== snapshot.lastEventSeq) {
        update({ lastEventSeq: advanced.cursor, sessionEpoch: event.session_epoch });
      }
      if (advanced.backfill) requestEventCatchUp();
      processVoiceEvent(event);
    }
    return;
  }
  const embeddedTask = data.task_event ?? data.taskEvent;
  const taskPayload =
    embeddedTask && typeof embeddedTask === "object"
      ? (embeddedTask as Record<string, unknown>)
      : data;
  const taskType = String(taskPayload.type ?? data.type ?? payload.type ?? "");
  if (isVoiceTaskEventType(taskType)) {
    const taskEventSeq = Number(taskPayload.event_seq ?? taskPayload.eventSeq ?? taskPayload.seq);
    applyTaskEvent({
      ...taskPayload,
      type: taskType,
      event_id: taskPayload.event_id ?? taskPayload.eventId,
      event_seq: Number.isFinite(taskEventSeq) ? taskEventSeq : undefined,
    });
    return;
  }
  switch (payload.type) {
    case "user-transcription":
      // Durable user events own row identity. Observer messages may precede
      // them, but must not create another row or overwrite a confirmed segment.
      update({ interimUserText: data.final ? "" : String(data.text ?? "") });
      return;
    case "user-stopped-speaking":
      markVoiceLatency({ stage: "userDone", source: "rtvi" });
      flushPendingUserFinal();
      return;
    case "bot-llm-started":
      markVoiceLatency({ stage: "llmStart", source: "rtvi" });
      flushPendingUserFinal();
      if (snapshot.state !== "speaking") update({ state: "thinking" });
      return;
    case "bot-llm-text":
      markVoiceLatency({ stage: "llmFirst", source: "rtvi" });
      return;
    case "bot-llm-stopped":
    case "server-message":
    case "bot-output":
      // These messages can run ahead of audio. Text is rendered exclusively
      // from the media ledger, whose events carry turn and sentence identity.
      return;
    case "bot-tts-text":
      // 排障旁路（后端 `bot_tts_enabled=False`），不参与渲染。
      return;
    default:
      // `metrics` / `bot-ready` carry nothing the captions need.
      return;
  }
}

/**
 * 测试与排障入口：把一条 RTVI 原始报文喂给控制器。
 *
 * 数据通道上走的就是这条路径（`dataChannel.onmessage` → `handleRtviMessage`）。以前这条
 * 函数是模块私有的，于是"bot-output 进来之后字幕状态如何变化"这件事只能靠真机麦克风验证；
 * 这里把它暴露出来，让单测能在 jsdom 里喂真实报文格式跑完整链路。
 */
export function ingestVoiceRtviMessage(raw: string): void {
  handleRtviMessage(raw);
}

function emit() { for (const listener of listeners) listener(); }
function update(patch: Partial<VoiceSessionSnapshot>) {
  snapshot = { ...snapshot, ...patch };
  if (patch.lastEventSeq !== undefined && snapshot.sessionId) {
    try { localStorage.setItem(`learngraph.voice.cursor.${snapshot.sessionId}`, JSON.stringify({ seq: snapshot.lastEventSeq, epoch: snapshot.sessionEpoch })); } catch { /* optional */ }
  }
  emit();
}
function readVoiceCursor(sessionId: string): { seq: number; epoch: number } {
  try { const value = JSON.parse(localStorage.getItem(`learngraph.voice.cursor.${sessionId}`) || "null"); return { seq: Number(value?.seq || 0), epoch: Number(value?.epoch || 0) }; } catch { return { seq: 0, epoch: 0 }; }
}
function readLimit(): ThinkingLimit {
  try { const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null")?.thinkingLimit; return ["off", "low", "medium", "high", "xhigh"].includes(value) ? value : "high"; } catch { return "high"; }
}
function persistLimit(value: ThinkingLimit) { try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ thinkingLimit: value })); } catch { /* storage is optional */ } }

/**
 * Turn a `getUserMedia` rejection into something the user can act on.
 *
 * The browser's own text ("Permission denied") says what happened but not what
 * to do next, and the call cannot recover on its own: this page only re-asks for
 * the microphone when the user asks it to (auto-dial runs once per session), so
 * the wording has to name the retry. Returns null for errors this mapping does
 * not know, which then fall through to the browser's message.
 */
function describeMicrophoneError(error: unknown): string | null {
  if (!(error instanceof DOMException)) return null;
  switch (error.name) {
    case "NotAllowedError":
    case "PermissionDeniedError":
      return "浏览器阻止了麦克风。请点地址栏的权限图标允许麦克风，然后点「重试连接」。";
    case "NotFoundError":
    case "DevicesNotFoundError":
      return "没有找到麦克风设备。接上麦克风后点「重试连接」。";
    case "NotReadableError":
    case "TrackStartError":
      return "麦克风被其它程序占用了。关掉占用它的程序后点「重试连接」。";
    case "OverconstrainedError":
      return "当前麦克风不满足通话要求。换一个输入设备后点「重试连接」。";
    default:
      return null;
  }
}
function cleanupAudioTransport() {
  if (peerConnection) {
    peerConnection.onicecandidate = null;
    peerConnection.onconnectionstatechange = null;
    peerConnection.oniceconnectionstatechange = null;
  }
  stopBotAudioLevelMonitor();
  stopUserAudioLevelMonitor();
  // A pending reconnect timer must not survive a teardown: it would resurrect a
  // session the user already hung up.
  if (reconnectTimer !== null) {
    window.clearTimeout(reconnectTimer);
    reconnectTimer = null;
  }
  stopEventPolling();
  if (dataChannel) {
    dataChannel.onopen = null;
    dataChannel.onmessage = null;
    dataChannel.close();
    dataChannel = null;
  }
  localStream?.getTracks().forEach((track) => track.stop());
  localStream = null;
  remoteAudioBlocked = false;
  // The remote <audio> element keeps playing if it is not explicitly released.
  if (remoteAudio) {
    try {
      remoteAudio.pause();
      remoteAudio.srcObject = null;
    } catch { /* element already detached */ }
    remoteAudio.remove();
    remoteAudio = null;
  }
  peerConnection?.close();
  peerConnection = null;
}

/**
 * Idempotent session teardown.
 *
 * `DELETE /voice/sessions/{id}` is what stops the worker: it cancels the
 * pipeline, closes the ASR/TTS websockets and releases the WebRTC tracks. It
 * must run exactly once per remote session; repeated disconnects and error
 * cleanup must not fire it twice, but a failure must not block local UI
 * teardown either.
 */
function closeRemoteSession(remoteId: string | null | undefined) {
  if (!remoteId || closedRemoteSession === remoteId) return;
  closedRemoteSession = remoteId;
  void apiClient.delete(`/voice/sessions/${remoteId}`).catch(() => undefined);
}

// Do not DELETE from `pagehide`: a reload is a reconnect, and terminating the
// logical session there would orphan its durable transcript, task links and
// results. Explicit `disconnect()` remains the teardown boundary; a closed tab
// also drops the WebRTC peer, which lets the runtime stop its own pipeline.

let audioContext: AudioContext | null = null;
let analyserTimer: number | null = null;
let userAudioContext: AudioContext | null = null;
let userAnalyserTimer: number | null = null;
let lastBotLoudAt = 0;
let botAudioLevel = 0;
let userAudioLevel = 0;
function playVoiceCue(frequency: number) {
  try { const Ctor = window.AudioContext; const ctx = new Ctor(); const osc = ctx.createOscillator(); const gain = ctx.createGain(); osc.frequency.value = frequency; gain.gain.setValueAtTime(0.045, ctx.currentTime); gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.14); osc.connect(gain).connect(ctx.destination); void ctx.resume(); osc.start(); osc.stop(ctx.currentTime + 0.14); osc.onended = () => void ctx.close(); } catch { /* audio cue is best effort */ }
}

function publishAudioLevel() {
  const botActive = botAudioLevel > 0.01;
  const userActive = !snapshot.muted && userAudioLevel > 0.012;
  const audioSource: VoiceAudioSource = botActive ? "assistant" : userActive ? "user" : "idle";
  const audioLevel = botActive ? botAudioLevel : userActive ? userAudioLevel : 0;
  if (
    audioSource !== snapshot.audioSource ||
    Math.abs(audioLevel - snapshot.audioLevel) > 0.015
  ) update({ audioLevel, audioSource });
}

function stopBotAudioLevelMonitor() {
  if (analyserTimer !== null) {
    window.clearInterval(analyserTimer);
    analyserTimer = null;
  }
  void audioContext?.close().catch(() => undefined);
  audioContext = null;
  lastBotLoudAt = 0;
  botAudioLevel = 0;
  publishAudioLevel();
}

function stopUserAudioLevelMonitor() {
  if (userAnalyserTimer !== null) {
    window.clearInterval(userAnalyserTimer);
    userAnalyserTimer = null;
  }
  void userAudioContext?.close().catch(() => undefined);
  userAudioContext = null;
  userAudioLevel = 0;
  publishAudioLevel();
}

/**
 * 麦克风电平表。
 *
 * 这里**只**产出 UI 用的电平（波形/球），不再参与任何"用户是不是在说话"的判断：
 * 人声判定一律以服务端 VAD（Silero）+ 回合判定（Smart Turn）为准，即账本的
 * `user.started` 与 RTVI 的 `vad-user-stopped-speaking` / `user-stopped-speaking`。
 * 曾经在这里用 RMS 阈值猜"用户说完了"当兜底锚点，那是拿响度冒充 VAD。
 */
function startUserAudioLevelMonitor(stream: MediaStream) {
  try {
    const Ctor = window.AudioContext;
    userAudioContext = new Ctor();
    const analyser = userAudioContext.createAnalyser();
    analyser.fftSize = 512;
    userAudioContext.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    void userAudioContext.resume().catch(() => undefined);
    userAnalyserTimer = window.setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const value of samples) sum += value * value;
      const rms = Math.sqrt(sum / samples.length);
      userAudioLevel = snapshot.muted ? 0 : Math.min(1, rms * 18);
      publishAudioLevel();
    }, 70);
  } catch {
    // The call itself remains usable when Web Audio metering is unavailable.
  }
}

/**
 * Derives "导师正在说话" from the bot's own audio energy.
 *
 * The pipeline's RTVI stream does not carry `bot-started-speaking` /
 * `bot-stopped-speaking` in this topology (the output transport pushes those
 * frames past the observer), so the orb would never leave "listening" and the
 * barge-in button would never appear. Measuring the remote track is both
 * truthful and independent of that gap: `bot-llm-started` still drives
 * "thinking", and real audible output drives "speaking".
 */
function startBotAudioLevelMonitor(stream: MediaStream) {
  try {
    const Ctor = window.AudioContext;
    audioContext = new Ctor();
    const analyser = audioContext.createAnalyser();
    analyser.fftSize = 1024;
    audioContext.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    void audioContext.resume().catch(() => undefined);
    analyserTimer = window.setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      let sum = 0;
      for (const value of samples) sum += value * value;
      const rms = Math.sqrt(sum / samples.length);
      botAudioLevel = Math.min(1, rms * 18);
      publishAudioLevel();
      const now = Date.now();
      if (rms > 0.01) {
        lastBotLoudAt = now;
        // 这里**不再**打 `botSpeaking` 延迟锚点：远端音频能量只是"响不响"，它没有
        // 回合语义，一旦落在"服务端已收尾、浏览器还在播缓冲"的空档，就会凭空开出
        // 一轮"导师出声 0 ms / 整屏等待"的幻影轮。延迟面板的"导师出声"改用账本
        // `assistant.sentence.playback_started`（带 turn 语义，见那里的注释）。
        if (snapshot.transport === "connected" && snapshot.state !== "speaking") {
          update({ state: "speaking" });
        }
      } else if (snapshot.state === "speaking" && now - lastBotLoudAt > 450) {
        update({ state: "listening" });

      }
    }, 100);
  } catch {
    // Level metering is best effort; captions and transcripts do not depend on it.
  }
}

/**
 * Mute stays local by design (A6 follow-up): the browser track is disabled and
 * the pipeline is never told, so no extra data-channel topic is needed.
 */
function applyMutedToLocalStream() {
  localStream?.getAudioTracks().forEach((track) => {
    track.enabled = !snapshot.muted;
  });
  if (snapshot.muted) {
    userAudioLevel = 0;
    publishAudioLevel();
  }
}

export const voiceSessionController = {
  getSnapshot: () => snapshot,
  subscribe(listener: () => void) { listeners.add(listener); return () => listeners.delete(listener); },
  open(workspaceId: string, sessionId: string, modelId?: string | null, providerId?: string | null) {
    const same = snapshot.workspaceId === workspaceId && snapshot.sessionId === sessionId;
    if (!same) {
      playbackTranscript.reset();
      userTranscript.reset();
      if (snapshot.sessionId) sessionCache.set(`${snapshot.workspaceId}:${snapshot.sessionId}`, snapshot);
      taskState = emptyVoiceTaskState();
      syncTaskState();
      const cached = sessionCache.get(`${workspaceId}:${sessionId}`);
      const cursor = readVoiceCursor(sessionId);
      const persistedRemote = readPersistedRemoteSession(workspaceId, sessionId);
      update(cached ? { ...cached, sessionIdRemote: cached.sessionIdRemote ?? persistedRemote, state: cached.state === "closed" ? "ready" : cached.state, modelId: modelId ?? cached.modelId, providerId: providerId ?? cached.providerId, lastEventSeq: Math.max(cached.lastEventSeq, cursor.seq), sessionEpoch: Math.max(cached.sessionEpoch, cursor.epoch) } : { ...defaultSnapshot, workspaceId, sessionId, sessionIdRemote: persistedRemote, modelId: modelId ?? null, providerId: providerId ?? null, thinkingLimit: readLimit(), state: "ready", lastEventSeq: cursor.seq, sessionEpoch: cursor.epoch });
      if (cached?.tasks?.length) {
        taskState = mergeVoiceTaskSnapshots(
          taskState,
          cached.tasks.map((task) => ({ ...task, task_id: task.taskId })),
        );
        syncTaskState();
      }
    }
    else if (snapshot.state === "closed") update({ state: "ready", modelId: modelId ?? snapshot.modelId, providerId: providerId ?? snapshot.providerId });
    else if (modelId !== undefined || providerId !== undefined) {
      const nextModel = modelId ?? snapshot.modelId;
      const nextProvider = providerId ?? snapshot.providerId;
      const changed = nextModel !== snapshot.modelId || nextProvider !== snapshot.providerId;
      update({ modelId: nextModel, providerId: nextProvider });
      if (snapshot.sessionIdRemote && snapshot.transport === "connected" && changed) {
        void apiClient.patch(`/voice/sessions/${snapshot.sessionIdRemote}/model`, { model_id: nextModel, provider_id: nextProvider })
          .catch((error) => update({ error: error instanceof Error ? `模型切换失败：${error.message}` : "模型切换失败" }));
      }
    }
  },
  setThinkingLimit(value: ThinkingLimit) { persistLimit(value); update({ thinkingLimit: value }); },
  setMuted(value: boolean) { update({ muted: value }); applyMutedToLocalStream(); },
  /**
   * Start the tutor's audio after the browser blocked it.
   *
   * Must be called from a user gesture (the "恢复声音" button): the autoplay
   * policy only allows `play()` without one before any sound has been played,
   * and re-issuing it outside a gesture would fail exactly as it did on `ontrack`.
   */
  async resumeRemoteAudio(): Promise<boolean> {
    const audio = remoteAudio;
    if (!audio) return false;
    try {
      await audio.play();
      remoteAudioBlocked = false;
      update({ playbackBlocked: false });
      return true;
    } catch {
      remoteAudioBlocked = true;
      update({ playbackBlocked: true });
      return false;
    }
  },
  async connect() {
    if (!snapshot.sessionId || snapshot.transport === "connecting" || snapshot.transport === "connected") return;
    if (snapshot.transport === "reconnecting") cleanupAudioTransport();
    abortController?.abort();
    const connectionController = new AbortController();
    abortController = connectionController;
    let reconnectingExistingSession = Boolean(snapshot.sessionIdRemote);
    if (!reconnectingExistingSession) {
      pendingUserFinal = "";
      activeUserTurnId = "";
      playbackTranscript.reset();
      userTranscript.reset();
      resetTranscriptState();
      resetTaskState();
      durableEventCount = 0;
      closedRemoteSession = null;
      // 延迟面板按"每次通话"清空：上一通电话的耗时数字对新会话没有意义。
      resetVoiceLatency();
    }
    update({ transport: "connecting", state: "ready", error: null, interimUserText: "", streamingAssistantText: "", audioLevel: 0, audioSource: "idle", playbackBlocked: false });
    let remoteId: string | null = snapshot.sessionIdRemote;
    try {
      // The endpoint is intentionally explicit: until the backend voice contract
      // is deployed, the UI reports the unavailable service instead of faking a call.
      const path = import.meta.env.VITE_VOICE_SESSION_PATH || "/voice/sessions";
      // Reconnect the existing durable voice session so its event cursor and
      // unfinished turn survive WebRTC renegotiation.  A fresh session is only
      // created for the first connection.
      const createSession = () => apiClient.post<{ id?: string; session_id?: string; sessionId?: string; runtime_ready?: boolean; signaling_url?: string | null; reason?: string; session_epoch?: number; tasks?: Array<Record<string, unknown>>; results?: Array<Record<string, unknown>> }>(path, { session_id: snapshot.sessionId, thinking_limit: snapshot.thinkingLimit, model_id: snapshot.modelId, provider_id: snapshot.providerId, after_event_seq: snapshot.lastEventSeq, last_event_seq: snapshot.lastEventSeq }, { signal: connectionController.signal });
      let result;
      if (snapshot.sessionIdRemote) {
        try {
          result = await apiClient.get<{ id?: string; session_id?: string; sessionId?: string; runtime_ready?: boolean; signaling_url?: string | null; reason?: string; session_epoch?: number; tasks?: Array<Record<string, unknown>>; results?: Array<Record<string, unknown>> }>(`/voice/sessions/${snapshot.sessionIdRemote}`);
        } catch (error) {
          if (!(error instanceof ApiError) || ![403, 404].includes(error.status)) throw error;
          forgetRemoteSession(snapshot.workspaceId, snapshot.sessionId);
          update({ sessionIdRemote: null });
          remoteId = null;
          reconnectingExistingSession = false;
          result = await createSession();
        }
      } else {
        result = await createSession();
      }
      remoteId = result?.id || result?.session_id || result?.sessionId || null;
      if (remoteId) persistRemoteSession(snapshot.workspaceId, snapshot.sessionId, remoteId);
      if (Array.isArray(result?.tasks)) {
        taskState = mergeVoiceTaskSnapshots(taskState, result.tasks);
        syncTaskState();
      }
      if (Array.isArray(result?.results)) {
        taskState = mergeVoiceTaskResults(taskState, result.results);
        syncTaskState();
      }
      if (result?.runtime_ready !== true) {
        setVoiceSessionActive(snapshot.workspaceId, snapshot.sessionId, false);
        const error = result?.reason === "voice_runtime_dependency_missing" ? "语音运行时依赖未安装，请安装 LearnGraph 的 voice 依赖后重启后端。" : "语音会话已创建，但内置 SmallWebRTC 运行时尚未就绪。";
        update({ transport: "error", state: "error", sessionIdRemote: remoteId, signalingUrl: result?.signaling_url || null, error });
        // A session created here can never be used: leaving it `active` would
        // keep a runner slot reserved for a call that will not happen.
        forgetRemoteSession(snapshot.workspaceId, snapshot.sessionId);
        closeRemoteSession(remoteId);
        return;
      }
      const configuredOfferPath = (result?.signaling_url || "").replace("{session_id}", remoteId || "");
      // The backend advertises its mounted API path (`/api/v1/...`), while
      // apiClient already prefixes relative requests with `/api/v1`. Strip
      // that prefix once to avoid posting to `/api/v1/api/v1/...`.
      const apiBase = apiClient.baseUrl.replace(/\/+$/, "");
      const offerPath = configuredOfferPath.startsWith(`${apiBase}/`)
        ? configuredOfferPath.slice(apiBase.length)
        : configuredOfferPath;
      if (!offerPath || typeof RTCPeerConnection === "undefined" || !navigator.mediaDevices?.getUserMedia) {
        throw new Error("当前浏览器或内置语音运行时不支持 WebRTC。");
      }
      // Deployment-wide ICE servers (STUN/TURN) from the instance administrator.
      // Resolved before the peer connection exists; failures degrade to an empty
      // list so a broken relay cannot stop a call that could still go direct.
      const iceServers = await loadIceServers();
      peerConnection = new RTCPeerConnection({ iceServers });
      iceRestartAttempted = false;
      const handleTransportDrop = () => {
        const state = peerConnection?.connectionState;
        const iceState = peerConnection?.iceConnectionState;
        const broken = state === "failed" || state === "disconnected" || iceState === "failed" || iceState === "disconnected";
        if (!broken) return;
        // Prefer an ICE restart over a full re-offer: it keeps the peer
        // connection, the data channel and the durable cursor intact, and it is
        // the correct response to a momentary network blip (treating a first
        // `disconnected` as fatal used to tear down a healthy call).
        if (!iceRestartAttempted && state !== "failed" && iceState !== "failed") {
          iceRestartAttempted = true;
          try {
            peerConnection?.restartIce();
            update({ transport: "reconnecting", error: "网络抖动，正在尝试恢复语音连接…" });
            return;
          } catch { /* fall through to a full reconnect */ }
        }
        scheduleReconnect();
      };
      peerConnection.onconnectionstatechange = handleTransportDrop;
      peerConnection.oniceconnectionstatechange = handleTransportDrop;
      // RTVI data channel. It must exist before the offer so the SDP carries the
      // m-line; the backend drops every message when the client never opens one
      // (it warns "Data channel not established within 10s"). Opening it is what
      // turns the call from a black box into a captioned, text-drivable session:
      // the bot broadcasts user/assistant transcripts here and accepts typed
      // turns (`send-text`) and custom control messages.
      dataChannel = peerConnection.createDataChannel("chat");
      dataChannel.onopen = () => {
        sendRtvi({
          type: "client-ready",
          data: {
            version: RTVI_VERSION,
            about: { library: "learngraph-web", library_version: "1.0", platform: "browser" },
          },
        });
      };
      dataChannel.onmessage = (event) => {
        if (typeof event.data === "string") handleRtviMessage(event.data);
      };
      // Trickle ICE：候选地址边收集边 PATCH 给后端 SmallWebRTC handler
      // （Pipecat 的 handle_patch_request）。必须在 setLocalDescription 之前
      // 挂上，否则会漏掉首批候选；在拿到 offer 响应里的 pc_id 之前先缓冲，
      // 避免 PATCH 打到尚未注册的 peer connection（后端会 404）。
      const pendingCandidates: VoiceIceCandidate[] = [];
      let remotePcId: string | null = null;
      const flushIceCandidates = () => {
        if (!remotePcId || pendingCandidates.length === 0) return;
        const candidates = pendingCandidates.splice(0, pendingCandidates.length);
        // 失败不致命：非 trickle 路径仍可依赖 SDP 内联候选建连。
        void apiClient
          .patch<unknown, { pc_id: string; candidates: VoiceIceCandidate[] }>(offerPath, {
            pc_id: remotePcId,
            candidates,
          })
          .catch(() => undefined);
      };
      peerConnection.onicecandidate = (event) => {
        // 空 candidate 字符串是 RFC 8840 的 end-of-candidates 标记，
        // Pipecat 侧明确支持（映射为 aiortc 的 None）。
        pendingCandidates.push({
          candidate: event.candidate?.candidate ?? "",
          sdp_mid: event.candidate?.sdpMid ?? "0",
          sdp_mline_index: event.candidate?.sdpMLineIndex ?? 0,
        });
        flushIceCandidates();
      };
      localStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      localStream.getTracks().forEach((track) => peerConnection?.addTrack(track, localStream!));
      applyMutedToLocalStream();
      startUserAudioLevelMonitor(localStream);
      peerConnection.ontrack = (event) => {
        // Retain the element: releasing `srcObject` and pausing on teardown is
        // the only reliable way to stop the bot's audio when the PC closes.
        const audio = remoteAudio ?? new Audio();
        remoteAudio = audio;
        audio.autoplay = true;
        audio.srcObject = event.streams[0];
        // Autoplay policy: a `play()` that rejects means the tutor's audio never
        // reaches the speakers, and no amount of retrying fixes it without a user
        // gesture. Surface it instead of swallowing it, and clear it when playback
        // does start (either from the gesture-triggered retry or a later frame).
        const markPlayback = (blocked: boolean) => {
          if (remoteAudioBlocked === blocked) return;
          remoteAudioBlocked = blocked;
          update({ playbackBlocked: blocked });
        };
        audio.addEventListener("playing", () => markPlayback(false));
        void audio.play().then(() => markPlayback(false)).catch(() => markPlayback(true));
        if (event.streams[0]) startBotAudioLevelMonitor(event.streams[0]);
      };
      const offer = await peerConnection.createOffer();
      await peerConnection.setLocalDescription(offer);
      const answer = await apiClient.post<{ sdp: string; type: RTCSdpType; pc_id?: string }>(offerPath, { sdp: offer.sdp, type: offer.type, request_data: { voice_session_id: remoteId } });
      await peerConnection.setRemoteDescription(answer);
      // 拿到 pc_id 后补发缓冲中的候选，并放行后续候选。
      remotePcId = answer?.pc_id ?? null;
      flushIceCandidates();
      if (remoteId) setVoiceSessionActive(snapshot.workspaceId, snapshot.sessionId, true);
      // A page reload cannot keep the peer connection, but the call itself is
      // still standing on the server: remember it so the reloaded page can offer
      // "回到通话中" instead of silently dropping the user out of the session.
      markVoiceSessionResumable(snapshot.workspaceId, snapshot.sessionId);
      reconnectAttempt = 0;
      update({
        transport: "connected", state: "listening", sessionIdRemote: remoteId,
        signalingUrl: result?.signaling_url || null,
        sessionEpoch: Number(result?.session_epoch ?? snapshot.sessionEpoch),
        // A fresh connection re-resolves the provider, so whatever was pinned
        // while the call was down is now in force and no longer "pending".
        effectiveModelId:
          ((result as { model_id?: string | null } | undefined)?.model_id ??
            snapshot.modelId),
        modelPin: null,
      });
      // Recover the authoritative transcript (refresh / reconnect) and then
      // catch up on durable events emitted while the channel was down.
      if (remoteId) {
        void recoverVoiceSessionState().then(() => Promise.all([pollVoiceEvents(), pollVoiceTasks()]));
        startEventPolling();
        startTaskPolling();
      }
      playVoiceCue(880);
    } catch (error) {
      cleanupAudioTransport();
      if (remoteId && !reconnectingExistingSession) {
        forgetRemoteSession(snapshot.workspaceId, snapshot.sessionId);
        void apiClient.delete(`/voice/sessions/${remoteId}`).catch(() => undefined);
      }
      if (error instanceof DOMException && error.name === "AbortError") return;
      const message = describeMicrophoneError(error) ?? (error instanceof ApiError && error.status === 404 ? "语音服务尚未启用，请先部署 SmallWebRTC 语音服务。" : error instanceof Error ? error.message : "语音连接失败");
      update({ transport: "error", state: "error", error: message });
    }
  },
  disconnect() { const remote = snapshot.sessionIdRemote; const wasConnected = snapshot.transport === "connected" || snapshot.transport === "connecting" || snapshot.transport === "reconnecting"; if (reconnectTimer !== null) { window.clearTimeout(reconnectTimer); reconnectTimer = null; } abortController?.abort(); abortController = null; pendingUserFinal = ""; activeUserTurnId = ""; resetVoiceLatency(); for (const clientId of Array.from(typedAcceptTimers.keys())) clearTypedAcceptTimer(clientId); cleanupAudioTransport(); forgetRemoteSession(snapshot.workspaceId, snapshot.sessionId); closeRemoteSession(remote); setVoiceSessionActive(snapshot.workspaceId, snapshot.sessionId, false); clearVoiceSessionResumable(snapshot.workspaceId, snapshot.sessionId); const next = { ...snapshot, transport: "idle" as const, state: "closed" as const, sessionIdRemote: null, signalingUrl: null, muted: false, playbackBlocked: false, interimUserText: "", streamingAssistantText: "", audioLevel: 0, audioSource: "idle" as const }; sessionCache.set(`${snapshot.workspaceId}:${snapshot.sessionId}`, next); update(next); if (wasConnected) playVoiceCue(440); },
  async interrupt() {
    // Durable first: the interrupt must reach the worker even when this page is
    // the one that lost its data channel. The RTVI path stays as a faster copy
    // on the same pipeline (identical to an automatic barge-in).
    const remote = snapshot.sessionIdRemote;
    let delivered = false;
    if (remote) {
      try { await apiClient.post(`/voice/sessions/${remote}/interrupt`, {}); delivered = true; }
      catch (error) { update({ error: error instanceof Error ? error.message : "语音打断失败" }); }
    }
    if (sendRtvi({ type: "client-message", data: { t: VOICE_INTERRUPT_MESSAGE, d: null } })) delivered = true;
    if (delivered) update({ state: "ready" });
    return delivered;
  },
  /**
   * Submits typed text as a real voice turn.
   *
   * The pipeline's `send-text` handler appends the message to the same LLM
   * context the ASR transcript feeds and runs the same turn, so typing while in
   * voice mode is not a second, parallel chat request — it is the same turn
   * semantics as speaking.
   */
  sendText(text: string, existingClientMessageId?: string): boolean {
    const content = text.trim();
    if (!content) return false;
    const clientMessageId = existingClientMessageId || createUuid();
    // Announce the idempotency key first, on the same ordered channel: the
    // worker needs it to attach to the turn `persistAcceptedTurn` is about to
    // create (otherwise one typed utterance becomes the client's turn plus a
    // worker-opened one -- two rows, two bubbles).
    sendRtvi({
      type: "client-message",
      data: {
        t: VOICE_TYPED_TURN_MESSAGE,
        d: { client_message_id: clientMessageId, text: content },
      },
    });
    if (!sendRtvi({
      type: "send-text",
      data: { content, client_message_id: clientMessageId, options: { run_immediately: true, audio_response: true } },
    })) {
      update({ error: "语音通道尚未就绪，请先连接语音导师。" });
      return false;
    }
    pendingTyped.set(clientMessageId, { text: content });
    // 打字回合也参与延迟统计：起点是"发送"这一刻（照 demo 的 text 轮），
    // 后续环节与语音轮共用同一条链路。
    markVoiceLatency({ stage: "userDone", source: "typed", kind: "text" });
    // Create the durable idempotent turn record up front.  The worker attaches
    // to this same turn (it is idempotent on `client_message_id`), so a typed
    // message can never become two turns.
    persistAcceptedTurn(clientMessageId, content, clientMessageId);
    // Show a speculative pending bubble. It is promoted to authoritative when
    // the durable `turn.accepted` event echoes this idempotency key, and marked
    // failed (with a retry that reuses the same key) if that never arrives.
    dispatchVoiceRender({ id: userEntryId(undefined, clientMessageId), role: "user", text: content, final: false, pending: true, clientMessageId, deliveryStatus: "pending", createdAt: new Date().toISOString() });
    armTypedAcceptTimer(clientMessageId);
    return true;
  },
  retryText(clientMessageId: string): boolean {
    const pending = pendingTyped.get(clientMessageId);
    return pending ? voiceSessionController.sendText(pending.text, clientMessageId) : false;
  },
  /**
   * Legacy entry point kept for callers that only have text.
   *
   * It used to POST `/voice/sessions/{id}/turns`, which starts a *separate*
   * detached ChatService agent turn: the pipeline answered the audio turn while
   * the HTTP path answered the text turn, i.e. two answers for one utterance.
   * Everything now goes through the single RTVI channel the pipeline owns.
   */
  async sendTurn(text: string, _role: VoiceTranscriptRole = "user") {
    return voiceSessionController.sendText(text);
  },
  async startTask(
    prompt: string,
    title = "",
    options: {
      roleKey?: string;
      tools?: string[];
      skills?: string[];
      writeSet?: string[];
      outputContract?: Record<string, unknown>;
      sandboxSessionId?: string;
    } = {},
  ) {
    const remote = snapshot.sessionIdRemote;
    if (!remote || !prompt.trim()) return null;
    try {
      const task = await requestVoiceTaskStart(remote, {
        prompt: prompt.trim(),
        title: title.trim() || undefined,
        role_key: options.roleKey,
        thinking_mode: snapshot.thinkingLimit,
        tools: options.tools,
        skills: options.skills,
        write_set: options.writeSet,
        output_contract: options.outputContract,
        sandbox_session_id: options.sandboxSessionId,
      });
      const taskId = String(task.task_id ?? task.subagent_id ?? task.id ?? "");
      applyTaskEvent({
        ...task,
        event_id: `task-started:${taskId}:${String(task.requirement_version ?? task.request_revision ?? 1)}`,
        type: "task.accepted",
      }, taskId || undefined);
      return task;
    } catch (error) {
      update({ error: error instanceof Error ? error.message : "任务启动失败" });
      return null;
    }
  },
  async cancelTask(taskId: string): Promise<boolean> {
    const remote = snapshot.sessionIdRemote;
    if (!remote || !taskId || cancellingTaskIds.has(taskId)) return false;
    cancellingTaskIds.add(taskId);
    try {
      const task = await requestVoiceTaskCancel(remote, taskId);
      applyTaskEvent({
        ...task,
        task_id: task.task_id ?? taskId,
        event_id: `task-cancel:${taskId}:${String(task.event_seq ?? Date.now())}`,
        type: "task.cancel_requested",
      }, taskId);
      return true;
    } catch (error) {
      update({ error: error instanceof Error ? error.message : "任务取消失败" });
      return false;
    } finally {
      cancellingTaskIds.delete(taskId);
    }
  },
  appendTranscript(item: VoiceTranscript) {
    // 同一条转录可能既被内部路径追加、又被页面监听器回灌（CustomEvent 会再
    // 次触发 append），按 id 去重避免气泡/字幕出现重复行。
    if (snapshot.transcript.some((existing) => existing.id === item.id)) return;
    update({ transcript: [...snapshot.transcript.slice(-49), item] });
  },
  upsertTask(event: VoiceTaskPatch) {
    taskState = mergeVoiceTaskPatches(taskState, [event]);
    syncTaskState();
  },
};
/** Bridges RTVI/WebRTC adapters to the UI without coupling them to React. */
export function dispatchVoiceTranscript(item: VoiceTranscript) { if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("learngraph:voice-transcript", { detail: item })); }
export function dispatchVoiceTask(item: VoiceTaskEvent) { if (typeof window !== "undefined") window.dispatchEvent(new CustomEvent("learngraph:voice-task", { detail: item })); }

const VoiceSessionContext = createContext(voiceSessionController);
export function VoiceSessionProvider({ children }: { children: ReactNode }) {
  useEffect(() => {
    const onTranscript = (event: Event) => {
      const item = (event as CustomEvent<VoiceTranscript>).detail;
      if (item?.id && item.text) voiceSessionController.appendTranscript(item);
    };
    const onTask = (event: Event) => {
      const item = (event as CustomEvent<VoiceTaskEvent>).detail;
      if (item?.taskId && item.status) voiceSessionController.upsertTask(item);
    };
    window.addEventListener("learngraph:voice-transcript", onTranscript);
    window.addEventListener("learngraph:voice-task", onTask);
    return () => { window.removeEventListener("learngraph:voice-transcript", onTranscript); window.removeEventListener("learngraph:voice-task", onTask); };
  }, []);
  return <VoiceSessionContext.Provider value={voiceSessionController}>{children}</VoiceSessionContext.Provider>;
}
export function useVoiceSession(workspaceId?: string, sessionId?: string, modelId?: string | null, providerId?: string | null) {
  const controller = useContext(VoiceSessionContext);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  useEffect(() => { if (workspaceId && sessionId) controller.open(workspaceId, sessionId, modelId, providerId); }, [controller, sessionId, workspaceId, modelId, providerId]);
  return useMemo(() => ({ ...state, connect: controller.connect, disconnect: controller.disconnect, interrupt: controller.interrupt, setThinkingLimit: controller.setThinkingLimit, setMuted: controller.setMuted, appendTranscript: controller.appendTranscript, upsertTask: controller.upsertTask, sendText: controller.sendText, retryText: controller.retryText, sendTurn: controller.sendTurn, startTask: controller.startTask, cancelTask: controller.cancelTask }), [controller, state]);
}
