import { createUuid } from "@/lib/uuid";
import type { VoiceRenderUpdate } from "./voice-session-controller";

type Row = { id: string; turnId?: string; confirmed: string; createdAt: string };

/** ASR hypotheses replace the current segment; only final segments accumulate.
 * Each backend turn has one row. The common history projection combines
 * consecutive user rows across turns until a visible assistant answer exists.
 */
export class VoiceUserTranscript {
  private rows = new Map<string, Row>();
  private current: Row | null = null;

  reset() { this.rows.clear(); this.current = null; }

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
    let row = this.rows.get(turnId);
    if (row?.confirmed === text) return null;
    const liveId = this.current?.id;
    if (!row) {
      row = this.current && !this.current.turnId ? this.current : {
        id: clientMessageId ? `user-typed-${clientMessageId}` : `user-${turnId}`,
        confirmed: "", createdAt: new Date().toISOString(),
      };
      row.turnId = turnId;
      this.rows.set(turnId, row);
      if (this.rows.size > 100) this.rows.delete(this.rows.keys().next().value!);
    }
    // payload.text is the authoritative *whole turn*, never a delta.
    row.confirmed = text;
    this.current = null;
    return { ...this.render(row, text, true), removes: liveId && liveId !== row.id ? [liveId] : [], clientMessageId, authoritative: true };
  }

  private render(row: Row, text: string, final: boolean): VoiceRenderUpdate {
    return { id: row.id, role: "user", text, final, turnId: row.turnId,
      createdAt: row.createdAt, pending: false };
  }
}
