# `extract_audio.py` — 抽音频/转码/归一化

> 用 ffmpeg 抽取音频轨道、转码、重采样/声道归一化，或截取片段。**转录前先用它归一化到 16kHz 单声道 WAV**，再交给宿主 `sandbox_transcribe_audio`。

## 用法

```bash
# 转录准备（最常用）
python scripts/extract_audio.py --input inputs/课堂录音.m4a --output outputs/asr.wav \
  --sample-rate 16000 --channels 1

# 转码
python scripts/extract_audio.py --input 录音.m4a --output 分享.mp3 --format mp3 --bitrate 128k

# 截取片段
python scripts/extract_audio.py --input 长录音.mp3 --output 片段.mp3 --ss 600 --t 60
```

## 参数

| 参数 | 说明 |
|---|---|
| `--sample-rate/-sr` | 重采样 Hz（ASR 用 16000） |
| `--channels/-ac` | 声道（ASR 用 1） |
| `--bitrate` | 音频码率 |
| `--ss` / `--t` | 起始 / 时长（秒），限片段防超时 |

## 输入 / 输出

- 输入：任意 ffmpeg 可读媒体。
- 输出：由扩展名决定格式（`.wav`/`.mp3` 等）。stdout 打印 bytes/sha256。

## 最佳组合

```text
extract_audio(-sr 16000 -ac 1) ──> asr.wav ──> sandbox_transcribe_audio（宿主 ASR）
media_info ──> 确定时长 → extract_audio 截取片段
```

## 限制与失败

- 无有效音频轨（纯视频）→ ffmpeg 报错。
- 长片段用 `--ss/--t` 限制，避免 wall-time 180s 超时。
