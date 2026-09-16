import type { Message } from "@/types/sessions";
import type { UnknownRecord } from "@/types/common";

/**
 * Voice row ownership on the conversation canvas.
 *
 * One user turn must end up as exactly one bubble. While the turn is open the
 * call shows one row per ASR segment (the pauses are visible, and that is on
 * purpose); those rows are *fragments*, and the single authoritative row of the
 * turn supersedes them. Keeping that rule in one pure function is what stops the
 * two failure shapes this module exists for:
 *
 *  - a fragment surviving next to the settled bubble (nothing in the canvas can
 *    take a row back off on its own, so the retirement has to be stated);
 *  - the retirement relying on a *list of ids the client happens to remember*.
 *    Any `user.final` re-delivered after a flush (the durable push channel and
 *    the HTTP event catch-up both feed the same handler) allocates a row key no
 *    retire list ever saw, so the update states the *turn* instead and the
 *    canvas resolves ownership from the row's own trace.
 */
export interface VoiceRenderRetirement {
  /** Row ids to retire (a snapshot of what this client remembers). */
  removes?: readonly string[] | null;
  /** Turn whose per-segment rows must go, whoever created them. */
  retireTurnSegments?: string | null;
}

/** True when this row is an ephemeral per-segment fragment of a user turn. */
export function isVoiceSegmentRow(row: Pick<Message, "provider_trace">): boolean {
  return row.provider_trace?.voice_segment === true;
}

/** True when this row must leave the canvas for the given update. */
export function voiceRowRetired(
  row: Pick<Message, "id" | "provider_trace">,
  retirement: VoiceRenderRetirement,
): boolean {
  const removals = retirement.removes ?? [];
  if (removals.some((retired) => row.id === `temp-voice-${retired}`)) return true;
  const turnId = String(retirement.retireTurnSegments ?? "");
  if (!turnId) return false;
  return isVoiceSegmentRow(row) && row.provider_trace?.turn_id === turnId;
}

/** Apply a voice render's retirement to the canvas rows. */
export function retireVoiceRows(
  rows: readonly Message[],
  retirement: VoiceRenderRetirement,
): Message[] {
  const removals = retirement.removes ?? [];
  if (!removals.length && !retirement.retireTurnSegments) return [...rows];
  return rows.filter((row) => !voiceRowRetired(row, retirement));
}

/**
 * Fold a voice render's trace into an existing row's trace without erasing it.
 *
 * The authoritative row of a typed turn carries `client_message_id`, while the
 * update that re-renders it (a merged `user.final`, a `turn.finalized`) may not;
 * the durable-twin lookup needs that key to survive a refetch, otherwise the
 * persisted message paints a second bubble next to the voice row. Only defined
 * values overwrite.
 */
export function mergeVoiceTrace(
  current: UnknownRecord | null | undefined,
  incoming: UnknownRecord,
): UnknownRecord {
  const merged: UnknownRecord = { ...(current ?? {}) };
  for (const [key, value] of Object.entries(incoming)) {
    if (value !== undefined) merged[key] = value;
  }
  return merged;
}
