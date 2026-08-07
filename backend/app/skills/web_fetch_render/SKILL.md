---
name: web-fetch-render
description: 在沙箱容器内按统一权限清单抓取网页并可选 chromium 渲染；仅当 egress 门开启且授权域名非空时使用。
---

# 受审网页抓取与渲染

> **门控 Skill**：这个包默认**可发现但不激活**。只有以下全部满足时才可用：
> `LEARNGRAPH_SANDBOX_EGRESS_ENABLED=true` + `LEARNGRAPH_SANDBOX_WEB_FETCH_ENABLED=true` + 工作区 `web_fetch.policy.allowed_domains` 非空 + egress 代理在线且镜像含 `web_fetch` 任务。不满足时，网页抓取应走外部 FetchProvider（Crawl4AI/Firecrawl/Qwen），本包内容不适用。

## When to use

- 用户要求抓取某个**已授权域名**的网页（`fetch_web_page` / `SourceService.fetch`），且统一权限清单已含该域名。
- 页面是 JS 重 SPA 或静态提取过薄，需要 chromium 渲染兜底。
- 用户只读公开内容；不做登录、表单提交、购买等写操作。

## 安全边界（不可违反）

- **宿主永不解析不可信 HTML**：抓取、解析、渲染全部在沙箱容器内完成，宿主只读回结构化 Markdown。
- **唯一出口**是 egress 代理（`HTTPS_PROXY=http://egress-proxy:8888`）；每次 CONNECT 按 `LEARNGRAPH_EGRESS_POLICY_DIGEST` 重审目标主机。
- **fail-closed**：未列主机、非法重定向、缺 digest、策略过期 → 拒绝并失败，绝不回退任意抓取。
- 渲染用容器内无状态 chromium（无用户浏览器 cookie/profile）；子资源请求同样受代理策略约束。
- 响应体 ≤ `sandbox_web_fetch_max_bytes`（默认 2MB）；重定向 ≤5 跳；超时 ≤ `sandbox_web_fetch_timeout_seconds`。

## 使用流程

1. 确认目标域名在 `web_fetch.policy.allowed_domains`；不在则走授权流（`allow_always` 需 `workspace.manage`）或改用其他通道。
2. 调用 `fetch_web_page`（Agent 工具）或 `SourceService.fetch`（来源服务）；宿主做会话级域名预检。
3. 沙箱容器内执行固定 `web_fetch` 任务：httpx GET（经代理）→ trafilatura 静态提取 → Markdown；过薄/命中 `<noscript>` 触发 chromium 渲染兜底。
4. 宿主读回 artifact 并校验 `schema_version`/`spec_sha256`/`final_url`，把 `FetchedDocument` 交给 Agent/来源服务（零改动）。

## 详细说明

- 组合与决策：`references/best-combinations.md`
- 输入/输出契约（spec/artifact、大小/超时/重定向）：`references/input-output-contract.md`
- 常见失败与诊断：`references/troubleshooting.md`

> 本包**不含可直接运行的脚本**：网页抓取是受审的固定容器任务，不由普通脚本自行联网实现。任何“在脚本里 `requests.get` 外网”的做法都违反沙箱离线边界，禁止。
