# 常见失败与诊断

按宿主返回的错误码 / 现象分类，附处置步骤。所有失败都应如实呈现给用户，不编造成功结果。

## 1. 工具不可用 / 未配置 Provider

| 现象 | 原因 | 处置 |
|---|---|---|
| `image_provider_unavailable` / "No usable image generation Provider is configured" | 工作区未启用图片生成 Provider | 引导用户到 Provider 管理新增/启用（openai_images、qwen-image-edit-max、wanx 等），配置 base_url + API Key + 默认模型后重试 |
| 工具列表里根本没有 `generate_image` | `_image_available` 为假（provider 缺失、非 remote、或未授权） | 同上；不要假装调用了工具 |

## 2. 图生图失败

| 现象 | 原因 | 处置 |
|---|---|---|
| `image_edit_model_unsupported` | 当前图片模型不支持编辑（`supports_image_edit=false`） | 换 qwen-image-edit-max / gpt-image-2 等编辑模型；**不要**降级成"重新描述生成一张新图" |
| `invalid_tool_arguments`：source_file_ids 超 4 个 | `MAX_IMAGE_EDIT_SOURCES=4` | 让用户选最多 4 张关键图，或分多次编辑 |
| `invalid_tool_arguments`：引用非图片文件 | `source_file_ids` 必须指向图片 | 先 `list_session_files` 核对 `is_image`，再取 `file_id` |
| `file_unavailable` / storage 未就绪 | 源文件不在对象存储 | 稍后重试；若持续失败，告知用户重新上传 |
| 图生图输出与源图无关 | 模型把"改图"当成了"凭描述生成" | 确认 prompt 是"改什么"，且 `source_file_ids` 确实传入；必要时重试一次 |

## 3. 文生图质量问题

| 现象 | 处置 |
|---|---|
| 图不对题 / 元素缺失 | 细化 prompt：主体、背景、风格（写实/插画/3D/扁平）、构图、色彩、文字要求 |
| 尺寸不对（要 16:9 出的是方形） | 显式传 `size`：`2048x1152`(16:9) / `1152x2048`(9:16) / `1536x1152`(4:3) |
| 文字渲染错乱 | 图片模型的文字能力有限；减少 prompt 中的精确文字要求，或拆成短句 |
| 生成内容疑似违规/侵权 | 不重试；如实说明内容策略限制 |

## 4. 会话文件解析问题

| 现象 | 处置 |
|---|---|
| 用户说"上面的图"但找不到 | 先 `list_session_files` 核对当前会话；跨会话引用需用户明确指定会话 |
| `read_session_file` 返回 `image_attached=false`（模型不支持图像输入） | 仍可图生图：用其 `file_id` 传 `source_file_ids`；解释图片内容则用 `analyze_image`（独立视觉模型） |
| 图片 >10 MB / >4000 万像素 | `read_session_file` 会转 `workspace` 目标或报限制；提示用户压缩后重传 |

## 5. 边界与禁止事项

- **禁止**用 `search_images` 的 URL 冒充 `generate_image.source_file_ids`（后者只接受会话内 `file_id`）。
- **禁止**凭训练记忆"还原"用户上传的旧图——必须先 `list_session_files` + `read_session_file` 拿到真实 `file_id`。
- **禁止**自己写脚本/调用外部图像 API 代替宿主 `generate_image`；本 Skill 无 `scripts/`，生成只能走宿主工具。
- 单轮失败只重试一次，连续失败转人工处理。
