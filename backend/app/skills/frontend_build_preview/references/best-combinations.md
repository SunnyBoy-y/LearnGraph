# 最佳组合（frontend-build-preview）

> 组合原则：源码 → 构建 → 渲染验收 → 发布。发布由宿主 `sandbox_publish_web_app` 完成，本 Skill 只产出 `dist/`。

## 常见任务 → 脚本链

| 任务 | 脚本链 | 说明 |
|---|---|---|
| 从零做一个页面 | `scaffold_vite.py` → `build_frontend.py` | 先脚手架再构建 |
| 已有项目构建 | `build_frontend.py` | 自动识别 Vite/React/Vue/HTML |
| 构建后视觉验收 | `build_frontend.py` → `render_preview.py` | 渲染 PNG/PDF |
| 检查产物外链 | `check_static_assets.py` | 安全/自包含校验 |
| 发布给用户 | `build_frontend.py` → 宿主 `sandbox_publish_web_app` | 本 Skill 只产出 dist |

## 跨 Skill 组合示例

```text
用户要一个学习打卡页面
  1) frontend-build-preview/scaffold_vite.py --framework vue → 项目/
  2) （在项目 src 里写页面，用 sandbox 文件工具）
  3) frontend-build-preview/build_frontend.py → dist/
  4) frontend-build-preview/render_preview.py → preview.png（视觉验收）
  5) 宿主 sandbox_publish_web_app → 分享链接
```

```text
把一份 Markdown 讲义变成可读网页
  1) data-processing/make_report.py → 报告.md
  2) scaffold_vite.py --framework plain-html → 页面/
  3) build_frontend.py → dist/（index.html 自包含）
  4) render_preview.py → PNG 预览
```

## 选择依据

- 从零开始 → `scaffold_vite`；已有代码 → 直接 `build_frontend`。
- 要视觉确认 → `render_preview`；要安全校验 → `check_static_assets`。
- 要发布 → 用宿主 `sandbox_publish_web_app`，不要自己“发布”到外网（沙箱无公网）。
