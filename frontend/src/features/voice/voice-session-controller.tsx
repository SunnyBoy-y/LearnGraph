import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { apiClient, ApiError } from "@/api/client";

export type VoiceTransportState = "idle" | "connecting" | "connected" | "reconnecting" | "error";
export type VoiceSessionState = "closed" | "ready" | "listening" | "thinking" | "speaking" | "error";
export type ThinkingLimit = "off" | "low" | "medium" | "high" | "xhigh";
export type VoiceTranscriptRole = "user" | "assistant";
export interface VoiceTranscript { id: string; role: VoiceTranscriptRole; text: string; final: boolean; createdAt: string }
export interface VoiceTaskEvent { taskId: string; status: "queued" | "running" | "blocked" | "completed" | "failed" | "cancelled"; title?: string; summary?: string; progress?: number; updatedAt: string }
export interface VoiceSessionSnapshot {
  workspaceId: string; sessionId: string; transport: VoiceTransportState; state: VoiceSessionState;
  thinkingLimit: ThinkingLimit; transcript: VoiceTranscript[]; tasks: VoiceTaskEvent[]; error: string | null;
  sessionIdRemote: string | null;
}

const STORAGE_KEY = "learngraph.voice.preferences.v1";
const defaultSnapshot: VoiceSessionSnapshot = { workspaceId: "", sessionId: "", transport: "idle", state: "closed", thinkingLimit: "high", transcript: [], tasks: [], error: null, sessionIdRemote: null };
let snapshot: VoiceSessionSnapshot = { ...defaultSnapshot };
const sessionCache = new Map<string, VoiceSessionSnapshot>();
const listeners = new Set<() => void>();
let abortController: AbortController | null = null;

function emit() { for (const listener of listeners) listener(); }
function update(patch: Partial<VoiceSessionSnapshot>) { snapshot = { ...snapshot, ...patch }; emit(); }
function readLimit(): ThinkingLimit {
  try { const value = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null")?.thinkingLimit; return ["off", "low", "medium", "high", "xhigh"].includes(value) ? value : "high"; } catch { return "high"; }
}
function persistLimit(value: ThinkingLimit) { try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ thinkingLimit: value })); } catch { /* storage is optional */ } }

export const voiceSessionController = {
  getSnapshot: () => snapshot,
  subscribe(listener: () => void) { listeners.add(listener); return () => listeners.delete(listener); },
  open(workspaceId: string, sessionId: string) {
    const same = snapshot.workspaceId === workspaceId && snapshot.sessionId === sessionId;
    if (!same) {
      if (snapshot.sessionId) sessionCache.set(`${snapshot.workspaceId}:${snapshot.sessionId}`, snapshot);
      const cached = sessionCache.get(`${workspaceId}:${sessionId}`);
      update(cached ? { ...cached, state: cached.state === "closed" ? "ready" : cached.state } : { ...defaultSnapshot, workspaceId, sessionId, thinkingLimit: readLimit(), state: "ready" });
    }
    else if (snapshot.state === "closed") update({ state: "ready" });
  },
  setThinkingLimit(value: ThinkingLimit) { persistLimit(value); update({ thinkingLimit: value }); },
  async connect() {
    if (!snapshot.sessionId || snapshot.transport === "connecting" || snapshot.transport === "connected") return;
    abortController?.abort(); abortController = new AbortController();
    update({ transport: "connecting", state: "ready", error: null });
    try {
      // The endpoint is intentionally explicit: until the backend voice contract
      // is deployed, the UI reports the unavailable service instead of faking a call.
      const path = import.meta.env.VITE_VOICE_SESSION_PATH || "/voice/sessions";
      const result = await apiClient.post<{ id?: string; session_id?: string; sessionId?: string; runtime_ready?: boolean }>(path, { session_id: snapshot.sessionId, thinking_limit: snapshot.thinkingLimit }, { signal: abortController.signal });
      const remoteId = result?.id || result?.session_id || result?.sessionId || null;
      if (result?.runtime_ready !== true) {
        update({ transport: "error", state: "error", sessionIdRemote: remoteId, error: "语音会话已创建，但 SmallWebRTC 实时服务尚未部署。" });
        return;
      }
      update({ transport: "connected", state: "listening", sessionIdRemote: remoteId });
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      const message = error instanceof ApiError && error.status === 404 ? "语音服务尚未启用，请先部署 SmallWebRTC 语音服务。" : error instanceof Error ? error.message : "语音连接失败";
      update({ transport: "error", state: "error", error: message });
    }
  },
  disconnect() { abortController?.abort(); abortController = null; const next = { ...snapshot, transport: "idle" as const, state: "closed" as const, sessionIdRemote: null }; sessionCache.set(`${snapshot.workspaceId}:${snapshot.sessionId}`, next); update(next); },
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
export function useVoiceSession(workspaceId?: string, sessionId?: string) {
  const controller = useContext(VoiceSessionContext);
  const state = useSyncExternalStore(controller.subscribe, controller.getSnapshot, controller.getSnapshot);
  useEffect(() => { if (workspaceId && sessionId) controller.open(workspaceId, sessionId); }, [controller, sessionId, workspaceId]);
  return useMemo(() => ({ ...state, connect: controller.connect, disconnect: controller.disconnect, toggleListening: controller.toggleListening, setThinkingLimit: controller.setThinkingLimit, appendTranscript: controller.appendTranscript, upsertTask: controller.upsertTask, sendTurn: controller.sendTurn, startTask: controller.startTask }), [controller, state]);
}
