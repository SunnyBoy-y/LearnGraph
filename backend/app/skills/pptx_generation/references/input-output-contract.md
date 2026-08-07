# 输入/输出契约（pptx-generation）

## 大纲 JSON 结构（build_deck 输入）

```json
{
  "title": "演示标题",
  "subtitle": "可选副标题",
  "slides": [
    {
      "title": "第 1 页标题",
      "points": ["要点一", "要点二", "要点三"],
      "notes": "演讲备注（可选）"
    }
  ],
  "theme": { "accent": "C4472E", "title_size": 32, "point_size": 18 }
}
```

- `slides` 至少 1 项；每项 `title` 必填，`points` 数组（可空），`notes` 可选。
- `theme` 可选；`accent` 为十六进制颜色（默认品牌蓝 `#4472C4`）。

## 路径规则

- 所有路径为工作区内相对路径；拒绝绝对路径、`..`。
- 输出已存在需 `--overwrite`。
- 大纲 JSON 放 `inputs/` 或工作区根；产物放 `outputs/`。

## 通用 CLI

```text
build_deck.py:  --input <outline.json> --output <deck.pptx> [--overwrite]
inspect_deck.py:--input <deck.pptx> [--output <summary.json>] [--overwrite]
deck_to_html.py:--input <deck.pptx> --output <preview.html> [--title <t>] [--overwrite]
```

## stdout 约定

成功输出单行 JSON（`status:"ok"`、`output`、`slides`/`bytes`/`sha256`）。
失败在 stderr 输出 JSON 并以非零退出码结束。

## 资源预算

- 大纲 JSON ≤ 2MB；PPTX 产物 ≤ 64MB；wall-time ≤ 180s。
- 幻灯片数量建议 ≤ 100 页，避免超时与文件过大。

## 成功判据

- `build_deck`：产物可被 `inspect_deck` 打开且页数 == 大纲 `slides` 数。
- `deck_to_html`：产物为自包含 HTML，可被 `html_to_pdf`/`html_to_png` 渲染。
