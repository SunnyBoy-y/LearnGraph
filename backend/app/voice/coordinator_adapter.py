"""Bridge the embedded Tutor delegation port to the durable Agent coordinator.

The audio pipeline only knows the narrow ``VoiceDelegationPort`` protocol.  This
adapter resolves the durable voice session on a worker thread, rebuilds the
ordinary workspace permission context, and calls the existing coordinator.  It
deliberately contains no model calls, task scheduler, or audio output.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.errors import AppError
from app.services.authorization import AuthorizationService
from app.domain.models import SandboxAgentTask, SandboxAgentEvent, VoiceSpeechDeliveryRecord, VoiceTurnRecord
from app.services.chat_service_factory import (
    build_background_workspace_context,
    build_voice_chat_service,
)
from app.services.learning import ExerciseService
from app.domain.schemas.learning import AnswerRequest
from app.domain.models import Exercise
from app.services.voice_agent_coordinator import VoiceAgentCoordinator
from app.services.voice_context import VOICE_MEMORY_AGENT_ID
from app.voice.coordinator import VoiceTaskCoordinator, capture_voice_task_outcome
from app.voice.capabilities import resolve_voice_capability
from app.voice.embedded_delegation import (
    DelegationKind,
    DelegationRequest,
    TaskControlRequest,
    VoiceExerciseAnswerRequest,
    register_delegation_port_factory,
)
from app.voice.events import emit_event, load_session
from app.voice.turns import open_turn
from app.voice.scene import scene_grounding, speech_brief, valid_visuals


logger = logging.getLogger(__name__)

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

    async def pause_task(self, request: TaskControlRequest) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._control_execution_sync, request.task_id, "pause")

    async def resume_task(self, request: TaskControlRequest) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._control_execution_sync, request.task_id, "resume")

    async def focus_scene(self, request: TaskControlRequest) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._focus_scene_sync, request.task_id, request.instruction)

    async def show_scene(self, task_id: str, anchor_id: str) -> Mapping[str, Any]:
        return await asyncio.to_thread(self._focus_scene_sync, task_id, anchor_id, presentation_mode="display")

    def _focus_scene_sync(self, task_id: str, anchor_id: str, *, expected_turn_id: str | None = None,
                          presentation_mode: str = "explain") -> dict[str, Any]:
        with self._coordinator() as (_coordinator, reliability, _chat, turn_id):
            if expected_turn_id is not None and turn_id != expected_turn_id:
                return {"task_id": task_id, "status": "unavailable"}
            link = reliability.required_link(self.voice_session_id, task_id)
            row = next((item for item in reliability.list_results(self.voice_session_id, include_terminal=True, limit=200)
                        if item.subagent_id == task_id and item.requirement_version == link.requirement_version
                        and item.status not in {"stale", "dismissed"}), None)
            if row is None:
                return {"task_id": task_id, "status": "unavailable"}
            handle = load_session(self.voice_session_id)
            scene = scene_grounding(dict(row.payload_json or {}), summary=row.summary or "",
                session_epoch=int(getattr(handle, "session_epoch", 1)), requirement_version=row.requirement_version,
                result_version=row.result_version)
            if anchor_id not in scene["available_anchors"]:
                return {"task_id": task_id, "status": "unconfirmed", "instruction": "页面位置未确认，请直接口述。"}
            emit_event(self.voice_session_id, "scene.focus", payload={
                "task_id": task_id, "result_id": row.id, "requirement_version": row.requirement_version,
                "result_version": row.result_version,
                "anchor_id": anchor_id,
                "presentation_mode": presentation_mode,
            }, turn_id=turn_id, request_id="scene-focus:" + hashlib.sha256(
                f"{getattr(handle, 'session_epoch', 1)}:{turn_id}:{row.id}:{row.result_version}:{anchor_id}:{presentation_mode}".encode()).hexdigest()[:40])
            rendered = anchor_id in scene["anchors"]
            return {"task_id": task_id, "status": "focused" if rendered else "requested", "anchor_id": anchor_id,
                    "instruction": "已定位到已显示组件。" if rendered else "已请求临时展开和定位，收到页面显示回执前不要声称用户已看见；可以依据结果口述。"}

    def _control_execution_sync(self, task_id: str, operation: str) -> dict[str, Any]:
        from app.voice.task_control import control_task
        with self._coordinator() as (coordinator, reliability, _chat, _turn):
            link = reliability.required_link(self.voice_session_id, task_id)
            if link.capability_id:
                self._require_capability_permission(coordinator, link.capability_id)
            else:
                coordinator._require_workspace_manage()
            return control_task(reliability, voice_session_id=self.voice_session_id, task_id=task_id,
                                operation=operation, actor_id=coordinator.actor_id)

    async def confirm_task(self, request: TaskControlRequest) -> Mapping[str, Any]:
        return await asyncio.to_thread(
            self._confirm_task_sync,
            request.task_id,
            request.instruction,
        )

    async def submit_exercise_answer(
        self, request: VoiceExerciseAnswerRequest
    ) -> Mapping[str, Any]:
        return await asyncio.to_thread(
            self._submit_exercise_answer_sync,
            request,
        )

    async def ready_results(self, *, limit: int = 10) -> list[dict[str, Any]]:
        return await asyncio.to_thread(self._ready_results_sync, limit)

    async def current_results(self, *, limit: int = 200) -> list[dict[str, Any]]:
        """Scene lookup and post-claim validation also need delivered results."""
        return await asyncio.to_thread(self._ready_results_sync, limit, include_delivered=True)

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

    async def update_speech_delivery(
        self, speech_id: str, state: str
    ) -> Mapping[str, Any]:
        return await asyncio.to_thread(
            self._update_speech_delivery_sync,
            speech_id,
            state,
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
            settings = get_settings()
            base_context = self._knowledge_context()
            request_context = request.context
            if isinstance(request_context, Mapping):
                request_context_value: dict[str, Any] = dict(request_context)
            else:
                request_context_value = {"user_context": str(request_context or "")}
            context = {
                **base_context,
                "request": request_context_value,
                "purpose": request.purpose,
                "capability_id": request.capability_id,
                "session_epoch": request.session_epoch,
                "context_version": request.context_version,
                # Server-owned policy: the foreground Tutor only enqueues;
                # independent background tasks may be scheduled concurrently
                # up to the scheduler's configured chat/user/workspace quotas.
                "execution_policy": {
                    "lane": "background_agent",
                    "parallel_allowed": True,
                    "parallel_max_tasks": min(
                        (
                            limit
                            for limit in (
                                settings.sandbox_subagent_max_parallel,
                                settings.sandbox_subagent_max_concurrent_chat,
                                settings.sandbox_subagent_max_concurrent_user,
                                settings.sandbox_subagent_max_concurrent_workspace,
                            )
                            if limit > 0
                        ),
                        default=0,
                    ),
                    "foreground_must_not_wait": True,
                },
            }
            capability_id = request.capability_id
            if not capability_id:
                capability_id = {
                    DelegationKind.RESEARCH: "research",
                    DelegationKind.REASONING: "reasoning",
                    DelegationKind.TOOL: "workspace_read",
                }[request.kind]
            capability = resolve_voice_capability(capability_id)
            effective_turn_id = request.trigger_turn_id or turn_id
            if capability.requires_confirmation and not bool(
                (request.metadata or {}).get("confirmed")
            ):
                approval_payload = {
                    "kind": request.kind.value,
                    "query": request.query,
                    "purpose": request.purpose,
                    "context": context,
                    "capability_id": capability.capability_id,
                    "turn_id": effective_turn_id,
                    "session_epoch": request.session_epoch,
                    "context_version": request.context_version,
                }
                approval_digest = hashlib.sha256(
                    json.dumps(
                        {
                            "voice_session_id": self.voice_session_id,
                            "turn_id": effective_turn_id,
                            "capability_id": capability.capability_id,
                            "query": request.query,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()[:32]
                link, _created = reliability.reserve_link(
                    voice_session_id=self.voice_session_id,
                    chat_session_id=chat_session_id,
                    prompt=request.query,
                    title=capability.capability_id,
                    role_key=capability.role_key,
                    thinking_mode="medium",
                    trigger_turn_id=effective_turn_id,
                    idempotency_key=f"voice-approval:{approval_digest}",
                    allowed_tools=list(capability.tools),
                    capability_id=capability.capability_id,
                    status="waiting_approval",
                    approval_status="pending",
                    approval_request=approval_payload,
                    approval_expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                )
                try:
                    emit_event(
                        self.voice_session_id,
                        "task.approval_required",
                        payload={
                            "task_id": link.subagent_id,
                            "subagent_id": link.subagent_id,
                            "status": "waiting_approval",
                            "capability_id": capability.capability_id,
                            "approval_status": link.approval_status,
                            "approval_expires_at": (
                                link.approval_expires_at.isoformat()
                                if link.approval_expires_at
                                else None
                            ),
                            "title": capability.capability_id,
                        },
                        turn_id=effective_turn_id,
                        request_id=f"voice-task-approval:{link.id}",
                    )
                except Exception:
                    logger.debug("voice task approval event failed", exc_info=True)
                return {
                    "status": "needs_confirmation",
                    "task_id": link.subagent_id,
                    "capability_id": capability.capability_id,
                    "requires_confirmation": True,
                    "approval_status": link.approval_status,
                    "approval_expires_at": (
                        link.approval_expires_at.isoformat()
                        if link.approval_expires_at
                        else None
                    ),
                    "message": "该操作会修改学习资料或工作区，请先确认后再执行。",
                }
            if request.kind is DelegationKind.RESEARCH:
                result = coordinator.delegate_research(
                    query=request.query,
                    purpose=request.purpose,
                    context=context,
                    chat_session_id=chat_session_id,
                    voice_session_id=self.voice_session_id,
                    turn_id=effective_turn_id,
                )
            elif request.kind is DelegationKind.REASONING:
                result = coordinator.delegate_reasoning(
                    question=request.query,
                    context=context,
                    chat_session_id=chat_session_id,
                    voice_session_id=self.voice_session_id,
                    turn_id=effective_turn_id,
                )
            else:
                result = coordinator.delegate(
                    role_key=capability.role_key,
                    capability_id=capability.capability_id,
                    objective=request.query,
                    title=capability.capability_id,
                    tools=list(capability.tools),
                    skills=list(capability.skills),
                    write_set=list(capability.write_set),
                    output_contract=dict(capability.output_contract or {}),
                    context=context,
                    chat_session_id=chat_session_id,
                    voice_session_id=self.voice_session_id,
                    turn_id=effective_turn_id,
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
                turn_id=effective_turn_id,
                allowed_tools=list(capability.tools),
            )
            try:
                emit_event(
                    self.voice_session_id,
                    "task.accepted",
                    payload={
                        **dict(result),
                        "task_id": result.get("task_id") or result.get("subagent_id"),
                        "status": result.get("status") or "queued",
                    },
                    turn_id=effective_turn_id,
                    request_id=f"voice-task-accepted:{result.get('task_id') or result.get('subagent_id')}",
                )
            except Exception:
                logger.debug("voice task accepted event failed", exc_info=True)
            return dict(result)

    def _task_status_sync(self, task_id: str) -> dict[str, Any]:
        with self._coordinator() as (coordinator, reliability, _chat_session_id, _turn_id):
            reliability.required_link(self.voice_session_id, task_id)
            result = coordinator.get_background_task(task_id)
            result["agent_result"] = speech_brief(result.get("agent_result"))
            result.pop("deliverables", None)
            result["events"] = list(result.get("events") or [])[-5:]
            row = next((item for item in reliability.list_results(self.voice_session_id, include_terminal=True, limit=200)
                        if item.subagent_id == task_id and item.status not in {"stale", "dismissed"}), None)
            if row is not None:
                handle = load_session(self.voice_session_id)
                result["scene"] = scene_grounding(dict(row.payload_json or {}), summary=row.summary or "",
                    session_epoch=int(getattr(handle, "session_epoch", 1)),
                    requirement_version=row.requirement_version, result_version=row.result_version)
                result["scene"] = {key: result["scene"][key] for key in ("status", "anchors", "available_anchors")}
            else:
                # Published intermediate scenes live in the durable event log,
                # independently of terminal results. Expose bounded data rather
                # than tool transcripts or speculative hidden reasoning.
                task = coordinator._load_task(task_id)
                events = reliability.db.scalars(select(SandboxAgentEvent).where(
                    SandboxAgentEvent.workspace_id == reliability.workspace_id,
                    SandboxAgentEvent.task_id == task.id,
                    SandboxAgentEvent.event_type == "artifact.upsert",
                ).order_by(SandboxAgentEvent.seq.desc()).limit(40)).all()
                blocks: dict[str, Any] = {}
                removed_blocks: set[str] = set()
                for event in events:
                    payload = dict(event.payload or {})
                    if int(payload.get("requirement_version") or 1) != int(result.get("requirement_version") or 1):
                        continue
                    raw_scene = payload.get("snapshot")
                    if isinstance(raw_scene, dict) and raw_scene.get("state") == "removed" and raw_scene.get("block_id") not in blocks:
                        removed_blocks.add(str(raw_scene.get("block_id") or ""))
                    for scene in valid_visuals([payload.get("snapshot")]):
                        if scene["block_id"] in blocks or scene["block_id"] in removed_blocks or len(blocks) >= 8:
                            continue
                        blocks[scene["block_id"]] = {
                            "anchor_id": f"visual-{scene['block_id']}", "revision": scene["revision"],
                            "title": scene["props"]["title"], "source_note": scene["props"]["source_note"],
                            "state": scene["state"], "evidence_status": "worker_reported",
                            "columns": scene["props"]["columns"][:5],
                            "rows": [{"id": item["id"], "label": item["label"][:120],
                                      "values": {key: value[:120] if isinstance(value, str) else value
                                                 for key, value in list(item["values"].items())[:5]}}
                                     for item in scene["rows"][:5]],
                            "items": [{"id": item["id"], "label": item["label"], "value": str(item["value"])[:120]}
                                      for item in scene["items"][:5]],
                            "nodes": [{"id": item["id"], "label": item["label"]} for item in scene["nodes"][:5]],
                            "truncated": True,
                        }
                result["partial_results"] = list(blocks.values())
                while len(json.dumps(result["partial_results"], ensure_ascii=False)) > 12_000:
                    result["partial_results"].pop()
                result["scene"] = {"status": "unconfirmed", "anchors": []}
            result["events"] = []  # execution logs belong in the task UI, not speech context
            return result

    def _cancel_task_sync(self, task_id: str) -> dict[str, Any]:
        with self._coordinator() as (coordinator, reliability, _chat_session_id, _turn_id):
            link = reliability.required_link(self.voice_session_id, task_id)
            if link.status == "waiting_approval":
                rejected = reliability.reject_link(link, reason="voice_tutor_cancel")
                return {
                    "task_id": rejected.subagent_id,
                    "status": "cancelled",
                    "approval_status": rejected.approval_status,
                }
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

    def _confirm_task_sync(self, task_id: str, instruction: str) -> dict[str, Any]:
        with self._coordinator() as (
            coordinator,
            reliability,
            _chat_session_id,
            _turn_id,
        ):
            link = reliability.required_link(self.voice_session_id, task_id)
            if link.capability_id:
                self._require_capability_permission(coordinator, link.capability_id)
            approved = str(instruction or "approve").strip().casefold() not in {
                "reject",
                "rejected",
                "deny",
                "denied",
                "no",
                "false",
            }
            if not approved:
                rejected = reliability.reject_link(link, reason="voice_user_rejected")
                try:
                    emit_event(
                        self.voice_session_id,
                        "task.rejected",
                        payload={
                            "task_id": rejected.subagent_id,
                            "status": "cancelled",
                            "approval_status": rejected.approval_status,
                        },
                        turn_id=rejected.trigger_turn_id,
                        request_id=f"voice-task-rejected:{rejected.id}",
                    )
                except Exception:
                    logger.debug("voice task rejection event failed", exc_info=True)
                return {
                    "task_id": rejected.subagent_id,
                    "status": "cancelled",
                    "approval_status": rejected.approval_status,
                }
            reliability.approve_link(link)
            try:
                result = coordinator.approve_pending_voice_task(link)
            except Exception:
                link.status = "FAILED"
                link.approval_status = "failed"
                reliability.db.commit()
                raise
            reliability.complete_link(
                link,
                subagent_id=str(result.get("task_id") or result.get("subagent_id") or ""),
                job_id=str(result.get("job_id") or "") or None,
                status=str(result.get("status") or "queued"),
            )
            result = dict(result)
            result["approval_status"] = "approved"
            result["previous_task_id"] = task_id
            try:
                emit_event(
                    self.voice_session_id,
                    "task.approved",
                    payload={
                        **result,
                        "status": result.get("status") or "queued",
                    },
                    turn_id=link.trigger_turn_id,
                    request_id=f"voice-task-approved:{link.id}",
                )
            except Exception:
                logger.debug("voice task approval event failed", exc_info=True)
            return result

    @staticmethod
    def _require_capability_permission(
        coordinator: VoiceAgentCoordinator,
        capability_id: str,
    ) -> None:
        """Keep voice-tool approval under the same server permission gate as HTTP."""

        coordinator._require_capability_permission(capability_id)

    def _submit_exercise_answer_sync(
        self, request: VoiceExerciseAnswerRequest
    ) -> dict[str, Any]:
        handle = load_session(self.voice_session_id)
        if handle is None:
            raise AppError(404, "voice_session_not_found", "Voice session was not found")
        with SessionLocal() as db:
            context = build_background_workspace_context(
                db,
                workspace_id=handle.workspace_id,
                actor_id=handle.owner_user_id,
            )
            exercise = db.scalar(
                select(Exercise).where(
                    Exercise.id == request.exercise_id,
                    Exercise.workspace_id == context.workspace_id,
                )
            )
            if exercise is None:
                raise AppError(404, "exercise_not_found", "Exercise was not found")
            if not AuthorizationService(db, context.principal).can_access_bindings(
                context.workspace, "write", node_id=exercise.node_id
            ):
                raise AppError(403, "exercise_forbidden", "Exercise is not writable")
            value: str | list[str] = request.answer.strip()
            if exercise.question_type == "multiple_choice":
                raw_parts = [
                    item.strip()
                    for item in re.split(r"[,，、;；/|\n]+", value)
                    if item.strip()
                ]
                options = list(exercise.options or [])
                selected: list[str] = []
                for part in raw_parts:
                    if len(part) == 1 and part.isalpha():
                        index = ord(part.upper()) - ord("A")
                        if 0 <= index < len(options):
                            selected.append(options[index])
                            continue
                    selected.append(part)
                value = selected
            provider = None
            try:
                from app.providers.factory import model_provider_for_workspace

                provider = model_provider_for_workspace(
                    db, context.workspace_id, get_settings()
                )
            except Exception:
                logger.debug("voice exercise grading provider resolution failed", exc_info=True)
            result = ExerciseService(
                db,
                context.workspace_id,
                context.principal.user_id,
                provider,
                get_settings(),
            ).submit_answer(
                request.exercise_id,
                AnswerRequest(answer=value),
                attempt_index=request.attempt_index,
            )
            return result.model_dump(mode="json")


    def _ready_results_sync(self, limit: int, *, include_delivered: bool = False) -> list[dict[str, Any]]:
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
                include_terminal=True,
                limit=200,
            )
            handle = load_session(self.voice_session_id)
            recent_turns = list(reliability.db.scalars(select(VoiceTurnRecord).where(
                VoiceTurnRecord.voice_session_id == self.voice_session_id,
            ).order_by(VoiceTurnRecord.created_at.desc()).limit(32)))
            from app.voice.turn_plan import topic_turn_ids
            from app.voice.embedded_turn_intent import is_asr_filler, is_backchannel
            # Keep related follow-ups in the originating topic. A new independent
            # request fences automatic delivery without cancelling its task.
            topic_turns = topic_turn_ids([{'turn_id': turn.id, 'user_text': turn.user_text} for turn in recent_turns if turn.user_text and
                not is_asr_filler(turn.user_text) and not is_backchannel(turn.user_text) and
                turn.status != 'failed'])
            topic_id = next((turn.id for turn in reversed(recent_turns) if turn.id in topic_turns), None)
            by_task = {link.subagent_id: link for link in links}
            results: list[dict[str, Any]] = []
            for row in rows:
                payload = dict(row.payload_json or {})
                link = by_task.get(row.subagent_id)
                if link is not None and int(row.requirement_version) < int(link.requirement_version or 1):
                    continue
                delivery = reliability.deliveries.get_by_result(
                    row.id,
                    tenant_id=reliability.tenant_id,
                )
                manual_delivery = bool(delivery and delivery.playback_status == "claimed"
                                       and not str(delivery.idempotency_key).startswith("voice-auto:"))
                if row.status not in {"ready", "failed", "stale"} and not manual_delivery and not (include_delivered and row.status == "delivered"):
                    continue
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
                        "agent_result": speech_brief(payload.get("agent_result") or payload),
                        "auto_delivery": bool(getattr(link, "auto_delivery", True)),
                        "manual_delivery": manual_delivery,
                        "scene": scene_grounding(
                            payload, summary=row.summary or "",
                            session_epoch=int(getattr(handle, "session_epoch", 1)),
                            requirement_version=int(row.requirement_version or 1),
                            result_version=int(row.result_version or 1),
                        ),
                        "topic_current": bool(getattr(link, "trigger_turn_id", None) in topic_turns),
                        "topic_id": topic_id,
                        "playback_status": delivery.playback_status if delivery else None,
                        "deliveries": payload.get("deliveries") or {},
                    }
                )
            return sorted(results, key=lambda item: not item["manual_delivery"])[:limit]

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
            link = reliability.required_link(self.voice_session_id, task_id)
            if row.status in {"stale", "dismissed"} or row.requirement_version < int(link.requirement_version or 1):
                raise AppError(409, "voice_result_stale", "Voice result is no longer current")
            existing = reliability.deliveries.get_by_result(row.id, tenant_id=reliability.tenant_id)
            if (delivery_state == "delivered" and existing is not None
                    and existing.playback_status == "claimed"
                    and not str(existing.idempotency_key).startswith("voice-auto:")):
                adopted = reliability.db.execute(update(VoiceSpeechDeliveryRecord).where(
                    VoiceSpeechDeliveryRecord.id == existing.id,
                    VoiceSpeechDeliveryRecord.playback_status == "claimed",
                ).values(playback_status="enqueued")).rowcount
                reliability.db.commit()
                return {"result_id": row.id, "speech_id": existing.speech_id, "created": bool(adopted)}
            if delivery_state == "dismissed":
                dismissed = reliability.dismiss_result(
                    voice_session_id=self.voice_session_id,
                    result_id=row.id,
                )
                return {"result_id": dismissed.id, "status": dismissed.status}
            request_id = f"voice-auto:{row.id}:{int(row.result_version or 1)}"
            if delivery_state == "released":
                if existing is not None:
                    request_id = existing.idempotency_key
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

    def _update_speech_delivery_sync(self, speech_id: str, state: str) -> dict[str, Any]:
        with self._coordinator() as (_coordinator, reliability, _chat, _turn):
            return reliability.update_speech_delivery(
                voice_session_id=self.voice_session_id,
                speech_id=speech_id,
                state=state,
            )


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
        allowed_tools: list[str] | None = None,
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
            allowed_tools=allowed_tools,
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
