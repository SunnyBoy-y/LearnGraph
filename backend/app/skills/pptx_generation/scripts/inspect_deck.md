# `inspect_deck.py` — 抽取 PPTX 内容

> 读取既有 PPTX：页数、每页标题/文本/形状数、演讲备注。输出 JSON，可落盘供下游使用。

## 用法

```bash
python scripts/inspect_deck.py --input inputs/旧.pptx
python scripts/inspect_deck.py --input 旧.pptx --output outputs/旧内容.json
```

## 输入 / 输出

- 输入：`.pptx`。
- 输出：stdout JSON（每页 `title/texts/shape_count/notes`）；`--output` 可把报告写成 `.json` 文件。

## 最佳组合

```text
inspect_deck ──> 摘要 JSON ──> 记忆/图谱/报告
inspect_deck ──> 确认页数/标题 ──> build_deck 重建
```

## 限制与失败

- 只读文本层；不渲染，不识别图片/图表内容。
- 加密或损坏的 PPTX 报错。
