from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import Principal, hash_session_token
from app.domain.models import AuthSession, User, Workspace, utc_now
from app.providers.factory import transcription_provider_for_workspace
from app.providers.qwen_catalog import is_dashscope_api_base_url
from app.providers.remote.transcription import TranscriptionProviderError
from app.repositories.audit import AuditRepository
from app.services.authorization import AuthorizationService
from app.services.billing import BillingService


# A dictation segment is a few seconds of opus/webm audio; this bound exists to
# reject accidental full-file uploads on the microphone endpoint, not to size
# normal traffic.
MAX_DICTATION_SEGMENT_BYTES = 10 * 1024 * 1024


def authenticate_realtime_dictation(
    db: Session, token: str, workspace_id: str
) -> str | None:
    """Validate a WS first-message token; return the user id or None.

    Browsers cannot attach Authorization headers to native WebSockets, so the
    realtime dictation endpoint authenticates with the same session token sent
    as the first JSON frame.  Mirrors the HTTP dependency chain: live auth
    session, active user, workspace in tenant, ``workspace.write`` permission.
    """

    if not token or not workspace_id:
        return None
    auth_session = db.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_session_token(token))
    )
    now = utc_now()
    if auth_session is None or auth_session.revoked_at is not None:
        return None
    expires_at = auth_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= now:
        return None
    user = db.scalar(
        select(User).where(
            User.id == auth_session.user_id,
            User.tenant_id == auth_session.tenant_id,
        )
    )
    if user is None or user.status != "active" or user.must_change_password:
        return None
    workspace = db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == user.tenant_id,
        )
    )
    if workspace is None:
        return None
    principal = Principal(
        user_id=user.id,
        username=user.username,
        tenant_id=user.tenant_id,
        session_id=auth_session.id,
        display_name=user.display_name or user.username,
        is_system_admin=user.is_system_admin,
        must_change_password=user.must_change_password,
    )
    permissions = AuthorizationService(db, principal).workspace_permissions(workspace)
    if "workspace.write" not in permissions:
        return None
    return user.id


def is_realtime_transcription_model(model_id: str | None) -> bool:
    """DashScope realtime ASR models are WebSocket-only.

    ``qwen3-asr-flash-realtime``, ``paraformer-realtime-v2``,
    ``gummy-realtime-v1`` and friends all reject the HTTP
    ``/audio/transcriptions`` endpoint, so the model id decides the transport.
    """

    return "realtime" in (model_id or "").casefold()


def dashscope_realtime_ws_url(base_url: str | None) -> str | None:
    """Derive the DashScope realtime inference WebSocket URL for ``base_url``.

    The configured Provider row stores the compatible-mode HTTP origin; the
    realtime ASR service lives at ``/api-ws/v1/inference`` on the same host.

    Both the public DashScope gateway (``dashscope*.aliyuncs.com``) and the
    dedicated per-tenant deployments (``*.maas.aliyuncs.com``) advertise the
    realtime ASR models on their compatible-mode ``/models`` list, so the WS
    endpoint is derived from whichever origin the Provider row stores.  This
    mirrors ``dashscope_native_generation_url`` (image generation): private MaaS
    tenants route neither the OpenAI path nor DashScope-flavoured HTTP, only the
    WS inference path of the same host.
    """

    if not base_url:
        return None
    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").casefold()
    if parsed.scheme.casefold() != "https" or not host:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    if not is_dashscope_api_base_url(base_url) and not host.endswith(
        ".maas.aliyuncs.com"
    ):
        return None
    return f"wss://{parsed.netloc}/api-ws/v1/inference"


def build_realtime_run_task(model_id: str, sample_rate: int) -> tuple[str, str]:
    """Build the DashScope ``run-task`` frame; returns (task_id, JSON text)."""

    task_id = uuid4().hex
    message = {
        "header": {
            "action": "run-task",
            "task_id": task_id,
            "streaming": "duplex",
        },
        "payload": {
            "task_group": "audio",
            "task": "asr",
            "function": "recognition",
            "model": model_id,
            "parameters": {"format": "pcm", "sample_rate": sample_rate},
            "input": {},
        },
    }
    return task_id, json.dumps(message, ensure_ascii=False)


def build_realtime_finish_task(task_id: str) -> str:
    return json.dumps(
        {
            "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {"input": {}},
        },
        ensure_ascii=False,
    )


@dataclass(slots=True)
class RealtimeUpstreamEvent:
    event: str
    text: str | None = None
    final: bool = False
    error: str | None = None
    usage: dict[str, int] = field(default_factory=dict)


def parse_realtime_upstream_event(raw: str | bytes) -> RealtimeUpstreamEvent:
    """Normalize a DashScope inference event into transport-neutral fields.

    Handles both result shapes: ``output.sentence`` (paraformer /
    qwen3-asr-flash realtime recognition) and ``output.transcription``
    (gummy realtime).  Unknown events pass through with just their name so the
    proxy can ignore them.
    """

    try:
        payload = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return RealtimeUpstreamEvent(event="invalid")
    if not isinstance(payload, dict):
        return RealtimeUpstreamEvent(event="invalid")
    header = payload.get("header")
    header = header if isinstance(header, dict) else {}
    event = str(header.get("event") or "")
    if event == "task-failed":
        code = str(header.get("error_code") or "").strip()
        message = str(header.get("error_message") or "").strip()
        detail = ": ".join(part for part in (code, message) if part)
        return RealtimeUpstreamEvent(
            event=event, error=detail or "DashScope realtime task failed"
        )
    body = payload.get("payload")
    body = body if isinstance(body, dict) else {}
    if event == "task-finished":
        usage: dict[str, int] = {}
        raw_usage = body.get("usage")
        if isinstance(raw_usage, dict):
            for key, value in raw_usage.items():
                if isinstance(value, int) and not isinstance(value, bool):
                    usage[str(key)] = value
        return RealtimeUpstreamEvent(event=event, usage=usage)
    if event != "result-generated":
        return RealtimeUpstreamEvent(event=event or "unknown")
    output = body.get("output")
    output = output if isinstance(output, dict) else {}
    result = output.get("sentence")
    if not isinstance(result, dict):
        result = output.get("transcription")
    if not isinstance(result, dict):
        return RealtimeUpstreamEvent(event=event)
    text = result.get("text")
    if not isinstance(text, str):
        return RealtimeUpstreamEvent(event=event)
    end_time = result.get("end_time")
    final = result.get("sentence_end") is True or isinstance(end_time, (int, float))
    return RealtimeUpstreamEvent(event=event, text=text, final=final)


class DictationService:
    """Live microphone dictation via the workspace transcription Provider.

    Unlike ``FileService.transcribe`` this path never stores audio: the
    browser streams short voice segments (cut at natural pauses) and each is
    forwarded to the remote ASR endpoint, so the Provider's native punctuation
    survives and the microphone session itself is never interrupted.
    """

    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        self.audit = AuditRepository(db, workspace_id)

    def transcribe_segment(
        self,
        *,
        content: bytes,
        mime_type: str,
        filename: str,
        provider_id: str | None = None,
        model_id: str | None = None,
        language: str | None = None,
    ) -> dict:
        if not content:
            raise AppError(422, "audio_segment_empty", "The dictation segment has no audio bytes")
        if len(content) > MAX_DICTATION_SEGMENT_BYTES:
            raise AppError(
                413,
                "audio_segment_too_large",
                "Dictation segments are limited to 10 MB; upload longer audio as a stored file instead",
            )
        normalized_mime = (mime_type or "").split(";", 1)[0].strip().casefold()
        if not normalized_mime.startswith("audio/") and normalized_mime != "video/webm":
            raise AppError(415, "audio_required", "Dictation segments must be audio uploads")
        provider = transcription_provider_for_workspace(
            self.db,
            self.workspace_id,
            self.settings,
            provider_id=provider_id,
            model_id=model_id,
            purpose="stored",
        )
        if provider is None:
            raise AppError(
                503,
                "transcription_provider_unavailable",
                "No enabled remote ASR Provider matches this request",
            )
        billing = BillingService(self.db, self.workspace_id, self.actor_id)
        quote = billing.preflight_model_call(
            provider_id=provider.provider_id,
            model_id=provider.model_id,
            feature="audio_transcription",
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            remote_capability=True,
        )
        started = time.monotonic()
        try:
            result = provider.transcribe(
                filename=filename,
                mime_type=mime_type,
                content=content,
                language=language,
                # Segments cut on silence can legitimately transcribe to "".
                allow_empty=True,
            )
        except TranscriptionProviderError as exc:
            self.audit.record(
                actor_id=self.actor_id,
                action="chat.dictation.transcription_failed",
                resource_type="provider",
                resource_id=provider.provider_id,
                outcome="failed",
                details={"model_id": provider.model_id},
            )
            self.db.commit()
            raise AppError(502, "transcription_provider_failed", str(exc)) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        billing.record_usage(
            quote,
            input_tokens=int(result.usage.get("input_tokens") or 0),
            output_tokens=int(result.usage.get("output_tokens") or 0),
            attempt=1,
            latency_ms=latency_ms,
            usage_reported=bool(result.usage),
        )
        self.db.commit()
        return {
            "text": result.text,
            "language": result.language,
            "duration_seconds": result.duration_seconds,
            "request_id": result.request_id,
        }
