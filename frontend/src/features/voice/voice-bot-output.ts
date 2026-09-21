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
 *
 * 两处与本文件的"纯官方"口径不同的地方，都是被真机逼出来的：
 *
 * * `source` —— 官方通道不保证把每一句都送到。账本 `assistant.sentence.queued` 是同一句
 *   的**第二个**文本来源（它只在那一句的音频真的被写出去时才到），用它兜底，屏幕才不会
 *   整轮空着。标记来源只为去重：同一句被两条通道各宣布一次时，屏幕上只能出现一句。
 * * `revealedParts` —— 官方通道一次把整轮的文本都送来（`new` 比音频早十几秒），所以上屏
 *   还要按"点亮进度"裁一刀，否则整段回答会在开口前就全铺出来。
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
  /**
   * The sentence has crossed the audio queue's start marker.  `bot-output/new`
   * is only a speculative preview; this flag is the frontend's durable
   * boundary for an interruption: a started sentence belongs to the heard
   * prefix even if its end marker has not arrived yet.
   */
  playbackStarted?: boolean;
  /**
   * 这一段是谁宣布的。
   *
   * `bot-output`（缺省）是官方预读通道；`ledger` 是账本兜底——那一句的音频已经开始
   * 写出、官方那条却还没到（或永远不到）时，由 `assistant.sentence.queued` 补上。
   * 只在去重时用：两条通道为同一句各开一段，屏幕上就会是同一句话出现两次。
   */
  source?: "bot-output" | "ledger";
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
    // 否则同一句话会在屏幕上出现两次。账本兜底为同一句先开过段时也要认出来——它的
    // `segment_id` 是空的（那条通道不携带官方 id），只比 id 是比不上的。
    if (
      tail &&
      tail.text === text &&
      tail.status !== "completed" &&
      (tail.segmentId === segmentId || tail.source === "ledger")
    ) {
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
        playbackStarted: false,
        source: "bot-output",
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
      {
        segmentId,
        text,
        spokenChars: text.length,
        status: "completed",
        willBeSpoken,
        playbackStarted: true,
        source: "bot-output",
      },
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
  copy[index] = {
    ...target,
    spokenChars: target.text.length,
    status: "completed",
    playbackStarted: true,
  };
  return copy;
}

/**
 * Mark the sentence whose first audio frame reached the output transport.
 *
 * The official `bot-output/new` message can arrive well before audio.  Only
 * the ledger's `assistant.sentence.queued` event is allowed to set this bit;
 * this keeps a pre-read B out of an interruption result until B actually
 * started playing.
 */
export function markPlaybackStarted(
  parts: readonly VoiceBotOutputPart[],
  text?: string,
): VoiceBotOutputPart[] {
  const normalized = String(text ?? "").trim();
  let index = parts.findIndex(
    (part) =>
      part.willBeSpoken &&
      !part.playbackStarted &&
      part.status !== "completed" &&
      Boolean(normalized) &&
      part.text.trim() === normalized,
  );
  if (index === -1) return parts as VoiceBotOutputPart[];
  const target = parts[index];
  const copy = [...parts];
  copy[index] = { ...target, playbackStarted: true, status: "in-progress" };
  return copy;
}

/**
 * Return the text that belongs to an interrupted answer.
 *
 * Completed sentences are fully spoken.  A sentence with a playback start
 * marker is also retained, because the user may interrupt in its middle and
 * the server's sentence-level contract treats that sentence as part of the
 * audible prefix unless a finer playback acknowledgement is available.
 */
export function heardParts(
  parts: readonly VoiceBotOutputPart[],
): VoiceBotOutputPart[] {
  return parts
    .filter((part) => part.willBeSpoken && (part.status === "completed" || part.playbackStarted))
    .map((part) => ({
      ...part,
      spokenChars: part.text.length,
      status: "completed" as const,
      playbackStarted: true,
    }));
}

/** 段落列表拼回整段文本（预览/复制用，包含尚未朗读的灰字）。 */
export function captionText(parts: readonly VoiceBotOutputPart[]): string {
  return parts.map((part) => part.text).join("");
}

/**
 * 该上屏的那几段：**已点亮的 + 已经开始播放的当前句**（浅灰），更远的预读句先不显示。
 *
 * 官方通道把整轮的文本一次性送来（`new` 比音频早十几秒，见 `lightFirstUnlit`），照单
 * 全铺出来就不是"说一句显一句"了，等于在开口前把整个回答摊在屏幕上。这里按真实播放
 * 标记裁一刀：已播完的照常显示，只有跨过 `assistant.sentence.queued` 的当前句才显示为
 * 浅灰；其余预读段落继续留在内存表里。
 *
 * 注意它只裁剪**送出去渲染的那份**：控制器手里的段落表始终是完整的，`lightFirstUnlit`
 * 要点亮的就是还没上屏的那几句。
 */
export function revealedParts(
  parts: readonly VoiceBotOutputPart[],
): VoiceBotOutputPart[] {
  const out: VoiceBotOutputPart[] = [];
  for (const part of parts) {
    // `will_be_spoken=false`（代码块）没有语音播放边界，可以立即显示。
    if (!part.willBeSpoken) {
      out.push(part);
      continue;
    }
    if (part.status === "completed") {
      out.push(part);
      continue;
    }
    // A `new` preview is retained for later matching, but it is not visible
    // until the audio queue emits the causal start marker.
    if (part.playbackStarted) out.push(part);
    break;
  }
  return out;
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
