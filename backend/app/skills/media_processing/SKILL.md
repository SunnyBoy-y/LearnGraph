---
name: media-processing
description: 音视频元信息、转码、抽取音频、抽帧与媒体报告。
---

# 音视频处理

## When to use

- 用户上传或引用了音视频文件（`.mp3/.wav/.m4a/.mp4/.mov/.webm` 等），需要**读元信息、转码、抽音频、抽帧、做媒体报告**。
- 需要把录音/音频归一化后交给 `sandbox_transcribe_audio`（宿主 ASR 桥）转录。

## 决策顺序

1. 先 `media_info.py` 用 ffprobe 看格式/编码/时长/流，决定能否处理。
2. 转录准备：`extract_audio.py --sample-rate 16000 --channels 1` 归一化，再走宿主 ASR（不在此 Skill 内联网）。
3. 转码：`extract_audio.py --format mp3` 等。
4. 抽帧/截图：`extract_frames.py`（视频按时间/频率截取 PNG）。
5. 汇总报告：`media_report.py` 输出结构化 JSON（供下游展示/入库存档）。

## 脚本索引

| 脚本 | 用途 |
|---|---|
| `media_info.py` | ffprobe 流/格式元信息 |
| `extract_audio.py` | 抽音频/转码/归一化（ffmpeg） |
| `extract_frames.py` | 视频抽帧为 PNG（ffmpeg） |
| `media_report.py` | 元信息 + 指纹 + 摘要 → JSON 报告 |

## 组合路线

```text
视频 ──media_info──> 流信息
录音 ──extract_audio(16k mono)──> wav ──> sandbox_transcribe_audio（宿主 ASR）
视频 ──extract_frames──> 关键帧 PNG
音视频 ──media_report──> report.json
```

## 安全与限制

- 离线运行；`ffmpeg/ffprobe` 来自镜像。
- 转录由宿主 ASR 桥完成，本 Skill 只做预处理；不在这里放密钥或联网。
- 超大媒体用 `--ss/--t` 限定片段，避免 wall-time 180s 超时。
- 不支持的编码/损坏文件如实报告，不编造信息。

## 详细说明

组合配方见 `references/best-combinations.md`，输入/输出契约见 `references/input-output-contract.md`，常见失败见 `references/troubleshooting.md`。每个脚本的完整用法见 `scripts/*.md`。
