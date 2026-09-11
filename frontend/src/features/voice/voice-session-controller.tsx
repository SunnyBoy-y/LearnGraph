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
export interface VoiceTranscript { id: string; role: VoiceTranscriptRole; text: string; final: boolean; createdAt: string }
export interface VoiceTaskEvent { taskId: string; status: "queued" | "running" | "blocked" | "completed" | "failed" | "cancelled"; title?: string; summary?: string; progress?: number; updatedAt: string }
/** 与 Pipecat `SmallWebRTCPatchRequest.candidates[].IceCandidate` 一一对应（snake_case）。 */
export interface VoiceIceCandidate { candidate: string; sdp_mid: string; sdp_mline_index: number }
export interface VoiceSessionSnapshot {
  workspaceId: string; sessionId: string; transport: VoiceTransportState; state: VoiceSessionState;
  thinkingLimit: ThinkingLimit; transcript: VoiceTranscript[]; tasks: VoiceTaskEvent[]; error: string | null;
  modelId: string | null; providerId: string | null;
  sessionIdRemote: string | null;
  signalingUrl: string | null;
}

const STORAGE_KEY = "learngraph.voice.preferences.v1";
const defaultSnapshot: VoiceSessionSnapshot = { workspaceId: "", sessionId: "", transport: "idle", state: "closed", thinkingLimit: "high", transcript: [], tasks: [], error: null, modelId: null, providerId: null, sessionIdRemote: null, signalingUrl: null };
let snapshot: VoiceSessionSnapshot = { ...defaultSnapshot };
const sessionCache = new Map<string, VoiceSessionSnapshot>();
const listeners = new Set<() => void>();
let abortController: AbortController | null = null;
let peerConnection: RTCPeerConnection | null = null;
let localStream: MediaStream | null = null;

function emit() { for (const listener of listeners) listener(); }
function update(patch: Partial<VoiceSessionSnapshot>) { snapshot = { ...snapshot, ...patch }; emit(); }
function readLimit(): ThinkingLimit {
  try { const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null")?.thinkingLimit; return ["off", "low", "medium", "high", "xhigh"].includes(value) ? value : "high"; } catch { return "high"; }
}
function persistLimit(value: ThinkingLimit) { try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ thinkingLimit: value })); } catch { /* storage is optional */ } }
function cleanupAudioTransport() {
  if (peerConnection) peerConnection.onicecandidate = null;
  localStream?.getTracks().forEach((track) => track.stop());
  localStream = null;
  peerConnection?.close();
  peerConnection = null;
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
  async connect() {
    if (!snapshot.sessionId || snapshot.transport === "connecting" || snapshot.transport === "connected") return;
    abortController?.abort(); abortController = new AbortController();
    update({ transport: "connecting", state: "ready", error: null });
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
      peerConnection.ontrack = (event) => {
        const audio = new Audio();
        audio.autoplay = true;
        audio.srcObject = event.streams[0];
        void audio.play().catch(() => undefined);
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
    } catch (error) {
      cleanupAudioTransport();
      if (remoteId) void apiClient.delete(`/voice/sessions/${remoteId}`).catch(() => undefined);
      if (error instanceof DOMException && error.name === "AbortError") return;
      const message = error instanceof ApiError && error.status === 404 ? "语音服务尚未启用，请先部署 SmallWebRTC 语音服务。" : error instanceof Error ? error.message : "语音连接失败";
      update({ transport: "error", state: "error", error: message });
    }
  },
  disconnect() { const remote = snapshot.sessionIdRemote; abortController?.abort(); abortController = null; cleanupAudioTransport(); if (remote) void apiClient.delete(`/voice/sessions/${remote}`).catch(() => undefined); setVoiceSessionActive(snapshot.workspaceId, snapshot.sessionId, false); const next = { ...snapshot, transport: "idle" as const, state: "closed" as const, sessionIdRemote: null, signalingUrl: null }; sessionCache.set(`${snapshot.workspaceId}:${snapshot.sessionId}`, next); update(next); },
  async interrupt() {
    const remote = snapshot.sessionIdRemote;
    if (!remote) return false;
    try { await apiClient.post(`/voice/sessions/${remote}/interrupt`, {}); update({ state: "ready" }); return true; }
    catch (error) { update({ error: error instanceof Error ? error.message : "语音打断失败" }); return false; }
  },
  toggleListening() {
    if (snapshot.transport !== "connected") return;
    update({ state: snapshot.state === "listening" ? "ready" : "listening" });
  },
  async sendTurn(text: string, role: VoiceTranscriptRole = "user") {
    const remote = snapshot.sessionIdRemote;
    if (!remote || !text.trim()) return false;
    const item: VoiceTranscript = { id: crypto.randomUUID(), role, text: text.trim(), final: true, createdAt: new Date().toISOString() };
    try {
      await apiClient.post(`/voice/sessions/${remote}/turns`, { role, text: item.text, final: true, thinking_mode: snapshot.thinkingLimit });
      voiceSessionController.appendTranscript(item);
      return true;
    } catch (error) { update({ error: error instanceof Error ? error.message : "语音回合提交失败" }); return false; }
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
  appendTranscript(item: VoiceTranscript) { update({ transcript: [...snapshot.transcript.slice(-49), item] }); },
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
  return useMemo(() => ({ ...state, connect: controller.connect, disconnect: controller.disconnect, interrupt: controller.interrupt, toggleListening: controller.toggleListening, setThinkingLimit: controller.setThinkingLimit, appendTranscript: controller.appendTranscript, upsertTask: controller.upsertTask, sendTurn: controller.sendTurn, startTask: controller.startTask }), [controller, state]);
}
