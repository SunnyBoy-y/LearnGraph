from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
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


# ---- DashScope 实时 ASR 的两个端点（按模型家族分工，2026-09-20 实测）----------
#
# 同一个 host、同一把 key，两个端点说的是两套协议，且**各自只认自己的模型家族**：
#
#   /api-ws/v1/inference   老 ``run-task`` 协议（task_group=audio / task=asr）
#                          → paraformer-realtime-* / gummy-realtime-*
#   /api-ws/v1/realtime    OpenAI realtime 协议（session.update + append/commit）
#                          → qwen3-asr-flash-realtime 家族
#
# 把 ``qwen3-asr-flash-realtime`` 发到 inference 端点会立刻收到
# ``task-failed / error_code=ModelNotFound``（"Model not found
# (qwen3-asr-flash-realtime)!"），听写实时通道因此永远等不到 ``task-started``，
# 只能一路降级到分段上传。所以端点必须由模型 id 决定，不能只看 host。
DASHSCOPE_INFERENCE_WS_PATH = "/api-ws/v1/inference"
DASHSCOPE_OPENAI_REALTIME_WS_PATH = "/api-ws/v1/realtime"

# 归属 OpenAI realtime 端点的模型家族。判据刻意保守——只有 qwen 系的 realtime
# 模型经过实测，其余（paraformer / gummy / 未知型号）保持历史行为走 inference
# 端点，避免"没验过的型号被改道"。
_OPENAI_REALTIME_MODEL_PREFIXES = ("qwen",)


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


def uses_openai_realtime_transport(model_id: str | None) -> bool:
    """该实时 ASR 模型是否必须走 DashScope 的 OpenAI realtime 端点。

    与语音通话路径（``app/providers/remote/dashscope_realtime.py``）一致：
    ``qwen3-asr-flash-realtime`` 只提供 ``/api-ws/v1/realtime`` 的 OpenAI 方言，
    inference 端点会以 ``ModelNotFound`` 拒掉它。
    """

    name = (model_id or "").strip().casefold()
    if "realtime" not in name:
        return False
    return name.startswith(_OPENAI_REALTIME_MODEL_PREFIXES)


def dashscope_realtime_ws_url(
    base_url: str | None, model_id: str | None = None
) -> str | None:
    """Derive the DashScope realtime ASR WebSocket URL for ``base_url``.

    The configured Provider row stores the compatible-mode HTTP origin; the
    realtime ASR service lives on the same host, but the **path depends on the
    model family** (see the module constants): ``qwen3-asr-flash-realtime`` is
    served at ``/api-ws/v1/realtime`` (and needs the model id in the query
    string), while the ``paraformer``/``gummy`` realtime families are served at
    ``/api-ws/v1/inference``.

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
    if uses_openai_realtime_transport(model_id):
        query = urlencode({"model": str(model_id).strip()})
        return urlunsplit(
            ("wss", parsed.netloc, DASHSCOPE_OPENAI_REALTIME_WS_PATH, query, "")
        )
    return f"wss://{parsed.netloc}{DASHSCOPE_INFERENCE_WS_PATH}"


def build_realtime_run_task(
    model_id: str,
    sample_rate: int,
    language: str | None = None,
    hotwords: list[str] | None = None,
) -> tuple[str, str]:
    """Build the DashScope ``run-task`` frame; returns (task_id, JSON text).

    ``language`` (BCP-47, e.g. zh-CN / en-US) is passed through to the model
    when explicitly requested. ``hotwords`` is injected into ``parameters``
    for models that natively support vocabulary boosting (paraformer-realtime
    family); models that ignore unknown parameters are unaffected, and the
    exact key name for other families is verified per real gateway before use.
    """

    task_id = uuid4().hex
    parameters: dict[str, Any] = {"format": "pcm", "sample_rate": sample_rate}
    if language and language != "auto":
        # 与 input_audio 通道一致：DashScope 实时 ASR 只接受 ISO 639-1 语言码。
        parameters["language"] = language.split("-")[0]
    if hotwords:
        parameters["hotwords"] = hotwords
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
            "parameters": parameters,
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


# ---- OpenAI realtime 方言（/api-ws/v1/realtime）-------------------------------
#
# 帧构造与事件名与语音通话路径（``app/voice/embedded_dashscope_stt.py``）同源，
# 那条路径已在真实网关跑通；这里只是把它搬到听写代理里，让打字框旁的实时听写
# 用同一个上游协议。


def build_openai_realtime_session_update(
    model_id: str,
    sample_rate: int,
    language: str | None = None,
) -> str:
    """Build the DashScope realtime ``session.update`` frame.

    听写通道只负责"边说边出字"，不管理回合，所以断句交给云端 VAD
    （``turn_detection=server_vad``）：浏览器持续上行 PCM（含静音），云端据此
    自然切段并下发 ``.completed``。参数与语音通话路径取同一组。

    ``hotwords`` 不下发：该方言的热词字段未经真实网关验证，语音通话路径同样不发
    （paraformer 家族走 inference 端点时仍照旧透传）。
    """

    transcription: dict[str, Any] = {"model": model_id}
    if language and language != "auto":
        # 与 inference 通道一致：DashScope 只接受 ISO 639-1 语言码（zh-CN → zh）。
        transcription["language"] = language.split("-")[0]
    message = {
        "event_id": f"evt_session_{uuid4().hex[:12]}",
        "type": "session.update",
        "session": {
            "modalities": ["text"],
            "input_audio_format": "pcm",
            "sample_rate": sample_rate,
            "input_audio_transcription": transcription,
            "turn_detection": {
                "type": "server_vad",
                # threshold 0.0 最灵敏（不吞音量偏小的说话），噪音过滤交给识别器；
                # silence_duration_ms 400 是官方推荐值。
                "threshold": 0.0,
                "silence_duration_ms": 400,
            },
        },
    }
    return json.dumps(message, ensure_ascii=False)


def build_openai_realtime_audio_frame(audio: bytes) -> str:
    """Wrap one PCM16 chunk as an ``input_audio_buffer.append`` frame."""

    return json.dumps(
        {
            "type": "input_audio_buffer.append",
            "audio": base64.b64encode(audio).decode("ascii"),
        },
        ensure_ascii=False,
    )


def build_openai_realtime_finish() -> str:
    """``session.finish``：服务端先补尾句 final，再回 ``session.finished``。"""

    return json.dumps({"type": "session.finish"}, ensure_ascii=False)


def _usage_from_openai_realtime_event(payload: dict[str, Any]) -> dict[str, int]:
    raw_usage = payload.get("usage")
    usage: dict[str, int] = {}
    if isinstance(raw_usage, dict):
        for key, value in raw_usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                usage[str(key)] = value
    return usage


def parse_openai_realtime_upstream_event(raw: str | bytes) -> RealtimeUpstreamEvent:
    """Normalize a DashScope OpenAI-realtime event into transport-neutral fields.

    只认三个事件：``conversation.item.input_audio_transcription.text``（整段单调
    增长的假设文本，旧版协议把它放在 ``stash`` 里）、``...completed``（定稿）、
    ``session.finished``（收尾）。其余（``session.created`` / ``speech_started`` /
    ``input_audio_buffer.committed`` 等）原样透传事件名，由调用方忽略。

    失败事件统一归一成 ``task-failed``：听写代理的握手循环与运行循环都只认这一个
    名字，两套方言因此共用同一段错误处理。
    """

    try:
        payload = json.loads(raw if isinstance(raw, str) else raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return RealtimeUpstreamEvent(event="invalid")
    if not isinstance(payload, dict):
        return RealtimeUpstreamEvent(event="invalid")
    event = str(payload.get("type") or "")
    if event == "conversation.item.input_audio_transcription.text":
        # 空文本的 partial 不上屏（文本层用 None 表示"这条事件没有文本"）。
        text = str(payload.get("text") or payload.get("stash") or "").strip()
        return RealtimeUpstreamEvent(event=event, text=text or None)
    if event == "conversation.item.input_audio_transcription.completed":
        text = str(payload.get("transcript") or payload.get("text") or "").strip()
        return RealtimeUpstreamEvent(
            event=event,
            text=text or None,
            final=True,
            usage=_usage_from_openai_realtime_event(payload),
        )
    if event == "session.finished":
        return RealtimeUpstreamEvent(
            event=event, usage=_usage_from_openai_realtime_event(payload)
        )
    if event in {"error", "asr.error"}:
        code = str(payload.get("code") or "").strip()
        message = str(payload.get("message") or payload.get("error") or "").strip()
        detail = ": ".join(part for part in (code, message) if part)
        return RealtimeUpstreamEvent(
            event="task-failed", error=detail or "DashScope realtime ASR failed"
        )
    return RealtimeUpstreamEvent(event=event or "unknown")


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
    # ``sentence_end`` is the only authoritative end-of-sentence signal.
    # Realtime recognition (qwen3-asr-flash / paraformer-realtime v2) repeats
    # the same sentence text across several incremental events, and every one
    # of them carries an integer ``end_time`` -- treating ``end_time`` as a
    # final marker turns each repeat into a separate final, and the client
    # (which appends finals) renders one spoken sentence several times.
    # ``end_time`` remains a fallback only for result shapes that never emit
    # ``sentence_end`` at all (gummy realtime), never for False/absent-marker
    # events of models that do.
    sentence_end = result.get("sentence_end")
    final = sentence_end is True or (
        sentence_end is None and isinstance(result.get("end_time"), (int, float))
    )
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
        # Release preflight writes BEFORE the long transcribe call.
        self.db.commit()
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
