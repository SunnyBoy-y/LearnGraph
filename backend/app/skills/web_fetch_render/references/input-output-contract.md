# 输入/输出契约（web-fetch-render）

## 固定任务契约

`fetch_web_page` / `SourceService.fetch` 触发沙箱容器内的固定 `web_fetch` 任务。宿主写入不可变 spec，容器产出 artifact。

### spec（宿主写入）

```json
{
  "schema_version": "1.0",
  "url": "https://example.com/docs/foo",
  "mode": "allowlist",
  "allowed_domains": ["example.com"],
  "max_redirects": 5,
  "max_bytes": 2097152,
  "timeout_seconds": 30,
  "policy_digest": "sha256:...",
  "spec_sha256": "..."
}
```

### artifact（容器产出）

```json
{
  "schema_version": "1.0",
  "task_type": "web_fetch",
  "status": "ok",
  "final_url": "https://...",
  "title": "...",
  "markdown": "...",
  "extracted_by": "trafilatura|chromium",
  "truncated": false,
  "spec_sha256": "..."
}
```

## 限制

| 项 | 值 |
|---|---|
| 响应体上限 | `sandbox_web_fetch_max_bytes`（默认 2 MiB） |
| 重定向上限 | 5 跳，每跳 host 必须在允许集 |
| 超时 | `sandbox_web_fetch_timeout_seconds`（默认 30s，渲染兜底 ≤15s） |
| 内容类型 | text/html、application/xhtml+xml、text/plain、text/markdown |
| 网络 | 仅 egress 代理；digest 必须可解析；私网/元数据/保留段全拒 |

## 成功判据

- `status:"ok"` 且 `markdown` 非空；宿主复核 `schema_version`/`spec_sha256`/`final_url` 后交付 `FetchedDocument`。
- 重定向后 `final_url` 仍落在授权域名集合。

## 失败语义

- 未列主机 / 非法重定向 / 缺 digest / 策略过期 / 响应超限 → 任务失败，fail-closed，不回退。
- 宿主侧映射：策略外 → `UnsafeFetchURL`；超时 → `FetchProviderTimeout`；其余 → `FetchProviderError`。
