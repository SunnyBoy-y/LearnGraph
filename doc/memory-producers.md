# Memory Domain Producers

Each memory entry point has exactly one producer. Session chat extraction and
domain projections are deliberately disjoint: the ordinary chat extractor only
consumes completed user messages, while exercise, file, task, and agent-run
events are consumed by their own projections.

| 入口 | 事件 | 唯一生产者服务 | 当前状态 |
| --- | --- | --- | --- |
| 普通聊天 | `message.completed` | `ChatService` -> `enqueue_memory_extraction` | 已实现（M0） |
| 题目作答 | `learning.evidence_recorded` | `ExerciseService.answer`（批改服务） | 已补齐（M4） |
| 文档处理完成 | `document.parse_index` / `artifact.revision_activated` | `DocumentLearningService` + `MemoryFileInvalidationService` | 已实现 |
| 任务状态变化 | `task.*` | `MemoryTaskService`（`memory_tasks.py`） | 已实现 |
| 文件更新 | `artifact.revision_invalidated` / `activated` | `MemoryFileInvalidationService` | 已实现 |
| Agent Run 结束 | `agent.run_completed` / `agent.run_failed` | `AgentRunProjectionService` / `ChatService._record_agent_run_event` | 已补齐（M4） |

## 防重复规则

- `MemoryExtractionState` 只覆盖会话类入口；`memory_enhancement._new_messages_since`
  查询限定 `Message.role == "user"`。
- 题目、文件、任务、Agent Run 不进入普通 `extract_session_memories`。
- 领域事件通过 `MemoryEventIngestor.ingest()` 写入，`producer` 使用
  `api / chat / file / tool / scheduler / migration / agent` 枚举。
- 题目提交只产生 `learning.evidence_recorded`，不产生 `memory.atom_created`，
  避免错题原文污染用户画像。
- Agent Run 结果只作为事件/投影，`summary_eligibility=excluded`，不直接写用户记忆。
