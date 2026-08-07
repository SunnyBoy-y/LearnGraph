# 输入/输出契约（media-processing）

## 支持的输入

- 常见容器/编码（mp3, wav, m4a, aac, flac, ogg, mp4, mov, webm, mkv, avi …），由 ffmpeg 支持列表决定。
- 损坏/不支持的编码会在 ffprobe/ffmpeg 阶段报错。

## 路径规则

- 工作区内相对路径；拒绝绝对路径、`..`。
- 输出已存在需 `--overwrite`。
- 输入放 `inputs/`，产物放 `outputs/`。

## 通用 CLI

```text
--input <rel>      源媒体
--output <rel>     目标文件
--format <ext>     可选，输出容器（extract_audio）
--sample-rate/-sr  重采样（归一化用 16000）
--channels/-ac     声道数（归一化用 1）
--bitrate          音频码率，如 128k
--ss / --t         起始/时长（秒），限片段
--every N          extract_frames 每 N 秒抽一帧
--overwrite        覆盖输出
```

## stdout 约定

成功单行 JSON（`status:"ok"`、`output`、`duration`/`frames`/`bytes`/`sha256`）。
失败 stderr JSON + 非零退出码。

## 资源预算

- 产物 ≤ 256MB；wall-time ≤ 180s。
- 长媒体转码/抽帧用 `--ss/--t` 限定片段，避免超时。

## 成功判据

- `media_info`：`streams`/`format` 非空。
- `extract_audio`：输出可被 `media_info` 读取且时长>0。
- `extract_frames`：输出 PNG 目录中至少 1 帧。
- `media_report`：JSON 含 `duration`/`format_name`/`streams`。
