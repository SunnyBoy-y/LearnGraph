# 最佳组合（web-fetch-render）

> 网页抓取走固定容器任务，不是普通脚本。组合发生在**数据流**层面：抓到的 Markdown 是下游分析的输入。

## 常见任务 → 组合

| 任务 | 组合 | 说明 |
|---|---|---|
| 抓授权页正文 | `fetch_web_page`（sandbox web_fetch） | 宿主只收 Markdown |
| JS 重页面 | `fetch_web_page` 自动 chromium 兜底 | `extracted_by=chromium` |
| 抓到的内容做分析 | 抓取 → `document-conversion/extract_text` 不需要 | 已是 Markdown，直接进图谱/记忆/报告 |
| 抓多页汇总 | 逐页 `fetch_web_page` → `data-processing/make_report` | 每页结果合并成报告 |

## 与其他通道的边界

- 域名未授权、egress 门关闭、无代理 → **本包不适用**；用外部 FetchProvider（Crawl4AI/Firecrawl/Qwen），行为由 `fetch_provider_for_workspace` 工厂门控决定。
- 不要在同一任务里“先抓后渲染”混合静默回退：白名单命中走白名单；`allow_without_confirmation` 是显式开关，不是失败兜底。

## 选择依据

- 域名在 `allowed_domains` 且门开启 → 沙箱 web_fetch。
- 否则 → 走外部 FetchProvider 或请求用户授权（`allow_always` 需 `workspace.manage`）。
