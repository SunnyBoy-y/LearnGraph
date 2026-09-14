# build_preview.py

从一套已生成的 SVG 幻灯片创建单文件 `preview.html`，用于在交付前通览全部页面。

## 用法

```bash
python3 scripts/build_preview.py <deck-dir>
```

- `<deck-dir>`：沙箱工作区内的相对目录。
- 脚本读取 `<deck-dir>/slides/*.svg`，按文件名排序。
- 输出固定为 `<deck-dir>/preview.html`，已存在时直接覆盖。

## 输入契约

- `slides/` 至少包含一个 UTF-8 SVG 文件。
- 每个 SVG 应是完整、自包含的幻灯片，不得引用外部字体、脚本或图片。
- 推荐 `viewBox="0 0 1280 720"`；预览页面按 16:9 展示。

## 输出契约

- 单个自包含 HTML 文件，内联全部 SVG。
- 页面按文件名顺序显示页数、文件名和 16:9 预览。
- 不生成额外资源，不访问网络。

## 失败模式

- 参数数量不正确：输出用法并返回非零状态。
- `slides/` 不存在或没有 `*.svg`：返回非零状态。
- SVG 不是 UTF-8：读取失败并返回非零状态。

## 边界

- 只做拼接与预览，不验证 SVG 视觉质量，也不修复坐标、溢出或字体。
- 视觉 QA 主路径仍是对每页调用 `document-conversion/html_to_png.py`。
- 必须在离线沙箱中通过 `skill.sandbox-run` 或 `sandbox_exec` 执行。
