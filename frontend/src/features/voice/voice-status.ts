import type {
  VoiceModelPin,
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

/**
 * The model-pin clause of the voice status line, or "" when no switch is in play.
 *
 * A pin only exists between "the user asked for a different model" and "the
 * running pipeline confirmed it", and the two states must never be worded as
 * each other. A request whose target cannot be named is described as a request
 * rather than with a placeholder model name: a literal 「新模型」 reads to the
 * user as a model that is actually called that (it was rendered on every call
 * creation while a context snapshot was mistaken for a switch).
 */
export function voiceModelPinText(pin: VoiceModelPin | null): string {
  if (!pin) return "";
  if (pin.repointed) {
    const live = pin.effectiveModelId ?? pin.requestedModelId;
    return live ? ` · 已切换到「${live}」，下一轮生效` : " · 已切换模型，下一轮生效";
  }
  const pending = pin.requestedModelId
    ? `，「${pin.requestedModelId}」将在下次接通后生效`
    : "，将在下次接通后生效";
  return ` · 本次通话仍在使用「${pin.effectiveModelId ?? "当前模型"}」${pending}`;
}
