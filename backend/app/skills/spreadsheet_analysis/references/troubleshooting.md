# 常见失败与处理（spreadsheet-analysis）

## 编码错误（CSV）

- 现象：`UnicodeDecodeError`。
- 处理：用 `--encoding gbk`（中文场景常用）重试；仍失败则提示用户另存 UTF-8。

## 老格式读取失败

- `.xls` 用 xlrd、`.xlsb` 用 pyxlsb、`.ods` 用 odfpy；个别加密/损坏文件会失败。
- 处理：如实报告格式限制，建议用户导出为 `.xlsx` 或 `.csv` 后再处理。

## 混合类型 / 脏数据

- 同一列混数字与文本 → pandas 会推断为 object。
- 处理：在 `clean_table.py --dtype` 指定列类型，或在 Agent 层用 `json_transform` 预处理。

## 大表超时

- wall-time 180s。
- 处理：用 `--rows` 取样、`--columns` 选列；分批处理。

## 多 sheet Excel

- 默认读第一个 sheet。
- 处理：`--sheet <name>` 指定；先用 `inspect_table` 列出 sheet 名。

## 公式与格式

- openpyxl 默认 `data_only` 读缓存值；无缓存值的公式读到空。
- 处理：提示用户用能保存缓存值的工具重存，或我们按空值处理。

## 输出已存在

- 需 `--overwrite`。
