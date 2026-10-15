import { describe, expect, it } from 'vitest';
import { VoiceUserTranscript } from './voice-user-transcript';
describe('typed voice row identity', () => {
  it('reuses the pending client row when the server turn id arrives later', () => {
    const transcript = new VoiceUserTranscript();
    const pending = transcript.startTyped('cm-1', '请回答 2+2');
    const final = transcript.final('turn-1', '请回答 2+2', 'cm-1');
    expect(final?.id).toBe(pending.id);
    expect(final?.turnId).toBe('turn-1');
    expect(final?.clientMessageId).toBe('cm-1');
  });
  it('matches a typed pending row when a user final arrives without the client id', () => {
    const transcript = new VoiceUserTranscript();
    const pending = transcript.startTyped('cm-2', '同一条问题');
    const final = transcript.final('turn-2', '同一条问题');
    expect(final?.id).toBe(pending.id);
    expect(final?.turnId).toBe('turn-2');
  });
});
