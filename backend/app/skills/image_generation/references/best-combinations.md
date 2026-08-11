# 组合路线与意图判别矩阵

## 一句话判别

| 用户意图信号 | 用哪个工具 |
|---|---|
| "找一张 …的图 / 搜图 / 有没有 …的图" | `search_images`（文搜图/图搜图，互联网查找） |
| "画一张 / 生成 / 做一张 / 配图 / 插画" | `generate_image`（文生图） |
| "把这张图改成 / 基于这张图 / 在上面加 …" | `generate_image` + `source_file_ids`（图生图编辑） |
| "这张图里是什么 / 描述这张图" | `analyze_image` / `read_session_file`（视觉理解） |
| "帮我搜一下这个话题"（纯文本） | `search_web` |

> 核心区分：**找现成图 = search_images；造新图/改旧图 = generate_image**。两套工具可以组合（先搜到参考图，再让模型生成类似风格），但语义不能混用。

## 组合路线

### A. 文生图（从零生成）

```text
用户描述需求
  └─> 直接 generate_image { prompt: 详细描述 }
        └─> 返回 file_id ──> 展示；后续可再编辑（路线 B）
```

### B. 图生图（基于已有会话图片编辑）—— 渐进三步

```text
用户："改一下上面的图"
  └─1─> list_session_files ──> 解析出候选 file_id
  └─2─> read_session_file { file_id } ──> 确认就是用户指的那张（可看图）
  └─3─> generate_image { prompt: 改什么, source_file_ids: [file_id] }
        └─> 返回新 file_id（原图不变，产生新图）
```

### C. 搜图 → 生图（参考风格创作）

```text
search_images { query } ──> 得到参考图 URL/标题
  └─> generate_image { prompt: "参考 <描述> 的风格生成 …" }   # 不传 URL 作 source（source_file_ids 只接受会话文件）
```

> 注意：`search_images` 返回的是互联网 URL，`generate_image.source_file_ids` 只接受**会话内** `file_id`。若要把网络参考图作为图生图源，先由用户上传/保存为会话文件再编辑。

### D. 生图 → 复用（延续性）

```text
generate_image ──> file_id（持久）
  └─> 后续轮次：read_session_file / generate_image(source_file_ids=[file_id]) 均可复用
```

## Provider / 模型选择建议

- **文生图 + 图生图都支持**：qwen-image-edit-max（DashScope）、gpt-image-2（OpenAI）——编辑能力需 `supports_image_edit`。
- 纯生成模型（如部分 wanx 快照）不支持 `source_file_ids`：图生图会报 `image_edit_model_unsupported`，此时应提示换编辑模型，而不是降级成文生图。
- 工作区默认图片模型未配置时，先走 Provider 管理选择，再调用工具；不要自行假设默认模型存在。

## 与文搜图/图搜图 Skill 的衔接

- 用户在对话里"搜了图"之后说"照着它生成一张"——先 `search_images` 拿到参考语义，再 `generate_image` 创作；两者是不同 provider 通道（`qwen_image_search` vs `image_generation`），互不替代。
- 本 Skill 只管**生成**；`search_images` 的用法见文搜图/图搜图相关 Skill 文档。
