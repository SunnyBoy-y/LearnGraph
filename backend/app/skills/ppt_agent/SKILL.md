---
name: ppt-agent
description: >
  端到端 PPT 生成助手（LearnGraph 官方技能）:把"人类顶级 PPT 团队"的工作流——需求调研→大纲→资料检索→策划稿→整页
  SVG 设计——固化成流水线,产出可拖进 PowerPoint 编辑的 1280×720 整页 SVG 幻灯片 + 网页预览 + 原生 .pptx。
  当用户想做/生成/制作一套 PPT、幻灯片、演示文稿、slides、deck,或要为汇报/答辩/路演/组会/
  课堂/产品介绍准备演示,或说"用 ppt-agent"、"帮我做个关于 X 的 PPT"、"把这个主题做成幻灯片"
  时触发。全程两道人审关口(需求、大纲,用 canvas 确认卡交互),其余自动;调研走 LearnGraph 门控网页抓取
  (fetch_web_page / 外部搜索通道)与 search_images,配图/示意图用 generate_image 生成并以 base64 内联,
  出片在离线沙箱内执行 build_preview.py / build_pptx.py,视觉 QA 用 document-conversion 渲染逐页检查,
  交付经 sandbox_publish_file 发布 .pptx 与 preview.html。确认后自动产出**每个色块/文字/线条都是原生
  PowerPoint 形状、打开即可改字改色**的 .pptx;若起点是已有 .pptx 模板、或要做幻灯片级 OOXML 增删/
  文本提取,请改用 LearnGraph 内置 pptx-generation 技能。
---

# PPT Agent（LearnGraph 官方技能）

## Overview
把"人类顶级 PPT 团队"的工作流固化成流水线:需求调研 → 大纲 → 资料检索 → 策划稿 → 整页 SVG 设计 → 预览交付。逐页产出 1280×720 的 SVG,再逐元素翻译成由原生形状 / 文本框组成、打开即可改字改色的 .pptx,并生成网页预览。核心信条:**PPT 的灵魂是内容不是皮囊**——先想清楚为谁做、做什么,再谈设计。

本版按 LearnGraph 系统组件做了适配:调研走**门控网页抓取**与**文搜图**,配图走**文生图/图生图**,确认关口用 **canvas 交互卡**,脚本在**离线沙箱**内出片(`skill.sandbox-run` / `sandbox_exec`),视觉 QA 用 **document-conversion** 渲染,交付经 **sandbox_publish_file**。本技能已注册为官方技能(`backend/app/skills/ppt_agent/`),随工作区自动启用、自动获得系统授权。

## 工作流总览
七阶段顺序执行;**只在 ① 需求、② 大纲两处停下等用户确认**,其余自动跑完。

| # | 阶段 | 关口 | 读取的提示词 / 资源 | LearnGraph 组件 | 落盘 |
|---|---|---|---|---|---|
| 1 | 需求调研 | 🛑 等确认 | `references/01-requirement-interview.md` | 门控抓取 `fetch_web_page`(egress 开启且域名授权)或外部搜索通道;`search_images` 图调;canvas 卡收确认 | `00-research.md` |
| 2 | 大纲 | 🛑 等确认 | `references/02-outline-architect.md` | `canvas_emit_trusted_component`(`option_group` + `allow_custom`)确认/调整 | `01-outline.json` |
| 3 | 资料检索 | 自动 | (工具驱动,见下) | `fetch_web_page` / 搜索通道;`search_images` + `download_external_image`(审批制) | `02-content.md` |
| 4 | 策划稿 | 自动 | `references/03-planning.md` + `references/bento-grid.md` | —(纯推理;配图/图表需求记占位) | `03-plan.md`(含全局风格令牌)、复杂页 `slides/wire-NN.svg` |
| 5 | SVG 设计 | 自动 | `references/04-design-svg.md` + `references/bento-grid.md` | 配图/示意图 `generate_image`(文生图/图生图)→ base64 data URI 内联 | `slides/slide-NN.svg` |
| 6 | 预览 + 出片 | 自动 | `scripts/build_preview.py`、`scripts/build_pptx.py`、`references/svg-to-powerpoint.md` | 离线沙箱内执行(`skill.sandbox-run` / `sandbox_exec`;python-pptx/lxml 已预装) | `preview.html`、`<主题>.pptx`、`README.md` |
| 7 | 视觉 QA | 自动 | (渲染逐页检查,见下) | `document-conversion` 的 `html_to_png` / `html_to_pdf`(沙箱 Chromium) | 修复后重出片 |

## 快速通道(跳过已完成阶段)
用户已提供**确认过的大纲 / 逐页内容**时(如给了大纲文件或对话中已敲定每页写什么),**不得重复问需求、重新生成大纲**:把已有材料整理为 `00-research.md` + `01-outline.json` + `02-content.md` 直接落盘,从阶段 4 开跑。同理,资料已给足则跳过阶段 3。需要朴素模板(标题+要点,无设计)时可直接转 LearnGraph 内置 `pptx-generation` 的 `build_deck.py`,不必走本流水线。

## 开始前
确认或推断 PPT 主题,在**沙箱工作区**建产物目录 `ppt-decks/<今天日期>-<主题slug>/`(内含 `slides/` 子目录),用 `sandbox_write_file` 落盘后续文件。用户指定了别的位置就用用户的。交付前把产物发布给用户(阶段 6/7)。

## 分阶段执行

### 阶段 1 · 需求调研 🛑
读 `references/01-requirement-interview.md` 照其执行:先调研主题,再向用户提 3-5 个关键问题;把调研摘要 + 需求纪要写入 `00-research.md`。**停下**,等用户补全 / 确认需求再继续。

- 调研通道(按可用性依次选择):① 门控网页抓取 `fetch_web_page`(仅当 egress 开启且目标域名在 `web_fetch.policy.allowed_domains`,见 web-fetch-render 技能);② 对话内可用的外部搜索 / FetchProvider 通道;③ 图片调研 `search_images`(文搜图)。
- 所有通道都不可用、且用户未给足素材时:**如实说明**,基于用户素材 + 模型知识完成需求纪要,并在 `00-research.md` 中标注"事实性数据待用户补充",**不得编造数据与来源**。
- 提问可直接对话,也可用 `canvas_emit_trusted_component`(`short_answer_table` / `fill_blank`)做一张批量作答卡。

### 阶段 2 · 大纲 🛑
读 `references/02-outline-architect.md`,把 `00-research.md` 填入 `{{CONTEXT}}`、目标页数填入 `{{PAGE_REQUIREMENTS}}`,生成 `[PPT_OUTLINE]` JSON 并提取存为 `01-outline.json`。再以"数字便利贴"形式**每页一行**展示给用户(封面 / 目录 / 各章节页 / 结尾),供其增删、改写、调序;随后用 `canvas_emit_trusted_component` 发一张确认卡(`option_group`,选项如「确认,按此开跑」「我要调整」,`allow_custom=true` 接收自由文本意见)。**停下**,等用户确认大纲再继续(卡上提交、对话回复、逐页修改意见均视为确认信号)。

### 阶段 3 · 资料检索(自动)
按确认后的大纲**逐页 / 逐节**检索,为每页备齐要点、数据、案例(标注来源),汇总写入 `02-content.md`、按大纲结构组织。

- 文字资料:同阶段 1 的调研通道;抓到的页面若需保存正文,可用 document-conversion 的 `extract_text` 抽取后整理。
- 图片素材:需要**真实照片/案例图**时用 `search_images` 搜;确认要"用图"后接 `download_external_image`(单张 `url+destination_path`,多张 `urls+destination_dir`;命中白名单自动放行,否则弹审批卡),成功后拿到 `file_id` 备用。搜到但未下载的图片**只是链接,不是文件**,不要直接当素材。
- 用户已给足素材或主题无需外部资料时,可精简此步。

### 阶段 4 · 策划稿(自动)
读 `references/03-planning.md` 与 `references/bento-grid.md`,结合 `01-outline.json` + `02-content.md`:先定**全局风格令牌**(主辅色/字号阶梯/卡片圆角/视觉母题,写在 `03-plan.md` 开头),再为每页规划版面(便当组合、视觉层级、占位元素),写入 `03-plan.md`;对复杂页(≥4 个内容块或用户指明的重点页)另产出低保真线框 `slides/wire-NN.svg`。

- 占位元素明确三类:**内联可画**(图表/图标 → 阶段 5 用内联 SVG 路径绘制)、**可生成配图**(示意图/插画 → 阶段 5 用 `generate_image`)、**用户素材**(照片等 → 阶段 5 用 base64 内联)。在 `03-plan.md` 里注明每处配图的生成描述。

### 阶段 5 · SVG 设计(自动)
读 `references/04-design-svg.md` 与 `references/bento-grid.md`。**逐页**把该页在 `03-plan.md` 的规划填入 `{{PAGE_PLAN}}`、该页内容填入 `{{PAGE_CONTENT}}`、需求中的风格填入 `{{STYLE}}`、`03-plan.md` 开头的风格令牌填入 `{{STYLE_TOKENS}}`(每页相同、原样注入),生成纯 SVG,用 `sandbox_write_file` 存为 `slides/slide-01.svg`、`slide-02.svg`……(两位数序号,与页序一致)。有线框的页面以线框为版面基准。

- 配图:策划稿标注"可生成配图"的页,用 `generate_image`(文生图;16:9 配图用 `2048x1152`;基于用户图片改版用图生图 `source_file_ids`),拿到 `file_id` 后用 `read_session_file` 取字节,**转 base64 data URI 内联**进该页 SVG 的 `<image>`(带 `x/y/width/height`);用户提供的照片同理。
- 严格遵守 04 号提示词的硬性要求:**不引用任何外部资源**;base64 内联不算外部资源,外链 URL 禁止。

### 阶段 6 · 预览 + 出片(自动)
1. **预览**:在**离线沙箱**内执行 `scripts/build_preview.py <deck-dir>`,生成 `preview.html`(单文件、内联全部 SVG)。
2. **出 .pptx**:沙箱内执行 `scripts/build_pptx.py <deck-dir> [<输出名>.pptx]`——把每页 SVG **逐元素翻译成原生 PowerPoint 形状**(矩形/圆角矩形、椭圆、连接线、文本框、渐变填充逐一还原;`<image>` 位图还原为图片形状),**打开即可直接改字、改色、挪位置**;只有复杂图标/箭头/装饰路径(`<path>` 与浅色 `<g opacity>`)合成一张透明 PNG 叠在最上层。沙箱镜像已预装 `python-pptx`、`lxml`;图标叠层需任一 SVG 渲染器(Chrome/Edge / rsvg-convert / inkscape / cairosvg,脚本自动探测,缺了就跳过图标,不影响形状与文字)。
3. **README**:按 `references/svg-to-powerpoint.md` 写 `README.md`(主题 / 页数 / 风格 + 如何打开 .pptx,附手工导入 SVG 的兜底法)。

**执行方式**(本技能已注册为官方沙箱技能包,脚本随工作区自动物化进沙箱 `skills/ppt-agent/scripts/`):用 `skill.sandbox-run` 运行 —— `skill_key=ppt-agent`,`script_path=scripts/build_preview.py`(或 `scripts/build_pptx.py`),`argv_extra=[<deck 相对路径>]`;或直接用 `sandbox_exec` 在沙箱工作区内执行 `python3 scripts/build_pptx.py <deck>`。**宿主兜底**:本机装有 python-pptx + lxml 时可直接运行(Windows 加 `PYTHONUTF8=1`)。沙箱离线,不联网、不 pip install、不下载字体。

### 阶段 7 · 视觉 QA(自动)
用 LearnGraph 渲染链路把页面渲染成图片逐页检查:
1. **主路径**:对每页 SVG 包一层最小 HTML(仅 `<html><body style="margin:0"><svg …>`),用 document-conversion 的 `html_to_png` 逐页渲染为 `qa/slide-NN.png`;或对整份 `preview.html` 用 `html_to_pdf` 出 PDF 通览。
2. **兜底**:渲染通道不可用(沙箱异常等)时,退化为逐页审读 SVG 源码核对坐标与层级。

逐页检查:
- 文字溢出卡片 / 画布边界(最常见,优先查)
- 元素重叠、卡片间距 <20px、明显失衡的留白
- 页与页配色 / 字号不一致(违反风格令牌)
- 低对比(浅底浅字、深底深字)
- 配图位置 / 比例与占位规划一致

发现问题→回改对应 `slides/slide-NN.svg`→重跑阶段 6 出片,直至通过。**QA 未通过不得向用户交付。**

## 交付
1. `sandbox_publish_file` 发布 `<主题>.pptx` 与 `preview.html`(文件卡片,用户可直接下载 / 打开)。
2. 报告产物目录,提示用户直接打开 `<主题>.pptx`,或先看 `preview.html` 通览。

## 关键约束
- **不跳过两道关口**:需求、大纲未经用户确认,不得进入后续自动阶段(快速通道除外——用户已确认的材料即视为过关;canvas 卡上提交、对话回复均算确认)。
- **SVG 严格 `viewBox="0 0 1280 720"`**:文字用 `<text>`(可编辑)、不引外部资源(配图仅允许 base64 data URI 内联)。
- **沙箱离线边界**:出片与渲染 QA 全程不联网、不装包、不下载模板字体;联网只发生在阶段 1/3 的门控调研通道。
- **重要 PPT**:用户要求时可在阶段 4 后加设一道"看策划稿再出图"的确认关;默认不停。
- 阶段 6 自动产出 .pptx(每页逐元素翻译成原生形状 / 文本框,打开即可改字改色;仅复杂图标走透明 PNG 叠层),阶段 7 视觉 QA 通过后方可交付。但**若起点是已有 .pptx 模板、或要做幻灯片级 OOXML 增删 / 文本提取**,那是 LearnGraph 内置 `pptx-generation` 技能的活,转过去。
