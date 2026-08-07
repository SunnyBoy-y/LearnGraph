# `media_report.py` — 媒体报告

> 汇总 ffprobe 元信息为一份紧凑结构化 JSON：格式、时长、音频/视频流、SHA-256。可落盘供入库存档或展示。

## 用法

```bash
python scripts/media_report.py --input inputs/lecture.mp4
python scripts/media_report.py --input lecture.mp4 --output outputs/report.json
```

## 输入 / 输出

- 输入：媒体文件。
- 输出：stdout JSON；`--output` 可写成 `.json` 文件。

## 最佳组合

```text
media_report ──> report.json ──> 展示/存档
media_report ──> 音频流参数 ──> 决定 extract_audio 归一化参数
```

## 限制与失败

- 与 `media_info` 同源（ffprobe）；损坏文件报错。
- 不做转码/抽帧，只汇总。
