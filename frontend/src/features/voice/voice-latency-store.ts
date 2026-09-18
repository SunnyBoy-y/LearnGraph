/**
 * 语音延迟快照的模块级 store（与 `voice-session-controller` 同构，但独立）。
 *
 * 为什么要独立一份而不是塞进 `VoiceSessionSnapshot`：延迟是**纯调试**数据，
 * 每来一条 RTVI 报文都会变；混进会话快照会让 `useVoiceSession()` 的每个消费者
 * （字幕、气泡、球）跟着重渲染。独立 store 意味着只有打开面板的那一个组件订阅它。
 *
 * 面板默认关闭（`voice.debug_panel` 设置项），关掉时这份数据照常记录但不渲染 —— 
 * 记录的开销是几个数字，把开关做成"打开后才有数据"反而会让面板一开就是空的。
 */
import { useSyncExternalStore } from "react";

import {
  emptyVoiceLatencyState,
  reduceVoiceLatency,
  type VoiceLatencyMark,
  type VoiceLatencyState,
} from "./voice-latency";

let state: VoiceLatencyState = emptyVoiceLatencyState();
const listeners = new Set<() => void>();

function emit() {
  for (const listener of listeners) listener();
}

function publish(next: VoiceLatencyState) {
  if (next === state) return;
  state = next;
  emit();
}

/** 全链路只用这一个时钟，避免 `Date.now()` 与 `performance.now()` 混算。 */
export function voiceLatencyNow(): number {
  return typeof performance !== "undefined" && typeof performance.now === "function"
    ? performance.now()
    : Date.now();
}

export function getVoiceLatencySnapshot(): VoiceLatencyState {
  return state;
}

export function subscribeVoiceLatency(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * 记一个环节时间点。参数里的 `at` 缺省即此刻。
 *
 * 没有任何旁路信号能在这里开轮：能不能开轮、能不能收尾，全部由
 * `voice-latency.ts` 的归轮不变量决定（只有用户侧人声信号能开轮）。
 */
export function markVoiceLatency(
  mark: Omit<VoiceLatencyMark, "at"> & { at?: number },
): void {
  publish(reduceVoiceLatency(state, { ...mark, at: mark.at ?? voiceLatencyNow() }));
}

export function resetVoiceLatency(): void {
  state = emptyVoiceLatencyState();
  emit();
}

/** 面板订阅入口（不依赖 `VoiceSessionProvider`，与 chat-pages 里的裸用法一致）。 */
export function useVoiceLatency(): VoiceLatencyState {
  return useSyncExternalStore(
    subscribeVoiceLatency,
    getVoiceLatencySnapshot,
    getVoiceLatencySnapshot,
  );
}
