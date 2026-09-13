import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { apiClient, ApiError } from "@/api/client";
import { setVoiceSessionActive } from "./voice-session-markers";

export type VoiceTransportState = "idle" | "connecting" | "connected" | "reconnecting" | "error";
export type VoiceSessionState = "closed" | "ready" | "listening" | "thinking" | "speaking" | "error";
export type ThinkingLimit = "off" | "low" | "medium" | "high" | "xhigh";
export type VoiceTranscriptRole = "user" | "assistant";
export type VoiceAudioSource = "idle" | "user" | "assistant";
export interface VoiceTranscript { id: string; role: VoiceTranscriptRole; text: string; final: boolean; createdAt: string; interrupted?: boolean }
export interface VoiceRenderUpdate { id: string; role: VoiceTranscriptRole; text: string; final: boolean; createdAt: string; interrupted?: boolean }
export interface VoiceTaskEvent { taskId: string; status: "queued" | "running" | "blocked" | "completed" | "failed" | "cancelled"; title?: string; summary?: string; progress?: number; updatedAt: string }
/** 与 Pipecat `SmallWebRTCPatchRequest.candidates[].IceCandidate` 一一对应（snake_case）。 */
export interface VoiceIceCandidate { candidate: string; sdp_mid: string; sdp_mline_index: number }
export interface VoiceSessionSnapshot {
  workspaceId: string; sessionId: string; transport: VoiceTransportState; state: VoiceSessionState;
  thinkingLimit: ThinkingLimit; transcript: VoiceTranscript[]; tasks: VoiceTaskEvent[]; error: string | null;
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
}

const STORAGE_KEY = "learngraph.voice.preferences.v1";
const defaultSnapshot: VoiceSessionSnapshot = { workspaceId: "", sessionId: "", transport: "idle", state: "closed", thinkingLimit: "high", transcript: [], tasks: [], error: null, modelId: null, providerId: null, sessionIdRemote: null, signalingUrl: null, muted: false, interimUserText: "", streamingAssistantText: "", audioLevel: 0, audioSource: "idle" };
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

function appendTranscriptItem(role: VoiceTranscriptRole, text: string, interrupted = false, id: string = crypto.randomUUID()) {
  const clean = text.trim();
  if (!clean) return;
  const item: VoiceTranscript = {
    id, role, text: clean, final: true,
    createdAt: new Date().toISOString(), interrupted,
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
let activeAssistantTurnId = "";
let assistantLlmText = "";
let assistantSpokenText = "";
let assistantSentenceSequence = 0;
let assistantLlmComplete = false;
let assistantFinalized = false;

function ensureUserTurnId() {
  if (!activeUserTurnId) activeUserTurnId = crypto.randomUUID();
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
  if (text.trim()) appendTranscriptItem("user", text, false, ensureUserTurnId());
  activeUserTurnId = "";
}

function beginAssistantTurn() {
  activeAssistantTurnId = crypto.randomUUID();
  assistantLlmText = "";
  assistantSpokenText = "";
  assistantSentenceSequence = 0;
  assistantLlmComplete = false;
  assistantFinalized = false;
}

function emitAssistantSpoken() {
  update({ streamingAssistantText: assistantSpokenText });
  if (assistantSpokenText.trim()) {
    dispatchVoiceRender({
      id: activeAssistantTurnId, role: "assistant", text: assistantSpokenText,
      final: false, createdAt: new Date().toISOString(),
    });
  }
}

function appendAssistantSentence(text: string, sequence?: number) {
  const clean = text.trim();
  if (!clean) return;
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
  if (text) appendTranscriptItem("assistant", text, interrupted, activeAssistantTurnId);
  update({ streamingAssistantText: "" });
  activeAssistantTurnId = "";
  assistantLlmText = "";
  assistantSpokenText = "";
  assistantSentenceSequence = 0;
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
  switch (payload.type) {
    case "user-transcription": {
      const text = String(data.text ?? "");
      if (data.final) {
        // 一个「用户回合」可能产生多段 final：本机 VAD 判定停止即 commit
        // （见 §6.5.2 的延迟修复），所以说话中途的停顿也会各出一段 final。
        // 这里只暂存，等聚合器广播回合边界（user-stopped-speaking）时再落
        // 一条记录，避免中途停顿被拆成两个气泡/两条字幕。
        pendingUserFinal += text;
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
        appendAssistantSentence(String(data.text ?? ""), Number(data.sequence));
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
function update(patch: Partial<VoiceSessionSnapshot>) { snapshot = { ...snapshot, ...patch }; emit(); }
function readLimit(): ThinkingLimit {
  try { const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null")?.thinkingLimit; return ["off", "low", "medium", "high", "xhigh"].includes(value) ? value : "high"; } catch { return "high"; }
}
function persistLimit(value: ThinkingLimit) { try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ thinkingLimit: value })); } catch { /* storage is optional */ } }
function cleanupAudioTransport() {
  if (peerConnection) peerConnection.onicecandidate = null;
  stopBotAudioLevelMonitor();
  stopUserAudioLevelMonitor();
  if (dataChannel) {
    dataChannel.onopen = null;
    dataChannel.onmessage = null;
    dataChannel.close();
    dataChannel = null;
  }
  localStream?.getTracks().forEach((track) => track.stop());
  localStream = null;
  peerConnection?.close();
  peerConnection = null;
}

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
      const cached = sessionCache.get(`${workspaceId}:${sessionId}`);
      update(cached ? { ...cached, state: cached.state === "closed" ? "ready" : cached.state, modelId: modelId ?? cached.modelId, providerId: providerId ?? cached.providerId } : { ...defaultSnapshot, workspaceId, sessionId, modelId: modelId ?? null, providerId: providerId ?? null, thinkingLimit: readLimit(), state: "ready" });
    }
    else if (snapshot.state === "closed") update({ state: "ready", modelId: modelId ?? snapshot.modelId, providerId: providerId ?? snapshot.providerId });
    else if (modelId !== undefined || providerId !== undefined) update({ modelId: modelId ?? snapshot.modelId, providerId: providerId ?? snapshot.providerId });
  },
  setThinkingLimit(value: ThinkingLimit) { persistLimit(value); update({ thinkingLimit: value }); },
  setMuted(value: boolean) { update({ muted: value }); applyMutedToLocalStream(); },
  async connect() {
    if (!snapshot.sessionId || snapshot.transport === "connecting" || snapshot.transport === "connected") return;
    abortController?.abort(); abortController = new AbortController();
    pendingUserFinal = "";
    activeUserTurnId = "";
    activeAssistantTurnId = "";
    assistantLlmText = "";
    assistantSpokenText = "";
    assistantSentenceSequence = 0;
    assistantLlmComplete = false;
    assistantFinalized = false;
    update({ transport: "connecting", state: "ready", error: null, interimUserText: "", streamingAssistantText: "", audioLevel: 0, audioSource: "idle" });
    let remoteId: string | null = null;
    try {
      // The endpoint is intentionally explicit: until the backend voice contract
      // is deployed, the UI reports the unavailable service instead of faking a call.
      const path = import.meta.env.VITE_VOICE_SESSION_PATH || "/voice/sessions";
      const result = await apiClient.post<{ id?: string; session_id?: string; sessionId?: string; runtime_ready?: boolean; signaling_url?: string | null; reason?: string }>(path, { session_id: snapshot.sessionId, thinking_limit: snapshot.thinkingLimit, model_id: snapshot.modelId, provider_id: snapshot.providerId }, { signal: abortController.signal });
      remoteId = result?.id || result?.session_id || result?.sessionId || null;
      if (result?.runtime_ready !== true) {
        setVoiceSessionActive(snapshot.workspaceId, snapshot.sessionId, false);
        const error = result?.reason === "voice_runtime_dependency_missing" ? "语音运行时依赖未安装，请安装 LearnGraph 的 voice 依赖后重启后端。" : "语音会话已创建，但内置 SmallWebRTC 运行时尚未就绪。";
        update({ transport: "error", state: "error", sessionIdRemote: remoteId, signalingUrl: result?.signaling_url || null, error });
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
        const audio = new Audio();
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
      update({ transport: "connected", state: "listening", sessionIdRemote: remoteId, signalingUrl: result?.signaling_url || null });
      playVoiceCue(880);
    } catch (error) {
      cleanupAudioTransport();
      if (remoteId) void apiClient.delete(`/voice/sessions/${remoteId}`).catch(() => undefined);
      if (error instanceof DOMException && error.name === "AbortError") return;
      const message = error instanceof ApiError && error.status === 404 ? "语音服务尚未启用，请先部署 SmallWebRTC 语音服务。" : error instanceof Error ? error.message : "语音连接失败";
      update({ transport: "error", state: "error", error: message });
    }
  },
  disconnect() { const remote = snapshot.sessionIdRemote; const wasConnected = snapshot.transport === "connected" || snapshot.transport === "connecting"; abortController?.abort(); abortController = null; pendingUserFinal = ""; activeUserTurnId = ""; activeAssistantTurnId = ""; assistantLlmText = ""; assistantSpokenText = ""; assistantSentenceSequence = 0; assistantLlmComplete = false; assistantFinalized = false; cleanupAudioTransport(); if (remote) void apiClient.delete(`/voice/sessions/${remote}`).catch(() => undefined); setVoiceSessionActive(snapshot.workspaceId, snapshot.sessionId, false); const next = { ...snapshot, transport: "idle" as const, state: "closed" as const, sessionIdRemote: null, signalingUrl: null, muted: false, interimUserText: "", streamingAssistantText: "", audioLevel: 0, audioSource: "idle" as const }; sessionCache.set(`${snapshot.workspaceId}:${snapshot.sessionId}`, next); update(next); if (wasConnected) playVoiceCue(440); },
  async interrupt() {
    // Prefer the RTVI channel: it reaches the very pipeline that is speaking
    // (same path as an automatic barge-in), without depending on an HTTP route
    // the embedded runtime does not mount. The HTTP call stays as a fallback for
    // channels that are not open yet.
    if (sendRtvi({ type: "client-message", data: { t: VOICE_INTERRUPT_MESSAGE, d: null } })) {
      return true;
    }
    const remote = snapshot.sessionIdRemote;
    if (!remote) return false;
    try { await apiClient.post(`/voice/sessions/${remote}/interrupt`, {}); update({ state: "ready" }); return true; }
    catch (error) { update({ error: error instanceof Error ? error.message : "语音打断失败" }); return false; }
  },
  /**
   * Submits typed text as a real voice turn.
   *
   * The pipeline's `send-text` handler appends the message to the same LLM
   * context the ASR transcript feeds and runs the same turn, so typing while in
   * voice mode is not a second, parallel chat request — it is the same turn
   * semantics as speaking.
   */
  sendText(text: string): boolean {
    const content = text.trim();
    if (!content) return false;
    if (!sendRtvi({
      type: "send-text",
      data: { content, options: { run_immediately: true, audio_response: true } },
    })) {
      update({ error: "语音通道尚未就绪，请先连接语音导师。" });
      return false;
    }
    appendTranscriptItem("user", content);
    return true;
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
  async startTask(prompt: string, title = "") {
    const remote = snapshot.sessionIdRemote;
    if (!remote || !prompt.trim()) return null;
    try {
      const task = await apiClient.post<{ subagent_id: string; status: VoiceTaskEvent["status"]; title?: string }>(`/voice/sessions/${remote}/tasks`, { prompt: prompt.trim(), title, thinking_mode: snapshot.thinkingLimit });
      voiceSessionController.upsertTask({ taskId: task.subagent_id, status: task.status, title: task.title || title, updatedAt: new Date().toISOString() });
      return task;
    } catch (error) { update({ error: error instanceof Error ? error.message : "任务启动失败" }); return null; }
  },
  appendTranscript(item: VoiceTranscript) {
    // 同一条转录可能既被内部路径追加、又被页面监听器回灌（CustomEvent 会再
    // 次触发 append），按 id 去重避免气泡/字幕出现重复行。
    if (snapshot.transcript.some((existing) => existing.id === item.id)) return;
    update({ transcript: [...snapshot.transcript.slice(-49), item] });
  },
  upsertTask(event: VoiceTaskEvent) { update({ tasks: [...snapshot.tasks.filter((task) => task.taskId !== event.taskId), event].slice(-20) }); },
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
  return useMemo(() => ({ ...state, connect: controller.connect, disconnect: controller.disconnect, interrupt: controller.interrupt, setThinkingLimit: controller.setThinkingLimit, setMuted: controller.setMuted, appendTranscript: controller.appendTranscript, upsertTask: controller.upsertTask, sendText: controller.sendText, sendTurn: controller.sendTurn, startTask: controller.startTask }), [controller, state]);
}
