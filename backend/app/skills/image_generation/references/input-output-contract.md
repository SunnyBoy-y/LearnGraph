# 工具输入 / 输出契约

> 宿主 Agent 工具由 `AgentToolRuntime` 提供（`backend/app/services/agent_runtime.py`）。本文件固化 `generate_image` 及配套工具的输入输出契约与硬限制，供 Skill 决策时对照。

## 1. `generate_image`（文生图 / 图生图）

### 参数

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | string | ✅ | 1–2000 字符。用用户的自然语言写详细描述；图生图时描述"改什么"，不是复述原图。 |
| `title` | string | – | 可选展示标题（≤120 字符），缺省取 prompt 前 80 字符。 |
| `provider_id` | string | – | 指定图片生成 Provider；省略用工作区默认。 |
| `model_id` | string | – | 指定已启用且支持图片生成的模型；省略用默认图片模型。 |
| `size` | enum | – | `auto` / `2048x2048` / `2048x1152`（16:9）/ `1152x2048`（9:16）/ `1536x1152`（4:3）/ `1152x1536`（3:4）。缺省 `auto`。 |
| `source_file_ids` | string[] | 图生图✅ | 0–4 个会话内图片 `file_id`。**图生图必传**；文生图省略。 |

### 硬限制（宿主强制，违反即 422）

- `prompt` 去空白后必须 1–2000 字符（`MAX_AGENT_IMAGE_PROMPT_CHARS`）。
- `size` 必须是上述枚举之一。
- `source_file_ids` 必须全部是**本工作区、storage_status=stored 的图片文件**；单文件 ≤10 MB（`AGENT_IMAGE_INPUT_MAX_BYTES`）、像素 ≤4000 万；最多 4 张（`MAX_IMAGE_EDIT_SOURCES`）。
- 图生图时目标模型必须声明 `supports_image_edit`，否则返回 `image_edit_model_unsupported`。

### 返回（成功）

```json
{
  "generated": true,
  "generation_id": "<task id>",
  "file_id": "<durable file id>",
  "mime_type": "image/png",
  "title": "...",
  "prompt": "...",
  "source_file_ids": []
}
```

- `file_id` 是**可持久引用**的会话文件标识：后续 `read_session_file`、再编辑（`generate_image.source_file_ids`）、会话文件列表都能用它。
- 结果以 `image` 类型的 message part 落库（含 `width`/`height`/`aspect_ratio`），聊天界面直接渲染。

### 失败形态（宿主返回错误码）

| 错误码 | 含义 | 处置 |
|---|---|---|
| `image_provider_unavailable` | 未配置可用图片生成 Provider | 告知用户到 Provider 管理启用（openai_images / qwen-image-edit-max / wanx 等） |
| `image_model_unavailable` | 指定的 provider/model 未启用 | 检查 provider_id/model_id 拼写与启用状态 |
| `image_edit_model_unsupported` | 图生图但模型不支持编辑 | 换用 qwen-image-edit-max / gpt-image-2 等编辑模型 |
| `invalid_tool_arguments` | 参数不满足上述硬限制 | 按契约修正后重试一次 |
| `image_generation_failed` / `image_generation_incomplete` | provider 失败或流中断 | 如实报告，不重试超过一次 |

## 2. `list_session_files`（探索层）

- 用途：把"上面的图 / 之前的文件"解析成准确 `file_id`；默认当前会话，只有用户明确要求时才传其他 `session_id`。
- 返回每项含 `file_id`、`filename`、`mime_type`、`size_bytes`、`origin`（`user_attachment` / `generated_image`）、`is_image`。
- 限制：最多返回最近 50 个文件（`SESSION_FILE_LIST_MAX`）；跨会话访问会记录审计。

## 3. `read_session_file`（读取层）

- 用途：读取文件内容。图片：模型支持图像输入时把图附加进对话（`image_attached=true`）；否则返回尺寸/类型并提示可用 `generate_image.source_file_ids` 编辑或转 `workspace` 目标给沙箱。
- 参数 `target`：`context`（默认，回读到对话）/ `workspace`（物化到会话工作区 `inputs/`）。
- 限制：图片 ≤10 MB、像素 ≤4000 万；文本 ≤40 000 字符。

## 4. 关联但**不属于**本 Skill 的工具

| 工具 | 归属 | 说明 |
|---|---|---|
| `search_images` | 文搜图/图搜图 Skill（`qwen_image_search` provider） | 在互联网上**查找**图片（文搜图 / 图搜图），返回 `title`+`url` 列表 |
| `analyze_image` | 视觉模型工具 | 用独立视觉模型**理解**一张图片 |
| `search_web` | 网页搜索 | 文本检索，不产出图片 |
