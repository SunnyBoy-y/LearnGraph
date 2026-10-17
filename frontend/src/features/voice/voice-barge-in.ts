/** Experimental, reversible playback suppression. ASR final still owns turns. */
export interface VoiceSpeechEvidence { probability: number; rms: number; durationMs: number }
export class VoiceBargeIn {
  private onsetMs = 0;
  private lastSpeechAt = -Infinity;
  private mutedAt: number | null = null;
  private confirmedAt: number | null = null;
  private lastFrameAt = -Infinity;
  private lastTextAt = -Infinity;
  private readonly apply: (muted: boolean, reason: string) => void;
  constructor(apply: (muted: boolean, reason: string) => void) { this.apply=apply; }
  get muted() { return this.mutedAt !== null; }
  frame(evidence: VoiceSpeechEvidence, now: number, botActive: boolean) {
    const gap = now - this.lastFrameAt;
    this.lastFrameAt = now;
    if (gap > 100) this.onsetMs = 0;
    const voiced = Number.isFinite(evidence.probability) && evidence.probability >= 0.65
      && Number.isFinite(evidence.rms) && evidence.rms >= 0.0015;
    if (voiced) {
      this.lastSpeechAt = now;
      this.onsetMs += Math.max(0, Math.min(20, evidence.durationMs));
    } else this.onsetMs = 0;
    if (botActive && !this.muted && this.onsetMs >= 60) {
      this.mutedAt = now; this.confirmedAt = null;
      this.apply(true, 'local-speech');
    }
    this.tick(now);
  }
  confirm(now: number) { if (this.muted) this.confirmedAt = now; }
  transcription(text:string, now:number) {
    if(this.muted && text.replace(/[\s\p{P}]/gu,'').length>=2)this.lastTextAt=now;
  }
  botStarted(now: number) {
    // A replacement reply may start before an old tail has drained. Wait for
    // actual silence from the user before releasing a confirmed candidate.
    if (this.confirmedAt !== null && now - this.lastSpeechAt > 200) this.reset('replacement-reply');
  }
  tick(now: number) {
    if (!this.muted) return;
    if (this.confirmedAt === null && now - this.lastSpeechAt > 900 && now - this.lastTextAt > 1500) this.reset('unconfirmed-resume');
    else if (this.confirmedAt !== null && now - this.confirmedAt > 2500 && now - this.lastSpeechAt > 900) this.reset('confirmed-tail-drained');
    else if (now - this.lastFrameAt > 1500) this.reset('detector-unavailable');
  }
  reset(reason = 'reset') {
    const wasMuted=this.muted;
    this.mutedAt = this.confirmedAt = null; this.onsetMs = 0;
    this.lastTextAt=-Infinity;
    if(wasMuted)this.apply(false,reason);
  }
}

export function fastBargeInEnabled() {
  try { return localStorage.getItem('learngraph.voice.fastBargeIn') === '1'; }
  catch { return false; }
}
