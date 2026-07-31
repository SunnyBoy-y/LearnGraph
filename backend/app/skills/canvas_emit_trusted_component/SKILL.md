---
name: canvas-emit-trusted-component
description: Teach the Agent how to call canvas_emit_trusted_component with valid channel-A props so UI cards render instead of degrading.
---

# Canvas 可信组件发布（channel A）

## When to use

- 用户要在对话里看到**可交互表单 / 选择题 / 填空 / 天气卡 / 指标卡 / 图片框**
- 你需要把结构化 UI 写入助手消息流（而不是用 Markdown 伪表单或 HTML）
- 工具 `canvas_emit_trusted_component` 已出现在本回合 tool 列表中

不要用本 skill 去写任意 React/HTML；那是 `canvas_emit_magic_card`（通道 B，需在同一次调用里给出完整自包含的 `preview_html`）。

## Instructions

1. **先选组件类型**（`component_type` 必须是下列之一）：
   - 交互：`option_group` | `single_choice` | `multiple_choice` | `fill_blank` | `short_answer_table`
   - 展示：`weather_card` | `metric_card` | `image_frame`
2. **不要传 `null`**。可选字段要么省略，要么给真实字符串/数组/布尔值。
3. **选项类必须带非空 `options`**（至少 1 项；每项 `id` + `label`）。
4. **填空类**至少要有可读 `title`/`prompt`；后端会补 `blank_ids`，但你最好显式传。
5. 调用成功后，宿主会把 `component` Message Part 插入对话；**不要**再把同一份 JSON 用 Markdown 贴一遍。
6. 若工具返回 `error` / `component_data_schema_mismatch`，根据 `message` 修正 props 后重试一次；不要假装已经渲染成功。

## 工具调用契约

```json
{
  "component_type": "<见上>",
  "props": { },
  "component_id": "optional-stable-id",
  "allowed_events": ["submit"],
  "schema_version": "1.0"
}
```

`props` 是**唯一数据面**：不要把业务字段塞到 `component_type` 外层。

## 各类型最小合法 props

### `single_choice` / `multiple_choice` / `option_group`

```json
{
  "title": "你更希望怎样验收这次学习？",
  "description": "单选一项后继续",
  "options": [
    { "id": "project", "label": "完成一个小项目" },
    { "id": "explain", "label": "能够清楚讲解" }
  ],
  "allow_custom": true,
  "allow_skip": true,
  "submit_label": "确认并继续"
}
```

说明：

- `title` 与 `prompt` 二选一即可（后端会互相同步）。
- `options` / `choices` 二选一；每项必须有非空 `id`、`label`。
- **禁止** `options: []` 或 `description: null`。

### `fill_blank`

```json
{
  "title": "请补全 ACID 中的 A",
  "prompt": "ACID 中的 A 代表 ____",
  "placeholder": "Atomicity / 原子性",
  "multiline": false,
  "submit_label": "提交",
  "blank_ids": ["answer"]
}
```

### `short_answer_table`

```json
{
  "title": "简答题",
  "columns": ["问题", "你的回答"],
  "rows": [["为什么需要索引？", ""]]
}
```

### `weather_card`

```json
{
  "title": "杭州明日天气",
  "location": "杭州",
  "condition": "多云",
  "temperature_c": 27,
  "high_c": 29,
  "low_c": 21,
  "summary": "适合户外轻量复习",
  "unit": "C",
  "actions": [
    { "id": "create_plan", "label": "生成明日学习计划", "event": "create_plan" }
  ]
}
```

`location` / `condition` / `temperature_c` 必填。`allowed_events` 应包含 action 的 `event` 或 `id`。

### `metric_card`

```json
{
  "title": "今日学习指标",
  "description": "来自当前目标进度",
  "metrics": [
    { "id": "mastery", "label": "掌握度", "value": "62%", "hint": "近 7 日" },
    { "id": "reviews", "label": "待复习", "value": 3 }
  ]
}
```

### `image_frame`

```json
{
  "title": "示意图",
  "alt": "B+ 树结构示意",
  "status": "placeholder"
}
```

`status` 用 `placeholder` | `ready` | `failed`（或前端别名 `queued` / `completed`）。有图时再加 `src` 并设 `ready`/`completed`。

## Steps

1. 判断场景是否真的需要 UI 控件；纯讲解用 Markdown 即可。
2. 选最小组件类型（表单优先 `option_group` / `fill_blank`，天气用 `weather_card`）。
3. 按上面模板组装 **完整非空** props。
4. 调用 `canvas_emit_trusted_component`；需要像素/主题时先 `canvas_get_render_contract`。
5. 用一两句自然语言说明卡片用途；不要复述原始 JSON。
6. 等待用户在卡片上的提交事件（`allowed_events`），再继续对话。

## Examples

- **User:** 「用前端控件渲染天气」  
  **Agent:** 检索/推算天气事实 → `canvas_emit_trusted_component` (`weather_card`) → 简短说明。

- **User:** 「让我选一种验收方式」  
  **Agent:** `single_choice` / `option_group`，`options` 至少两项，带 `title`。

- **User:** 「做一个填空练习」  
  **Agent:** `fill_blank`，带 `title`/`prompt` 与 `blank_ids`。

## Notes

- 通道 A 是**声明式可信渲染**；非法数据会在前端显示「组件已安全降级」，并露出 JSON，不会执行脚本。
- 通道 B（`canvas_emit_magic_card`）在隔离沙箱 iframe 里执行内联 HTML/CSS/JS，必须一次提交全部源码且不能依赖任何网络资源；**不要**拿它替代表单。
- 本 skill 只注入指令，不会注册新 function tool；实际工具名仍是 `canvas_emit_trusted_component`。
