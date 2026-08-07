# 常见失败与处理（web-fetch-render）

## 403 / CONNECT 被拒

- 现象：抓取返回 403 或代理拒绝连接。
- 原因：digest 未知/缺失，或目标主机不在该工作区 derived 策略内（策略过期/未重载）。
- 处理：确认 `web_fetch.policy.allowed_domains` 含目标域名且策略文件未过期；egress 代理 `--reload-seconds` 已收窄（1s）后再试。**不要回退到直接联网。**

## 域名未授权

- 现象：宿主预检报 `UnsafeFetchURL`。
- 处理：请求用户授权（`allow_once`，或 `allow_always` 需 `workspace.manage`）后再试；或用外部 FetchProvider 通道。

## JS 页面提取为空

- 现象：静态提取为空/过薄。
- 处理：这是预期触发渲染兜底（`extracted_by=chromium`）；若仍空，说明页面内容本身极少或需登录——如实说明，不伪造正文。

## 重定向到未授权主机

- 现象：任务失败（fail-closed）。
- 处理：这是安全不变量；不尝试绕过。告知用户目标跳转到未授权域名。

## egress 门未开启

- 现象：factory 回落到外部 FetchProvider，或 sandbox web_fetch 不可用。
- 处理：这是设计行为；本包内容不适用，改用已配置的外部抓取通道。

## 响应超限 / 超时

- 现象：`web fetch response exceeds the configured byte limit` 或 timeout。
- 处理：目标页面过大或过慢；缩小页面/改用 API 或提示用户。

## 代理未启动

- 现象：容器内 `web_fetch requires the sandbox egress proxy`。
- 处理：确认 `run_sandbox_egress_proxy.py` 在容器内运行且策略目录非空（fail-closed 启动要求）。
