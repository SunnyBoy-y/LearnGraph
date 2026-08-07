# `extract_text.py` — 文档正文抽取（DOC/DOCX/RTF/HTML）

> 沙箱内运行。把一份老式/HTML 文档抽取为 UTF-8 纯文本，供下游分析、图谱、记忆使用。宿主进程永远不解析这些不可信文档。

## 用法

```bash
python scripts/extract_text.py --input inputs/讲义.docx --output outputs/讲义.txt
python scripts/extract_text.py --input inputs/legacy.doc --format doc --output outputs/legacy.txt
python scripts/extract_text.py --input notes.html --format html --output outputs/notes.txt
```

也可通过 `skill.sandbox-run`（skill_key=`document-conversion`，script_path=`scripts/extract_text.py`，argv_extra 传 flags）。

## 输入 / 输出

- 输入：`.doc`（antiword）、`.docx`（mammoth）、`.rtf`（striprtf）、`.html/.htm/.xhtml`（BeautifulSoup+lxml）。
- 输出：UTF-8 纯文本文件；stdout 打印 JSON 摘要（chars、sha256）。

## 参数

| 参数 | 说明 |
|---|---|
| `--input` | 源相对路径（必填） |
| `--output` | 目标 `.txt` 相对路径（必填） |
| `--format` | 覆盖扩展名推断：`doc\|docx\|rtf\|html` |
| `--overwrite` | 允许覆盖已存在输出 |

## 最佳组合

```text
DOCX ──extract_text──> 正文.txt ──> graph-generation / 记忆 / 摘要
RTF ──extract_text──> 正文.txt ──> spreadsheet-analysis（若含表格则先转 xlsx 不可行，仅文本）
HTML ──extract_text──> 正文.txt ──> data-processing/json_transform 做清洗
```

## 限制与失败

- `.doc` 复杂版式（文本框/公式）会丢失；仅文本可靠。
- 空文档报 `no extractable text`。
- 输出已存在需 `--overwrite`。
- 失败时 stderr 有 `{status:"error", error}`，退出码非零。
