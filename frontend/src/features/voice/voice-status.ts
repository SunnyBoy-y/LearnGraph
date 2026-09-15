import type {
  VoiceSessionState,
  VoiceTransportState,
} from "./voice-session-controller";

/**
 * The one human-readable sentence describing what a live call is doing.
 *
 * Kept out of `voice-orb.tsx` so that file stays component-only (fast refresh)
 * and so the same wording is used everywhere the call state is shown — the
 * composer button and the status line above it must never disagree.
 */
export function voiceStatusText(
  transport: VoiceTransportState,
  state: VoiceSessionState,
  error?: string | null,
): string {
  if (transport === "connecting") return "正在接通语音导师…";
  if (transport === "reconnecting") return "语音连接中断，正在重连…";
  if (transport === "error") return error || "语音连接失败";
  if (state === "speaking") return "导师正在回答，你随时可以插话";
  if (state === "thinking") return "导师正在思考，你随时可以插话";
  if (state === "listening") return "正在聆听，请直接说话";
  if (transport === "connected") return "语音导师已连接，请直接说话";
  return "语音导师未连接";
}
