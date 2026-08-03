# 任务 P2-A｜第三方组件隔离 renderer 与签名信任链（不可并行）

## 元信息块

```text
并行性   : 不可并行（对 backend/app/services/components.py 的大改动 + 涉及安全边界；实现依赖 P1-B 的队列/恢复底座）
状态     : 已完成 —— 2026-08-03（签名信任库 + 服务端校验 + 隔离 renderer runtime + 可信 renderer 通道（消费 trusted_bundle_eligible）+ capability token 协议；未验证/撤销仍降级 sandbox_artifact；真实 Docker 容器 E2E 已补：重建镜像含 render_component 任务，scripts/verify_sandbox_container_tasks.py 离线容器内 6 项通过）
主要文件 : backend/app/services/components.py、backend/app/services/component_trust.py、backend/app/services/component_renderer.py、backend/app/services/component_renderer_protocol.py、tests/services/test_component_trusted_renderer.py
依赖     : P1-B（实现阶段）—— 设计阶段可先行
口音标注 : 无
```

> 放 02_不可并行 因为改动集中且对安全基线敏感，且实现前期依赖 P1-B 落地；设计（阶段1）若想并行，
> 可把「信任模型 / renderer 契约」写成 RFC 文档单独 push，不碰 components.py。

## 背景

TODO —— 复制 ROADMAP P2-A 目标：从「Manifest 记录 + 安全降级」扩展到可验证、可撤销的第三方组件发布，
同时**确保第三方代码绝不进主应用 DOM**。阶段1产出设计，阶段2依赖 P1-B。

## 实施范围

- [x] 阶段1（设计）：受信发行者、公钥/证书、密钥轮换、撤销、包哈希、签名覆盖范围与算法约束；服务端校验（信任不得由客户端/Agent 声）；隔离 renderer：独立 origin/强隔离 iframe、严格 CSP/权限策略、最小消息协议；trusted-bundle/sandbox/降级规则（`component_trust.py`：ed25519、key_id、旋转、撤销；`component_renderer.py`：`default-src 'none'`、`script-src 'none'`、`connect-src 'none'`）。
- [x] 阶段2（实现，等 P1-B）：把信任校验、撤销、降级、跨工作区与 renderer 消息边界的端到端测试落地（`tests/services/test_component_trusted_renderer.py`；真实 Docker 容器 render 经 `scripts/verify_sandbox_container_tasks.py` 验证）。
- [x] 保持现状安全降级基线不变：任何校验失败均不得放宽，并产生可审计原因。

## 边界（防冲突）

- 不碰 `mcp.py`（P2-B）、不碰前端 query 键（P0）。安全基线文件 `components.py` 大改须在全仓库较空时做。

## 验收条件

- [x] 只有「登记发行者 + 有效签名 + 匹配包哈希 + 授权工作区」同时满足才走可信 renderer。
- [x] 第三方代码读不到宿主 DOM、认证 token、Provider 凭据、非授权工作区数据。
- [x] 任一校验失败不放宽降级基线且有审计原因。