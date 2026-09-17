/**
 * 句级语音字幕（"卡拉OK"的句档）。
 *
 * 口径与后端一致（见 `voice-session-controller` 的 `VoiceSpokenSegment`）：
 *
 *  - **还没开始播放的句子不渲染**。后端把 `voice-sentence-start` 排期到这一句真正开始
 *    播放的时刻才下发，所以"没收到 = 没开始"是结构保证，前端不需要任何播放时钟，
 *    也不存在"字跑在声音前面"的可能。
 *  - **正在朗读的那一句用一点点灰度**（`is-reading`）。
 *  - **已播完的句子回归正常色**：播完由服务端的 `voice-sentence-end` 标记，它同样
 *    排期到这一句播完的时刻。
 *  - 被打断时保留已经出现过的句子（它们确实被听到过），只是不再有"正在读"的那一句。
 *
 * 不做逐字高亮、不承诺毫秒级对齐——句内进度不在契约里。
 */

export interface VoiceCaptionSegment {
  /** 该句原文（原样，含它自己的空格），逐句拼接即为整段回答。 */
  text: string;
  /** true = 这一句正在朗读（还没收到它的 `voice-sentence-end`）。 */
  reading: boolean;
}

export function VoiceSentenceCaptions({
  segments,
}: {
  segments: readonly VoiceCaptionSegment[];
}) {
  if (!segments.length) return null;
  return (
    <div className="chat-voice-captions">
      {segments.map((segment, index) => (
        <span
          className={
            segment.reading
              ? "chat-voice-captions__sentence is-reading"
              : "chat-voice-captions__sentence"
          }
          key={`${index}-${segment.text.length}`}
        >
          {segment.text}
        </span>
      ))}
    </div>
  );
}

/** 从消息 part 的 data 里读回字幕段（写入口在 `chat-pages` 的 voice render 监听里）。 */
export function readVoiceCaptionSegments(
  data?: Record<string, unknown> | null,
): VoiceCaptionSegment[] {
  const raw = data?.voice_captions;
  if (!raw || typeof raw !== "object") return [];
  const list = (raw as { segments?: unknown }).segments;
  if (!Array.isArray(list)) return [];
  const segments: VoiceCaptionSegment[] = [];
  for (const item of list) {
    if (!item || typeof item !== "object") continue;
    const record = item as { text?: unknown; reading?: unknown };
    if (typeof record.text !== "string") continue;
    segments.push({ text: record.text, reading: record.reading === true });
  }
  return segments;
}
