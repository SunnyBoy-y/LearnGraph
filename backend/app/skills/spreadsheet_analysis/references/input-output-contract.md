# 输入/输出契约（spreadsheet-analysis）

## 支持的输入格式

| 格式 | 引擎 |
|---|---|
| `.csv` / `.tsv` | pandas（自动尝试 `utf-8` 与 `gbk`；可 `--encoding`/`--sep`） |
| `.xlsx` | openpyxl |
| `.xls` | xlrd |
| `.xlsb` | pyxlsb |
| `.ods` | odfpy |

## 路径规则

- 工作区内相对路径；拒绝绝对路径、`..`。
- 输出已存在需 `--overwrite`。
- 输入放 `inputs/`，产物放 `outputs/`。

## 通用 CLI

```text
--input <rel>       源表
--output <rel>      产物（clean/write 需要）
--sheet <name>      可选，Excel 多 sheet 时指定
--encoding <enc>    可选，csv 编码（默认自动：utf-8→gbk）
--sep <char>        可选，csv 分隔符（默认自动）
--overwrite         覆盖输出
```

## stdout 约定

成功输出单行 JSON（`status:"ok"`、`rows`/`columns`/`output`/`sha256`）。
失败 stderr JSON + 非零退出码。

## 资源预算

- 单表 ≤ 256MB；超大表用 `--columns`/`--rows` 限定，避免 wall-time 180s 超时。
- `summarize_table` 对超大列数会限制输出字段数（前 40 列）。

## 成功判据

- `inspect_table`：`rows`/`columns` 非空。
- `clean_table`/`write_xlsx`：产物可被 `inspect_table` 读取且行列符合预期。
- `summarize_table`：`summary` 非空 JSON。
