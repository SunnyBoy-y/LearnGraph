# 常见失败与处理（archive-workspace）

## zip-slip / 路径逃逸

- 现象：`archive_extract` 报 `zip entry escapes target` 或 `unsafe path`。
- 处理：**不强行解压**；先用 `archive_manifest` 看成员，确认 zip 是否可信。此类 zip 视为不可信，报告并拒绝，不部分解压。

## 符号链接成员

- 现象：`unsupported link entry`。
- 处理：跳过并明确报告；不得创建链接文件（防宿主路径逃逸）。

## 密码/损坏 zip

- `zipfile` 不支持加密解压或损坏 zip 报错。
- 处理：如实说明；要求用户提供未加密/未损坏版本。

## 解压超时/磁盘超限

- wall-time 180s 或输出超 256MB。
- 处理：先 `archive_manifest` 看总大小；分批处理或提示用户拆分。

## 目标已存在

- 需 `--overwrite`。

## 错误退出码

- 非零退出 = 失败；stderr 有 `{status:"error", error}`。不把部分产物当成功。
