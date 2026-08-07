---
name: archive-workspace
description: 安全创建/解压归档并生成成员清单，防止 zip-slip 与路径逃逸。
---

# 归档与解压

## When to use

- 需要把工作区文件/目录**打包成 zip**（交付、备份、转移）。
- 需要**解压**上传的压缩包，并安全处理其内容。
- 需要生成归档的**成员清单**（路径、大小、哈希）供审计/核对。

## 决策顺序

1. 打包：`archive_create.py` 选文件/目录 → 输出 `.zip`（可加 `.manifest.json`）。
2. 解压：`archive_extract.py` 安全解压到目标目录（防 zip-slip、防绝对路径、防链接逃逸）。
3. 核对：`archive_manifest.py` 生成成员清单（路径/size/sha256）。
4. 组合优先：`archive_create → archive_manifest`；上传的 zip 先 `archive_manifest` 看清单再 `archive_extract`。

## 脚本索引

| 脚本 | 用途 |
|---|---|
| `archive_create.py` | 打包文件/目录为 zip |
| `archive_extract.py` | 安全解压 zip（防逃逸） |
| `archive_manifest.py` | 生成 zip/目录成员清单 |

## 组合路线

```text
文件/目录 ──archive_create──> bundle.zip ──archive_manifest──> 清单.json
上传.zip ──archive_manifest──> 清单 ──archive_extract──> 解压到 outputs/
```

## 安全与限制

- 离线运行；zip 用 Python `zipfile`。
- **zip-slip 防护**：解压时拒绝 `..`、绝对路径、符号链接逃逸；清单可事先扫描。
- 只处理工作区内相对路径；不扫描宿主路径。
- 归档/解压产物遵守 64MB/256MB/180s 限额。

## 详细说明

组合配方见 `references/best-combinations.md`，输入/输出契约见 `references/input-output-contract.md`，常见失败见 `references/troubleshooting.md`。每个脚本的完整用法见 `scripts/*.md`。
