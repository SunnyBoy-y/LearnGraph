import { describe, expect, it } from 'vitest';
import { ingestVoiceRtviMessage, type VoiceRenderUpdate } from './voice-session-controller';
import { applyVoiceCanvasUpdate } from './voice-canvas';
import type { Message } from '@/types/sessions';

describe('unplayed voice drafts stay off the canvas', () => {
  it('ignores every assistant.llm.delta until playback authorizes text', () => {
    let messages: Message[] = [];
    let seq = 0;
    const updates: VoiceRenderUpdate[] = [];
    const listener = (event: Event) => {
      const item = (event as CustomEvent<VoiceRenderUpdate>).detail;
      updates.push(item);
      messages = applyVoiceCanvasUpdate(messages, item, 'test-workspace', 'test-session');
    };
    window.addEventListener('learngraph:voice-render', listener);
    try {
      const turnId = 'draft-regression';
      const send = (text: string) => ingestVoiceRtviMessage(JSON.stringify({
        label: 'rtvi-ai',
        type: 'server-message',
        data: {
          type: 'voice-event',
          event: {
            event_id: `draft-regression-event-${++seq}`,
            event_seq: seq,
            turn_id: turnId,
            type: 'assistant.llm.delta',
            payload: { text },
          },
        },
      }));

      send('你好呀！');
      send('有什么我可以帮你的吗？');
      send('这段文字从未进入播放。');

      expect(updates).toHaveLength(0);
      expect(messages).toHaveLength(0);
    } finally {
      window.removeEventListener('learngraph:voice-render', listener);
    }
  });

  it('ignores raw text from the RTVI observer channel', () => {
    const updates: VoiceRenderUpdate[] = [];
    const listener = (event: Event) => updates.push((event as CustomEvent<VoiceRenderUpdate>).detail);
    window.addEventListener('learngraph:voice-render', listener);
    try {
      ingestVoiceRtviMessage(JSON.stringify({
        label: 'rtvi-ai',
        type: 'bot-llm-text',
        data: { text: '观察通道的未播放回答' },
      }));
      expect(updates).toHaveLength(0);
    } finally {
      window.removeEventListener('learngraph:voice-render', listener);
    }
  });
});
