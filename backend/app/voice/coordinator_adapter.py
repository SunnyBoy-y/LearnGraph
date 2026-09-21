"""Bridge the embedded Tutor delegation port to the durable Agent coordinator.

The audio pipeline only knows the narrow ``VoiceDelegationPort`` protocol.  This
adapter resolves the durable voice session on a worker thread, rebuilds the
ordinary workspace permission context, and calls the existing coordinator.  It
deliberately contains no model calls, task scheduler, or audio output.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Mapping

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.domain.models import SandboxAgentTask
from app.services.chat_service_factory import (
    build_background_workspace_context,
    build_voice_chat_service,
)
from app.services.voice_agent_coordinator import VoiceAgentCoordinator
from app.services.voice_context import VOICE_MEMORY_AGENT_ID
from app.voice.coordinator import VoiceTaskCoordinator, capture_voice_task_outcome
from app.voice.embedded_delegation import (
    DelegationKind,
    DelegationRequest,
    TaskControlRequest,
    register_delegation_port_factory,
)
from app.voice.events import load_session
from app.voice.turns import open_turn


logger = logging.getLogger(__name__)

_READ_ONLY_TOOL_DEFAULTS = [
    "get_current_time",
    "sandbox_list_files",
    "sandbox_grep",
    "sandbox_read_file",
    "sandbox_skill_list",
    "sandbox_skill_read",
]


class CoordinatorDelegationPort:
    """Async, bounded adapter around the synchronous coordinator service."""

    def __init__(self, voice_session_id: str) -> None:
        self.voice_session_id = str(voice_session_id or "")

    async def delegate(self, request: DelegationRequest) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._delegate_sync, request)

    async def task_status(self, request: TaskControlRequest) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._task_status_sync, request.task_id)

    async def cancel_task(self, request: TaskControlRequest) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._cancel_task_sync, request.task_id)

    async def revise_task(self, request: TaskControlRequest) -> Mapping[str, Any]:
        return await asyncio.to_thread(
            self._revise_task_sync,
            request.task_id,
            request.instruction,
        )

    async def set_task_delivery(self, request: TaskControlRequest) -> Mapping[str, Any]:
        return await asyncio.to_thread(
            self._set_delivery_sync,
            request.task_id,
            request.instruction,
        )

    async def ready_results(self, *, limit: int = 10) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._ready_results_sync, limit)

    async def acknowledge_result(
        self,
        task_id: str,
        *,
        delivery_state: str = "delivered",
    ) -> Mapping[str, Any]:
        return await asyncio.to_thread(
            self._acknowledge_result_sync,
            task_id,
            delivery_state,
        )

    async def search_memory(self, query: str, *, limit: int = 5) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._search_memory_sync, query, limit)

    async def list_artifacts(self, query: str = "", *, limit: int = 5) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._list_artifacts_sync, query, limit)

    def _search_memory_sync(self, query: str, limit: int) -> dict[str, Any]:
        """Read memory through the ordinary chat read path, labelled as voice.

        Deliberately not routed through the task coordinator: this is a read of
        the same pool a typed question would read, and the only voice-specific
        part is the trace label.
        """

        text = str(query or "").strip()
        if not text:
            return {"items": [], "count": 0, "reason": "empty_query"}
        handle = load_session(self.voice_session_id)
        if handle is None:
            return {"items": [], "count": 0, "reason": "voice_session_not_found"}
        with SessionLocal() as db:
            chat = build_voice_chat_service(
                db,
                workspace_id=handle.workspace_id,
                actor_id=handle.owner_user_id,
                settings=get_settings(),
                model_id=handle.model_id,
                provider_id=handle.provider_id,
                thinking_mode=handle.max_thinking_mode,
            )
            result = chat.voice_memory_search(
                handle.chat_session_id,
                text,
                limit=limit,
                agent_id=VOICE_MEMORY_AGENT_ID,
            )
            # Same as the per-turn recall: this session is a read session, so the
            # commit only publishes the retrieval trace.  A failed commit is
            # reported, not raised -- the lookup result is already in hand -- and
            # closing the session discards the transaction for us.
            try:
                db.commit()
            except Exception:
                logger.warning("voice memory search trace commit failed", exc_info=True)
        return dict(result or {})

    def _list_artifacts_sync(self, query: str, limit: int) -> dict[str, Any]:
        """List what the artifacts page lists: cards, plus published collections.

        Both halves of that page are included on purpose -- a user asking "what
        have I got" means the page, not one of its two tables.
        """

        from app.services.artifact_cards import ArtifactCardService
        from app.services.artifact_gateway import ArtifactGatewayService

        needle = str(query or "").strip().casefold()
        top_k = max(1, min(int(limit or 5), 20))
        handle = load_session(self.voice_session_id)
        if handle is None:
            return {"items": [], "count": 0, "reason": "voice_session_not_found"}
        with self._coordinator() as (_coordinator, reliability, _chat, _turn):
            db = reliability.db
            cards = ArtifactCardService(
                db, reliability.workspace_id, reliability.tenant_id
            ).list_cards(sort="updated_at", order="desc", limit=50)
            collections = ArtifactGatewayService(
                db,
                reliability.workspace_id,
                handle.owner_user_id,
                reliability.tenant_id,
            ).list_artifact_summaries()
        return _artifact_items(cards, collections, needle=needle, top_k=top_k)

    def _delegate_sync(self, request: DelegationRequest) -> dict[str, Any]:
        with self._coordinator() as (
            coordinator,
            reliability,
            chat_session_id,
            turn_id,
        ):
            context = self._knowledge_context()
            if request.kind is DelegationKind.RESEARCH:
                result = coordinator.delegate_research(
                    query=request.query,
                    purpose=request.purpose,
                    context=context,
                    chat_session_id=chat_session_id,
                    voice_session_id=self.voice_session_id,
                    turn_id=turn_id,
                )
            elif request.kind is DelegationKind.REASONING:
                result = coordinator.delegate_reasoning(
                    question=request.query,
                    context=context,
                    chat_session_id=chat_session_id,
                    voice_session_id=self.voice_session_id,
                    turn_id=turn_id,
                )
            else:
                requested_tools = (
                    request.metadata.get("tools") if request.metadata else None
                )
                tools = (
                    [str(item) for item in requested_tools if str(item).strip()]
                    if isinstance(requested_tools, (list, tuple))
                    else list(_READ_ONLY_TOOL_DEFAULTS)
                )
                result = coordinator.delegate_tool_task(
                    goal=request.query,
                    tools=tools,
                    context=context,
                    chat_session_id=chat_session_id,
                    voice_session_id=self.voice_session_id,
                    turn_id=turn_id,
                )
            self._record_task_link(
                reliability,
                task_id=str(result.get("task_id") or ""),
                job_id=str(result.get("job_id") or "") or None,
                role_key=str(result.get("role_key") or request.kind.value),
                prompt=request.query,
                title=str(result.get("title") or request.query[:80]),
                thinking_mode=str(result.get("thinking_mode_requested") or "medium"),
                chat_session_id=chat_session_id,
                turn_id=turn_id,
            )
            return dict(result)

    def _task_status_sync(self, task_id: str) -> dict[str, Any]:
        with self._coordinator() as (coordinator, _reliability, _chat_session_id, _turn_id):
            return coordinator.get_background_task(task_id)

    def _cancel_task_sync(self, task_id: str) -> dict[str, Any]:
        with self._coordinator() as (coordinator, reliability, _chat_session_id, _turn_id):
            link = reliability.required_link(self.voice_session_id, task_id)
            reliability.request_cancel(link, reason="voice_tutor")
            result = coordinator.cancel_background_task(task_id)
            reliability.acknowledge_cancel(link, dict(result))
            return result

    def _revise_task_sync(self, task_id: str, instruction: str) -> dict[str, Any]:
        if not str(instruction or "").strip():
            raise AppError(
                422,
                "voice_revision_required",
                "A revised request is required",
            )
        with self._coordinator() as (coordinator, reliability, chat_session_id, turn_id):
            previous = coordinator.get_background_task(task_id)
            link = reliability.required_link(self.voice_session_id, task_id)
            reliability.revise_requirement(
                link,
                prompt=instruction,
                note="voice_tutor",
            )
            result = coordinator.revise_background_task(
                task_id,
                revised_request=instruction,
                turn_id=turn_id,
            )
            replacement = dict(result.get("new_task") or {})
            replacement_id = str(
                replacement.get("task_id")
                or replacement.get("subagent_id")
                or ""
            )
            self._record_task_link(
                reliability,
                task_id=replacement_id,
                job_id=str(replacement.get("job_id") or "") or None,
                role_key=str(previous.get("role_key") or "generic"),
                prompt=instruction,
                title=str(previous.get("title") or instruction[:80]),
                thinking_mode=str(
                    previous.get("thinking_mode_effective")
                    or previous.get("thinking_mode_requested")
                    or "medium"
                ),
                chat_session_id=chat_session_id,
                turn_id=turn_id,
                requirement_version=int(result.get("requirement_version") or 2),
            )
            reliability.mark_cancel_superseded(link, status="SUPERSEDED")
            return result

    def _set_delivery_sync(self, task_id: str, instruction: str) -> dict[str, Any]:
        with self._coordinator() as (_coordinator, reliability, _chat, _turn):
            link = reliability.required_link(self.voice_session_id, task_id)
            link.auto_delivery = str(instruction or "").strip().casefold() not in {
                "manual",
                "false",
                "off",
                "pause",
            }
            reliability.db.commit()
            reliability.db.refresh(link)
            return {
                "task_id": link.subagent_id,
                "status": link.status,
                "auto_delivery": bool(link.auto_delivery),
            }


    def _ready_results_sync(self, limit: int) -> list[dict[str, Any]]:
        with self._coordinator() as (
            _coordinator,
            reliability,
            _chat_session_id,
            _turn_id,
        ):
            links = reliability.list_links(self.voice_session_id)
            for link in links:
                if not link.subagent_id or link.subagent_id.startswith("pending_"):
                    continue
                task = reliability.db.scalar(
                    select(SandboxAgentTask).where(
                        SandboxAgentTask.workspace_id == reliability.workspace_id,
                        SandboxAgentTask.task_id == link.subagent_id,
                    )
                )
                if task is not None:
                    capture_voice_task_outcome(reliability.db, task)
            reliability.db.commit()
            rows = reliability.list_results(
                self.voice_session_id,
                include_terminal=False,
                limit=limit,
            )
            by_task = {link.subagent_id: link for link in links}
            results: list[dict[str, Any]] = []
            for row in rows:
                payload = dict(row.payload_json or {})
                link = by_task.get(row.subagent_id)
                results.append(
                    {
                        "task_id": row.subagent_id,
                        "subagent_id": row.subagent_id,
                        "result_id": row.id,
                        "result_version": int(row.result_version or 1),
                        "requirement_version": int(row.requirement_version or 1),
                        "status": str(row.status or "ready"),
                        "title": str(getattr(link, "title", "") or "后台任务"),
                        "summary": str(row.summary or ""),
                        "source_count": int(row.source_count or 0),
                        "safe_error": str(row.safe_error or ""),
                        "agent_result": payload.get("agent_result") or payload,
                        "auto_delivery": bool(getattr(link, "auto_delivery", True)),
                        "deliveries": payload.get("deliveries") or {},
                    }
                )
            return results

    def _acknowledge_result_sync(
        self,
        task_id: str,
        delivery_state: str,
    ) -> dict[str, Any]:
        with self._coordinator() as (
            _coordinator,
            reliability,
            _chat_session_id,
            _turn_id,
        ):
            row = next(
                (
                    item
                    for item in reliability.list_results(
                        self.voice_session_id,
                        include_terminal=True,
                        limit=200,
                    )
                    if item.subagent_id == task_id
                ),
                None,
            )
            if row is None:
                raise AppError(
                    404,
                    "voice_result_not_found",
                    "Voice result was not found",
                )
            if delivery_state == "dismissed":
                dismissed = reliability.dismiss_result(
                    voice_session_id=self.voice_session_id,
                    result_id=row.id,
                )
                return {"result_id": dismissed.id, "status": dismissed.status}
            request_id = f"voice-auto:{row.id}:{int(row.result_version or 1)}"
            if delivery_state == "released":
                released = reliability.release_delivery(
                    voice_session_id=self.voice_session_id,
                    result_id=row.id,
                    request_id=request_id,
                )
                return {"result_id": released.id, "status": released.status, "created": False}
            result, delivery, created = reliability.deliver_result(
                voice_session_id=self.voice_session_id,
                result_id=row.id,
                request_id=request_id,
            )
            return {
                "result_id": result.id,
                "delivery_id": delivery.id,
                "speech_id": delivery.speech_id,
                "status": result.status,
                "created": created,
            }


    def _record_task_link(
        self,
        reliability: VoiceTaskCoordinator,
        *,
        task_id: str,
        job_id: str | None,
        role_key: str,
        prompt: str,
        title: str,
        thinking_mode: str,
        chat_session_id: str,
        turn_id: str | None,
        requirement_version: int = 1,
    ) -> None:
        if not task_id:
            return
        idempotency_key = f"voice-agent-link:{task_id}"
        link, created = reliability.reserve_link(
            voice_session_id=self.voice_session_id,
            chat_session_id=chat_session_id,
            prompt=prompt,
            title=title,
            role_key=role_key,
            thinking_mode=thinking_mode,
            trigger_turn_id=turn_id,
            requirement_version=requirement_version,
            idempotency_key=idempotency_key,
        )
        if (
            created
            or link.subagent_id.startswith("pending_")
            or link.subagent_id != task_id
        ):
            reliability.complete_link(
                link,
                subagent_id=task_id,
                job_id=job_id,
                status="queued",
            )


    class _CoordinatorContext:
        def __init__(self, port: "CoordinatorDelegationPort", db: Any) -> None:
            self.port = port
            self.db = db
            self.handle = load_session(port.voice_session_id)
            if self.handle is None:
                raise AppError(404, "voice_session_not_found", "Voice session was not found")
            context = build_background_workspace_context(
                db,
                workspace_id=self.handle.workspace_id,
                actor_id=self.handle.owner_user_id,
            )
            self.chat_session_id = self.handle.chat_session_id
            turn = open_turn(port.voice_session_id)
            self.turn_id = str((turn or {}).get("turn_id") or "") or None
            self.coordinator = VoiceAgentCoordinator(
                db,
                get_settings(),
                workspace_id=self.handle.workspace_id,
                actor_id=self.handle.owner_user_id,
                permissions=context.permissions,
            )
            self.reliability = VoiceTaskCoordinator(
                db,
                workspace_id=self.handle.workspace_id,
                tenant_id=context.principal.tenant_id,
            )

        def __enter__(
            self,
        ) -> tuple[VoiceAgentCoordinator, VoiceTaskCoordinator, str, str | None]:
            return (
                self.coordinator,
                self.reliability,
                self.chat_session_id,
                self.turn_id,
            )

        def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
            self.db.close()
            return False

    def _coordinator(self) -> "_CoordinatorContext":
        db = SessionLocal()
        try:
            return self._CoordinatorContext(self, db)
        except Exception:
            db.close()
            raise

    def _knowledge_context(self) -> dict[str, Any]:
        handle = load_session(self.voice_session_id)
        snapshot = dict(getattr(handle, "context_snapshot", None) or {})
        return {
            "voice_session_id": self.voice_session_id,
            "chat_session_id": getattr(handle, "chat_session_id", None),
            "context_build_id": snapshot.get("context_build_id") or snapshot.get("version"),
            "prompt_block": str(snapshot.get("prompt_block") or "")[:24_000],
        }


_FACTORY_INSTALLED = False


def _artifact_items(
    cards: list[dict[str, Any]],
    collections: list[tuple[Any, int]],
    *,
    needle: str,
    top_k: int,
) -> dict[str, Any]:
    """Shape cards and published collections into one list for the tutor.

    Split out of the adapter so the filtering and ordering can be asserted
    without a database: it is the part that decides what the tutor is allowed to
    claim exists.
    """

    items: list[dict[str, Any]] = []
    for card in cards:
        title = str(card.get("title") or "")
        if needle and needle not in title.casefold():
            continue
        updated_at = card.get("updated_at")
        items.append(
            {
                "kind": "card",
                "id": str(card.get("card_id") or ""),
                "title": title,
                "status": str(card.get("status") or ""),
                "card_type": str(card.get("card_type") or ""),
                "interactive": bool(card.get("interactive")),
                "version_count": int(card.get("version_count") or 0),
                "chat_session_id": card.get("chat_session_id"),
                "updated_at": updated_at.isoformat() if updated_at is not None else None,
            }
        )
    for artifact, version_count in collections:
        name = str(getattr(artifact, "name", "") or "")
        if needle and needle not in name.casefold():
            continue
        created_at = getattr(artifact, "created_at", None)
        items.append(
            {
                "kind": "collection",
                "id": str(getattr(artifact, "id", "")),
                "title": name,
                "status": str(getattr(artifact, "status", "") or ""),
                "version_count": int(version_count or 0),
                "updated_at": created_at.isoformat() if created_at is not None else None,
            }
        )
    items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return {"items": items[:top_k], "count": min(len(items), top_k)}


def ensure_coordinator_delegation_port_factory() -> None:
    """Install the process-level factory once before the Tutor is constructed."""
    global _FACTORY_INSTALLED
    if _FACTORY_INSTALLED:
        return
    register_delegation_port_factory(
        lambda voice_session_id: CoordinatorDelegationPort(voice_session_id)
    )
    _FACTORY_INSTALLED = True
