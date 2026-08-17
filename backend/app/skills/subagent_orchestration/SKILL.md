---
name: subagent-orchestration
description: 沙箱子代理的编排与调用（sandbox_subagent / sandbox_subagent_status）——何时委派、如何写子代理任务、如何轮询结果。
---

# 子代理编排（subagent-orchestration）

## When to use

- 有一批**相互独立**的工作（多文件调研、多个候选方案实现、分头清洗/生成），可以并行执行。
- 任务**很长或很绕**，会挤占主对话上下文：把完整上下文隔离进子代理，只回收最终结果。
- 需要"隔离执行"：子代理有独立消息历史与受限工具集，不会污染主链的决策。
- 主代理要先做别的（写文档、等用户输入），子代理在后台跑。

## 何时不要用

- **简单任务**：几步就能做完的，直接自己做（子代理有启动开销 + LLM 成本）。
- **需要交互**：子代理不能弹授权卡片（sandbox_fetch / sandbox_search_web / sandbox_git_clone / 嵌套子代理不在默认工具集内）——需要联网/授权的工作留在主链。
- **需要共享状态**：子代理的工作区与主代理是同一个沙箱会话，但**消息上下文完全隔离**；依赖主链临时结论的工作不要委派。
- **结果必须立等可用**：子代理是后台线程 + 轮询，不是同步返回。

## 正确调用姿势

### 1. 写自包含的 prompt（关键！）

子代理看不到你的对话历史，prompt 必须包含：

```text
目标：<一句话要完成什么>
输入：<明确的工作区路径，如 work/repos/a/，用 sandbox_list_files 确认>
约束：<不允许做什么，如"不要删除 inputs/ 文件"、"只改 work/ 下文件">
输出契约：<最终回答必须包含什么，如"列出修改的文件路径 + 关键结论 + 遗留问题">
完成标准：<什么算完成，如"测试通过/文件已生成">
```

### 2. 选择工具子集（可选 `tools`）

默认子集 = 文件工具 + bash + exec + todo + apply_patch + git（离线）。仅在子代理只需只读调研时传更小的子集：

```json
{"tools": ["sandbox_list_files", "sandbox_grep", "sandbox_read_file", "sandbox_bash"]}
```

### 3. 轮询（不要死等）

`sandbox_subagent` 返回 `subagent_id` 后立即轮询 `sandbox_subagent_status`：

```text
1. 提交后先做你自己的其他工作（写文件、整理思路）。
2. 再查状态：queued → running → completed / failed / cancelled。
3. completed 后取 result 文本（≤16k）；failed 时看 error_class/error_message 决定重试还是降级。
4. 不要在一个回合里连续轮询多次——每次轮询是一轮工具调用，有模型成本。
```

### 4. 失败处理

- `failed` + `TimeoutError`：子代理超时（墙钟 300s / max_rounds），缩小任务范围或拆小重试。
- `failed` + 其他：把 error_message 带回主链，判断是 prompt 不清晰还是工具问题。
- 结果不满意：在结果上继续迭代（不要无脑重跑整个子代理）。

## 组合路线

```text
并行调研: subagent(prompt=调研A) + subagent(prompt=调研B) + subagent(prompt=调研C)
          → 各自轮询 → 汇总三份 result 成主链结论
拆分实现: 先写主设计 → 每个模块一个 subagent(输出契约: 文件路径+实现说明)
          → 主代理用 sandbox_apply_patch / sandbox_edit_file 复核合并
跟踪: subagent 提交前先 sandbox_todo add 任务 → 完成后 todo done
验证: subagent 产物用 sandbox_grep / sandbox_exec 复核
```

## 限制（务必遵守）

- 子代理**不能**触发用户授权（fetch/search/clone 不在默认工具集）；需要联网/授权的工作留在主链。
- 注册表是进程内的：应用重启后子代理结果丢失，`sandbox_subagent_status` 返回 not_found。
- `max_rounds`（默认 6，上限 12）与墙钟（默认 300s）是硬上限；任务写小写明确。
- 子代理与主代理共用同一沙箱工作区，注意文件命名冲突（用不同 work/ 子目录隔离）。
- 一个子代理的最终结果只回传纯文本；需要产物文件时让子代理写到工作区，主代理再读。

## 详细说明

- 工具输入输出契约见 `references/tool-contract.md`
- 子代理运行器实现与限制见 `references/runner.md`
