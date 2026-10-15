import { createUuid } from "@/lib/uuid";
import type { VoiceRenderUpdate } from "./voice-session-controller";

type Row = { id: string; turnId?: string; clientMessageId?: string; pendingText?: string; confirmed: string; createdAt: string };

/** ASR hypotheses replace the current segment; only final segments accumulate.
 * Each backend turn has one row. The common history projection combines
 * consecutive user rows across turns until a visible assistant answer exists.
 */
export class VoiceUserTranscript {
  private rows = new Map<string, Row>();
  private typed = new Map<string, Row>();
  private current: Row | null = null;

  reset() { this.rows.clear(); this.typed.clear(); this.current = null; }

  startTyped(clientMessageId: string, text: string): VoiceRenderUpdate {
    let row = this.typed.get(clientMessageId);
    if (!row) {
      row = { id: `user-typed-${clientMessageId}`, clientMessageId, pendingText: text, confirmed: "", createdAt: new Date().toISOString() };
      this.typed.set(clientMessageId, row);
    }
    return { ...this.render(row, text, false), pending: true, deliveryStatus: "pending" };
  }

  get(turnId: string): VoiceRenderUpdate | null {
    const row = this.rows.get(turnId);
    return row ? this.render(row, row.confirmed, true) : null;
  }

  answerStarted() { /* A final may still correct an existing hypothesis. */ }

  interim(text: string, _turnId?: string): VoiceRenderUpdate | null {
    if (!text.trim()) return null;
    if (!this.current) {
      this.current = {
        id: `user-draft-${createUuid()}`, confirmed: "", createdAt: new Date().toISOString(),
      };
    }
    return this.render(this.current, text, false);
  }

  final(turnId: string, text: string, clientMessageId?: string): VoiceRenderUpdate | null {
    if (!turnId || !text.trim()) return null;
    const previous = this.rows.get(turnId);
    let row = (clientMessageId ? this.typed.get(clientMessageId) : undefined) ?? previous;
    if (!row && !clientMessageId) {
      row = [...this.typed.values()].find((candidate) => candidate.pendingText === text);
    }
    const isTyped = Boolean(clientMessageId || row?.clientMessageId);
    const liveId = !isTyped ? this.current?.id : undefined;
    if (!row) {
      row = !isTyped && this.current && !this.current.turnId ? this.current : {
        id: clientMessageId ? `user-typed-${clientMessageId}` : `user-${turnId}`,
        confirmed: "", createdAt: new Date().toISOString(),
      };
    }
    row.turnId = turnId;
    row.clientMessageId = clientMessageId || row.clientMessageId;
    this.rows.set(turnId, row);
    if (row.clientMessageId) this.typed.set(row.clientMessageId, row);
    if (this.rows.size > 100) this.rows.delete(this.rows.keys().next().value!);
    if (this.typed.size > 100) this.typed.delete(this.typed.keys().next().value!);
    // payload.text is the authoritative *whole turn*, never a delta.
    row.confirmed = text;
    if (!isTyped) this.current = null;
    return {
      ...this.render(row, text, true), authoritative: true,
      removes: [liveId, previous?.id].filter((id): id is string => Boolean(id && id !== row.id)),
    };
  }

  private render(row: Row, text: string, final: boolean): VoiceRenderUpdate {
    return { id: row.id, role: "user", text, final, turnId: row.turnId, clientMessageId: row.clientMessageId,
      createdAt: row.createdAt, pending: false };
  }
}
