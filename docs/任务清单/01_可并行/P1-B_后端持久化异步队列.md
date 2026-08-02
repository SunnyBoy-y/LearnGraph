# 任务 P1-B｜后端持久化异步队列与中断续跑

## 元信息块

```text
并行性   : 可并行（独立后端域；不碰前端任何文件，也不与 P2 抢文件）
状态     : 待开始 —— 可与 P0-A 并行
主要文件 : backend/app/core/tasks.py、backend/app/services/chat.py、backend/app/api/routers/chat.py
依赖     : 无（可立即开）
口音标注 : 无
```

## >> 背景与目标

**背景（为什么做这个）**

当前 `InProcessTaskQueue` 是进程内线程池，进程重启或多进程部署下**无法恢复执行**。聊天流已经能持久化
事件和部分 Provider continuation state，也能把带检查点的遗留任务标成 `interrupted`，但**当前恢复入口仍是
重新生成/重试，并不是真正从断点续跑**。这意味着：长任务跑到一半服务重启，进度就丢了；message 流被打断，
只能整段重来。ROADMAP P1-B 要把它升级成可恢复、可审计、多租户隔离的后台队列，并覆盖故障注入测试。

**目标（做成什么样子）**

- 抽出**可替换的队列端口**：job 持久化、入队、领取、心跳、取消、重试、幂等语义拆开；`InProcessTaskQueue`
  保留仅作开发/兼容路径。
- 为聊天、文档学习、研究等后台流程定义**统一 job 状态机与恢复点**。
- 持久化 continuation payload、所属工作区、尝试次数、lease/heartbeat、可审计状态变更；敏感 Provider
  状态沿用现有加密/脱敏边界，不能把密钥写进普通审计正文。
- 服务启动 / 新 worker 时，**重新调度**有可恢复检查点的中断任务；不可恢复的给明确可操作的 retry 路径。
- 写**故障注入测试**：进程中断、服务重启、重复投递、lease 超时、取消与重试竞争。

**完成标准 / 验收条件**

- 进程在执行中终止后，具备检查点的任务可恢复，或明确进入「可操作、不丢上下文」的重试状态。
- 同一 job 被重复投递/重领时，**不产生重复业务事实**。
- 恢复、取消、失败、重试、完成全部持久化、可审计。
- 一个工作区的积压/失败不阻塞其它工作区的可执行任务（多租户隔离）。

## 现状与风险

- 既有 `InProcessTaskQueue`、chat 的 SSE 断线可继续生产、遗留 `interrupted` 标注。这些是现成底座。
- 风险：SQLite（`backend/data/learngraph.db`）是单文件，若用 DB 做队列，锁/并发要小心多租户隔离。
- 风险：变更点横跨 `tasks.py` / `chat.py` / `chat router`，是本仓库后端最敏感链路，改动前后要跑通现有后端测试。

## 实施范围

- [ ] 定义队列端口接口（enqueue / lease / heartbeat / cancel / retry / idempotency），`InProcessTaskQueue` 适配它。
- [ ] 定义统一 job 状态机（pending → running → done / failed / interrupted → retried/cancelled）与恢复点语义。
- [ ] 落盘 continuation、workspace 归属、尝试次数、lease/heartbeat、变更审计。
- [ ] 启动时分发可恢复中断任务；不可恢复的给 retry 路径。
- [ ] 写故障注入（见上）与多租户互不阻塞用例。

## 与其他任务的边界（防冲突）

- **只改** `backend/`：`backend/app/core/tasks.py`、`backend/app/services/chat.py`、`backend/app/api/routers/chat.py` 及配套测试。
- **不碰** `backend/app/providers/*`、`backend/app/services/components.py`、`mcp.py`（那些是 P2-A/P2-B 的地盘）；
  本任务只是让「队列/恢复」能用它们的续传状态，不重构它们本身。
- **完全不碰** `frontend/`。
- 与 P2-C 同在后端域，但 P2-C 改的是 sandbox/proxy/策略层，文件不重叠，可并行。

## 验收条件

- [ ] `backend` 测试（见 memory `backend-dev-workflow`：`uv run --with pytest` 在 `backend/` 下跑）通过，且新增故障注入用例绿。
- [ ] 重启后带检查点 job 被重新调度；重复投递不重复业务事实。
- [ ] 多租户隔离用例通过。

## 产出物交付给谁

- P2-A / P2-B 依赖它提供 queue/恢复底座（它们的实现阶段要等这任务落地）；设计阶段可并行推。