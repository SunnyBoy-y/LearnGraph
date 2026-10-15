import { describe, expect, it } from 'vitest';
import { VoicePlaybackTranscript } from './voice-playback';

describe('playback captions with repeated sentence text', () => {
  it('uses sentence_seq when two adjacent sentences are identical', () => {
    const playback = new VoicePlaybackTranscript();
    const queued1 = playback.apply({ type: 'assistant.sentence.queued', turn_id: 't', payload: { sentence_seq: 1, text: '好的。' } });
    const queued2 = playback.apply({ type: 'assistant.sentence.queued', turn_id: 't', payload: { sentence_seq: 2, text: '好的。' } });
    const ended2 = playback.apply({ type: 'assistant.sentence.ended', turn_id: 't', payload: { sentence_seq: 2, text: '好的。' } });
    expect(queued1?.captionParts).toHaveLength(1);
    expect(queued2?.captionParts).toHaveLength(2);
    expect(ended2?.captionParts?.map((part) => part.status)).toEqual(['completed', 'completed']);
  });
});

describe('terminal playback text', () => {
  it('respects an explicit empty heard_text instead of restoring queued text', () => {
    const playback = new VoicePlaybackTranscript();
    playback.apply({
      type: 'assistant.sentence.queued',
      turn_id: 't-empty',
      payload: { sentence_seq: 1, text: '这句从未被确认听到。' },
    });

    const terminal = playback.apply({
      type: 'turn.interrupted',
      turn_id: 't-empty',
      payload: { heard_text: '' },
    });

    expect(terminal?.final).toBe(true);
    expect(terminal?.interrupted).toBe(true);
    expect(terminal?.text).toBe('');
  });

  it('uses only the heard prefix supplied by the terminal event', () => {
    const playback = new VoicePlaybackTranscript();
    playback.apply({
      type: 'assistant.sentence.queued',
      turn_id: 't-prefix',
      payload: { sentence_seq: 1, text: '已经听到，后半句没有听到。' },
    });

    const terminal = playback.apply({
      type: 'turn.interrupted',
      turn_id: 't-prefix',
      payload: { heard_text: '已经听到' },
    });

    expect(terminal?.text).toBe('已经听到');
  });
});
