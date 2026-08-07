# 输入/输出契约（document-conversion）

## 路径规则

- 所有路径都是**沙箱工作区内相对路径**（例如 `inputs/report.docx`、`notes.html`、`outputs/report.pdf`）。
- 拒绝绝对路径、`..`、符号链接逃逸、隐藏段。
- 输出目录不存在会自动创建；**输出文件已存在时需显式 `--overwrite`**，否则报错退出。
- 约定：输入放 `inputs/` 或工作区根；产物放 `outputs/`。若用户未提供输出路径，先创建 `outputs/`。

## 通用 CLI

每个脚本都支持：

```text
--input <rel>      必填，源文件（相对工作区）
--output <rel>     必填，目标文件（相对工作区）
--format <fmt>     可选，extract_text 用；否则按扩展名推断
--overwrite        覆盖已存在的输出
```

## stdout 约定

成功：单行 JSON，含 `status: "ok"`、`input`、`output`、`chars`/`size`、`sha256`。
失败：stderr 输出 JSON `{status:"error", error:"..."}` 并以非零退出码结束。

正文/产物**写入文件**，不通过 stdout 传输大文本。

## 输入格式与限制

| 格式 | 解析器 | 限制 |
|---|---|---|
| `.doc` | antiword | 仅文本；复杂版式会丢失 |
| `.docx` | mammoth | 文本+简单样式；嵌入图片内联到 HTML |
| `.rtf` | striprtf | 仅文本 |
| `.html`/`.htm`/`.xhtml` | BeautifulSoup+lxml | 无外链；不执行 JS |

## 资源预算

- 单文件输出 ≤ 64MB；单次任务 stdout+产物 ≤ 256MB；wall-time ≤ 180s。
- 超大文档优先 `extract_text.py` 分段抽取，避免一次转换超时。

## 成功判据

- `extract_text.py`：输出非空 UTF-8 文本。
- `docx_to_pdf.py` / `html_to_pdf.py`：输出存在且可被 `pdf-processing/pdf_info.py` 读取（页数>0）。
- `html_to_png.py`：输出存在且为非空 PNG（可被 Pillow 打开）。
