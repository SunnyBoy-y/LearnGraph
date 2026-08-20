---
name: frontend-build-preview
description: 离线创建 Vite/React/Vue 项目、构建静态产物并渲染 PNG/PDF 预览；发布双向交互子应用（含官方 __lgSubapp SDK 埋点与数据分析契约）。
---

# 前端构建与预览

## When to use

- 用户要一个静态页面 / React / Vue 应用，并希望**离线生成可预览的构建产物**。
- 需要在沙箱内 `npm run build` 出 `dist/`，再渲染 PNG/PDF 做视觉验收，或准备发布。
- 需要把页面产物交给 `sandbox_publish_web_app` / `sandbox_publish_file` 分享。
- 用户要"可填写 / 可操作 / 提交后我能看到并针对性指导"的页面 —— 走**双向交互子应用**（本 Skill 后半部分）。

## 决策顺序

1. 有源码：`build_frontend.py` 在项目目录执行构建（Vite/React/Vue/HTML 均可），产物在 `dist/`。
2. 无源码但只有想法：`scaffold_vite.py` 生成最小可构建项目（react/vue/html 模板），再构建。
3. 构建后：`render_preview.py` 把 `dist/index.html` 渲染为 PNG/PDF 视觉验收。
4. 发布：把 `dist/` 打包或用 `sandbox_publish_web_app` 分享（本 Skill 只产出，发布由宿主工具完成）。

---

## 发布为双向交互子应用

当产物是**多文件交互应用**（表单、练习、行程/学习规划器、问卷、自测题）且需要"用户操作 → Agent 回写状态"的循环时，用 `sandbox_publish_web_app` 并携带 `interaction_contract`，让它成为**双向交互子应用**（不是静态预览）。

### 0. 硬性规则（违反即失败）

- **必须使用宿主注入的官方 SDK `window.__lgSubapp`** 上报事件和读取状态；**禁止**手写 `postMessage`、禁止调用 `window.component.event`、禁止自定义 `learngraph:*` 消息协议。SDK 已由宿主在页面加载时自动注入（`__lgSubapp`），无需引入任何外部文件。
- `event_schema` 描述的是**业务字段**（如 `{question_id, selected}`），**绝不包含 `type` 字段**——事件类型由 SDK 的 `emit(type, data)` 第一个参数指定，宿主自动路由。
- 显式提交事件（如 `answer.submitted`）才允许配置 `agent_triggers`；高频埋点（输入、切换、滚动）只能进 `analytics`，**永不触发 Agent**。

### 1. SDK 用法（页面内唯一的数据通道）

```js
// 上报一条语义事件（返回 Promise，persisted ACK 后 resolve）
await __lgSubapp.submit('answer.submitted', { question_id: 'q1', selected: '链地址法' });

// 上报埋点（不触发 Agent）
__lgSubapp.track('field.changed', { field_id: 'q2', filled: true });

// 订阅 Agent 推送的状态（含版本号）
__lgSubapp.onState((state, version) => { /* 渲染反馈 */ });

// 订阅通道状态（ready / persisted / rejected / retrying）
__lgSubapp.onStatus((status, detail) => { /* 页面显示"正在发送/已提交/重试中" */ });

// 触发"数据分析"（内置分析入口，见第 4 节）
__lgSubapp.requestAnalysis('找出反复修改和停留最长的题目');
```

页面**不要**在 SDK 就绪前假装"已提交"：只有 `onStatus('persisted')` 之后才能把按钮状态标绿；`retrying` 显示"正在重试"，`rejected` 显示失败原因。

### 2. 发布流程（推荐：契约外置为文件）

1. `sandbox_validate_web_app(output_root, entry_path)` 校验产物，拿到 `validation_id`。
2. 把契约写入工作区文件 `learngraph.subapp.json`（见第 3 节示例），再 `sandbox_validate_interaction_contract(path)` 校验，拿到通过结果。
3. `sandbox_publish_web_app(validation_id, title, contract_path)` —— **传 `contract_path`，不要把几百行的 JSON Schema 内联进工具参数**（内联极易因转义/截断产生 `invalid_tool_arguments`，且无法给出精确错误位置）。
4. 成功后返回 `subapp_mode:true` 与 `artifact_version_id`，聊天卡片会实例化为隔离 iframe 的双向子应用。
5. 运行中：用 `subapp_observe` 观察事件（返回里含 `session_state_version`）；用 `subapp_patch_state(session_id, state, expected_version)` 推送新状态（`expected_version` 用 `subapp_observe` 返回的 `session_state_version`，乐观锁冲突时重读重试）。

### 3. `learngraph.subapp.json` 契约示例

```json
{
  "event_schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["question_id", "selected"],
    "properties": {
      "question_id": { "type": "string" },
      "selected": { "type": "string" }
    }
  },
  "state_schema": {
    "type": "object",
    "additionalProperties": false,
    "required": ["view", "feedback"],
    "properties": {
      "view": { "type": "string" },
      "feedback": {
        "type": "object",
        "additionalProperties": false,
        "required": ["title", "body"],
        "properties": { "title": { "type": "string" }, "body": { "type": "string" } }
      }
    }
  },
  "agent_triggers": [
    { "event_type": "answer.submitted", "mode": "explicit" }
  ],
  "analytics": {
    "enabled": true,
    "track": ["field.changed", "choice.selected", "step.completed"],
    "summary_events": ["interaction.summary"],
    "privacy": "session"
  }
}
```

约束速查：`event_schema` / `state_schema` 必须是**顶层闭包 object**（`additionalProperties:false`），深度 ≤16、节点 ≤1000、体积 ≤64KiB；禁止外部 `$ref`、`pattern`、HTML/JS 内容字段（`html`/`script`/`srcdoc` 等）；`agent_triggers.event_type` 是小写事件标识（`^[a-z][a-z0-9_.-]{0,119}$`），去重，`mode` 只能 `explicit`；`analytics.privacy` 取 `session|workspace|none`。

### 4. 埋点与数据分析规范

- **事件名字典（稳定语义）**：`app.opened` / `view.changed` / `field.changed` / `choice.selected` / `step.completed` / `hint.requested` / `answer.submitted` / `analysis.requested` / `app.closed`。不要用页面文案当事件名。
- **提交物最小化**：`answer.submitted` 里只放结构化答案；不采集密码、token、cookie、DOM 全量、键盘逐键记录；自由文本只随显式提交发送。
- **内置分析**：页面内调用 `__lgSubapp.requestAnalysis(purpose)` 或用户在卡片点"数据分析"按钮，都会以结构化消息发给 Agent；Agent 用 `subapp_analyze_events` 读取埋点并生成定制指导。普通埋点永不自动触发 Agent。

### 5. 验收清单（发布前自查）

- [ ] 页面只用 `__lgSubapp.*`，无手写 postMessage / `window.component.event`
- [ ] `event_schema` 不含 `type` 字段
- [ ] `agent_triggers` 只列显式提交事件
- [ ] 已运行 `sandbox_validate_interaction_contract` 且通过
- [ ] 发布用 `contract_path`（或极简内联契约）
- [ ] 页面在 `persisted` 前不显示"已提交"

**何时不用契约**：静态展示页面、单文件网页/图片、下载型产物一律不带 `interaction_contract`——它们走静态预览或 `sandbox_publish_file`。

## 脚本索引

| 脚本 | 用途 |
|---|---|
| `scaffold_vite.py` | 生成最小 Vite 项目（react/vue/plain-html） |
| `build_frontend.py` | 在项目目录执行构建（自动选命令） |
| `render_preview.py` | 把 `dist/` 渲染为 PNG/PDF |
| `check_static_assets.py` | 校验 `dist/` 清单与无外链资源 |

## 组合路线

```text
scaffold_vite ──> 项目/ ──build_frontend──> dist/ ──render_preview──> preview.png
已有源码 ──build_frontend──> dist/ ──check_static_assets──> 清单.json
dist/ + lerarngraph.subapp.json ──(宿主 sandbox_publish_web_app contract_path)──> 双向子应用
```

## 安全与限制

- 离线构建：不联网、不执行 `npm install`（依赖已预装进镜像的 `/node_modules`）、不下载字体/CDN。
- 只允许构建工作区内相对路径的项目。
- 页面不得依赖外部资源（CSP/无外链约束）；`check_static_assets` 会校验。
- 构建产物遵守 64MB/256MB/180s 限额；超大 bundle 先精简再构建。
- 双向子应用的事件只走宿主注入的 `__lgSubapp` 通道；不信任任何 iframe 内自建的"上报通道"。

## 详细说明

组合配方见 `references/best-combinations.md`，输入/输出契约见 `references/input-output-contract.md`，常见失败见 `references/troubleshooting.md`。每个脚本的完整用法见 `scripts/*.md`。
