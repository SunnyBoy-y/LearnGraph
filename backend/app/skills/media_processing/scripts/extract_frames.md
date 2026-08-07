# `extract_frames.py` — 视频抽帧

> 用 ffmpeg 从视频按固定间隔抽取 PNG 帧，用于关键帧浏览、课件截图、视觉走查。

## 用法

```bash
python scripts/extract_frames.py --input inputs/lecture.mp4 --output outputs/frames
python scripts/extract_frames.py --input lecture.mp4 --output frames --every 5
python scripts/extract_frames.py --input lecture.mp4 --output intro --ss 0 --t 10 --every 1
```

## 参数

| 参数 | 说明 |
|---|---|
| `--every` | 每 N 秒一帧（默认 2） |
| `--ss` / `--t` | 起始 / 窗口（秒），限片段防超时 |

## 输入 / 输出

- 输入：视频。
- 输出：目录内的 `frame-0001.png`…。stdout 打印帧列表/数量。

## 最佳组合

```text
media_info ──> 确定时长 → extract_frames 抽关键帧
extract_frames ──> PNG ──> 视觉走查 / 插入文档
```

## 限制与失败

- 无视频轨 → ffmpeg 报错。
- 长视频用 `--ss/--t` 限制，避免 wall-time 180s 超时。
