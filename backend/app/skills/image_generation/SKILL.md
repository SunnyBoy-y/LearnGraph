---
name: image-generation
description: 用宿主 generate_image 工具完成文生图与图生图编辑：先解析会话图片 file_id 再生成或基于原图编辑，区分搜图与生图。
---

# 文生图 / 图生图

## When to use

- 用户要求**生成**一张新图片（配图、插画、示意图、海报、概念可视化等）→ 文生图。
- 用户要求**基于/修改**会话里已有图片（"改一下上面的图"、"把这张图做成 …"）→ 图生图编辑。
- 需要把抽象概念变成视觉呈现，或为回答/文档配图。

> **不是本 Skill 的职责**：找互联网上已有的图片（那是 `search_images` 文搜图/图搜图）；解释图片内容（那是视觉模型 / `read_session_file`）。**搜图 ≠ 生图**，先判断用户要的是"找"还是"造/改"。

## 搜图后下载（文搜图/图搜图 → 真实图片文件）

`search_images` 返回的只是链接，沙箱无网无法直接访问。用户要"用图 / 看图 / 分析 / 存下来"时，必须接 `download_external_image`：

- **单张**：`url` + `destination_path`（如 `inputs/images/xxx.png`）。
- **多张**：`urls` 数组（2–8 张）+ `destination_dir`，宿主侧**并行下载**，每张图单独净化、哈希、出来源凭据。
- 下载走审批制：命中统一白名单自动放行，否则弹审批卡，用户批准后模型用相同参数重试。
- 下载成功后返回 `file_id`，后续引用/再编辑以 `file_id` 为准（读图走 `read_session_file`，编辑走 `generate_image` 图生图）。
- 部分失败不中断：结果里有 `failed` 列表，如实向用户汇报失败项与原因。

## 渐进式工具语义（先探索 → 再读取 → 最后生成）

工具调用必须按顺序渐进，禁止跳步、禁止凭记忆描述旧图：

1. **list_session_files**：用户提到"上面的图 / 之前的图 / 这张图"时，**先**列出会话文件，解析出准确的 `file_id`。禁止用训练记忆或文字转述去"还原"一张旧图。
2. **read_session_file**（图生图前必做）：读取源图确认它就是用户指的那张（模型支持图像输入时可看到图），并拿到 `file_id`。
3. **generate_image**：
   - **文生图**：只传 `prompt`（详细、用用户语言的描述），**省略** `source_file_ids`。
   - **图生图**：`prompt` + `source_file_ids=[第 2 步解析到的 file_id]`，让 provider 编辑原图像素；**绝不**重新描述旧图去生成一张"看起来像"的新图。
   - 生成结果返回 `file_id`；后续引用、再编辑、再组合都以它为准。

## 决策要点

- **搜图 ≠ 生图**：找现成图 → `search_images`；造新图 / 改图 → 本 Skill 的 `generate_image`。搜图后要真实文件 → `download_external_image`（多张用 `urls` 并行下载）。
- 尺寸：16:9 → `2048x1152`；9:16 → `1152x2048`；4:3 → `1536x1152`；3:4 → `1152x1536`；不确定 → `auto`。
- 图生图一次最多 4 张源图（`source_file_ids` ≤ 4），且生成模型必须支持图片编辑（如 qwen-image-edit-max / gpt-image-2）。
- 未配置图片生成 Provider 时工具会明确失败；如实告知用户到 Provider 管理启用，不假装生成成功。
- `provider_id` / `model_id` 默认省略（用工作区默认图片模型）；只有用户明确指定或默认不可用时才显式选择。

## 安全与边界

- 生成内容遵守工作区内容策略；不生成违规、侵权或敏感图像。
- 本包是**宿主工具驱动**：无 `scripts/`，不引入沙箱脚本。任何"自己写脚本生成图片"的做法都不属于本 Skill。
- 如实报告失败（provider 未启用、模型不支持编辑、源文件过大等），不编造结果。

## 详细说明

- 工具输入/输出契约与限制：`references/input-output-contract.md`
- 组合路线与意图判别矩阵：`references/best-combinations.md`
- 常见失败与诊断：`references/troubleshooting.md`
