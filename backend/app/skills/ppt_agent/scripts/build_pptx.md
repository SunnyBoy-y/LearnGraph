# build_pptx.py

把 `<deck-dir>/slides/*.svg` 逐元素翻译成原生、可编辑的 `.pptx`。

## 用法

```bash
python3 scripts/build_pptx.py <deck-dir> [output.pptx]
```

- `<deck-dir>`：沙箱工作区内的相对目录。
- `[output.pptx]`：可选输出路径；省略时写入
  `<deck-dir>/<deck-dir-name>.pptx`。
- 输入按 `slides/*.svg` 文件名排序。

## 翻译契约

- `<rect>`：矩形或圆角矩形，支持填充、描边和圆角。
- `<circle>` / `<ellipse>`：椭圆形状。
- `<line>`：直线连接符。
- `<text>` / `<tspan>`：真实文本框；继承祖先的字号、字重、颜色和对齐属性。
- `<image>`：仅接受 base64 data URI，并还原为图片形状。
- 原生渐变背景：还原为 PowerPoint 渐变填充。
- 复杂 `<path>`、图标、箭头和浅色装饰组：合成为一张透明 PNG 叠层。

## 输入契约

- `slides/` 至少包含一个 UTF-8 SVG。
- 画布应为 `1280 × 720`；输出页面尺寸按 16:9 设置。
- 禁止外链图片、字体和脚本。
- 文字应保留为 `<text>`，不要转成路径，否则无法在 PowerPoint 中直接改字。

## 输出契约

- 生成原生可编辑的 `.pptx`，矩形、文本、线条和图片均为 PowerPoint 对象。
- 只有无法逐元素还原的装饰路径会进入透明 PNG 叠层。
- 无可用 SVG 渲染器时跳过图标叠层，但形状与文字仍正常输出。

## 依赖

- 必需：`python-pptx`、`lxml`，已预装于 LearnGraph 离线沙箱。
- 可选：Chrome/Edge、`rsvg-convert`、Inkscape 或 CairoSVG，用于装饰叠层。
- Windows 宿主兜底执行时设置 `PYTHONUTF8=1`。

## 失败模式

- 缺少参数或 `slides/*.svg`：返回非零状态。
- SVG 无法解析：脚本抛出解析错误，不生成半成品交付。
- base64 图片无效或缺少宽高：跳过该图片；应在视觉 QA 时发现并修正。
- 无 SVG 渲染器：保留原生形状和文字，跳过图标叠层并明确提示。

## 边界

- 不联网、不安装依赖、不下载字体。
- 不替代视觉 QA；生成后必须按文档转换链路渲染逐页检查。
- 只处理沙箱工作区内相对路径，不读取宿主绝对路径。
