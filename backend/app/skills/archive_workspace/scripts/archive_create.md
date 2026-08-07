# `archive_create.py` — 打包为 zip

> 把工作区内文件/目录打包成 `.zip`。安全：只处理相对路径，输出已存在需 `--overwrite`。

## 用法

```bash
python scripts/archive_create.py --inputs outputs/report.md outputs/fig.png --output outputs/交付.zip
python scripts/archive_create.py --inputs outputs --output outputs/全量.zip
```

## 参数

| 参数 | 说明 |
|---|---|
| `--inputs` | 要打包的文件/目录（相对路径，多个） |
| `--output` | 目标 `.zip`（必填） |
| `--overwrite` | 覆盖已存在输出 |

## 输入 / 输出

- 输入：工作区内文件/目录。
- 输出：`.zip`。stdout 打印文件数/bytes/sha256。

## 最佳组合

```text
多步产物 ──archive_create──> bundle.zip ──archive_manifest──> 清单.json
archive_create ──> zip ──> 宿主文件发布（sandbox_publish_file）
```

## 限制与失败

- 目录打包时成员路径相对于其父目录（不含绝对路径）。
- 不存在的输入 → 报错。
