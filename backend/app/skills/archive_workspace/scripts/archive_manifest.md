# `archive_manifest.py` — 归档/目录清单

> 列出 zip 或目录的成员清单（路径、大小、sha256、危险标记），用于核对、审计、解压前安全检查。

## 用法

```bash
python scripts/archive_manifest.py --zip inputs/proj.zip
python scripts/archive_manifest.py --dir outputs
python scripts/archive_manifest.py --zip proj.zip --output outputs/清单.json
```

## 参数

| 参数 | 说明 |
|---|---|
| `--zip` / `--dir` | 二选一，指定来源 |
| `--limit` | 限制列出条数 |
| `--output` | 可选，把清单写成 `.json` |

## 输入 / 输出

- 输入：zip 或目录。
- 输出：stdout JSON（`entries`、`unsafe_entries`、`total_bytes`）。

## 最佳组合

```text
不可信 zip ──archive_manifest──> 检查 unsafe_entries ──> archive_extract
archive_create ──> archive_manifest ──> 清单留档
```

## 限制与失败

- zip 的 `unsafe_entries` 标记 `unsafe_path`/`symlink`——存在即视为不可信，不强行解压。
- `--limit` 时 `entries_total` 为 `null`（未完整扫描）。
