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
  update: Pick<VoiceRenderUpdate, "final" | "role" | "text" | "removes" | "retireTurnSegments">,
): boolean {
  if (update.role !== "user" && update.role !== "assistant") return false;
  if (update.text.trim().length > 0) return true;
  return (update.removes?.length ?? 0) > 0 || Boolean(update.retireTurnSegments);
}

/**
 * Whether one voice update means "this session now holds user content".
 *
 * A settled user turn -- `final`, and not one ASR *segment* of a turn -- is the
 * voice twin of the first text message: it is what graduates an empty draft into
 * an ordinary sidebar session. Without that, the session keeps being treated as
 * an unused draft (hidden from the sidebar, reused by 「新会话」), which is what
 * made a voice conversation look like it never left the previous session.
 *
 * The judgement is deliberately transport-side only: whether it *also* names the
 * session is decided from the persisted first user message (see the chat page).
 */
export function voiceRenderStartsSession(
  update: Pick<VoiceRenderUpdate, "final" | "role" | "voiceSegment">,
): boolean {
  return (
    update.role === "user" &&
    update.final === true &&
    update.voiceSegment !== true
  );
}
