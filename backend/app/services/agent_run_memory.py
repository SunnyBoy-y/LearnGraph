from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.memory_event_models import AgentRunProjection, MemoryScopeContext, StrategyMemory, new_id, utc_now
from app.domain.memory_event_types import MemoryEventType
from app.services.memory_event_store import AppendEvent, MemoryEventStore


@dataclass(frozen=True, slots=True)
class RunStart:
    task_id: str | None
    agent_id: str
    model_id: str
    input_scope_hash: str
    context_build_id: str | None
    idempotency_key: str


class AgentRunProjectionService:
    """Projects verifiable run facts only; hidden reasoning is never accepted."""

    def __init__(self, db: Session, store: MemoryEventStore) -> None:
        self.db = db
        self.store = store

    def start(self, scope: MemoryScopeContext, actor_id: str, request: RunStart) -> AgentRunProjection:
        run_id = f"run_{new_id()}"
        event = self.store.append(
            scope,
            aggregate_type="agent_run",
            aggregate_id=run_id,
            expected_version=0,
            event=AppendEvent(
                event_type=MemoryEventType.AGENT_RUN_STARTED,
                payload={
                    "task_id": request.task_id,
                    "agent_id": request.agent_id,
                    "model_id": request.model_id,
                    "input_scope_hash": request.input_scope_hash,
                    "context_build_id": request.context_build_id,
                },
                idempotency_key=request.idempotency_key,
                actor_type="agent",
                actor_id=actor_id,
            ),
        ).event
        row = AgentRunProjection(
            id=run_id,
            stream_id=event.stream_id,
            tenant_id=scope.tenant_id,
            subject_user_id=scope.principal_user_id,
            workspace_id=scope.workspace_id,
            task_id=request.task_id,
            status="running",
            agent_id=request.agent_id,
            model_id=request.model_id,
            input_scope_hash=request.input_scope_hash,
            context_build_id=request.context_build_id,
            head_event_id=event.event_id,
        )
        self.db.add(row)
        self.db.commit()
        return row

    def complete(
        self,
        scope: MemoryScopeContext,
        run_id: str,
        *,
        actor_id: str,
        expected_version: int,
        result_summary: str,
        tool_call_refs: list[str],
        artifact_refs: list[str],
        succeeded: bool,
        idempotency_key: str,
    ) -> AgentRunProjection:
        row = self.db.scalar(
            select(AgentRunProjection).where(
                AgentRunProjection.id == run_id,
                AgentRunProjection.tenant_id == scope.tenant_id,
                AgentRunProjection.workspace_id == scope.workspace_id,
            )
        )
        if row is None:
            raise LookupError("agent run not found")
        event = self.store.append(
            scope,
            aggregate_type="agent_run",
            aggregate_id=run_id,
            expected_version=expected_version,
            event=AppendEvent(
                event_type=(
                    MemoryEventType.AGENT_RUN_COMPLETED
                    if succeeded
                    else MemoryEventType.AGENT_RUN_FAILED
                ),
                payload={
                    "result_summary": result_summary,
                    "tool_call_refs": tool_call_refs,
                    "artifact_refs": artifact_refs,
                    "succeeded": succeeded,
                },
                idempotency_key=idempotency_key,
                actor_type="agent",
                actor_id=actor_id,
            ),
        ).event
        row.status = "completed" if succeeded else "failed"
        row.result_summary = result_summary[:10_000]
        row.tool_call_refs_json = tool_call_refs
        row.artifact_refs_json = artifact_refs
        row.ended_at = utc_now()
        row.head_event_id = event.event_id
        row.projection_version = event.stream_version
        self.db.commit()
        return row


class StrategyPromotionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def evaluate(self, strategy: StrategyMemory, *, user_confirmed: bool = False) -> StrategyMemory:
        successes = len(set(strategy.successful_run_ids_json))
        failures = len(set(strategy.failed_run_ids_json))
        total = successes + failures
        strategy.success_rate = successes / total if total else 0.0
        if successes >= 2 or (successes >= 1 and user_confirmed):
            strategy.status = "verified"
        elif total >= 3 and strategy.success_rate < 0.5:
            strategy.status = "degraded"
        else:
            strategy.status = "candidate"
        strategy.confidence = min(1.0, total / 5.0) * strategy.success_rate
        self.db.flush()
        return strategy
