# 最佳组合（media-processing）

> 组合原则：`media_info` 先行判断可处理性；转录走“归一化 → 宿主 ASR”；视觉走“抽帧”。

## 常见任务 → 脚本链

| 任务 | 脚本链 | 说明 |
|---|---|---|
| 看音视频元信息 | `media_info.py` | 时长/编码/流 |
| 录音转文字 | `extract_audio.py -sr 16000 -ac 1` → `sandbox_transcribe_audio` | 先归一化再宿主 ASR |
| 视频关键帧 | `extract_frames.py --every 2` | 每 2 秒一帧 |
| 转成 mp3 便于分享 | `extract_audio.py --format mp3` | ffmpeg 转码 |
| 媒体报告入库 | `media_report.py` | 结构化 JSON |

## 跨 Skill 组合示例

```text
用户上传一段 40 分钟课程录音
  1) media-processing/media_info.py          → 确认时长/编码
  2) media-processing/extract_audio.py -sr 16000 -ac 1  → 归一化.wav
  3) sandbox_transcribe_audio（宿主 ASR 桥） → 转录文本
  4) 转录文本 → document-conversion/extract_text 不需要 → 直接进图谱/记忆/摘要
```

```text
从视频里抽课件截图
  1) media-processing/media_info.py
  2) media-processing/extract_frames.py --every 5 --format png
  3) 关键帧 PNG → 视觉走查/插入 PPT（pptx 手工放图）
```

## 选择依据

- 只要元信息 → `media_info`。
- 要文本 → 归一化 + 宿主 ASR（本 Skill 只预处理）。
- 要画面 → `extract_frames`。
- 要归档 → `media_report`。
