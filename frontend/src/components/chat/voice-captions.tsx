/**
 * 语音字幕渲染器：消费**官方** `bot-output` 折出来的段落（见 `features/voice/voice-bot-output`）。
 *
 * 三种模式（默认 Karaoke，与官方预构建客户端一致）：
 *
 *  - `karaoke`：整句都在，已读正常色、未读灰；
 *  - `captions`：只显示已读（未读整段丢弃）；
 *  - `instant`：全文一次性显示、不分色。
 *
 * 语义要点：灰着的句子是"已经拿到文本、还没被朗读"——它**只存在于前端内存**，不进账本、
 * 不进记忆；刷新或被打断后随段落列表一起消失（后端只认真的播过的句子，见
 * `app/voice/caption_ledger.py`）。
 */

import type {
  VoiceBotOutputPart,
  VoiceCaptionMode,
} from "@/features/voice/voice-bot-output";
import { splitPart } from "@/features/voice/voice-bot-output";

export interface VoiceCaptionsProps {
  parts: readonly VoiceBotOutputPart[];
  mode?: VoiceCaptionMode;
}

export function VoiceCaptions({ parts, mode = "karaoke" }: VoiceCaptionsProps) {
  if (!parts.length) return null;
  return (
    <div className="chat-voice-captions">
      {parts.map((part, index) => {
        const { spoken, unspoken } = splitPart(part, mode);
        return (
          <span
            className="chat-voice-captions__sentence"
            // 段落身份优先用官方 segment_id（同一句的 new/completed id 可能不同，所以
            // 兜底加序号，避免同 id 段复用同一个 React key）。
            key={`${part.segmentId ?? "part"}-${index}`}
          >
            {spoken}
            {unspoken && mode === "karaoke" ? (
              // 只有 Karaoke 模式给未读上灰：`captions` 已经把它清空，`instant` 则按
              // 官方语义全文同色（见 `splitPart`）。
              <span className="chat-voice-captions__unread">{unspoken}</span>
            ) : (
              unspoken
            )}
          </span>
        );
      })}
    </div>
  );
}

/** 从消息 part 的 data 里读回字幕段（写入口在 `chat-pages` 的 voice render 监听里）。 */
export function readVoiceCaptionParts(
  data?: Record<string, unknown> | null,
): VoiceBotOutputPart[] {
  const raw = data?.voice_captions;
  if (!raw || typeof raw !== "object") return [];
  const list = (raw as { parts?: unknown }).parts;
  if (!Array.isArray(list)) return [];
  const parts: VoiceBotOutputPart[] = [];
  for (const item of list) {
    if (!item || typeof item !== "object") continue;
    const record = item as Partial<VoiceBotOutputPart>;
    if (typeof record.text !== "string") continue;
    const status =
      record.status === "in-progress" || record.status === "completed"
        ? record.status
        : "new";
    parts.push({
      text: record.text,
      segmentId: typeof record.segmentId === "number" ? record.segmentId : null,
      spokenChars: typeof record.spokenChars === "number" ? record.spokenChars : 0,
      status,
      willBeSpoken: record.willBeSpoken !== false,
    });
  }
  return parts;
}
