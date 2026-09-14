import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { apiClient, ApiError } from "@/api/client";
import {
  cancelVoiceTask as requestVoiceTaskCancel,
  getVoiceSession as requestVoiceSession,
  getVoiceTask as requestVoiceTask,
  startVoiceTask as requestVoiceTaskStart,
} from "@/api/voice";
import { setVoiceSessionActive } from "./voice-session-markers";
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

export type VoiceTransportState = "idle" | "connecting" | "connected" | "reconnecting" | "error";
export type VoiceSessionState = "closed" | "ready" | "listening" | "thinking" | "speaking" | "error";
export type ThinkingLimit = "off" | "low" | "medium" | "high" | "xhigh";
export type VoiceTranscriptRole = TranscriptEntry["role"];
export type VoiceAudioSource = "idle" | "user" | "assistant";
export type VoiceTranscript = TranscriptEntry;
export type VoiceTaskEvent = VoiceTaskPatch;
export interface VoiceRenderUpdate extends Omit<Partial<TranscriptEntry>, "role"> {
  id: string;
  role: "user" | "assistant";
  text: string;
  final: boolean;
  createdAt: string;
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
  /** True when ordinary text chat must be allowed even though voice mode is on. */
  textFallback: boolean;
}

const STORAGE_KEY = "learngraph.voice.preferences.v1";
const REMOTE_SESSION_STORAGE_KEY = "learngraph.voice.remote-sessions.v1";

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
const defaultSnapshot: VoiceSessionSnapshot = { workspaceId: "", sessionId: "", transport: "idle", state: "closed", thinkingLimit: "high", transcript: [], tasks: [], error: null, modelId: null, providerId: null, sessionIdRemote: null, signalingUrl: null, muted: false, interimUserText: "", streamingAssistantText: "", audioLevel: 0, audioSource: "idle", lastEventSeq: 0, sessionEpoch: 0, effectiveModelId: null, modelPin: null, degradedStages: [], degradedNotice: null, textFallback: false };
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

function sendRtvi(message: { type: string; data?: unknown }): boolean {
  if (dataChannel?.readyState !== "open") return false;
  dataChannel.send(JSON.stringify({ label: RTVI_LABEL, id: crypto.randomUUID(), ...message }));
  return true;
}

function dispatchVoiceRender(update: VoiceRenderUpdate) {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("learngraph:voice-render", { detail: update }));
  }
}

function appendTranscriptItem(role: VoiceRenderUpdate["role"], text: string, interrupted = false, id: string = crypto.randomUUID(), meta: Partial<Omit<TranscriptEntry, "role">> = {}) {
  const clean = text.trim();
  if (!clean) return;
  const item: VoiceRenderUpdate & TranscriptEntry = {
    id, role, text: clean, final: true, phase: "authoritative",
    createdAt: new Date().toISOString(), interrupted, authoritative: true, ...meta,
  };
  voiceSessionController.appendTranscript(item);
  // 广播给页面：聊天画布据此把语音回合渲染成正常的消息气泡（打字得到的回合
  // 与说话得到的回合落在同一个对话里）。接收端按 id 去重，重复派发无害。
  dispatchVoiceTranscript(item);
  dispatchVoiceRender({ ...item, final: true });
}

/** 本回合已收到、但还没落到 transcript 的用户 final 转录（见 handleRtviMessage）。 */
let pendingUserFinal = "";
let activeUserTurnId = "";
let lastCommittedUserTurnId = "";
let activeAssistantTurnId = "";
let assistantLlmText = "";
let assistantSpokenText = "";
let assistantSentenceSequence = 0;
let assistantLlmComplete = false;
let assistantFinalized = false;
let lastAudioCursorMs = 0;
let reconnectTimer: number | null = null;
let reconnectAttempt = 0;
let iceRestartAttempted = false;
const seenEventIds = new Set<string>();
const pendingTyped = new Map<string, { text: string; turnId?: string }>();
const turnAudioCursors = new Map<string, number>();

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
  turnAudioCursors.clear();
}

/** Fold a durable event into the transcript projection. */
function applyDurableEvent(event: VoiceEventLike) {
  durableEventCount += 1;
  transcriptState = reduceVoiceEvent(transcriptState, event);
  syncTranscriptFromState();
}

/** How many durable events this page has folded in (0 = journal unavailable). */
let durableEventCount = 0;

function persistAcceptedTurn(turnId: string, userText: string, clientMessageId?: string) {
  const remote = snapshot.sessionIdRemote;
  if (!remote || !userText.trim()) return;
  void apiClient.post(`/voice/sessions/${remote}/turns/accept`, {
    turn_id: turnId,
    text: userText.trim(),
    client_message_id: clientMessageId,
  }).catch(() => undefined);
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

function persistFinalizedTurn(turnId: string, assistantText: string) {
  const remote = snapshot.sessionIdRemote;
  if (!remote || !assistantText.trim()) return;
  void apiClient.post(`/voice/sessions/${remote}/turns/${turnId}/finalize`, {
    assistant_text: assistantText.trim(),
  }).catch(() => undefined);
}

function joinTranscriptSegment(current: string, next: string): string {
  const a = current.trimEnd();
  const b = next.trimStart();
  if (!a) return b;
  if (!b) return a;
  // ASR finals often omit boundary whitespace. Add it only between latin/
  // numeric tokens; Chinese segments should remain adjacent.
  const needsSpace = /[A-Za-z0-9]$/.test(a) && /^[A-Za-z0-9]/.test(b);
  return `${a}${needsSpace ? " " : ""}${b}`;
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
        turns?: Array<{ turn_id: string; user_text?: string; assistant_text?: string; client_message_id?: string | null; finalized_at?: string | null }>;
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
  if (!activeUserTurnId) activeUserTurnId = crypto.randomUUID();
  try { localStorage.setItem(`learngraph.voice.turn.${snapshot.sessionId}`, activeUserTurnId); } catch { /* optional */ }
  return activeUserTurnId;
}

function dispatchUserDraft(text: string, final = false) {
  if (!text.trim()) return;
  dispatchVoiceRender({
    id: ensureUserTurnId(), role: "user", text, final,
    createdAt: new Date().toISOString(),
  });
}

/** 把一个用户回合的（可能多段）final 合成一条记录并广播出去。 */
function flushPendingUserFinal() {
  const text = pendingUserFinal;
  pendingUserFinal = "";
  const turnId = ensureUserTurnId();
  if (text.trim()) {
    // Audio turns are owned by the worker: it opens the durable turn and emits
    // `user.final`/`turn.accepted`, which is what becomes the authoritative
    // transcript entry. Creating a turn here as well would produce two turns
    // (and two chat messages) for one utterance, so the client only renders.
    if (durableEventCount === 0) appendTranscriptItem("user", text, false, turnId, { turnId });
    else dispatchVoiceRender({ id: userEntryId(turnId, undefined), role: "user", text, final: true, turnId, createdAt: new Date().toISOString() });
  }
  lastCommittedUserTurnId = turnId;
  activeUserTurnId = "";
}

function beginAssistantTurn() {
  activeAssistantTurnId = lastCommittedUserTurnId || activeUserTurnId || crypto.randomUUID();
  lastCommittedUserTurnId = "";
  try { localStorage.setItem(`learngraph.voice.turn.${snapshot.sessionId}`, activeAssistantTurnId); } catch { /* optional */ }
  assistantLlmText = "";
  assistantSpokenText = "";
  assistantSentenceSequence = 0;
  lastAudioCursorMs = 0;
  assistantLlmComplete = false;
  assistantFinalized = false;
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

function processVoiceEvent(event: VoiceEventEnvelope) {
  const payload = event.payload ?? {};
  const type = event.type || event.phase;
  const turnId = event.turn_id || String(payload.turn_id ?? payload.turnId ?? "") || undefined;
  const text = String(payload.text ?? payload.content ?? payload.delta ?? "");
  // Single ordering authority: every durable event is folded into the pure
  // reducer, which decides identity and monotonicity from server ids.
  applyDurableEvent({
    event_id: event.event_id,
    event_seq: event.event_seq,
    type,
    phase: event.phase,
    turn_id: turnId ?? null,
    audio_cursor_ms: event.audio_cursor_ms,
    causality: event.causality,
    payload,
  });
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
      update({ state: "ready", error: null });
      return;
    case "session.ready":
      // A rebuilt pipeline re-resolves every stage, so a previous degradation is
      // no longer a statement about the current one.
      update({
        transport: "connected", state: "listening", error: null,
        degradedStages: [], degradedNotice: null, textFallback: false,
      });
      return;
    case "session.reconnecting":
      update({ transport: "reconnecting", error: null });
      return;
    case "session.closed":
      forgetRemoteSession(snapshot.workspaceId, snapshot.sessionId);
      update({ transport: "idle", state: "closed", sessionIdRemote: null });
      return;
    case "user.started":
      activeUserTurnId = turnId || activeUserTurnId || crypto.randomUUID();
      update({ state: "listening", interimUserText: "" });
      return;
    case "user.interim":
      activeUserTurnId = turnId || activeUserTurnId || crypto.randomUUID();
      update({ interimUserText: text });
      dispatchVoiceRender({ id: activeUserTurnId, role: "user", text, final: false, pending: true, turnId: activeUserTurnId, createdAt: new Date().toISOString(), eventId: event.event_id });
      return;
    case "user.final":
      activeUserTurnId = turnId || activeUserTurnId || crypto.randomUUID();
      pendingUserFinal = joinTranscriptSegment(pendingUserFinal, text);
      update({ interimUserText: "" });
      return;
    case "turn.accepted": {
      const clientId = String(payload.client_message_id ?? payload.clientMessageId ?? "");
      if (clientId && pendingTyped.has(clientId)) {
        const pending = pendingTyped.get(clientId)!;
        pendingTyped.set(clientId, { ...pending, turnId });
        if (turnId) lastCommittedUserTurnId = turnId;
        clearTypedAcceptTimer(clientId);
        pendingTyped.delete(clientId);
        // The transcript entry is produced by the reducer from this same event,
        // so no manual append is needed (and would double the bubble).
        dispatchVoiceRender({
          id: userEntryId(turnId, clientId), role: "user", text: pending.text,
          final: true, turnId, clientMessageId: clientId, deliveryStatus: "accepted",
          createdAt: new Date().toISOString(), eventId: event.event_id,
        });
      }
      return;
    }
    case "turn.finalized":
      if (String(payload.role ?? "") === "user") {
        pendingUserFinal = joinTranscriptSegment(pendingUserFinal, text);
        activeUserTurnId = turnId || activeUserTurnId || crypto.randomUUID();
        flushPendingUserFinal();
      } else {
        if (pendingUserFinal.trim()) flushPendingUserFinal();
        if (text.trim()) {
          dispatchVoiceRender({
            id: `assistant-final-${turnId ?? "unknown"}`, role: "assistant", text,
            final: true, turnId, createdAt: new Date().toISOString(), eventId: event.event_id,
          });
        } else {
          finalizeAssistantTurn(false);
        }
      }
      return;
    case "turn.interrupted":
      finalizeAssistantTurn(true);
      return;
    case "assistant.llm.delta":
      if (!activeAssistantTurnId || (turnId && activeAssistantTurnId !== turnId)) beginAssistantTurn();
      assistantLlmText += text;
      update({ state: "thinking", streamingAssistantText: assistantLlmText });
      dispatchVoiceRender({ id: activeAssistantTurnId, role: "assistant", text: assistantLlmText, final: false, turnId, createdAt: new Date().toISOString(), eventId: event.event_id });
      return;
    case "assistant.sentence.queued":
    case "assistant.sentence.playback_started":
      appendAssistantSentence(text, Number(payload.sentence_seq ?? payload.sentenceSeq));
      return;
    case "assistant.sentence.playback_ended":
      if (payload.final === true || payload.turn_final === true) finalizeAssistantTurn(false);
      return;
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
      const pin = deriveModelPin(
        payload,
        event,
        snapshot.effectiveModelId ?? snapshot.modelId,
      );
      update({
        modelPin: pin,
        ...(pin.repointed && pin.effectiveModelId
          ? { effectiveModelId: pin.effectiveModelId }
          : {}),
      });
      return;
    }
    default:
      return;
  }
}

function emitAssistantSpoken() {
  update({ streamingAssistantText: assistantSpokenText });
  if (assistantSpokenText.trim()) {
    dispatchVoiceRender({
      id: activeAssistantTurnId, role: "assistant", text: assistantSpokenText,
      final: false, createdAt: new Date().toISOString(), turnId: activeAssistantTurnId,
      sentenceSeq: assistantSentenceSequence,
      audioCursorMs: lastAudioCursorMs,
    });
  }
}

function appendAssistantSentence(text: string, sequence?: number, audioCursorMs?: number) {
  const clean = text.trim();
  if (!clean) return;
  if (Number.isFinite(audioCursorMs)) lastAudioCursorMs = Math.max(lastAudioCursorMs, Number(audioCursorMs));
  if (!activeAssistantTurnId) beginAssistantTurn();
  // The backend marker is emitted after this sentence's first audio frame has
  // entered the output queue. Deduplicate by the backend sequence, never by
  // text: two consecutive sentences are allowed to have identical content.
  const markerSequence = Number(sequence);
  if (Number.isFinite(markerSequence) && markerSequence > 0) {
    if (markerSequence <= assistantSentenceSequence) return;
    assistantSentenceSequence = markerSequence;
  }
  // Preserve the original spacing between sentences.
  assistantSpokenText = assistantSpokenText ? `${assistantSpokenText}${text}` : text;
  emitAssistantSpoken();
}

function finalizeAssistantTurn(interrupted: boolean) {
  if (assistantFinalized || !activeAssistantTurnId) return;
  assistantFinalized = true;
  const text = (interrupted ? assistantSpokenText : (assistantLlmText || assistantSpokenText)).trim();
  const turnId = activeAssistantTurnId;
  turnAudioCursors.delete(turnId);
  if (text) {
    // The durable `turn.finalized` event is the authoritative source and the
    // only writer of the transcript/memory. Without the journal (older backend
    // or a channel that never delivered a single durable event) fall back to the
    // legacy local render so captions are not lost outright.
    if (durableEventCount === 0) {
      if (!interrupted) persistFinalizedTurn(turnId, text);
      appendTranscriptItem("assistant", text, interrupted, turnId, { turnId });
    } else {
      dispatchVoiceRender({ id: `assistant-final-${turnId}`, role: "assistant", text, final: true, interrupted, turnId, createdAt: new Date().toISOString() });
    }
  }
  update({ streamingAssistantText: "" });
  activeAssistantTurnId = "";
  assistantLlmText = "";
  assistantSpokenText = "";
  assistantSentenceSequence = 0;
  try { localStorage.removeItem(`learngraph.voice.turn.${snapshot.sessionId}`); } catch { /* optional */ }
}

/**
 * Maps the pipeline's RTVI server messages onto the voice snapshot.
 *
 * The bot already broadcasts its own transcript stream (`user-transcription`,
 * `bot-llm-text`) plus speaking state; this is the only channel that carries
 * "what was actually heard / answered" back to the page, so both the captions and
 * the orb's listening/thinking/speaking states are driven from here.
 */
function handleRtviMessage(raw: string) {
  type RtviEnvelope = { label?: string; type?: string; data?: Record<string, unknown> };
  let payload: RtviEnvelope | null = null;
  try { payload = JSON.parse(raw) as RtviEnvelope; } catch { return; }
  if (!payload || payload.label !== RTVI_LABEL) return;
  const data = payload.data ?? {};
  // New durable event envelopes may arrive wrapped in an RTVI server-message
  // or directly as `data.event`. Apply them before legacy observer messages.
  const candidate = (data.event ?? data.payload ?? data.data ?? data) as Record<string, unknown>;
  if (candidate && (candidate.event_id || candidate.eventId) && (candidate.event_seq !== undefined || candidate.eventSeq !== undefined)) {
    const event = normalizeVoiceEvent(candidate);
    if (event && !seenEventIds.has(event.event_id)) {
      seenEventIds.add(event.event_id);
      if (event.event_seq > snapshot.lastEventSeq) update({ lastEventSeq: event.event_seq, sessionEpoch: event.session_epoch });
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
    case "user-transcription": {
      const text = String(data.text ?? "");
      if (data.final) {
        // 一个「用户回合」可能产生多段 final：本机 VAD 判定停止即 commit
        // （见 §6.5.2 的延迟修复），所以说话中途的停顿也会各出一段 final。
        // 这里只暂存，等聚合器广播回合边界（user-stopped-speaking）时再落
        // 一条记录，避免中途停顿被拆成两个气泡/两条字幕。
        pendingUserFinal = joinTranscriptSegment(pendingUserFinal, text);
        update({ interimUserText: "" });
        dispatchUserDraft(pendingUserFinal);
      } else {
        update({ interimUserText: text });
        dispatchUserDraft(text);
      }
      return;
    }
    case "user-stopped-speaking":
      flushPendingUserFinal();
      return;
    case "bot-llm-started":
      // 安全网：万一没有收到回合边界消息，导师开始作答也意味着用户说完了。
      flushPendingUserFinal();
      beginAssistantTurn();
      update({ state: "thinking", streamingAssistantText: "" });
      return;
    case "bot-llm-text":
      if (!activeAssistantTurnId) beginAssistantTurn();
      assistantLlmText = `${assistantLlmText}${String(data.text ?? "")}`;
      return;
    case "bot-llm-stopped": {
      assistantLlmComplete = true;
      return;
    }
    case "bot-started-speaking":
      update({ state: "speaking" });
      return;
    case "bot-stopped-speaking": {
      update({ state: "listening" });
      finalizeAssistantTurn(false);
      return;
    }
    case "bot-interrupted": {
      update({ state: "listening" });
      lastBotLoudAt = 0;
      finalizeAssistantTurn(true);
      return;
    }
    case "server-message": {
      // The backend disables Pipecat's eager bot-output/bot-tts-text messages.
      // It emits this marker only after the first audio frame for a sentence has
      // entered the WebRTC output queue, giving the canvas a sentence-level
      // playback cursor instead of the whole LLM response.
      if (String(data.type ?? "") === "voice-sentence-start") {
        const markerId = String(data.event_id ?? data.eventId ?? "");
        if (markerId && seenEventIds.has(markerId)) return;
        if (markerId) seenEventIds.add(markerId);
        appendAssistantSentence(
          String(data.text ?? ""),
          Number(data.sequence ?? data.sentence_seq),
          Number(data.audio_cursor_ms),
        );
      }
      return;
    }
    case "bot-output":
    case "bot-tts-text":
      // Deliberately ignored: these observer messages are generated before the
      // browser has played the associated audio and caused the old eager render.
      return;
    default:
      // `metrics` / `bot-ready` carry nothing the captions need.
      return;
  }
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
        if (snapshot.transport === "connected" && snapshot.state !== "speaking") {
          update({ state: "speaking" });
        }
      } else if (snapshot.state === "speaking" && now - lastBotLoudAt > 450) {
        update({ state: "listening" });
        if (assistantLlmComplete) finalizeAssistantTurn(false);
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
      activeAssistantTurnId = "";
      assistantLlmText = "";
      assistantSpokenText = "";
      assistantSentenceSequence = 0;
      assistantLlmComplete = false;
      assistantFinalized = false;
      resetTranscriptState();
      resetTaskState();
      durableEventCount = 0;
      closedRemoteSession = null;
    }
    update({ transport: "connecting", state: "ready", error: null, interimUserText: "", streamingAssistantText: "", audioLevel: 0, audioSource: "idle" });
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
      peerConnection = new RTCPeerConnection();
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
        void audio.play().catch(() => undefined);
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
      const message = error instanceof ApiError && error.status === 404 ? "语音服务尚未启用，请先部署 SmallWebRTC 语音服务。" : error instanceof Error ? error.message : "语音连接失败";
      update({ transport: "error", state: "error", error: message });
    }
  },
  disconnect() { const remote = snapshot.sessionIdRemote; const wasConnected = snapshot.transport === "connected" || snapshot.transport === "connecting" || snapshot.transport === "reconnecting"; if (reconnectTimer !== null) { window.clearTimeout(reconnectTimer); reconnectTimer = null; } abortController?.abort(); abortController = null; pendingUserFinal = ""; activeUserTurnId = ""; lastCommittedUserTurnId = ""; activeAssistantTurnId = ""; assistantLlmText = ""; assistantSpokenText = ""; assistantSentenceSequence = 0; assistantLlmComplete = false; assistantFinalized = false; for (const clientId of Array.from(typedAcceptTimers.keys())) clearTypedAcceptTimer(clientId); cleanupAudioTransport(); forgetRemoteSession(snapshot.workspaceId, snapshot.sessionId); closeRemoteSession(remote); setVoiceSessionActive(snapshot.workspaceId, snapshot.sessionId, false); const next = { ...snapshot, transport: "idle" as const, state: "closed" as const, sessionIdRemote: null, signalingUrl: null, muted: false, interimUserText: "", streamingAssistantText: "", audioLevel: 0, audioSource: "idle" as const }; sessionCache.set(`${snapshot.workspaceId}:${snapshot.sessionId}`, next); update(next); if (wasConnected) playVoiceCue(440); },
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
    const clientMessageId = existingClientMessageId || crypto.randomUUID();
    if (!sendRtvi({
      type: "send-text",
      data: { content, client_message_id: clientMessageId, options: { run_immediately: true, audio_response: true } },
    })) {
      update({ error: "语音通道尚未就绪，请先连接语音导师。" });
      return false;
    }
    pendingTyped.set(clientMessageId, { text: content });
    lastCommittedUserTurnId = clientMessageId;
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
