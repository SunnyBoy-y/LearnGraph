import { useEffect, useState } from "react";

/**
 * Typewriter captions for a voice answer (2026-09-15).
 *
 * The whole answer is one flowing block -- breaking it into one paragraph per
 * sentence reads as a chopped-up answer rather than as speech -- and the part
 * being read is typed out inside it: sentences already heard are simply text,
 * and the sentence under the voice grows character by character with a caret.
 *
 * The contract is "what was read aloud is what appears": a sentence that has not
 * started playing is not rendered at all, and the tail of the current sentence
 * is not rendered ahead of the voice (no dimmed preview). The assistant's
 * on-screen text therefore never runs ahead of the audio, and the same rule
 * governs what reaches the database -- the backend stores exactly the sentences
 * whose playback started (see `VoiceTurnJournal._spoken_assistant_text`).
 *
 * The clock is the backend's own playback cursor. Every `voice-sentence-start`
 * marker carries `audio_cursor_ms` (the offset of that sentence's first audio
 * frame inside the reply's audio stream) and is emitted the moment that frame
 * enters the output queue, so `performance.now() - startedAt` measures real
 * playback time from the sentence's own first sample -- no separate transport
 * latency correction is needed. Characters then advance at a calibrated
 * per-character duration: the backend cursor of the *next* sentence is the
 * previous sentence's true audio duration, so `Δcursor / chars` keeps the
 * estimate honest as the call goes on (it starts at the Chinese TTS average of
 * ~4 characters/second).
 *
 * This is deliberately an estimate, not word-level truth: punctuation pauses
 * and tempo changes make it drift within a sentence, and a mid-sentence
 * interruption simply freezes the caret where the audio stopped, which is the
 * correct reading.
 */

export interface VoiceTypingTiming {
  /** Row id of the live voice bubble; the calibration key across sentences. */
  rowId: string;
  /** Offset (ms) of this sentence's first audio frame inside the reply audio. */
  cursorMs: number;
  /** `performance.now()` at which that frame entered the output queue. */
  startedAt: number;
  /**
   * Characters of the answer that precede this sentence.
   *
   * The whole answer is rendered as one flowing block (one line break per
   * sentence reads as a broken-up answer, not as speech), so the typewriter is
   * told where the sentence being read starts: everything before it is already
   * on screen and only the rest advances with the voice.
   */
  baseChars: number;
}

/** Chinese TTS reads at roughly four characters per second. */
const DEFAULT_CHAR_MS = 240;
const MIN_CHAR_MS = 40;
const MAX_CHAR_MS = 900;
const CALIBRATION_WEIGHT = 0.3;
const MAX_TRACKED_ROWS = 12;

let charMs = DEFAULT_CHAR_MS;
/** Last sentence seen per bubble: its cursor and length, for calibration. */
const rows = new Map<string, { cursorMs: number; chars: number }>();

/** Reads the timing payload `chat-pages` attaches to the streaming part. */
export function readVoiceTypingTiming(
  data?: Record<string, unknown> | null,
): VoiceTypingTiming | null {
  const raw = data?.voice_typing;
  if (!raw || typeof raw !== "object") return null;
  const record = raw as Record<string, unknown>;
  const rowId = typeof record.row_id === "string" ? record.row_id : "";
  const cursorMs = record.cursor_ms;
  const startedAt = record.started_at;
  const baseChars = record.base_chars ?? 0;
  // Strictly numbers: a missing or malformed anchor must fall back to the plain
  // renderer rather than typing on the wrong clock.
  if (!rowId) return null;
  if (typeof cursorMs !== "number" || !Number.isFinite(cursorMs)) return null;
  if (typeof startedAt !== "number" || !Number.isFinite(startedAt)) return null;
  if (typeof baseChars !== "number" || !Number.isFinite(baseChars)) return null;
  return { rowId, cursorMs, startedAt, baseChars: Math.max(0, baseChars) };
}

/**
 * Only plain spoken prose is typed out character by character.
 *
 * Typing needs the text in pieces, which would show raw markdown while a
 * sentence is still being read. Voice answers are conversational text, so
 * anything carrying markup falls back to the ordinary renderer -- the settled
 * bubble replaces it either way.
 */
export function isPlainSpokenSentence(text: string): boolean {
  return text.length > 0 && !/[`*_#|>[\]~]/.test(text);
}

function prefersReducedMotion(): boolean {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  try {
    return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch {
    return false;
  }
}

/** Folds one sentence's real duration back into the per-character estimate. */
function calibrate(rowId: string, cursorMs: number, chars: number): void {
  const previous = rows.get(rowId);
  if (previous && cursorMs > previous.cursorMs && previous.chars > 0) {
    const observed = (cursorMs - previous.cursorMs) / previous.chars;
    if (observed >= MIN_CHAR_MS && observed <= MAX_CHAR_MS) {
      charMs += (observed - charMs) * CALIBRATION_WEIGHT;
    }
  }
  rows.delete(rowId);
  rows.set(rowId, { cursorMs, chars });
  while (rows.size > MAX_TRACKED_ROWS) {
    const oldest = rows.keys().next();
    if (oldest.done) break;
    rows.delete(oldest.value);
  }
}

function estimateChars(timing: VoiceTypingTiming, total: number): number {
  const base = Math.max(0, Math.min(total, timing.baseChars));
  const remaining = total - base;
  if (remaining <= 0) return total;
  const elapsed = performance.now() - timing.startedAt;
  const typed = Math.max(0, Math.min(remaining, Math.floor(elapsed / charMs)));
  return base + typed;
}

/** Characters of `text` already heard, re-derived every frame. */
function useSpokenCharCount(
  text: string,
  timing: VoiceTypingTiming | null,
  active: boolean,
): number {
  const total = text.length;
  const [spoken, setSpoken] = useState(() =>
    timing && active ? estimateChars(timing, total) : total,
  );

  useEffect(() => {
    if (!timing) {
      setSpoken(total);
      return;
    }
    // Calibrate on this sentence's own length and real audio duration.
    calibrate(
      timing.rowId,
      timing.cursorMs,
      Math.max(1, total - Math.min(total, Math.max(0, timing.baseChars))),
    );
    if (!active || prefersReducedMotion()) {
      setSpoken(total);
      return;
    }
    let frame = 0;
    const tick = () => {
      const next = estimateChars(timing, total);
      setSpoken((current) => (current === next ? current : next));
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [active, total, timing?.rowId, timing?.cursorMs, timing?.startedAt, timing?.baseChars]);

  return spoken;
}

export function VoiceTypingText({
  text,
  timing,
  active,
}: {
  text: string;
  timing: VoiceTypingTiming | null;
  active: boolean;
}) {
  const spoken = useSpokenCharCount(text, timing, active);
  if (!active || !timing || spoken >= text.length) {
    return <div className="chat-voice-typing">{text}</div>;
  }
  return (
    <div className="chat-voice-typing">
      {text.slice(0, spoken)}
      <span aria-hidden="true" className="chat-voice-typing__caret" />
    </div>
  );
}
