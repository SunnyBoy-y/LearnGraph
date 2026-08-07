# 最佳组合（archive-workspace）

> 组合原则：打包/解压/清单三件套，先清单后解压保证安全。

## 常见任务 → 脚本链

| 任务 | 脚本链 | 说明 |
|---|---|---|
| 交付一组产物 | `archive_create.py` | 打包 zip |
| 收到压缩包先检查 | `archive_manifest.py` | 看成员/大小/哈希 |
| 安全解压 | `archive_extract.py` | zip-slip 防护 |
| 备份工作区 | `archive_create.py --dir . --output backup.zip` | 整目录打包 |
| 核对归档 | `archive_create` → `archive_manifest` | 清单留档 |

## 跨 Skill 组合示例

```text
把学习资料打包交付
  1) document-conversion/docx_to_pdf ──> 讲义.pdf
  2) data-processing/make_report ──> 报告.md
  3) archive-workspace/archive_create.py --inputs 讲义.pdf 报告.md --output outputs/资料.zip
  4) archive-workspace/archive_manifest.py --zip outputs/资料.zip ──> 清单.json
```

```text
用户上传一个项目 zip
  1) archive-workspace/archive_manifest.py --zip uploads/proj.zip  → 检查成员
  2) archive-workspace/archive_extract.py  → 解压到 outputs/proj/
  3) frontend-build-preview/build_frontend ──> 构建
```

## 选择依据

- 打包 → `archive_create`；解压 → `archive_extract`；核对 → `archive_manifest`。
- 对不可信 zip 先 `archive_manifest` 再 `archive_extract`，避免恶意成员逃逸。
