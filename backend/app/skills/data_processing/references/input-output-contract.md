# 输入/输出契约（data-processing）

## 路径规则

- 工作区内相对路径；拒绝绝对路径、`..`、`.`。
- 输出已存在需 `--overwrite`。
- 输入放 `inputs/`，产物放 `outputs/`。

## 通用 CLI

```text
json_transform.py: --input <json> --output <out> [--select a,b] [--filter k=op=v] [--limit N] [--overwrite]
csv_profile.py:    --input <csv> [--max-rows N] [--encoding E]
make_report.py:    --input <content.json> --output <report.md|.html> [--title T] [--format md|html] [--overwrite]
batch_rename.py:   --dir <rel> --prefix P [--suffix S] [--replace OLD=NEW] [--ext .png] [--pad N] [--dry-run]
```

## stdout 约定

成功单行 JSON（`status:"ok"`、`output`、`rows`/`files`/`bytes`/`sha256`）。
失败 stderr JSON + 非零退出码。

## 内容 JSON 结构（make_report 输入）

```json
{
  "title": "报告标题",
  "sections": [
    { "heading": "小节", "paragraphs": ["...", "..."],
      "bullets": ["..."], "table": { "columns": ["A","B"], "rows": [["a","b"]] } }
  ]
}
```

## 资源预算

- 单文件输出 ≤ 64MB；单次任务 ≤ 256MB；wall-time ≤ 180s。
- 大 JSON/CSV 用 `--limit`/`--max-rows` 抽样。

## 成功判据

- `json_transform`：输出为合法 JSON 且结构符合 `--select`。
- `csv_profile`：`columns`/`rows` 非空。
- `make_report`：输出为合法 Markdown 或自包含 HTML。
- `batch_rename`：改名后文件仍存在（`--dry-run` 不真正改名）。
