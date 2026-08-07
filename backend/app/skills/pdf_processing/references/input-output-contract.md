# 输入/输出契约（pdf-processing）

## 路径规则

- 所有路径为**沙箱工作区内相对路径**；拒绝绝对路径、`..`、符号链接逃逸。
- 输出目录自动创建；输出已存在需 `--overwrite`。
- 约定输入放 `inputs/`，产物放 `outputs/`。

## 通用 CLI

```text
--input <rel>      源 PDF（相对工作区）
--output <rel>     目标文件
--pages <spec>     extract_text / to_png 用：页码范围，如 "1-5"、"3"、"1,3-4"（1 起）
--start / --end    pdf_split 用：保留的页范围（含端点）
--overwrite        覆盖输出
```

## stdout 约定

成功输出单行 JSON（`status:"ok"`、`output`、`pages`/`chars`/`bytes`、`sha256`）。
失败在 stderr 输出 JSON 并以非零退出码结束。

## 输入限制

| 能力 | 限制 |
|---|---|
| 加密 PDF | 无密码无法离线处理；报错并说明 |
| 扫描 PDF | 无文字层时抽文本为空；改用 `pdf_to_png` 渲染 |
| 超大 PDF | 用 `--pages` 限定范围，避免 wall-time 180s 超时 |

## 资源预算

- 单文件输出 ≤64MB；单次任务 stdout+产物 ≤256MB；wall-time ≤180s。
- 抽文本时 stdout 只放摘要，正文写 `--output`。

## 成功判据

- `pdf_info`：`pages>0` 且 `encrypted=false` 为可处理。
- `pdf_extract_text`：输出非空（或按页分段非空）。
- `pdf_merge`/`pdf_split`：输出可被 `pdf_info` 读取且页数符合预期。
- `pdf_to_png`：输出为可被 Pillow 打开的非空 PNG。
