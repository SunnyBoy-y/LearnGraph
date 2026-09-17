/**
 * 已废弃：字速打字机（`charMs` 匀速估算）被**句级字幕**取代。
 *
 * 新的实现与口径在 `./voice-sentence-captions`：
 *   - 文本"什么时候出现"由后端排期到这一句真正开始播放的时刻，前端不需要播放时钟；
 *   - 正在朗读的那一句淡一档，读完回归正文色，未开始的句子不渲染。
 *
 * 这里保留文件与旧导出名，只是为了让历史引用（旧测试、旧文档）不断掉。
 */
export {
  VoiceSentenceCaptions,
  VoiceSentenceCaptions as VoiceTypingText,
  readVoiceCaptionSegments,
} from "./voice-sentence-captions";
export type { VoiceCaptionSegment } from "./voice-sentence-captions";
