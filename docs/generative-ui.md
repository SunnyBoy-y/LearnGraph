# 文档原生 Generative UI

LearnGraph 继续使用现有 Markdown → 消息 Part → 有序 SSE → 历史记录链路。普通回答通过 Streamdown 渲染；结构化内容由 SUIP 注册组件处理，不运行模型生成的 HTML、JavaScript、CSS 或 React。

## 排版与组件

- 正文阅读宽度 760px，H1/H2/H3 为 26/21/17px；保留已有正文、数学、代码和引用渲染。
- `props.density`：`compact`、`comfortable`、`editorial`。`props.width`：`reading`、`full`。均由前端设计参数控制。
- `document` 内可组合 `metric`、`chart`、`table`、`timeline`、`diagram/graph`，以及已有章节、图片、分栏、标签页、折叠和注册计算控件。
- `props.toc=true` 或 editorial 密度启用折叠目录。目录只索引显式 heading 节点；不会从任意文本推断文档结构。
- 完成的文档支持复制与 Markdown 导出。导出图表数据、关系文本和来源信息，不是截图、PDF 或可执行页面。

### 编辑式列表

IconFeatureList（`feature_list`）用于并列概念或特性，StepList（`step_list`）用于编号步骤，StatusList（`status_list`）用于已经提供的状态。它们复用 RailList 排版：左侧固定宽度视觉引导，右侧标题、Markdown 说明和补充说明；图标没有背景装饰，不画贯穿竖线，不包卡片。

沿用 document 的平面树和现有 upsert/delete，不另建 item.add 传输通道：

```json
{"upsert":{"blocks":[
  {"id":"modes","type":"feature_list"},
  {"id":"fast","parent_id":"modes","type":"rail_item","icon":"zap","label":"极速模式","text":"直接流式输出内容。","caption":"适合普通问答。"},
  {"id":"agent","parent_id":"modes","type":"rail_item","icon":"brain-circuit","label":"Agent模式","text":"使用工具获取数据。"}
]}}
```

列表节点可以用 label 提供可见标题。rail_item 必须提供非空 label，text 与 caption 可随稳定 ID 增量补充；一组最多12项，通常2至6项。item_status 允许 pending/active/completed/error，与内容生成状态 state 分开。状态列表不能把计划当作已执行结果。

图标仅可来自前后端一致的静态白名单，不接受 URL、SVG、JS、CSS 或任意图标名。极速声明、Agent工具流、SSE、历史恢复及 Markdown 导出使用同一份协议。列表项文字可在完整工具参数提交前出现；中断后保留已接收说明，停止显示仍在生成的状态。

`frontend/editorial-preview.html` 提供三种列表、实施路线、阶段与密度切换；旧排版视图是同内容的 Markdown 重建，明确标记为重建。截图与校验记录位于 `audit/editorial-lists/`。

`backend/app/services/genui_catalog.py` 为模型提供组件能力、适用/不适用条件、正反例与视觉预算。普通问答不增加分类或布局模型调用；提示词策略不是对模型选型质量的统计保证。

## 流式边界

保持 `ui.mount → ui.patch → ui.complete`，Part ID 和现有事件序列不变。工具参数预览沿用已存在的 `preview.base` 和正式 revision；原始、不完整的 JSON 不交给浏览器执行。

图表可先没有 columns 和 rows，作为占位。未提交的 chart/metric 参数预览不替换已提交数值；文档内的新数值块保持 pending。完整命令通过后端 schema 校验后才发布数值。可信 data_ref 的整个集合可由服务端确定性读取。新增正文节点和后续文字不等待数值图表完成。

完成但没有数据的组件显示“暂无可显示的数据”；中断的文档保留已生成内容。目录默认折叠，以免新增章节时将已展示正文向下挤开。

## 数据引用

chart/table 的原始记录可由受限映射转为 UI 行：

```json
{
  "props": {
    "title": "月活趋势",
    "columns": [{"key": "active", "label": "月活用户"}],
    "unit": "人",
    "time_range": "5月–10月",
    "data_ref": {
      "part_id": "已保存的工具PartID",
      "path": ["records"],
      "label_key": "month",
      "value_keys": {"active": "users"}
    }
  }
}
```

`path` 从工具记录的 `output` 开始，只支持最多六级字段名/数组索引，不支持表达式。没有 value_keys 时，引用的集合须已经是 rows/items 结构。metric/timeline 可直接引用 items 集合。

服务端限定同一 workspace、同一会话、当前消息版本、已完成的工具记录；不接受输入参数或任意 URL 作为数据源。引用集合覆盖模型提供的相应数据，后续 patch 不能偷偷添加模型行；脱离来源必须显式 `data_ref=null`。有合适结构化集合的工具结果会附上可用引用，随工具历史保存。工具自身结果是否真实可靠仍取决于该工具和数据源，此机制不独立核验外部事实。

直接由用户提供或模型根据已知数据整理的数值仍可使用，并通过 source_note 注明依据。虚构示例须 `data_kind=example`，在组件和导出中可见；系统不会将模型写的来源说明冒充为已验证的数据引用。

## 交互状态与后续修改

图表类型、最近3/5/10项、隐藏序列、表格筛选/排序、标签页和注册计算控件的参数保存在当前浏览器；按用户、workspace 和 Part ID 隔离，最多128块及256KB。格式或范围非法、存储不可用时安全回退。它不等于服务端或跨设备偏好同步。完整答案和 UI 数据继续由原有消息数据库保存。

点击“让 AI 解释”时，chart/table 会带上当前受限视图状态。服务端从保存的快照重建数据，并按图表显示范围/隐藏序列或表格筛选词提供上下文；客户端不能用该请求提交替换数据。

文档内的图表、表格、指标和关系节点也能发起解释：请求绑定父 Part ID、父 revision 与目标文档节点 ID，服务端只提取该节点已保存的 visual，而不是接受客户端传来的图表数据。未完成的父文档不允许提交追问。

Agent 可针对历史块提交：

```json
{"op":"revise","block_id":"原块ID","base_revision":2,"props":{"chart_type":"bar"}}
```

revise 只能选择同一会话内已结束回答的已完成块，须匹配最新 revision，不带 component。它在本轮回答中复制并修订该块，保留旧回答与旧事件，随后继续 patch/complete。它不原地改写旧消息，也不自动改变用户已保存的本地筛选选择。极速模式继续使用已有的受限 inline 文档声明，可在新回答中给出更新后的呈现。

## 验证与预览

`frontend/genui-preview.html` 使用实际 StreamUI、RichDocument 与 Markdown 渲染器，包含三种生成阶段、阅读密度、六类核心可视化及交互教学。展示数据明确为虚构示例；阶段切换是确定性快照，不是模型速度录制。

回归覆盖源数据作用域/字段映射、数值预览、历史修订、真实聊天 HTTP/SSE/数据库历史、目录、导出、状态恢复与非法数据回退。双主题桌面/390px浏览器检查截图保存在 `audit/genui-document/`。

部署需要一起更新前后端。此次本地实现与预览不等同于生产服务重启、真实模型质量评测、全部专业场景或跨设备状态同步已验收。


### Editorial Data UI 与确定性简化

组件渲染前使用 `presentation-simplifier.ts` 作纯视图投影，不改写保存的 snapshot、稳定 ID、revision 或 SSE 事件，也不增加 LLM 调用。

- Editorial：指标按首项核心、次项次要、其余背景的比例呈现；小表格、步骤、事件和简单流程直接融入文档。说明默认可读。
- Data：图表优先趋势、终点及区间变化。探索入口默认折叠；缺失值仍断开折线，零或负起点不计算容易误读的百分比。
- Canvas：只有分支、环、多实体或更大关系才保留搜索、缩放和重置。初始/重置使用实际节点范围适配，不使用固定大画布。
- 表格 8 行以内默认静态，9 行起提供搜索排序。`props.interaction=auto|static|explore` 在前后端均受 schema 校验；static 表格忽略旧筛选/排序但不覆盖已保存偏好，explore 明确启用操作。图表 static 不显示探索入口，explore 默认展开，auto 默认收起。
- 最终文档中，同一父节点下相邻 StepList 与 Graph/Diagram，只有链路顺序、所有标签和说明完全相同且没有额外边标签、分组、来源、时间说明或交互探索要求时，才仅展示步骤；流式预览不去重。相同投影用于 Markdown 导出，原始历史完整保存。模糊语义重复由 Presentation Engine 的提示约束避免，前端不通过关键词猜测删除互补内容。

本地真实渲染器预览：`/genui-preview.html`，可选择完整报告、简单流程、分支关系及生成阶段、密度、深浅主题。开发预览未加入生产路由。对应验证日志与改版前截图位于 `audit/editorial-data-ui/`；不代表生产部署或真实模型的排版质量验收。


目录使用注册的 `DocumentTOC`：桌面双列、窄屏单列，章节编号和当前章节状态统一呈现；链接最小点按高度44px，原生锚点保留 URL 与浏览器返回行为。折叠偏好按原有消息作用域保存，流式章节追加不重置；IntersectionObserver 更新可视章节，键盘跳转可直接聚焦标题。没有章节时不显示空目录。目录和指标的列表重置优先于普通正文样式，深色文档保持透明背景。

目录/手机实页验证与深浅截图保存在 `audit/genui-toc-mobile/`。开发预览入口复用生产入口的 KaTeX 样式，避免预览中的公式视觉重复；不影响生产数学渲染路径。


### 正式聊天集成与真实模型验收

正式入口仍是 MessagePartRenderer 的 `ui` 分支，调用同一 StreamUI/RichDocument/DocumentTOC；SSE、历史消息和消息版本选择复用同一持久 Part，不提供第二套生产渲染器。ui_action 经现有 ChatPage 绑定消息 Part 与文档 block revision。

模型侧的工具 JSON Schema 现在展开 UIProps 与 EmbeddedVisual，明确 props/rows/items 的嵌套关系和每个集合的约束。极速声明在完整 JSON 收到后可做有界语法兼容：将已知视觉参数移到 visual、将顶层列定义移到 visual.props、把确定的单行指标转为 items、把嵌套步骤条目展开为带 parent_id 的平面树。轴说明保留为描述，指标的独立单位保留在标签中；不修改测量值，不引入执行能力。未知字段、冲突内容、单位冲突和多行歧义指标仍拒绝，转换后的命令继续通过原有严格校验。兼容处理不增加模型调用，也不重写已保存历史。

真实 deepseek-flash 的智能体回答验证了 32 次预览、mount、两次 patch 与 complete，单一持久 Part 保存 22 个文档节点；极速回答在保留旧版本后成功提交原生报告。正式 UI 刷新前后目录锚点、组件、指标、数据点、步骤与表格投影一致，v1/v2 可往返查看。独立声明复现与早期失败样本保留在 audit/formal-genui，验证记录不作为模型未来输出必然正确的保证。
