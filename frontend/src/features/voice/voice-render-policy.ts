import type { VoiceRenderUpdate } from "./voice-session-controller";

/** Only authoritative voice turns may enter the durable chat message flow. */
export function shouldCommitVoiceRender(
  update: Pick<VoiceRenderUpdate, "final" | "role" | "text">,
): boolean {
  return (
    update.final === true &&
    (update.role === "user" || update.role === "assistant") &&
    update.text.trim().length > 0
  );
}
