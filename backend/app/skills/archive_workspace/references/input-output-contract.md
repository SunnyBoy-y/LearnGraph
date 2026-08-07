# 输入/输出契约（archive-workspace）

## 路径规则

- 工作区内相对路径；拒绝绝对路径、`..`、`.`。
- 输出已存在需 `--overwrite`。
- 输入放 `inputs/`，产物放 `outputs/`。

## 通用 CLI

```text
archive_create.py:   --inputs <path>... [--output <bundle.zip>] [--overwrite]
archive_extract.py:  --zip <rel> --output <dir> [--overwrite]
archive_manifest.py: --zip <rel> | --dir <rel> [--output <manifest.json>]
```

## 安全不变量

- 解压时每个成员必须先规范化，拒绝：
  - `..` / 绝对路径 / 盘符；
  - 符号链接（`symlink`）成员；
  - 越出目标目录的路径。
- 违反即整体失败，不部分解压。

## stdout 约定

成功单行 JSON（`status:"ok"`、`output`、`files`/`bytes`/`sha256`）。
失败 stderr JSON + 非零退出码。

## 资源预算

- 归档 ≤ 64MB（单个文件）；解压总大小 ≤ 256MB；wall-time ≤ 180s。
- 超大 zip 先 `archive_manifest` 看大小再解压。

## 成功判据

- `archive_create`：zip 可被 `archive_manifest` 读取且成员数匹配。
- `archive_extract`：解压出的文件与 zip 清单一致。
- `archive_manifest`：`entries` 非空（zip 或目录有内容）。
