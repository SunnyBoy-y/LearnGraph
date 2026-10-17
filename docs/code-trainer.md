# 编程判题训练器

已接入对话的可信组件注册表：`canvas_emit_trusted_component(component_type="code_trainer")`。
Python、JavaScript、TypeScript 在浏览器执行；其他语言仅将代码、题面、输入和公开样例组织成 prompt，经当前对话模型分析。
没有服务端 Runner、执行队列、隐藏测试或正式成绩。

```json
{
  "component_type": "code_trainer",
  "schema_version": "1.0",
  "allowed_events": ["submit"],
  "props": {
    "title": "两数之和",
    "description": "读取两个整数，输出它们的和。",
    "language": "python",
    "starter_code": "a, b = map(int, input().split())\nprint(a + b)",
    "stdin": "2 3",
    "time_limit_ms": 2000,
    "tests": [{"name": "正整数", "stdin": "2 3", "expected_stdout": "5"}]
  }
}
```

`language` 支持任意长度不超过40的语言标识；`py/js/ts` 自动归一化。代码最多16000字符、输入最多4000字符，最多12个公开样例；数据还受已有可信组件64 KiB总量限制。前后端分别验证契约。代码始终作为字符串渲染，包含 HTML 标签也不进入宿主 DOM。

## 执行和交互

- Python：[Pyodide](https://pyodide.org/en/stable/usage/api/js-api.html)，支持 `input()`、`sys.stdin.read()` 和内置标准库。
- JavaScript：[QuickJS WASM](https://github.com/justjake/quickjs-emscripten)，提供 `stdin` 字符串、`readline()`、`print()`、`console.log/info/warn/error()`。不提供 Node.js、npm、定时器、DOM 或网络 API。
- TypeScript：[esbuild-wasm](https://esbuild.github.io/api/#browser) 去除类型，再由 QuickJS 执行；转换不等同于 TypeScript 类型检查。
- 每次运行和每个样例创建新的 iframe / Worker，避免全局变量、模块或文件污染下一次运行。运行和样例测试完全由点击触发。
- 运行输出分 stdout/stderr；测试按行忽略行尾空白、统一 CRLF，保留行首和内部空格。仅正常完成且输出匹配才计为样例通过。
- 运行超时为100–5000ms，默认2000ms；加载另设45秒上限。外层超时、停止、卸载都会销毁 iframe 及其 Worker。强制终止时不保留尚未回传的输出。
- JS/TS 的 QuickJS 堆限制为64 MiB，栈为512 KiB。stdout/stderr 共16000字符上限。Python 没有可承诺的浏览器进程硬内存限额，不能当作生产服务端隔离或完整资源配额。
- 编辑器使用本地打包、按需加载的 Monaco：彩色语法、行号、括号匹配、折叠、多光标、查找、撤销/重做、JS/TS 语言服务和 Python 内置函数补全。Tab 缩进/接受建议，Ctrl+Space 调出补全，Ctrl+F 查找。“智能补全”开关关闭时仍保留语言关键词，例如 `imp` 提示 `import`。左右分屏为左侧题目和公开样例、右侧单个代码编辑器，两侧独立滚动；切换布局保留代码和撤销历史，支持全屏、退出全屏和横纵滚动条。全屏使用顶层弹出层，避免聊天容器的变换/裁剪限制。
- 每语言独立草稿、重置、公开样例和错误详情仍保留。消息 Part ID 范围内的草稿与结果只保存在当前页面的有界内存缓存，刷新后恢复题目初始代码。
- “请 AI 辅导”与“交给 AI 分析”均走现有鉴权、模型、预算和消息发送流程。本次请求显式关闭 Agent、Goal 和联网工具，跳过服务端沙箱预检，不改变输入框的常规模式。模型收到代码作为待分析数据，并被要求不把推演称为真实判题。过长上下文显式标记裁剪。

## 隔离边界

宿主只加载打包的固定运行时资源，不把用户代码交给主页面 `eval` 或主站同源 Worker。
实际执行链：`sandbox="allow-scripts"` 的不透明来源 iframe → Blob Worker → WASM。
iframe 不开放 `allow-same-origin`；CSP 设置 `default-src 'none'`、`connect-src 'none'`、`worker-src blob:`，禁止网络、子页面、表单与外部脚本。没有父页面文件、凭据或工具桥。
消息必须来自当前 iframe，来源为 `null`，且匹配每次运行的随机通道；回传结果再次通过有界 Schema 验证。

Pyodide 的 WASM、标准库和锁文件先以字节交给 Worker；初始化 fetch 只读取这份内存映射，绝不转发 URL。Python 的 `js` 模块使用空对象，CSP 仍是浏览器层的网络边界。运行时本身需要 WASM 和动态求值权限，因此这不是“禁用 eval”的方案，也不依赖危险代码关键字过滤。
本地浏览器结果可被终端用户改写，仅用于训练，不作为受信任的判题凭证。

## 构建和验证

`npm run dev` / `npm run build` 自动从 lockfile 中的本地依赖生成 `public/code-trainer/`，生产构建复制到 `dist/code-trainer/`；不依赖运行时 CDN。生成物已忽略，无需提交约27 MiB的 WASM/标准库。

```powershell
cd frontend
npm run dev
# 打开 /code-trainer.html：独立演示页，只显示待发送 prompt，不调用模型
npm run test -- src/components/chat/code-trainer.test.tsx src/components/chat/trusted-renderer.test.tsx src/components/chat/stream-ui.test.tsx --pool=threads --no-cache
npm run typecheck
npm run build
# 已启动 Vite 后，使用本地 Playwright；可用环境变量覆盖 URL 和 Chromium 路径
$env:CODE_TRAINER_URL='http://127.0.0.1:5205/code-trainer.html'
node scripts/test-code-trainer-browser.mjs
cd ../backend
.\.venv\Scripts\python.exe -m pytest tests/unit/test_code_trainer.py tests/unit/test_stream_ui.py -q
```

首次实现时，独立演示的 Chromium 23项、前端24项、后端27项回归通过。以下先记录独立环境验收，文末记录主应用部署与复验。

## 真实模型与生产页面验收（2026-10-15）

使用独立容器 `learngraph-code-trainer-validation`（18003）和 SQLite 数据库副本，复用副本内现有 **DeepSeek / deepseek-flash** 配置。生产前端构建运行于5208，开发验收服务为5207；未替换成 Stub，也未模拟模型接口。演示测试身份只在独立数据库中启用，原数据库保持不变。

- 实际会话：`f6e93546-d680-4c02-a710-eb1919eac80c`，工作区 `demo-workspace`。用户从正式输入框发送要求，模型调用一次 `canvas_get_render_contract` 和四次 `canvas_emit_trusted_component`，生成 Python/JavaScript/TypeScript/Java 四个可交互训练器。生成耗时18170ms。
- 在真实生成的前三个控件中将输入改为 `8 3`，均真实输出 `11`；每种语言三个公开测试均通过，共9/9。
- 在同一对话中验证：关闭智能补全后输入 `imp` 仍出现 `import`，Tab 接受补全；Tab 缩进；左侧题目和三个公开样例、右侧单个编辑器；布局切换保留代码，分屏下编辑与撤销；全屏及退出；语法着色。独立浏览器测试还验证 Python `print` 内置函数补全及横纵滚动条。修正分屏后的完整复验共7项全部通过。
- 点击 Java 控件“交给 AI 分析”，经正式消息接口发送题目、Java代码、输入与样例。请求 `agent_mode=false`、`goal_mode=false`、`web_search=false`，首次回复耗时4631ms，分屏修正后的复验回复耗时6267ms。回复明确写明“AI推演”“未真实执行”，给出三个样例的推演结果，没有工具调用。
- 生成及两次 Java 分析回复的 `provider_trace` 均为配置的真实远程Provider、`model_id=deepseek-flash`、`remote_capability=true`、`usage_is_estimate=false`，并有非零输出token记录。这里证明使用了现有远端模型链路，不对供应商内部模型身份作额外认证。
- 验收页面没有未捕获的浏览器错误。控件、题目和AI回答可以从持久化历史重放。

真实对话暴露并修复了两处独立演示没有覆盖的问题：主站 CSP 缺少 `worker-src 'self' blob:` 导致执行Worker被阻止；多个编辑器同时挂载时 Monaco 部分服务加载过晚导致 CodeLens/DnD 服务缺失。执行 iframe 内仍然保持 `connect-src 'none'` 与不透明来源隔离。

完整请求、SSE、消息、Provider trace、断言及截图保存在本地忽略目录 `backend/test-results/code-trainer-live/`，不包含登录token、API密钥或供应商密文。可复跑脚本为 `frontend/scripts/test-code-trainer-live.mjs` 和 `frontend/scripts/test-code-trainer-editor-browser.mjs`。生产构建、相关文件类型检查与lint、24项前端回归和27项后端回归通过。全量类型检查也已通过。

```powershell
cd frontend
# 现有完整验收会话可继续复用；不设 SESSION/CONTINUE 则创建新会话并再次调用真实模型生成
$env:CODE_TRAINER_LIVE_URL='http://127.0.0.1:5208'
$env:CODE_TRAINER_SESSION='f6e93546-d680-4c02-a710-eb1919eac80c'
$env:CODE_TRAINER_CONTINUE='1'
node scripts/test-code-trainer-live.mjs
node scripts/test-code-trainer-editor-browser.mjs
```

本地验收截图：Monaco训练器（`code-trainer-ide.png`，不随仓库分发）。
本地验收截图：左题目右代码分屏与全屏（`code-trainer-ide-fullscreen.png`，不随仓库分发）。

真实对话分屏修正前后（均为1500×1120浏览器截图）：

本地验收截图：修正前：两个代码视图（`code-trainer-chat-ide-before.png`，不随仓库分发）。
本地验收截图：修正后：左题目与样例、右代码（`code-trainer-chat-ide.png`，不随仓库分发）。

本地验收截图：浅色编程训练控件（`code-trainer-light.png`，不随仓库分发）。
本地验收截图：深色编程训练控件（`code-trainer-dark.png`，不随仓库分发）。

## 实际主应用重启后复验（2026-10-15）

主入口 `http://127.0.0.1:18000` 的当前镜像已包含训练器代码。逐文件核对容器内 `code_trainer.py`、`components.py`、`canvas_cards.py`、`agent_runtime.py` 与工作区一致；生产前端含 Monaco、运行时资源、Worker CSP 和左题目右代码布局。重启 `learngraph-app-1`，启动时间为北京时间21:13:38，健康检查返回 `healthy`，`/api/v1/health` 返回 `status=ok`。

此次直接使用应用内已登录的 `demo007`，从正常新对话输入框发送自然语言请求，经正式模型选择菜单使用现有 DeepSeek / deepseek-flash，没有复制数据库、快捷登录、注入登录状态或模拟模型响应。主数据库 `/data/learngraph.db` 持久化的新会话为：

[打开真实对话与训练器](http://127.0.0.1:18000/w/6e37956a-2dd4-4fd7-9774-9589016bbb23/chat/3f7fe339-974b-4d92-ad6c-cf6ef2e35dfb)

- 模型真实生成四个原生训练器，调用一次渲染契约工具、四次组件发布工具；生成耗时19706ms、输出2481 token。
- Python、JS、TS 默认输入 `8 3` 均实际输出 `11`，三个公开样例分别全部通过，共9/9；编辑器验收后恢复原始代码并再次通过 Python 的三个样例。
- 正常界面关闭智能补全后，键入 `imp` 可见 `import` 关键词，Tab 接受补全；另验证 Tab 缩进、长代码横纵滚动条、彩色语法、左题目右单个编辑器和全屏。最终恢复模型代码，并将真实训练器全屏展示给用户。
- Java 控件无运行按钮。点击“交给 AI 分析”经正常消息链路提交代码、输入和样例，模型8690ms后返回1177 token，明确说明未编译、未运行，仅为静态推演；没有工具调用，Agent、Goal、联网标志均关闭。
- 两条模型回复均记录 `remote_capability=true`、`usage_is_estimate=false` 和非零输出 token；正式验收标签页浏览器错误日志为空。

本次截图、可见对话文本、断言、主数据库中的相关消息与脱敏 Provider trace、容器镜像及启动信息保存在 `backend/test-results/code-trainer-deployed/`。对话标签页已保留为用户可直接操作的交付页面。

本地验收截图：实际主应用中的训练器（`code-trainer-deployed.jpg`，不随仓库分发）。

## 当前编辑代码的辅导请求（2026-10-15）

“请 AI 辅导”发送点击时的完整代码、当前输入、题目、公开样例和已有浏览器结果。请求明确区分本轮辅导意图与待分析代码，避免模型将整个请求当作参考材料而继续历史问题。代码以独立围栏原文发送；超过消息预算时仅裁剪辅助材料，保留完整代码。训练器可展开“AI 将读取当前完整代码”核对内容，修改输入后清除旧运行与样例结果。

主入口 18000 已更新前端。在截图所指的真实会话 `e40ea0c8-629b-4c2c-ae9e-6e53634fc756` 中，通过 Monaco 编辑器将 `print(-1)` 临时改成 `print(-1015)  # tutoring_probe_1015`，浏览器实际输出 `-1015`。点击辅导后，持久化请求保留了这段新代码；真实远端回复逐字引用新语句，指出该输入应输出下标 `3`，并讲解二分及首次出现的边界处理。回复记录 `remote_capability=true`、`usage_is_estimate=false`、2019 输出 token，Agent、Goal、联网均关闭。验收后恢复原稿。

相关 20 项前端测试、类型检查、lint 和生产构建通过。脱敏消息、断言和截图保存于本地忽略目录 `backend/test-results/code-trainer-tutoring/`。
