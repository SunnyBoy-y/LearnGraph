# `batch_rename.py` — 工作区批量重命名

> 在工作区一个目录内安全批量重命名文件：加前缀/后缀、子串替换、按序号补零。绝不跟随符号链接或改到目录外。

## 用法

```bash
# 预览（不真正改名）
python scripts/batch_rename.py --dir inputs --prefix lecture_ --dry-run

# 按序号补零
python scripts/batch_rename.py --dir inputs --pad 3

# 子串替换 + 扩展名限定
python scripts/batch_rename.py --dir inputs --replace "副本=final" --ext .png
```

## 参数

| 参数 | 说明 |
|---|---|
| `--prefix` / `--suffix` | 加前后缀 |
| `--replace OLD=NEW` | 子串替换（可多个） |
| `--ext` | 只处理该扩展名（如 `.png`） |
| `--pad N` | 用零填充序号替换文件名主干 |
| `--dry-run` | 只打印计划，不真正改名 |

## 输入 / 输出

- 输入：工作区内目录。
- 输出：stdout JSON（`renamed` 计划/结果、`count`）。

## 最佳组合

```text
batch_rename --dry-run ──> 核对计划 ──> 去掉 --dry-run 执行
批量素材整理 ──> 统一前缀/序号 ──> 后续归档/渲染
```

## 限制与失败

- 目标已存在 → 报错并跳过（不覆盖）。
- 只处理给定目录的直接子文件（不递归、不跟随链接）。
