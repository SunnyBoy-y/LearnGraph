import type { VoiceRenderUpdate } from "./voice-session-controller";
import type { VoiceBotOutputPart } from "./voice-bot-output";

type PlaybackEvent = {
  type?: string;
  turn_id?: string | null;
  event_id?: string;
  timestamp?: string | null;
  payload?: Record<string, unknown>;
};
type Turn = {
  parts: Map<number, VoiceBotOutputPart>;
  createdAt: string;
  terminal: boolean;
};

/** The media ledger is the only writer of the assistant's visible text.
 * Live events and DB replay share (turn_id, sentence_seq), including repeated
 * identical sentences. A stale event can never change a different turn.
 */
export class VoicePlaybackTranscript {
  private turns = new Map<string, Turn>();

  reset() { this.turns.clear(); }

  apply(event: PlaybackEvent): VoiceRenderUpdate | null {
    const turnId = event.turn_id;
    if (!turnId) return null;
    const payload = event.payload ?? {};
    const terminal = event.type === "turn.finalized" || event.type === "turn.interrupted";
    const starts = event.type === "assistant.sentence.queued";
    const ends = event.type === "assistant.sentence.ended";
    if (!terminal && !starts && !ends) return null;
    let turn = this.turns.get(turnId);
    if (!turn) {
      turn = { parts: new Map(), createdAt: event.timestamp || new Date().toISOString(), terminal: false };
      this.turns.set(turnId, turn);
      if (this.turns.size > 100) this.turns.delete(this.turns.keys().next().value!);
    }
    const sorted = () => [...turn.parts].sort(([a], [b]) => a - b).map(([, part]) => part);
    if (terminal) {
      const local = sorted().map((p) => p.text).join("");
      const interrupted = event.type === "turn.interrupted" || payload.interrupted === true || payload.outcome === "interrupted";
      const text = event.type === "turn.finalized"
        ? String(payload.text ?? "")
        : String(payload.heard_text || local);
      turn.terminal = true;
      return {
        id: `assistant-playback-${turnId}`, role: "assistant", turnId,
        text, final: true, authoritative: true, interrupted,
        createdAt: turn.createdAt, eventId: event.event_id,
        // Clean up rows created by earlier clients without touching another turn.
        removes: [`assistant-live-${turnId}`, `assistant-final-${turnId}`],
      };
    }
    if (turn.terminal) return null;
    const seq = Number(payload.sentence_seq);
    const text = String(payload.text ?? "");
    if (!Number.isInteger(seq) || seq < 1 || !text) return null;
    const matching = ends
      ? [...turn.parts.entries()].find(([, part]) => part.text === text)?.[0]
      : undefined;
    const effectiveSeq = matching ?? seq;
    const previous = turn.parts.get(effectiveSeq);
    if (previous && (previous.status === "completed" || starts)) return null;
    // The next audio start proves all preceding segments have drained, even
    // when an end event was missed. Future segments never exist in this map.
    for (const [index, part] of turn.parts) {
      if (index < effectiveSeq) turn.parts.set(index, { ...part, status: "completed", spokenChars: part.text.length });
    }
    turn.parts.set(effectiveSeq, {
      segmentId: effectiveSeq, text, spokenChars: ends ? text.length : 0,
      status: ends ? "completed" : "in-progress", willBeSpoken: true,
      playbackStarted: true, source: "ledger",
    });
    const parts = sorted();
    return {
      id: `assistant-playback-${turnId}`, role: "assistant", turnId,
      text: parts.map((p) => p.text).join(""), final: false,
      captionParts: parts, createdAt: turn.createdAt,
    };
  }
}
