# `media_info.py` — 音视频元信息

> 用 ffprobe 输出容器/流信息：格式、时长、流数量、编码、分辨率等。任何媒体处理前先跑它。

## 用法

```bash
python scripts/media_info.py --input inputs/课堂录音.mp3
python scripts/media_info.py --input inputs/lecture.mp4
```

## 输入 / 输出

- 输入：任意 ffmpeg 可读的媒体。
- 输出：stdout JSON（`format_name/duration_seconds/size_bytes/stream_count/streams`）。

## 最佳组合

```text
media_info ──> 决定转码/抽帧/归一化参数
media_info ──> 判断转录前是否需归一化
```

## 限制与失败

- 损坏/不支持编码会报错——如实说明。
- 只读元数据，不转码。
