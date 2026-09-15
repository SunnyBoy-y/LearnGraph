import type { VoiceRenderUpdate } from "./voice-session-controller";

/**
 * Which voice updates may enter the conversation canvas.
 *
 * Interim speech and sentence-by-sentence assistant text must reach the canvas
 * live -- that is the whole point of the transcript being on screen -- so
 * `final` is no longer the gate it once was. What still has to be rejected is
 * an update that would create an empty bubble, and anything that is not a
 * conversational role (task activity stays in the chip).
 *
 * A removal-only update is always allowed: it retires the per-segment rows of a
 * settled turn and carries no text of its own.
 */
export function shouldCommitVoiceRender(
  update: Pick<VoiceRenderUpdate, "final" | "role" | "text" | "removes">,
): boolean {
  if (update.role !== "user" && update.role !== "assistant") return false;
  if (update.text.trim().length > 0) return true;
  return (update.removes?.length ?? 0) > 0;
}
