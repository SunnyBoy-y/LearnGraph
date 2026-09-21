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
 * 本地语音回合里，导师那一行的"父行"是谁。
 *
 * 语音回合的两行都带同一把客户端回合键（`turn_id`），打字回合的用户行则按幂等键
 * 命名（`user-typed-<client_message_id>`，见 `userEntryId`）。把导师行挂到同一回合
 * 的用户行上，画布就能顺着"持久化用户行 → 持久化回答行的 `parent_message_id`"这条
 * 链找到它的孪生行 —— 与打字回合的乐观行走的是同一条规则。
 *
 * 为什么不能只认回合 id：服务端的回合 id 是**worker**开出来的，客户端只有在
 * `user.final` / `turn.accepted` 送到时才知道它；这两个事件丢过（或 worker 先开
 * 回合、id 与打字幂等键不同）时，本地行的键与服务端对不上，孪生行就永远配不上，
 * 屏幕上的结果就是同一条回答两份。
 */
export function voiceTurnParentRowId(
  rows: readonly Message[],
  identity: { turnId?: string | null; clientMessageId?: string | null },
): string | null {
  const clientMessageId = String(identity.clientMessageId ?? "");
  if (clientMessageId) {
    const typed = rows.find(
      (row) => row.role === "user" && row.id === `temp-voice-user-typed-${clientMessageId}`,
    );
    if (typed) return typed.id;
  }
  const turnId = String(identity.turnId ?? "");
  if (!turnId) return null;
  const sameTurn = rows.filter(
    (row) =>
      row.role === "user" &&
      String(row.provider_trace?.turn_id ?? "") === turnId,
  );
  // 一个回合可能有多个用户行：逐段的临时行与合成后的权威行。权威行才是持久化
  // 那一条的孪生（逐段行按内容永远配不上合并后的问题）。
  const settled = sameTurn.find((row) => !isVoiceSegmentRow(row));
  return (settled ?? sameTurn[sameTurn.length - 1])?.id ?? null;
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

function isVoice(row: Pick<Message, "provider_trace">): boolean {
  return row.provider_trace?.voice === true || row.provider_trace?.voice_turn === true;
}

export function voiceActionsVisible(row: Pick<Message, "provider_trace" | "status">): boolean {
  return !isVoice(row) || ["completed", "failed", "cancelled", "interrupted"].includes(row.status);
}

/** Shared projection for live rows, refetched history and calls that ended.
 * Backend turns remain separate. Adjacent voice user turns are one visible
 * utterance until a nonempty assistant message separates them. Keep the first
 * row/part identity while appending, so React doesn't remount on every pause.
 */
export function mergeAdjacentVoiceUserMessages(rows: readonly Message[]): Message[] {
  const result: Message[] = [];
  for (const row of rows) {
    if (isVoice(row) && row.role === "assistant" && !row.content?.trim() &&
        !row.parts?.some((p) => p.content?.trim())) continue;
    const previous = result[result.length - 1];
    if (previous?.role !== "user" || row.role !== "user" ||
        !isVoice(previous) || !isVoice(row) || previous.session_id !== row.session_id) {
      result.push(row);
      continue;
    }
    const a = previous.content ?? "";
    const b = row.content ?? "";
    const text = a + (/[A-Za-z0-9]$/.test(a) && /^[A-Za-z0-9]/.test(b) ? " " : "") + b;
    result[result.length - 1] = {
      ...previous, content: text, status: row.status,
      parts: [{
        id: previous.parts[0]?.id ?? `voice-part-${previous.id}`,
          type: "text", content: text, status: row.status as Message["status"], sequence: 0,
        data: { kind: "final_answer" },
      }],
    };
  }
  return result;
}
