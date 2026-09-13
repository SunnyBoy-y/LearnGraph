from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import ChatSession, SandboxAgentTask
from app.services.authorization import AuthorizationService
from app.services.sandbox import SandboxAgentWorkspaceService
from app.domain.schemas.sandbox import (
    SandboxAgentSubagentCancelRequest,
    SandboxAgentSubagentRequest,
    SandboxAgentSubagentStatusRequest,
)
from .policy import clip_thinking_mode, next_event_seq
from .runtime import runtime_info
from .schemas import (
    RTVIEventEnvelope, VoiceSession, VoiceTaskLink, VoiceTaskStartRequest,
    VoiceTaskView,
)

class VoiceSessionService:
    """Voice orchestration contract over existing chat, memory and sandbox APIs.

    Session envelopes are intentionally process-local in this first increment;
    durable chat messages, memory events and SandboxAgentTask rows remain the
    source of truth. A later persistence adapter can replace this registry
    without changing the HTTP or RTVI contracts.
    """
    _lock = threading.RLock()
    _sessions: dict[str, VoiceSession] = {}

    def __init__(self, db: Session, context: Any, settings: Any) -> None:
        self.db, self.context, self.settings = db, context, settings

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
        now = datetime.now(timezone.utc)
        session = VoiceSession(
            id=f"vs_{uuid4().hex[:24]}", session_id=None, chat_session_id=chat_session_id,
            workspace_id=self.context.workspace_id,
            owner_user_id=self.context.principal.user_id,
            max_thinking_mode=clip_thinking_mode(maximum, maximum),
            created_at=now, updated_at=now,
            model_id=model_id,
            provider_id=provider_id,
        )
        runtime = runtime_info(self.settings)
        session.runtime_ready = runtime.ready
        session.signaling_url = runtime.signaling_url
        session.session_id = session.id
        with self._lock:
            self._sessions[session.id] = session
        return session

    def get_session(self, voice_session_id: str, *, write: bool = False) -> VoiceSession:
        with self._lock:
            session = self._sessions.get(voice_session_id)
        if session is None or session.workspace_id != self.context.workspace_id or session.owner_user_id != self.context.principal.user_id:
            raise AppError(404, "voice_session_not_found", "Voice session was not found")
        self._chat(session.chat_session_id, write=write)
        return session

    def update_model(self, voice_session_id: str, model_id: str | None, provider_id: str | None) -> VoiceSession:
        """Update the model pin used by the next Pipecat turn/reconnect."""
        session = self.get_session(voice_session_id, write=True)
        with self._lock:
            session.model_id = model_id or None
            session.provider_id = provider_id or None
            session.updated_at = datetime.now(timezone.utc)
        return session

    def start_task(self, voice_session_id: str, request: VoiceTaskStartRequest) -> VoiceTaskView:
        session = self.get_session(voice_session_id, write=True)
        self.context.require_permission("workspace.manage")
        mode = clip_thinking_mode(request.thinking_mode, session.max_thinking_mode)
        payload = SandboxAgentSubagentRequest(
            chat_session_id=session.chat_session_id, prompt=request.prompt,
            title=request.title, role_key=request.role_key, tools=request.tools,
            skills=request.skills, write_set=request.write_set,
            output_contract=request.output_contract, sandbox_session_id=request.sandbox_session_id,
        )
        result = SandboxAgentWorkspaceService(
            self.db, self.context.workspace_id, self.context.principal.user_id,
            self.settings, workspace=self.context.workspace, principal=self.context.principal,
        ).toolkit_subagent(payload)
        subagent_id = str(result.get("subagent_id") or "")
        if not subagent_id:
            raise AppError(502, "voice_task_bridge_invalid", "Agent task bridge returned no task id")
        now = datetime.now(timezone.utc)
        link = VoiceTaskLink(voice_session_id=session.id, subagent_id=subagent_id,
                             chat_session_id=session.chat_session_id, status="queued",
                             thinking_mode=mode, title=request.title, created_at=now, updated_at=now)
        with self._lock:
            session.tasks.append(link)
            session.updated_at = now
        return self._task_view(session, subagent_id, result)

    def _task_view(self, session: VoiceSession, subagent_id: str, snapshot: dict[str, Any]) -> VoiceTaskView:
        link = next((x for x in session.tasks if x.subagent_id == subagent_id), None)
        mode = link.thinking_mode if link else session.max_thinking_mode
        return VoiceTaskView(voice_session_id=session.id, subagent_id=subagent_id,
            status=str(snapshot.get("status", "queued")), thinking_mode=mode,
            title=str(snapshot.get("title", link.title if link else "")), result=snapshot.get("result"),
            deliverables=snapshot.get("deliverables"), event_seq=int(snapshot.get("event_seq", 0)),
            events=list(snapshot.get("events") or []))

    def get_task(self, voice_session_id: str, subagent_id: str, after_event_seq: int | None = None) -> VoiceTaskView:
        session = self.get_session(voice_session_id)
        if not any(x.subagent_id == subagent_id for x in session.tasks):
            raise AppError(404, "voice_task_not_found", "Task is not linked to this voice session")
        snapshot = SandboxAgentWorkspaceService(
            self.db, self.context.workspace_id, self.context.principal.user_id,
            self.settings, workspace=self.context.workspace, principal=self.context.principal,
        ).toolkit_subagent_status(SandboxAgentSubagentStatusRequest(
            chat_session_id=session.chat_session_id, subagent_id=subagent_id,
            after_event_seq=after_event_seq,
        ))
        return self._task_view(session, subagent_id, snapshot)

    def cancel_task(self, voice_session_id: str, subagent_id: str) -> VoiceTaskView:
        session = self.get_session(voice_session_id, write=True)
        self.context.require_permission("workspace.manage")
        if not any(x.subagent_id == subagent_id for x in session.tasks):
            raise AppError(404, "voice_task_not_found", "Task is not linked to this voice session")
        snapshot = SandboxAgentWorkspaceService(
            self.db, self.context.workspace_id, self.context.principal.user_id,
            self.settings, workspace=self.context.workspace, principal=self.context.principal,
        ).toolkit_subagent_cancel(SandboxAgentSubagentCancelRequest(
            chat_session_id=session.chat_session_id, subagent_id=subagent_id,
        ))
        return self._task_view(session, subagent_id, snapshot)

    def envelope(self, voice_session_id: str, event_type: str, payload: dict[str, Any] | None = None, request_id: str | None = None) -> RTVIEventEnvelope:
        session = self.get_session(voice_session_id)
        with self._lock:
            session.seq = next_event_seq(session.seq)
            seq = session.seq
            session.updated_at = datetime.now(timezone.utc)
        return RTVIEventEnvelope(type=event_type, seq=seq, request_id=request_id or f"req_{uuid4().hex[:20]}", session_id=session.id, timestamp=datetime.now(timezone.utc), payload=payload or {})

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
            "spoken_style": True,
            "max_thinking_mode": session.max_thinking_mode,
        }

