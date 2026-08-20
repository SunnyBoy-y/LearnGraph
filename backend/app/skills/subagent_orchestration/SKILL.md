---
name: subagent-orchestration
description: 沙箱子代理的编排与调用（sandbox_subagent / _status / _wait / _cancel / _retry）——何时委派、收益门、Context Pack、写集、结构化交付与主代理接管。
---

# 子代理编排 v2（subagent-orchestration）

## When to use

先过**收益门**，全部满足才开子代理：

- 至少 2 个当前可独立执行的节点；
- 预计关键路径缩短 ≥ 25%；
- 拆分/等待/汇总/复核成本 ≤ 子任务总工作量 20%；
- 每个子任务预计 30~180 秒，有单一可验收交付物；
- 写集互斥或使用独立目录；
- 主代理仍保留预算做验收与收尾。

适合并行：多模块只读调研、独立候选方案、独立目录实现、不同测试面。
适合流水线（有依赖时）：调研 → 主代理冻结设计 → 多模块实现 → 验证 → 主代理收尾。
**不应委派**：少于约 3~4 次工具调用、需要联网/授权/用户交互、多个步骤频繁改同一文件、结果立等可用。

## 调用姿势

### 1. 写自包含 Context Pack

子代理看不到主对话历史，prompt 必须自包含：

```text
目标：<一句话>
为什么：<在父计划中的作用>
输入：<明确路径/文件/快照，用 sandbox_list_files 确认>
约束：<禁止项、兼容性、安全边界>
写集：<允许写的路径前缀；写集外写入会被拒绝 write_not_allowed>
接口契约：<DOM ID / schema / 类型 / 命名>
输出契约：<交付 JSON：summary / artifacts[] / evidence[] / acceptance[] / risks / unresolved>
完成标准：<什么算完成>
失败协议：<预算耗尽时输出当前进度与已写文件，不假成功>
```

### 2. 等待优先于轮询

- `sandbox_subagent_wait(subagent_ids, mode=all|any, timeout_ms, after_event_seq)`；
- 服务端按片返回，超时给 `retry_after_ms`，按它再次调用；
- **禁止**在一个回合内连续多次 `sandbox_subagent_status` 轮询；
- 等待期间主代理应继续做不冲突的工作（写文档、冻结接口、准备合并框架）。

### 3. 状态语义（只有 completed 才是成功）

| 状态 | 含义 |
|---|---|
| completed | 有非空最终答案且交付契约通过 |
| partial | 轮数/工具数/token/费用预算耗尽，可能有可用文件——检查产物 |
| failed | 异常或空结果（empty_result） |
| timed_out | 墙钟超限 |
| cancelled | 已确认取消（取消中是 running + cancel_requested） |

### 4. 失败处理

- 瞬时 Provider/容量错误：`sandbox_subagent_retry(scope=same)` 一次；
- 范围过大/预算耗尽：`sandbox_subagent_retry(scope=scoped, prompt_override=更窄的prompt)` 一次；
- 已有大部分产物、剩余工作量小、写集冲突、权限异常、重复失败：**主代理接管**，不整任务重跑；
- 结果不满意：在结果上迭代，不无脑重跑。

### 5. 写集与目录

- 派发时声明 `write_set`；文件类工具越界写入直接拒绝；
- 未声明时默认只允许 `work/subagents/<task_id>/` 车道；
- 每个子代理只写自己的目录，主代理按 manifest 合并，避免并行覆盖。

### 6. 交付说明（强制）

子代理最终回复必须带 JSON 交付块；`partial` 时也要输出进度与已写文件路径。主代理合并前用 `sandbox_read_file` / `sandbox_grep` 确定性复核，不采信"我说完成了"。

## 限制

- 子代理不能联网、不能弹用户授权、不能嵌套创建子代理；
- 任务持久化在数据库（sandbox_agent_tasks / sandbox_agent_events），重启不丢；
- 并发受 chat/user/workspace 配额约束；排队中任务自动开始，无需重提。

## 详细说明

- 工具契约见 `references/tool-contract.md`
- 执行器与调度说明见 `references/runner.md`
