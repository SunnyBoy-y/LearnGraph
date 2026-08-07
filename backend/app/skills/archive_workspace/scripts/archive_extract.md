# `archive_extract.py` — 安全解压 zip

> 安全解压 zip 到工作区目录，防 zip-slip、防绝对路径/盘符、防符号链接逃逸。任何不可信 zip 优先走 `archive_manifest` 再解压。

## 用法

```bash
python scripts/archive_extract.py --zip inputs/proj.zip --output outputs/proj
```

## 参数

| 参数 | 说明 |
|---|---|
| `--zip` | 源 `.zip`（相对路径） |
| `--output` | 解压目标目录（相对路径） |
| `--overwrite` | 允许解压进非空目录 |

## 输入 / 输出

- 输入：`.zip`。
- 输出：目标目录内的文件。stdout 打印文件数与成员列表。

## 最佳组合

```text
archive_manifest ──> 检查清单 ──> archive_extract 解压
archive_extract ──> 项目文件 ──> frontend/build / pdf 处理
```

## 限制与失败

- 恶意成员（`..`、绝对路径、盘符、符号链接）→ 整体失败，不部分解压。
- 加密 zip 无法解压（zipfile 不支持）——如实说明。
- 输出目录非空且未 `--overwrite` → 拒绝。
