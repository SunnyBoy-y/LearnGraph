/**
 * 语音字幕的数据源：**官方** RTVI `bot-output`（协议 2.x 的句级路径）。
 *
 * 为什么不再自研：官方句级路径把"到点"这件事交给输出传输的媒体队列——句尾文本帧
 * (`TTSTextFrame`) 排在该句音频后面，只有前序音频真的被写出时才被放行，所以客户端
 * 不需要任何播放时钟、锚点换算或 sleep（`transports/base_output.py` 的
 * `_audio_queue` + 写出阻塞）。我们只需消费它下发的三个字段：
 *
 * * `spoken_status`：`new`（整句先到，仍未被朗读）→ `in-progress` → `completed`；
 * * `spoken_progress.accumulated_text` / `remaining_text`：已读 / 未读文本；
 * * `segment_id`：这一段的来源帧 id，仅用于定位（句级路径下 `new` 与 `completed` 的
 *    id 并不相同，所以定位失败要退化成"第一个还没读完的段"，见官方的 `jn`）。
 *
 * 渲染语义照抄官方预构建客户端（bundle `index-B-ber7RZ.js`）：
 *
 * * `karaoke`（默认）：整句都在，已读正常色、未读灰；
 * * `captions`：只显示已读（未读整段丢弃）；
 * * `instant`：全文一次显示、不分色。
 *
 * 预读（Karaoke 里那些灰着的句子）是这条通道的天然行为：`new` 在音频之前到达，且在
 * 开口前被服务端扣住（`_queued_aggregated_text_frames`）。按本项目的口径它**只存在于
 * 前端内存**：刷新或被打断后灰字消失，账本/记忆永远只认"真的播过"的句子。
 */

export type VoiceCaptionMode = "karaoke" | "captions" | "instant";

/** 一段被官方通道宣布过的文本。 */
export interface VoiceBotOutputPart {
  /** 官方 `segment_id`（来源帧 id）；只用于定位，不作为身份键。 */
  segmentId: number | null;
  /** 整句文本，逐字保留（句间分隔空白属于该句自己的开头）。 */
  text: string;
  /** 已朗读的字符数（游标，来自 `accumulated_text.length`）。 */
  spokenChars: number;
  /** 生命周期状态；未知/缺省按 `new` 处理。 */
  status: "new" | "in-progress" | "completed";
  /** 是否会被朗读；false（如代码块）永远算未读。 */
  willBeSpoken: boolean;
}

/** `handleRtviMessage` 里已经归一化过的 `bot-output` 字段。 */
export interface BotOutputEvent {
  text: string;
  aggregatedBy?: string;
  spokenStatus?: string;
  willBeSpoken?: boolean;
  accumulatedText?: string;
  remainingText?: string;
  segmentId?: number | null;
}

export interface CaptionSplit {
  /** 已读部分（正常色）。 */
  spoken: string;
  /** 未读部分（灰）。 */
  unspoken: string;
}

function statusOf(value: string | undefined): VoiceBotOutputPart["status"] {
  return value === "in-progress" || value === "completed" ? value : "new";
}

/**
 * 把一条 `bot-output` 折进段落列表。
 *
 * 与官方 reducer 的三点一致：
 * 1. `new`（或 `will_be_spoken === false`）开一段新的；
 * 2. `in-progress` / `completed` 只推进游标，**不新建段**；
 * 3. 游标永远取 `min(accumulated_text.length, segment.text.length)`——超长的
 *    `accumulated_text`（服务端做过变换时可能出现）不能把切片越界。
 */
export function applyBotOutput(
  parts: readonly VoiceBotOutputPart[],
  event: BotOutputEvent,
): VoiceBotOutputPart[] {
  const text = event.text ?? "";
  if (!text) return parts as VoiceBotOutputPart[];
  const status = statusOf(event.spokenStatus);
  const willBeSpoken = event.willBeSpoken !== false;
  const segmentId = Number.isFinite(event.segmentId as number)
    ? (event.segmentId as number)
    : null;

  if (status === "new") {
    const tail = parts[parts.length - 1];
    // 重连/重复投递可能把同一条 `new` 再送一遍：按 (segmentId, text) 去重，
    // 否则同一句话会在屏幕上出现两次。
    if (tail && tail.segmentId !== null && tail.segmentId === segmentId && tail.text === text) {
      return parts as VoiceBotOutputPart[];
    }
    return [
      ...parts,
      {
        segmentId,
        text,
        spokenChars: 0,
        status: willBeSpoken ? "new" : "completed",
        willBeSpoken,
      },
    ];
  }

  // 定位目标段：先按 segment_id 精确匹配（词级路径下有效），失败则退化成"第一个还
  // 没读完的会被朗读的段"——句级路径下 `new` 与 `completed` 的 id 不同，这一步是常态。
  let index = segmentId === null ? -1 : parts.findIndex((part) => part.segmentId === segmentId);
  if (index === -1) {
    index = parts.findIndex((part) => part.willBeSpoken && part.status !== "completed");
  }
  if (index === -1) {
    // 没有任何可推进的段。两种可能：① `new` 那条丢了（数据通道丢包），② 服务端在讲
    // 一段我们没见过的文本。
    //   - `completed` 意味着这一句的音频**已经播完**（服务端在那时才下发），所以按它
    //     补一段"已点亮"的不会把没播过的文字放到屏幕上，反而救回一句真的说过的话；
    //   - `in-progress` 没有这个保证，宁可不显示也不凭它造一句。
    if (status !== "completed") return parts as VoiceBotOutputPart[];
    return [
      ...parts,
      { segmentId, text, spokenChars: text.length, status: "completed", willBeSpoken },
    ];
  }

  const target = parts[index];
  const accumulated = event.accumulatedText ?? "";
  const advanced =
    status === "completed" && !accumulated
      ? target.text.length
      : Math.min(accumulated.length, target.text.length);
  const spokenChars = Math.max(target.spokenChars, advanced);
  const next: VoiceBotOutputPart = {
    ...target,
    spokenChars,
    status: status === "completed" || spokenChars >= target.text.length ? "completed" : "in-progress",
  };
  const copy = [...parts];
  copy[index] = next;
  return copy;
}

/** 收尾（打断 / 回合结束）：灰着的段一律撤下，已点亮的段保留。 */
export function keepLitParts(
  parts: readonly VoiceBotOutputPart[],
): VoiceBotOutputPart[] {
  return parts.filter((part) => part.willBeSpoken && part.status === "completed");
}

/**
 * 点亮一句：把第一个还没读亮的段整段标成已读。
 *
 * 为什么不直接用官方 `bot-output` 的 `completed`：RTVI 消息走的是
 * `OutputTransportMessageUrgentFrame`，**不排在媒体队列里**，所以它到达的时刻与音频无关
 * （真机实测：无词级时间戳的服务商上，`completed` 比这一句的音频早 13.5 秒到）。官方
 * 通道在本地只能承担"文本 + 预读"，"到点"必须用真的排在音频后面的信号——也就是账本帧被
 * 输出传输放行时发出的 `assistant.sentence.ended`（S5）。
 *
 * `text` 用来在多句同文时挑对那一段；匹配不到就点亮最靠前的一段。
 */
export function lightFirstUnlit(
  parts: readonly VoiceBotOutputPart[],
  text?: string,
): VoiceBotOutputPart[] {
  const lit = (part: VoiceBotOutputPart): boolean =>
    !part.willBeSpoken || part.status === "completed";
  let index = parts.findIndex((part) => !lit(part) && text !== undefined && part.text === text);
  if (index === -1) index = parts.findIndex((part) => !lit(part));
  if (index === -1) return parts as VoiceBotOutputPart[];
  const copy = [...parts];
  const target = parts[index];
  copy[index] = { ...target, spokenChars: target.text.length, status: "completed" };
  return copy;
}

/** 段落列表拼回整段文本（预览/复制用，包含尚未朗读的灰字）。 */
export function captionText(parts: readonly VoiceBotOutputPart[]): string {
  return parts.map((part) => part.text).join("");
}

/**
 * 按模式切出"已读 / 未读"两截，交给渲染器上色。
 *
 * `captions` 只是把未读置空、`instant` 只是不区分颜色——与官方 `mr()` / `HG()` 等价。
 */
export function splitPart(part: VoiceBotOutputPart, mode: VoiceCaptionMode): CaptionSplit {
  if (!part.willBeSpoken) {
    return { spoken: "", unspoken: mode === "captions" ? "" : part.text };
  }
  const cursor = Math.max(0, Math.min(part.spokenChars, part.text.length));
  if (mode === "instant") return { spoken: part.text, unspoken: "" };
  if (mode === "captions") return { spoken: part.text.slice(0, cursor), unspoken: "" };
  return { spoken: part.text.slice(0, cursor), unspoken: part.text.slice(cursor) };
}
