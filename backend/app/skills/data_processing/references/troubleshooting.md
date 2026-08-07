# 常见失败与处理（data-processing）

## JSON 结构不符

- `json_transform` 对 `--select`/`--filter` 报错。
- 处理：先 `inspect` 输入（本 Skill 无独立 inspect，可用 `json_transform --limit 2` 输出样例）；确认输入是数组/对象再选路径。

## 大文件超时/输出过大

- wall-time 180s 或输出超 256MB。
- 处理：用 `--limit`/`--max-rows` 抽样；分块处理。

## CSV 编码错误

- 用 `--encoding gbk` 重试（中文场景常用）。

## 报告为空

- `make_report` 输入 `sections` 为空或标题为空 → 报错。
- 处理：先确认数据源有内容，再生成报告；不生成空报告。

## batch_rename 冲突

- 目标文件名已存在 → 报错并跳过（不会覆盖）。
- 处理：用 `--dry-run` 先看结果；用 `--pad` 避免重名。

## 输出已存在

- 需 `--overwrite`。

## 错误退出码

- 非零退出 = 失败；stderr 有 `{status:"error", error}`。不把部分产物当成功。
