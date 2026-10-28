from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    ChatSession,
    VoiceResultInboxRecord,
    VoiceSpeechDeliveryRecord,
    VoiceSessionRecord,
    VoiceTaskLinkRecord,
    VoiceTurnRecord,
)
from app.services.authorization import AuthorizationService
from app.services.sandbox import SandboxAgentWorkspaceService
from app.services.voice_agent_coordinator import VoiceAgentCoordinator
from app.domain.schemas.sandbox import (
    SandboxAgentSubagentCancelRequest,
    SandboxAgentSubagentRequest,
    SandboxAgentSubagentRetryRequest,
    SandboxAgentSubagentStatusRequest,
)
from .coordinator import (
    VoiceTaskCoordinator,
    normalize_voice_role,
    resolve_voice_tool_policy,
    resolve_voice_write_policy,
)
from .capabilities import capability_id_for_tools, resolve_voice_capability
from .events import (
    VoiceEventSink,
    claim_runtime_owner,
    close_session_record,
    heartbeat_runtime_owner,
    replay_events,
    release_runtime_owner,
)
from .policy import clip_thinking_mode
from .runtime import runtime_info, websocket_runtime_url
from .schemas import (
    RTVIEventEnvelope, VoiceSession, VoiceTaskLink, VoiceTaskStartRequest,
    VoiceTaskView,
)
from .schemas import VoiceResultDeliveryView, VoiceResultView
from .scene import SceneReceipt, project_scene
from .turns import (
    accept_turn as accept_voice_turn,
    close_voice_session,
    finalize_turn as finalize_voice_turn,
    list_turns,
)


def envelope_to_schema(envelope: dict[str, Any]) -> RTVIEventEnvelope:
    """Adapt a durable event dict to the RTVI response model."""
    payload = dict(envelope)
    return RTVIEventEnvelope(
        type=str(payload.get("type") or ""),
        seq=int(payload.get("event_seq") or 0),
        event_seq=int(payload.get("event_seq") or 0),
        event_id=payload.get("event_id"),
        request_id=str(payload.get("request_id") or ""),
        session_id=str(payload.get("session_id") or ""),
        session_epoch=int(payload.get("session_epoch") or 1),
        turn_id=payload.get("turn_id"),
        generation_id=payload.get("generation_id"),
        segment_id=payload.get("segment_id"),
        context_id=payload.get("context_id"),
        phase=str(payload.get("phase") or "authoritative"),
        causality=dict(payload.get("causality") or {}),
        audio_cursor_ms=payload.get("audio_cursor_ms"),
        timestamp=payload.get("timestamp"),
        payload=dict(payload.get("payload") or {}),
    )

class VoiceSessionService:
    """Voice orchestration contract over existing chat, memory and sandbox APIs.

    Durable session/event state is stored in the shared database; no process
    local registry is used, so workers and restarts observe one cursor.
    """
    def __init__(self, db: Session, context: Any, settings: Any) -> None:
        self.db, self.context, self.settings = db, context, settings

    def _coordinator(self) -> VoiceTaskCoordinator:
        return VoiceTaskCoordinator(
            self.db,
            workspace_id=self.context.workspace_id,
            tenant_id=self.context.principal.tenant_id,
        )

    def _result_view(self, record: Any) -> VoiceResultView:
        delivery = self.db.scalar(
            select(VoiceSpeechDeliveryRecord).where(
                VoiceSpeechDeliveryRecord.result_id == record.id,
                VoiceSpeechDeliveryRecord.workspace_id == record.workspace_id,
                VoiceSpeechDeliveryRecord.tenant_id == record.tenant_id,
            )
        )
        payload = dict(record.payload_json or {})
        raw_evidence = payload.get("evidence_refs") or payload.get("evidence") or []
        if not raw_evidence:
            agent_result = payload.get("agent_result")
            if isinstance(agent_result, dict):
                raw_evidence = agent_result.get("evidence_refs") or agent_result.get("evidence") or []
        evidence_refs: list[str] = []
        if isinstance(raw_evidence, list):
            for item in raw_evidence:
                if isinstance(item, dict):
                    value = item.get("id") or item.get("evidence_id") or item.get("ref")
                else:
                    value = item
                if isinstance(value, str) and value.strip() and value.strip() not in evidence_refs:
                    evidence_refs.append(value.strip())
        return VoiceResultView(
            id=record.id,
            link_id=record.task_link_id,
            voice_session_id=record.voice_session_id,
            subagent_id=record.subagent_id,
            requirement_version=record.requirement_version,
            result_version=record.result_version,
            status=record.status,
            result_type=record.result_type,
            summary=record.summary or "",
            payload=payload,
            source_count=int(record.source_count or 0),
            safe_error=record.safe_error or "",
            captured_at=record.captured_at,
            available_at=record.available_at,
            delivered_at=record.delivered_at,
            dismissed_at=record.dismissed_at,
            stale_at=record.stale_at,
            auto_speak=False,
            speech_id=delivery.speech_id if delivery is not None else None,
            playback_status=(
                delivery.playback_status
                or str((delivery.snapshot or {}).get("playback_state") or "claimed")
                if delivery is not None
                else None
            ),
            playback_ended_at=delivery.playback_ended_at if delivery is not None else None,
            evidence_refs=evidence_refs,
        )

    def _chat(self, chat_session_id: str, *, write: bool = False) -> ChatSession:
        chat = self.db.scalar(select(ChatSession).where(
            ChatSession.id == chat_session_id,
            ChatSession.workspace_id == self.context.workspace_id,
        ))
        if chat is None or not AuthorizationService(self.db, self.context.principal).can_access_resource(
            self.context.workspace, "session", chat_session_id, "write" if write else "read"
        ):
            raise AppError(404, "session_not_found", "Chat session was not found")
        return chat

    def create_session(self, chat_session_id: str, maximum: str, model_id: str | None = None, provider_id: str | None = None) -> VoiceSession:
        self._chat(chat_session_id, write=True)
        # A browser refresh may re-POST while the durable call is still active.
        # Reuse that row so event_seq, unfinished turns and transcript replay
        # continue on the same session instead of creating a parallel runner.
        existing = self.db.scalar(select(VoiceSessionRecord).where(
            VoiceSessionRecord.chat_session_id == chat_session_id,
            VoiceSessionRecord.workspace_id == self.context.workspace_id,
            VoiceSessionRecord.tenant_id == self.context.principal.tenant_id,
            VoiceSessionRecord.owner_user_id == self.context.principal.user_id,
            VoiceSessionRecord.status == "active",
        ).order_by(VoiceSessionRecord.updated_at.desc()))
        if existing is not None:
            return self._to_schema(existing)
        now = datetime.now(timezone.utc)
        sid = f"vs_{uuid4().hex[:24]}"
        runtime = runtime_info(self.settings)
        row = VoiceSessionRecord(
            id=sid, chat_session_id=chat_session_id,
            workspace_id=self.context.workspace_id,
            tenant_id=self.context.principal.tenant_id,
            owner_user_id=self.context.principal.user_id,
            max_thinking_mode=clip_thinking_mode(maximum, maximum),
            model_id=model_id, provider_id=provider_id,
            runtime_ready=runtime.ready, signaling_url=runtime.signaling_url,
            event_seq=0, session_epoch=1, context_snapshot={},
        )
        self.db.add(row)
        self.db.commit()
        session = self._to_schema(row)
        self.envelope(sid, "session.created", phase="authoritative")
        if runtime.ready:
            self.envelope(sid, "session.ready", phase="authoritative")
        session = self.get_session(sid)
        return session

    def _to_schema(self, row: VoiceSessionRecord) -> VoiceSession:
        from app.voice.scene import current_scene_presentation
        links = self.db.scalars(
            select(VoiceTaskLinkRecord).where(
                VoiceTaskLinkRecord.voice_session_id == row.id,
                VoiceTaskLinkRecord.workspace_id == row.workspace_id,
                VoiceTaskLinkRecord.tenant_id == row.tenant_id,
            )
        ).all()
        results = list(
            self.db.scalars(
                select(VoiceResultInboxRecord)
                .where(
                    VoiceResultInboxRecord.workspace_id == row.workspace_id,
                    VoiceResultInboxRecord.tenant_id == row.tenant_id,
                    VoiceResultInboxRecord.voice_session_id == row.id,
                )
                .order_by(VoiceResultInboxRecord.available_at.desc())
                .limit(50)
            ).all()
        )
        presentation = current_scene_presentation(self.db, row)
        if presentation and not any(
            result.id == presentation.get("result_id") and result.result_version == presentation.get("result_version")
            and result.status not in {"stale", "dismissed"} and any(
                link.subagent_id == result.subagent_id and link.requirement_version == result.requirement_version for link in links)
            for result in results
        ):
            presentation = None
        return VoiceSession(
            id=row.id, session_id=row.id, chat_session_id=row.chat_session_id,
            workspace_id=row.workspace_id, owner_user_id=row.owner_user_id,
            status=row.status, max_thinking_mode=row.max_thinking_mode,
            seq=row.event_seq, event_seq=row.event_seq, session_epoch=row.session_epoch,
            peer_generation=int(row.peer_generation or 0),
            created_at=row.created_at, updated_at=row.updated_at,
            model_id=row.model_id, provider_id=row.provider_id,
            runtime_ready=row.runtime_ready, signaling_url=row.signaling_url,
            websocket_url=websocket_runtime_url(self.settings),
            context_snapshot=row.context_snapshot or {},
            low_latency_mode=(row.context_snapshot or {}).get("low_latency_mode") is True,
            scene_presentation=presentation,
            tasks=[
                VoiceTaskLink(
                    link_id=x.id,
                    voice_session_id=x.voice_session_id,
                    subagent_id=x.subagent_id,
                    chat_session_id=x.chat_session_id,
                    status=x.status,
                    thinking_mode=x.thinking_mode,
                    title=x.title,
                    prompt=x.current_prompt or x.original_prompt,
                    capability_id=capability_id_for_tools(x.allowed_tools),
                    approval_status=x.approval_status,
                    approval_request=dict(x.approval_request_json or {}),
                    approval_request_json=dict(x.approval_request_json or {}),
                    approval_expires_at=x.approval_expires_at,
                    trigger_turn_id=x.trigger_turn_id,
                    requirement_version=x.requirement_version,
                    latest_result_version=x.latest_result_version,
                    auto_delivery=x.auto_delivery,
                    cancel_requested_at=x.cancel_requested_at,
                    cancel_acknowledged_at=x.cancel_acknowledged_at,
                    delivery_status=(
                        "delivered"
                        if any(
                            item.task_link_id == x.id and item.status == "delivered"
                            for item in results
                        )
                        else "stale"
                        if any(
                            item.task_link_id == x.id and item.status == "stale"
                            for item in results
                        )
                        else "failed"
                        if x.status in {"FAILED", "CANCELLED", "TIMED_OUT", "INTERRUPTED"}
                        else "ready"
                        if int(x.latest_result_version or 0) > 0
                        else "pending"
                    ),
                    created_at=x.created_at,
                    updated_at=x.updated_at,
                )
                for x in links
            ],
            results=[self._result_view(x) for x in results],
            ready_result_count=sum(1 for x in results if x.status == "ready"),
        )

    def get_session(self, voice_session_id: str, *, write: bool = False) -> VoiceSession:
        row = self.db.scalar(select(VoiceSessionRecord).where(
            VoiceSessionRecord.id == voice_session_id,
            VoiceSessionRecord.workspace_id == self.context.workspace_id,
            VoiceSessionRecord.tenant_id == self.context.principal.tenant_id,
            VoiceSessionRecord.owner_user_id == self.context.principal.user_id,
        ))
        if row is None:
            raise AppError(404, "voice_session_not_found", "Voice session was not found")
        session = self._to_schema(row)
        self._chat(row.chat_session_id, write=write)
        return session

    def update_model(self, voice_session_id: str, model_id: str | None, provider_id: str | None) -> VoiceSession:
        """Pin the model used from the next turn boundary onwards.

        Model switches are explicitly *next turn*, never immediate: the audio
        pipeline may already be mid-generation on the previous pin.  The event
        carries ``applied_epoch`` so the client can show the epoch that is
        really in force instead of assuming the request took effect.

        ``reason="model_switch"`` is the marker that separates a *request* from
        the two other things that share this event type: the context-snapshot
        refresh (``reason="context_snapshot"``, nothing to do with the model) and
        the worker's own acknowledgement (``origin="pipeline"``).  Without it a
        client that keys on the event type alone reports a model switch every
        time a call is created.
        """
        self.get_session(voice_session_id, write=True)
        row = self.db.get(VoiceSessionRecord, voice_session_id)
        row.model_id, row.provider_id = model_id or None, provider_id or None
        row.session_epoch += 1
        self.db.commit()
        effective = self.get_session(voice_session_id)
        self.envelope(voice_session_id, "context.updated", {
            "reason": "model_switch",
            "model_id": row.model_id,
            "provider_id": row.provider_id,
            "applies_to": "next_turn",
            "applied_epoch": effective.session_epoch,
        })
        return effective

    def update_client_environment(self, voice_session_id: str, environment) -> VoiceSession:
        from app.providers.realtime_information import ClientEnvironment
        validated = ClientEnvironment.model_validate(environment).model_dump(mode="json")
        self.get_session(voice_session_id, write=True)
        row = self.db.get(VoiceSessionRecord, voice_session_id)
        row.context_snapshot = {**(row.context_snapshot or {}), "client_environment": validated}
        self.db.commit()
        return self.get_session(voice_session_id)

    def update_preferences(self, voice_session_id: str, *, low_latency_mode: bool) -> VoiceSession:
        session = self.get_session(voice_session_id, write=True)
        if session.low_latency_mode == low_latency_mode:
            return session
        row = self.db.get(VoiceSessionRecord, voice_session_id)
        row.context_snapshot = {**(row.context_snapshot or {}), "low_latency_mode": low_latency_mode}
        self.db.commit()
        self.envelope(voice_session_id, "context.updated", {
            "reason": "voice_preferences",
            "low_latency_mode": low_latency_mode,
            "applies_to": "next_turn",
        })
        return self.get_session(voice_session_id)

    def update_context_snapshot(self, voice_session_id: str, snapshot: dict[str, Any]) -> VoiceSession:
        """Persist an immutable context package for reconnects/next turns.

        The event says "the context package changed", never "the model changed":
        it carries no ``model_id`` and is marked ``reason="context_snapshot"`` so
        a reader cannot mistake a snapshot write (which happens on every call
        creation) for a model switch.  This is also what the worker's watchdog
        needs in order to tell an instruction from its own acknowledgement --
        see ``VoiceControlWatchdog.tick``.
        """
        self.get_session(voice_session_id, write=True)
        row = self.db.get(VoiceSessionRecord, voice_session_id)
        import json
        # Dataclass snapshots include ``created_at``; normalize to transport
        # safe JSON before writing the JSON column.
        environment = (row.context_snapshot or {}).get("client_environment")
        low_latency_mode = (row.context_snapshot or {}).get("low_latency_mode") is True
        row.context_snapshot = json.loads(json.dumps(snapshot or {}, default=str, ensure_ascii=False))
        row.context_snapshot = {**row.context_snapshot, "low_latency_mode": low_latency_mode}
        if environment is not None:
            row.context_snapshot = {**row.context_snapshot, "client_environment": environment}
        row.session_epoch += 1
        self.db.commit()
        effective = self.get_session(voice_session_id)
        self.envelope(voice_session_id, "context.updated", {
            "reason": "context_snapshot",
            "context_version": row.context_snapshot.get("context_build_id") or row.context_snapshot.get("version"),
            "applies_to": "next_turn",
            "applied_epoch": effective.session_epoch,
        })
        return effective

    async def close(
        self, voice_session_id: str, *, reason: str = "client_request"
    ) -> VoiceSession:
        """Terminate durable state and await local runtime teardown."""
        self.get_session(voice_session_id, write=True)
        close_voice_session(voice_session_id, reason=reason, close_runtime=False)
        from .runner_registry import close_runner
        await close_runner(voice_session_id, reason=reason)
        self.db.expire_all()
        return self.get_session(voice_session_id)

    def require_runtime_access(
        self,
        voice_session_id: str,
        *,
        peer_connection_id: str | None = None,
        runtime_owner_id: str | None = None,
    ) -> VoiceSessionRecord:
        row = self.db.scalar(
            select(VoiceSessionRecord).where(
                VoiceSessionRecord.id == voice_session_id,
                VoiceSessionRecord.workspace_id == self.context.workspace_id,
                VoiceSessionRecord.tenant_id == self.context.principal.tenant_id,
                VoiceSessionRecord.owner_user_id == self.context.principal.user_id,
            )
        )
        if row is None or row.status != "active":
            raise AppError(404, "voice_session_not_found", "Voice session was not found")
        if (
            peer_connection_id
            and row.peer_connection_id != peer_connection_id
        ):
            raise AppError(409, "voice_peer_mismatch", "Voice peer is not bound to this session")
        if runtime_owner_id and row.runtime_owner_id not in {None, runtime_owner_id}:
            raise AppError(
                409,
                "voice_runtime_owned_elsewhere",
                "Voice runtime is owned by another instance",
            )
        return row

    def bind_peer(
        self, voice_session_id: str, peer_connection_id: str,
        *, owner_id: str | None = None,
    ) -> VoiceSession:
        row = self.require_runtime_access(voice_session_id)
        if owner_id is not None and row.runtime_owner_id not in {None, owner_id}:
            raise AppError(409, "voice_runtime_owned_elsewhere", "Voice runtime is owned by another instance")
        row.peer_connection_id = peer_connection_id
        row.peer_generation = int(row.peer_generation or 0) + 1
        self.db.commit()
        self.db.refresh(row)
        return self._to_schema(row)

    def claim_runtime_owner(
        self,
        voice_session_id: str,
        owner_id: str,
        *,
        force: bool = False,
        expected_session_epoch: int | None = None,
        expected_peer_generation: int | None = None,
    ) -> bool:
        self.require_runtime_access(voice_session_id)
        return claim_runtime_owner(
            voice_session_id,
            owner_id,
            lease_seconds=self.settings.voice_runtime_owner_lease_seconds,
            force=force,
            expected_session_epoch=expected_session_epoch,
            expected_peer_generation=expected_peer_generation,
        )

    def heartbeat_runtime_owner(
        self,
        voice_session_id: str,
        owner_id: str,
        *,
        expected_session_epoch: int | None = None,
        expected_peer_generation: int | None = None,
    ) -> bool:
        return heartbeat_runtime_owner(
            voice_session_id,
            owner_id,
            lease_seconds=self.settings.voice_runtime_owner_lease_seconds,
            expected_session_epoch=expected_session_epoch,
            expected_peer_generation=expected_peer_generation,
        )

    def release_runtime_owner(
        self,
        voice_session_id: str,
        owner_id: str,
        *,
        expected_session_epoch: int | None = None,
        expected_peer_generation: int | None = None,
    ) -> bool:
        return release_runtime_owner(
            voice_session_id,
            owner_id,
            expected_session_epoch=expected_session_epoch,
            expected_peer_generation=expected_peer_generation,
        )

    def start_task(
        self, voice_session_id: str, request: VoiceTaskStartRequest
    ) -> VoiceTaskView:
        session = self.get_session(voice_session_id, write=True)
        mode = clip_thinking_mode(request.thinking_mode, session.max_thinking_mode)
        try:
            capability = (
                resolve_voice_capability(request.capability_id)
                if request.capability_id
                else None
            )
        except ValueError as exc:
            raise AppError(422, "invalid_voice_capability", str(exc)) from exc
        self.context.require_permission(
            capability.required_permission if capability else "workspace.manage"
        )
        role = normalize_voice_role(capability.role_key if capability else request.role_key)
        tools = resolve_voice_tool_policy(
            role,
            list(capability.tools) if capability else request.tools,
        )
        write_set = resolve_voice_write_policy(
            role,
            list(capability.write_set) if capability else request.write_set,
        )
        if request.trigger_turn_id:
            turn = self.db.scalar(
                select(VoiceTurnRecord).where(
                    VoiceTurnRecord.id == request.trigger_turn_id,
                    VoiceTurnRecord.voice_session_id == session.id,
                    VoiceTurnRecord.workspace_id == self.context.workspace_id,
                    VoiceTurnRecord.tenant_id == self.context.principal.tenant_id,
                )
            )
            if turn is None:
                raise AppError(404, "voice_turn_not_found", "Voice turn was not found")
        coordinator = self._coordinator()
        requires_approval = bool(capability and capability.requires_confirmation)
        idempotency_key = request.idempotency_key or (
            f"voice-approval:{session.id}:{uuid4().hex}"
            if requires_approval
            else None
        )
        approval_request = {
            "prompt": request.prompt,
            "title": request.title,
            "capability_id": capability.capability_id if capability else None,
            "role_key": role,
            "tools": tools,
            "skills": (
                request.skills
                if request.skills is not None and role == "tool"
                else list(capability.skills) if capability else []
            ),
            "write_set": write_set,
            "output_contract": request.output_contract or (
                dict(capability.output_contract)
                if capability and capability.output_contract is not None else None
            ),
            "sandbox_session_id": request.sandbox_session_id,
            "thinking_mode": mode,
            "trigger_turn_id": request.trigger_turn_id,
        }
        link, created = coordinator.reserve_link(
            voice_session_id=session.id,
            chat_session_id=session.chat_session_id,
            prompt=request.prompt,
            title=request.title,
            role_key=role,
            thinking_mode=mode,
            trigger_turn_id=request.trigger_turn_id,
            idempotency_key=idempotency_key,
            allowed_tools=tools,
            capability_id=capability.capability_id if capability else None,
            status="waiting_approval" if requires_approval else "creating",
            approval_status="pending" if requires_approval else "none",
            approval_request=approval_request if requires_approval else None,
            approval_expires_at=(
                datetime.now(timezone.utc) + timedelta(minutes=10)
                if requires_approval
                else None
            ),
        )
        if not created:
            return self._task_view(link)
        if requires_approval:
            return self._task_view(
                link,
                {
                    "status": "waiting_approval",
                    "capability_id": capability.capability_id,
                    "approval_status": "pending",
                    "title": request.title or capability.capability_id,
                },
            )
        payload = SandboxAgentSubagentRequest(
            chat_session_id=session.chat_session_id,
            prompt=request.prompt,
            title=request.title,
            role_key=role,
            tools=tools,
            skills=(
                request.skills
                if request.skills is not None and role == "tool"
                else list(capability.skills) if capability else []
            ),
            write_set=write_set,
            output_contract=request.output_contract or (
                dict(capability.output_contract)
                if capability and capability.output_contract is not None else None
            ),
            sandbox_session_id=request.sandbox_session_id,
            voice_session_id=session.id,
            turn_id=request.trigger_turn_id,
            context_snapshot={
                "voice_session_id": session.id,
                "turn_id": request.trigger_turn_id,
                "capability_id": capability.capability_id if capability else None,
            },
        )
        try:
            result = SandboxAgentWorkspaceService(
                self.db,
                self.context.workspace_id,
                self.context.principal.user_id,
                self.settings,
                workspace=self.context.workspace,
                principal=self.context.principal,
            ).toolkit_subagent(payload)
        except Exception:
            link.status = "failed"
            link.cancel_checkpoint_json = {"phase": "task_start_failed"}
            self.db.commit()
            raise
        subagent_id = str(result.get("subagent_id") or "")
        if not subagent_id:
            link.status = "failed"
            self.db.commit()
            raise AppError(502, "voice_task_bridge_invalid", "Agent task bridge returned no task id")
        link = coordinator.complete_link(
            link,
            subagent_id=subagent_id,
            job_id=str(result.get("job_id") or "") or None,
            status=str(result.get("status") or "queued"),
        )
        return self._task_view(link, result)

    def _task_view(
        self,
        link: VoiceTaskLinkRecord,
        snapshot: dict[str, Any] | None = None,
    ) -> VoiceTaskView:
        snapshot = dict(snapshot or {})
        results = [
            self._result_view(item)
            for item in self._coordinator().list_results(link.voice_session_id)
            if item.subagent_id == link.subagent_id
        ]
        return VoiceTaskView(
            link_id=link.id,
            voice_session_id=link.voice_session_id,
            subagent_id=link.subagent_id,
            status=("cancel_requested" if snapshot.get("status_reason") == "cancel_requested"
                    and str(snapshot.get("status") or "").casefold() in {"queued", "running", "starting", "paused"}
                    else "pausing" if snapshot.get("status_reason") == "pause_requested"
                    and str(snapshot.get("status") or "").casefold() in {"queued", "running", "starting"}
                    else str(snapshot.get("status") or link.status or "queued")),
            thinking_mode=link.thinking_mode,
            title=str(snapshot.get("title") or link.title or ""),
            auto_delivery=bool(link.auto_delivery),
            prompt=str(snapshot.get("prompt") or link.current_prompt or link.original_prompt or ""),
            capability_id=(
                str(snapshot.get("capability_id") or "")
                or capability_id_for_tools(link.allowed_tools)
            ),
            approval_status=str(snapshot.get("approval_status") or link.approval_status or "none"),
            approval_expires_at=(
                snapshot.get("approval_expires_at") or link.approval_expires_at
            ),
            approval_request=dict(
                snapshot.get("approval_request")
                or snapshot.get("approval_request_json")
                or link.approval_request_json
                or {}
            ),
            approval_request_json=dict(
                snapshot.get("approval_request_json")
                or snapshot.get("approval_request")
                or link.approval_request_json
                or {}
            ),
            previous_task_id=(
                str(snapshot.get("previous_task_id") or "") or None
            ),
            requirement_version=int(link.requirement_version or 1),
            latest_result_version=int(link.latest_result_version or 0),
            cancel_requested_at=link.cancel_requested_at,
            cancel_acknowledged_at=link.cancel_acknowledged_at,
            result=snapshot.get("result"),
            deliverables=snapshot.get("deliverables"),
            event_seq=int(snapshot.get("event_seq") or link.last_observed_event_seq or 0),
            events=list(snapshot.get("events") or []),
            results=results,
        )

    def get_task(
        self,
        voice_session_id: str,
        subagent_id: str,
        after_event_seq: int | None = None,
    ) -> VoiceTaskView:
        session = self.get_session(voice_session_id)
        coordinator = self._coordinator()
        link = coordinator.required_link(voice_session_id, subagent_id)
        if link.subagent_id.startswith("pending_"):
            return self._task_view(link)
        snapshot = SandboxAgentWorkspaceService(
            self.db,
            self.context.workspace_id,
            self.context.principal.user_id,
            self.settings,
            workspace=self.context.workspace,
            principal=self.context.principal,
        ).toolkit_subagent_status(
            SandboxAgentSubagentStatusRequest(
                chat_session_id=session.chat_session_id,
                subagent_id=subagent_id,
                after_event_seq=after_event_seq,
            )
        )
        coordinator.reconcile_link(link, snapshot)
        link = coordinator.required_link(voice_session_id, subagent_id)
        return self._task_view(link, snapshot)

    def cancel_task(
        self,
        voice_session_id: str,
        subagent_id: str,
        *,
        reason: str = "user_requested",
    ) -> VoiceTaskView:
        session = self.get_session(voice_session_id, write=True)
        coordinator = self._coordinator()
        link = coordinator.required_link(voice_session_id, subagent_id)
        capability = resolve_voice_capability(link.capability_id) if link.capability_id else None
        self.context.require_permission(
            capability.required_permission if capability else "workspace.manage"
        )
        if link.status == "waiting_approval":
            link = coordinator.reject_link(link, reason=reason)
            self.envelope(
                voice_session_id,
                "task.rejected",
                {
                    "task_id": link.subagent_id,
                    "status": "cancelled",
                    "approval_status": link.approval_status,
                },
                turn_id=link.trigger_turn_id,
            )
            return self._task_view(link)
        coordinator.request_cancel(link, reason=reason)
        if link.subagent_id.startswith("pending_"):
            return self._task_view(link)
        snapshot = SandboxAgentWorkspaceService(
            self.db,
            self.context.workspace_id,
            self.context.principal.user_id,
            self.settings,
            workspace=self.context.workspace,
            principal=self.context.principal,
        ).toolkit_subagent_cancel(
            SandboxAgentSubagentCancelRequest(
                chat_session_id=session.chat_session_id,
                subagent_id=subagent_id,
            )
        )
        coordinator.acknowledge_cancel(link, snapshot)
        link = coordinator.required_link(voice_session_id, subagent_id)
        return self._task_view(link, snapshot)

    def approve_task(
        self,
        voice_session_id: str,
        subagent_id: str,
    ) -> VoiceTaskView:
        self.get_session(voice_session_id, write=True)
        coordinator = self._coordinator()
        link = coordinator.required_link(voice_session_id, subagent_id)
        capability = resolve_voice_capability(link.capability_id) if link.capability_id else None
        self.context.require_permission(
            capability.required_permission if capability else "workspace.manage"
        )
        coordinator.approve_link(link)
        agent = VoiceAgentCoordinator(
            self.db,
            self.settings,
            workspace_id=self.context.workspace_id,
            actor_id=self.context.principal.user_id,
            permissions=self.context.permissions,
        )
        try:
            result = agent.approve_pending_voice_task(link)
            task_id = str(result.get("task_id") or result.get("subagent_id") or "")
            if not task_id:
                raise AppError(502, "voice_task_bridge_invalid", "Approved voice task returned no task id")
            coordinator.complete_link(
                link,
                subagent_id=task_id,
                job_id=str(result.get("job_id") or "") or None,
                status=str(result.get("status") or "queued"),
            )
            link.approval_status = "approved"
            self.db.commit()
            self.envelope(
                voice_session_id,
                "task.approved",
                {
                    "task_id": task_id,
                    "previous_task_id": subagent_id,
                    "status": str(result.get("status") or "queued"),
                    "capability_id": link.capability_id,
                    "approval_status": "approved",
                },
                turn_id=link.trigger_turn_id,
            )
        except Exception:
            link.status = "FAILED"
            link.approval_status = "failed"
            self.db.commit()
            raise
        return self._task_view(
            link,
            {
                **dict(result),
                "approval_status": "approved",
                "previous_task_id": subagent_id,
            },
        )

    def reject_task(
        self,
        voice_session_id: str,
        subagent_id: str,
        *,
        reason: str = "user_rejected",
    ) -> VoiceTaskView:
        self.get_session(voice_session_id, write=True)
        task_coordinator = self._coordinator()
        link = task_coordinator.required_link(voice_session_id, subagent_id)
        capability = (
            resolve_voice_capability(link.capability_id)
            if link.capability_id
            else None
        )
        self.context.require_permission(
            capability.required_permission if capability else "workspace.manage"
        )
        link = task_coordinator.reject_link(link, reason=reason)
        self.envelope(
            voice_session_id,
            "task.rejected",
            {
                "task_id": link.subagent_id,
                "status": "cancelled",
                "approval_status": link.approval_status,
            },
            turn_id=link.trigger_turn_id,
        )
        return self._task_view(link)

    def revise_task(
        self,
        voice_session_id: str,
        subagent_id: str,
        *,
        prompt: str,
        title: str | None = None,
        note: str = "",
        idempotency_key: str,
    ) -> VoiceTaskView:
        session = self.get_session(voice_session_id, write=True)
        coordinator = self._coordinator()
        link = coordinator.required_link(voice_session_id, subagent_id)
        capability = resolve_voice_capability(link.capability_id) if link.capability_id else None
        self.context.require_permission(
            capability.required_permission if capability else "workspace.manage"
        )
        coordinator.revise_requirement(
            link, prompt=prompt, title=title, note=note
        )
        service = SandboxAgentWorkspaceService(
            self.db,
            self.context.workspace_id,
            self.context.principal.user_id,
            self.settings,
            workspace=self.context.workspace,
            principal=self.context.principal,
        )
        try:
            if link.latest_job_id:
                service.toolkit_subagent_cancel(
                    SandboxAgentSubagentCancelRequest(
                        chat_session_id=session.chat_session_id,
                        subagent_id=subagent_id,
                    )
                )
            snapshot = service.toolkit_subagent_retry(
                SandboxAgentSubagentRetryRequest(
                    chat_session_id=session.chat_session_id,
                    subagent_id=subagent_id,
                    scope="scoped",
                    note=f"{note} [revision {idempotency_key}]"[:500],
                    prompt_override=prompt,
                )
            )
        except Exception:
            link.status = "failed"
            self.db.commit()
            raise
        coordinator.complete_revision(link, snapshot)
        link = coordinator.required_link(voice_session_id, subagent_id)
        return self._task_view(link, snapshot)

    def list_results(
        self,
        voice_session_id: str,
        *,
        include_terminal: bool = True,
        limit: int = 50,
    ) -> list[VoiceResultView]:
        self.get_session(voice_session_id)
        rows = self._coordinator().list_results(
            voice_session_id,
            include_terminal=include_terminal,
            limit=limit,
        )
        return [self._result_view(item) for item in rows]

    def deliver_result(
        self,
        voice_session_id: str,
        result_id: str,
        *,
        request_id: str,
    ) -> VoiceResultDeliveryView:
        self.get_session(voice_session_id, write=True)
        result, delivery, created = self._coordinator().deliver_result(
            voice_session_id=voice_session_id,
            result_id=result_id,
            request_id=request_id,
        )
        return VoiceResultDeliveryView(
            result=self._result_view(result),
            delivery_id=delivery.id,
            speech_id=delivery.speech_id,
            request_id=request_id,
            playback_status=delivery.playback_status
            or str((delivery.snapshot or {}).get("playback_state") or "claimed"),
            already_delivered=not created,
        )

    def dismiss_result(
        self, voice_session_id: str, result_id: str
    ) -> VoiceResultView:
        self.get_session(voice_session_id, write=True)
        result = self._coordinator().dismiss_result(
            voice_session_id=voice_session_id, result_id=result_id
        )
        return self._result_view(result)

    def acknowledge_scene(self, voice_session_id: str, result_id: str, receipt: SceneReceipt) -> dict[str, Any]:
        session = self.get_session(voice_session_id, write=True)
        result = self._coordinator().get_result(voice_session_id, result_id)
        link = self._coordinator().required_link(voice_session_id, result.subagent_id)
        if (receipt.session_epoch != session.session_epoch
                or receipt.requirement_version != result.requirement_version
                or receipt.result_version != result.result_version
                or result.requirement_version < int(link.requirement_version or 1)
                or result.status in {"stale", "dismissed"}):
            raise AppError(409, "voice_scene_stale", "Scene receipt belongs to an obsolete version")
        payload = dict(result.payload_json or {})
        allowed = project_scene(payload, result.summary or "")["anchors"]
        if any(anchor not in allowed for anchor in receipt.anchors):
            raise AppError(422, "voice_scene_anchor_unknown", "Scene anchor does not exist")
        payload["_scene_receipt"] = {
            **receipt.model_dump(), "at": datetime.now(timezone.utc).isoformat(),
        }
        result.payload_json = payload
        self.db.commit()
        return {"result_id": result.id, "status": receipt.status}

    def set_task_delivery(self, voice_session_id: str, task_id: str, auto_delivery: bool) -> VoiceTaskView:
        self.get_session(voice_session_id, write=True)
        link = self._coordinator().required_link(voice_session_id, task_id)
        link.auto_delivery = auto_delivery
        self.db.commit()
        return self._task_view(link)

    def control_task_execution(self, voice_session_id: str, task_id: str, operation: str) -> VoiceTaskView:
        self.get_session(voice_session_id, write=True)
        from .task_control import control_task
        coordinator = self._coordinator()
        link = coordinator.required_link(voice_session_id, task_id)
        capability = resolve_voice_capability(link.capability_id) if link.capability_id else None
        self.context.require_permission(capability.required_permission if capability else "workspace.manage")
        snapshot = control_task(coordinator, voice_session_id=voice_session_id,
                                task_id=task_id, operation=operation, actor_id=self.context.principal.user_id)
        return self._task_view(link, snapshot)

    def interrupt(self, voice_session_id: str) -> VoiceSession:
        """Interrupt the current speech only; background tasks are untouched."""
        session = self.get_session(voice_session_id, write=True)
        from .turns import interrupt_turn
        interrupt_turn(voice_session_id, reason="client_interrupt")
        return session

    def envelope(self, voice_session_id: str, event_type: str, payload: dict[str, Any] | None = None,
                 request_id: str | None = None, *, turn_id: str | None = None,
                 phase: str = "authoritative", causality: dict[str, Any] | None = None,
                 audio_cursor_ms: int | None = None) -> RTVIEventEnvelope:
        """Append one durable event, shared with the audio worker's journal.

        Delegates to :class:`app.voice.events.VoiceEventSink` so the HTTP control
        plane and the pipeline allocate from one monotonic ``event_seq`` and
        agree on ``request_id`` idempotency.  The request-scoped ``self.db`` is
        intentionally not used: the sink commits its own short transaction, which
        keeps an event visible to a reconnecting client even if the surrounding
        request later fails.
        """
        self.get_session(voice_session_id)
        envelope = VoiceEventSink(voice_session_id).emit(
            event_type,
            payload,
            turn_id=turn_id,
            phase=phase,
            causality=causality,
            audio_cursor_ms=audio_cursor_ms,
            request_id=request_id,
        )
        return envelope_to_schema(envelope)

    def replay_events(self, voice_session_id: str, after_event_seq: int = 0) -> list[RTVIEventEnvelope]:
        self.get_session(voice_session_id)
        return [
            envelope_to_schema(item)
            for item in replay_events(voice_session_id, after_event_seq)
        ]

    def accept_turn(self, voice_session_id: str, user_text: str, client_message_id: str | None = None,
                    turn_id: str | None = None) -> VoiceTurnRecord:
        """Create an idempotent turn and emit ``turn.accepted``."""
        self.get_session(voice_session_id, write=True)
        accept_voice_turn(
            voice_session_id,
            user_text,
            client_message_id=client_message_id,
            turn_id=turn_id,
        )
        row = self.db.scalar(select(VoiceTurnRecord).where(
            VoiceTurnRecord.voice_session_id == voice_session_id,
            (VoiceTurnRecord.id == turn_id) if turn_id
            else (VoiceTurnRecord.client_message_id == client_message_id),
        ))
        if row is None:
            raise AppError(502, "voice_turn_not_created", "Voice turn was not created")
        return row

    def finalize_turn(self, voice_session_id: str, turn_id: str, assistant_text: str = "") -> VoiceTurnRecord:
        self.get_session(voice_session_id, write=True)
        # Same function the audio worker calls, so a turn finalized by the
        # pipeline and one finalized over HTTP converge on a single transcript
        # pair and a single memory job.
        finalize_voice_turn(voice_session_id, turn_id, assistant_text)
        self.db.expire_all()
        row = self.db.scalar(select(VoiceTurnRecord).where(
            VoiceTurnRecord.id == turn_id,
            VoiceTurnRecord.voice_session_id == voice_session_id))
        if row is None:
            raise AppError(404, "voice_turn_not_found", "Voice turn was not found")
        return row

    def transcript(self, voice_session_id: str, limit: int = 50, *, include_open: bool = True) -> list[dict[str, Any]]:
        """Authoritative transcript for reload/reconnect recovery.

        Settled turns are what make a page refresh reproduce the conversation
        without replaying speculative captions. The still-running turn is
        appended so a reload mid-answer keeps the user's own question on screen;
        it carries no assistant text until it settles.
        """
        self.get_session(voice_session_id)
        return list_turns(voice_session_id, limit=limit, include_open=include_open)

    def runtime_config(self, voice_session_id: str) -> dict[str, Any]:
        """Return non-secret provider choices for the optional audio worker."""
        session = self.get_session(voice_session_id)
        runtime = runtime_info(self.settings)
        asr = tts = None
        try:
            from app.providers.factory import (
                realtime_asr_provider_for_workspace,
                tts_provider_for_workspace,
            )
            asr = realtime_asr_provider_for_workspace(self.db, self.context.workspace_id, self.settings)
            tts = tts_provider_for_workspace(self.db, self.context.workspace_id, self.settings)
        except Exception:
            # A worker can still use its own configured provider when the
            # optional provider extra is not installed in the API process.
            pass
        return {
            "session_id": session.id,
            "runtime_ready": runtime.ready,
            "signaling_url": runtime.signaling_url,
            "asr": {"provider_id": asr.provider_id, "model_id": asr.model_id} if asr else None,
            "tts": {"provider_id": tts.provider_id, "model_id": tts.model_id} if tts else None,
            "llm": {"provider_id": session.provider_id, "model_id": session.model_id},
            "spoken_style": True,
            "max_thinking_mode": session.max_thinking_mode,
        }
