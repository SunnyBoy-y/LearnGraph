# 06 - 安全、权限与隐私审计（Security Report）

> 方法：安全代码审计（security 子智能体，全库静态分析）+ 运行时验证（无效 token / 爆破 / 注册 / 隔离）。

## 总体结论
**未发现 P0/P1。** 认证强制、工作区隔离、密钥加密与掩码、SSRF 防御、沙箱 egress 私网拒绝、预览 origin 隔离均实现扎实；未发现 API Key 进入前端响应、日志或错误信息。存在 **4 项 P2、7 项 P3**。

## P2（上线前建议修复）

| ID | 问题 | 位置 | 影响 |
|---|---|---|---|
| S1-1 | 审批控制面缺发起人/管理者校验：fetch_authorizations decision/resume 仅按 workspace 取 pending，任意成员可代决定/恢复他人**付费**抓取+搜索生成 | fetch_authorizations.py:26-47,140-180（对照 egress_approvals.py:369-380 已有 actor 闸） | 组织工作区：越权触发付费、改写消息卡片状态 |
| S1-2 | 研究任务无 created_by 校验：成员可读他人研究问题全文、代批准（触发付费 deep research）、代取消 | research.py:48-112,237-299（仅 workspace 过滤） | 敏感内容跨成员可见；共享预算被消耗 |
| S1-3 | 认证端点无 IP 级限流；注册匿名无验证码/频率限制；demo 登录默认开启 | config.py:36（enable_demo_login=True）；auth.py:50-71,62-71 | 实测：5 次错密码锁 15min（账号 DoS）；注册 3 连发 201（灌水） |
| S1-4 | 上传上限 20GiB 无工作区配额 | config.py:55；files.py:520-617 | 共享部署磁盘可被耗尽 |

## P3

| ID | 问题 | 位置 |
|---|---|---|
| P3-1 | SSE 断连后付费生成继续（产品语义需透明化；detached 流无并发上限） | chat.py:99-136,189-256 |
| P3-2 | resume 类接口可由任意成员触发付费生成（与 S1-1 同源） | fetch_authorizations.py:140-180 |
| P3-3 | 设备登录轮询返回明文 token 一次（受 workspace.manage 门控） | management.py:2084,2129 |
| P3-4 | artifact 分享计数非原子 + Cache-Control: public, immutable, max-age=1y（revoke 语义失真） | artifact_gateway.py:157-175,143-163 |
| P3-5 | 上传信任客户端 Content-Type（无 magic-byte 校验；可伪装 audio/ 触发 ASR） | files.py:595,322-324 |
| P3-6 | 转写失败错误原文落库并回传客户端 | files.py:457-470 |
| P3-7 | 注册/账号增长无防护（每账号自动建工作区+技能包） | auth.py:62-71；auth.py:193-258 |

## 确认无问题（重点核查项）
1. **路由强制认证与工作区隔离**：X-Workspace-ID 必选 + tenant 归属 + 资源级 can_access_resource；30 个 router 仅 health 与 artifact-share（token 即授权）公开。
2. **任务/状态接口隔离**：memory_tasks _get 强制 tenant+workspace+subject_user_id；episodes 会话级 ACL；research/document_jobs workspace 级 ScopedRepository。
3. **WebSocket 鉴权**：dictation 首帧 token→AuthSession 校验（未过期/未撤销/active/tenant/workspace.write）后才建上游连接；ASR 用服务端密钥不落前端。
4. **密钥与隐私**：ProviderView 仅 api_key_masked；provider_secrets Fernet+主密钥版本加密；secret_references 仅 label/masked/版本，resolve() 明确"never return"；全库 grep 无日志记录 prompt/api_key/password；context_telemetry 仅存 sha256。
5. **注入与隔离**：组件预览 HTML 全转义 + script-src 'none'；preview 独立进程/端口 + CSP script-src 'self' + capability token（TTL 300s）；bundle 入口禁 base/iframe/form/外部 URL；上传下载强制 attachment。
6. **SSRF**：fetch.py:37-72 getaddrinfo 全地址集合校验（任一私网/回环/链路本地/组播/保留即整体拒绝，防 DNS rebinding）；URL 禁 userinfo；sandbox exact-host allowlist + 容器内 egress 代理 CONNECT 重分类（metadata IP 169.254.169.254/100.100.100.200/192.0.0.192 显式封禁）；审批只写 hostname 不写 IP/CIDR。
7. **资源与副作用**：研究任务远程路径强制 awaiting_approval 后才发起付费；取消先 provider.cancel_task 且 poll 在 terminal 退出；chat 取消为协作式多边界检查；图形权限缓存为请求内实例级 memo（无跨用户共享）；沙箱任务/会话按 owner 过滤。

## 运行时验证（实测）
- 无效 token → 401 14ms（会话过期语义）✅
- 无 X-Workspace-ID → 422 ✅
- 登录爆破 → 第 6 次 429 ✅（但账号 DoS 面，见 S1-3）
- 注册连发 → 全 201 无限制 ❌（S1-3/S1-7 佐证）
- 隔离实例密钥：provider 密文行在 keyring 主密钥下跨库可解密（验证加密体系一致性）✅

## 结论
安全基线扎实，无高危漏洞；**上线前必须修复 S1-1/S1-2（越权+付费）与 S1-3（认证限流 + demo 默认关闭）**。P3 项中 A1-4（关页计费）涉及产品决策需明确；其余 P3 可上线后处理。
