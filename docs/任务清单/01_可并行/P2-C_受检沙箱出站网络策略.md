# 任务 P2-C｜受检沙箱出站网络策略（可选）

## 元信息块

```text
并行性   : 可并行（独立后端域：sandbox / 代理 / 策略层；不与 P1-B 抢后端文件，也不碰前端）
状态     : 已完成 —— 2026-08-03 策略层 + 可执行 CONNECT 代理 + Docker 注入 + 安全测试落地；默认仍完全离线
主要文件 : backend/app/services/sandbox_network_policy.py、backend/app/services/sandbox_egress_proxy.py、backend/app/providers/remote/sandbox.py、backend/app/services/sandbox.py
依赖     : 无（可立即开；ROADMAP 明确这是可选扩展，默认离线已经是基线）
口音标注 : 无
```

## >> 背景与目标

**背景（为什么做这个、以及为什么它是可选的）**

沙箱**默认拒绝网络**已经是安全基线（`network_policy={"mode":"none","allowed_hosts":[]}`），不修不危机。
但 ROADMAP 预见产品可能会需要「允许访问少数外部队列地址」的能力。此刻它不算缺口——所以本期可选。
真正要谨慎的不是「加不加网络」，而是「一旦要加，**不能把默认离线基线放水**」：只允许显式授权的
allow-list，绝不能让 allow-host 变成通向环回/私网/云元数据的后门，也不能让“把 host 列表写进容器元数据”
这种假策略糊弄过去。

**目标（做成什么样子）**

- 保持默认 `network_mode="none"`；网络启用必须是**显式工作区/任务级策略**，默认永远拒绝。
- 定义 allow-list 审核、主机规范化、DNS 重绑定防护、私网/环回/链路本地/云元数据/APIPA 地址拒绝、
  端口与协议约束、审计与过期机制。
- 用一个**可执行的出站代理或网络策略层**去落地，而不是只把 host 列表写进容器元数据。
- 覆盖 IPv4/IPv6、DNS 变化、重定向链、策略绕过的安全测试。

**完成标准 / 验收条件**

- 未显式授权的沙箱**始终离线**。
- allow-host **不能**访问环回、私网、链路本地、云元数据、重定向绕过目标。
- 每次允许的访问都能关联到工作区、任务、审批/策略记录；策略不确定时**一律拒绝**。

## 现状与风险

- 现状：默认离线已在 [sandbox.py](docs/任务清单/01_可并行/backend/app/services/sandbox.py) 实现，属已完成基线，不要误当缺口又去“实现网络”。
- 风险：DNS 重绑定、环回别名、重定向链是经典绕过点，测试必须正面对抗它们。
- 风险：这是可选能力，若没有明确产品需求，**可以不做**——这一项允许「结论是保持现状」的交付。

## 实施范围

- [x] 设计 allow-list 审核与主机规范化流程（IP vs 域名、IDNA/尾点规范化、端口/协议白名单、过期与审批字段）。
- [x] 落地 DNS 重绑定防护与私网/环回/链路本地/云元数据/APIPA 地址拒绝（IPv4 + IPv6，连接时重分类）。
- [x] 用可执行出站 CONNECT 代理（`SandboxEgressProxy`）落地，并在每次决策里记录归属（workspace/approval/policy_digest/host/port/reason）。
- [x] 写 IPv4/IPv6、DNS 变化/重绑定、私网/元数据、端口/协议、非法方法、IP 字面量、策略过期/缺失/损坏、跨策略的绕过测试。不确定 → 拒绝。

## 与其他任务的边界（防冲突）

- **只改** `backend/app/services/sandbox.py` 及新建的代理/策略/测试文件。
- **不碰** `backend/app/core/tasks.py` / `chat.py`（P1-B 地盘）——P1-B 若需要网络状态，只消费你给的「离线/受限」标记，不重建。
- **完全不碰** `frontend/`。
- 与 P1-B 并行：两者都在 backend，但文件不重叠。

## 验收条件

- [x] 默认策略下沙箱完全离线（`network_mode="none"`，且 `SandboxCreateSpec.egress=None` 时强制）；仅显式授权、未过期、已审批的策略才注入 egress 网络。
- [x] 环回/私网/链路本地/云元数据/重定向绕过全部被拒，且有测试证明（策略层 + 代理层 + Docker 注入路径）。
- [x] 每次允许/拒绝访问可审计归属；不确定即拒（`EgressPolicyDenied` 拒绝闭合）。

## 产出物交付给谁

- 后端沙箱栈。部署侧如需启用：置 `LEARNGRAPH_SANDBOX_EGRESS_ENABLED=true`，在 `sandbox_egress_policy_dir` 放置 `<workspace_id>.json` 已审批策略，并单独运行 `SandboxEgressProxy` 作为唯一出口。