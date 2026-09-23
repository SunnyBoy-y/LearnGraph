from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import weakref
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    Request,
    Query,
    Response,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import AppSettings, CurrentWorkspace, DB, WorkspaceContext
from app.core.admission import (
    AdmissionTicket,
    acquire_agent_stream_slot,
    snapshot_admission_metrics,
)
from app.core.errors import AppError
from app.domain.models import (
    Message,
    Workspace,
)
from app.domain.schemas.chat import (
    BranchRequest,
    ConceptBranchCreateRequest,
    ConceptBranchPromoteRequest,
    DictationCleanupRequest,
    DictationCleanupView,
    DictationTranscriptionView,
    MessageCreateRequest,
    MessageListPageView,
    MessageRetryRequest,
    MessageSnapshotView,
    MessageView,
    MessageVersionView,
    SSEEventEnvelope,
    SessionActivitySummaryRequest,
    SessionAutoTitleRequest,
    SessionContextUsageView,
    CompactContextResultView,
    SessionCreateRequest,
    SessionFileView,
    SessionView,
    SuggestedPromptBatchView,
    SuggestedPromptGenerateRequest,
)
from app.domain.schemas.common import ActionResponse
from app.domain.schemas.graphs import GraphChangeSetView, RejectGraphChangeSetRequest
from app.providers.factory import (
    fetch_provider_for_workspace,
    image_provider_for_workspace,
    model_provider_for_workspace,
    transcription_provider_for_workspace,
)
from app.services.chat import ChatService
from app.services.dictation import (
    DictationService,
    authenticate_realtime_dictation,
    build_openai_realtime_audio_frame,
    build_openai_realtime_finish,
    build_openai_realtime_session_update,
    build_realtime_finish_task,
    build_realtime_run_task,
    dashscope_realtime_ws_url,
    is_realtime_transcription_model,
    parse_openai_realtime_upstream_event,
    parse_realtime_upstream_event,
    uses_openai_realtime_transport,
)
from app.services.billing import BillingService
from app.services.graph_changes import GraphChangeSetService
from app.services.image_chat import ImageChatService
from app.services.authorization import AuthorizationService


router = APIRouter(prefix="/sessions", tags=["chat"])
SSE_TRANSPORT_READY_COMMENT = ": learngraph-stream-ready\n\n"
logger = logging.getLogger(__name__)


def _admit_agent_generation() -> Iterator[AdmissionTicket]:
    """Admission dependency for the three generation endpoints.

    Declared as the FIRST dependency of each endpoint — ordered ahead of
    ``db: DB`` / ``context: CurrentWorkspace`` — so FastAPI resolves it before
    creating any database session. That ordering is the entire point. Those
    dependencies run DB queries, and a gate that ran later (in the handler
    body) would already have spent a pooled connection on the very request it
    is about to reject: under a burst of simultaneous requests the pool could
    still time out before the gate ever saw them, which is exactly the opaque
    failure this module exists to replace.

    Being a dependency also means a rejection consumes no connection at all
    and skips auth/preflight, so the cost is that a saturated deployment
    answers 429 even for a session id that does not exist. That is an
    acceptable trade for an immediate, legible reason instead of a 10-second
    stall followed by an unreadable ``QueuePool limit ... reached``.

    It is a *generator* dependency on purpose. A plain function dependency
    would only be able to release inside the handler, and the handler never
    runs when a later dependency (auth / workspace) rejects the request — so
    every 403/404 raised after admission would leak one slot until the gate
    wedged. FastAPI guarantees a yield dependency's ``finally`` runs even when
    a later dependency or the endpoint raises, which is the one place that
    covers every non-handoff outcome.
    """
    lease = acquire_agent_stream_slot()
    if lease is None:
        raise AppError(
            429,
            "agent_stream_capacity_exceeded",
            "同时进行的对话已达到本机上限，请稍后重试。",
            details=snapshot_admission_metrics(),
        )
    try:
        yield lease
    finally:
        # Skipped once the transport has claimed ownership, because a detached
        # generation may outlive this request; the worker, the never-started
        # guard and the generator's GC finalizer release it in that case.
        lease.release_if_unclaimed()


# Placed ahead of ``db: DB`` in the signature so it is solved first; see the
# dependency's docstring for why the ordering is load-bearing.
AgentStreamLease = Annotated[AdmissionTicket, Depends(_admit_agent_generation)]


@dataclass(frozen=True, slots=True)
class _DetachedStreamFailure:
    error: BaseException


_DETACHED_STREAM_END = object()


def _detached_sse_transport(
    producer: Callable[[], Iterable[str]],
    *,
    session_id: str,
    thread_name: str,
    on_disconnect: Callable[[], None] | None = None,
    lease: AdmissionTicket | None = None,
):
    output: queue.Queue[str | object | _DetachedStreamFailure] = queue.Queue(
        maxsize=256
    )
    subscriber_active = threading.Event()
    subscriber_active.set()

    def publish(item: str | object | _DetachedStreamFailure) -> None:
        while subscriber_active.is_set():
            try:
                output.put(item, timeout=0.1)
                return
            except queue.Full:
                continue

    def produce() -> None:
        try:
            for chunk in producer():
                # Once the client leaves, discard transport chunks while
                # continuing the provider iterator and its durable writes.
                if subscriber_active.is_set():
                    publish(chunk)
        except BaseException as exc:
            if not isinstance(exc, AppError):
                logger.exception(
                    "Detached message stream worker failed",
                    extra={
                        "session_id": session_id,
                        "error_type": type(exc).__name__,
                    },
                )
            publish(_DetachedStreamFailure(exc))
        finally:
            # Release before signalling completion, so "the consumer saw the
            # end of the stream" implies "the slot is already back". Both the
            # worker's database sessions are closed by this point: the inner
            # producer's ``with session_factory()`` block and its own finally
            # have already run, which is also why the *generation* — not the
            # subscriber — owns the slot lifetime.
            if lease is not None:
                try:
                    lease.release()
                except Exception:
                    # Never let bookkeeping hang the response: a failure in
                    # the release path must not stop the end marker below, or
                    # the consumer would block on output.get() forever. A
                    # leaked slot is loud in the metrics; a wedged SSE stream
                    # is not.
                    logger.exception("Agent stream admission release failed")
            publish(_DETACHED_STREAM_END)

    worker = threading.Thread(
        target=produce,
        name=thread_name,
        daemon=True,
    )

    def events():
        worker_started = False
        try:
            worker.start()
            worker_started = True
            yield SSE_TRANSPORT_READY_COMMENT
            while True:
                item = output.get()
                if item is _DETACHED_STREAM_END:
                    return
                if isinstance(item, _DetachedStreamFailure):
                    error = item.error
                    if isinstance(error, AppError):
                        error_payload = {
                            "code": error.code,
                            "message": error.message,
                            "details": error.details,
                        }
                    else:
                        error_payload = {
                            "code": "stream_setup_failed",
                            "message": "The message stream could not be initialized",
                            "details": {"error_type": type(error).__name__},
                        }
                    event_id = str(uuid4())
                    data = {
                        "schema_version": "1.0",
                        "event_id": event_id,
                        "sequence": 0,
                        "session_id": session_id,
                        "message_id": "",
                        "message_version_id": "",
                        "part_id": None,
                        "type": "message.failed",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "payload": {"status": "failed", "error": error_payload},
                        "event": "message.failed",
                        "status": "failed",
                    }
                    yield (
                        f"id: {event_id}\n"
                        "event: message.failed\n"
                        f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                    )
                    return
                yield item
        finally:
            # Do not stop the worker. Clearing this flag only disables the
            # abandoned transport queue, preventing disconnect backpressure.
            subscriber_active.clear()
            # The worker owns the slot once it has started. If it never did
            # (the client aborted before the response body was iterated at
            # all), its own finally will never run, so hand the slot back here
            # instead of leaking one admission slot per abandoned request.
            if lease is not None and not worker_started:
                lease.release()
            if on_disconnect is not None:
                try:
                    on_disconnect()
                except Exception:
                    # Disconnect-side cancellation is best-effort; the worker
                    # continues its own persistence either way.
                    pass

    stream = events()
    if lease is not None:
        # Taking ownership: from here the detached generation releases the slot
        # itself, so the request-lifecycle owner stops touching it.
        lease.claim()
        # Safety net for the one path that executes no ``finally`` at all. If
        # the response object is built but never iterated (the client aborted
        # before the response started, so Starlette never began the body
        # iterator), then neither the worker nor the generator ever runs — and
        # closing or collecting an *unstarted* generator executes no code
        # either, so the slot would leak on every such request. release() is
        # idempotent, so this never double-frees the normal paths.
        weakref.finalize(stream, lease.release)
    return stream


def _detached_message_stream(
    *,
    context: WorkspaceContext,
    settings,
    session_id: str,
    payload: MessageCreateRequest,
    idempotency_key: str | None,
    last_event_id: str | None,
    session_factory: sessionmaker,
    lease: AdmissionTicket | None = None,
):
    """Run generation independently from the HTTP subscriber.

    A browser refresh closes the StreamingResponse iterator. The provider
    generator must keep its own database session and continue persisting
    events so a later subscriber can follow the same idempotent submission.
    """

    def produce():
        # Count this generation as an active stream so non-urgent scheduler
        # sweeps and the WAL TRUNCATE checkpoint defer while it runs (P3-S1).
        from app.core.database import enter_active_stream, exit_active_stream

        enter_active_stream()
        try:
            with session_factory() as worker_db:
                workspace = worker_db.get(Workspace, context.workspace_id)
                if workspace is None:
                    raise AppError(
                        404,
                        "workspace_not_found",
                        "The workspace no longer exists",
                    )
                worker_context = WorkspaceContext(
                    principal=context.principal,
                    workspace=workspace,
                    permissions=context.permissions,
                )
                if payload.generation_mode == "image":
                    worker_service = ImageChatService(
                        worker_db,
                        worker_context.workspace_id,
                        worker_context.principal.user_id,
                        settings,
                        image_provider_for_workspace(
                            worker_db,
                            worker_context.workspace_id,
                            settings,
                            model_id=payload.model_id,
                            provider_id=payload.provider_id,
                        ),
                    )
                else:
                    worker_service = service(
                        worker_db,
                        worker_context,
                        settings,
                        model_id=payload.model_id,
                        provider_id=payload.provider_id,
                        thinking_mode=payload.thinking_mode,
                        search_route=payload.search_route,
                        agent_mode=payload.agent_mode,
                    )
                yield from worker_service.create_stream(
                    session_id,
                    payload,
                    idempotency_key=idempotency_key,
                    last_event_id=last_event_id,
                )
        finally:
            exit_active_stream()

    return _detached_sse_transport(
        produce,
        session_id=session_id,
        thread_name=f"learngraph-message-{session_id[:8]}",
        lease=lease,
    )


def _detached_retry_stream(
    *,
    context: WorkspaceContext,
    settings,
    session_id: str,
    message_id: str,
    payload: MessageRetryRequest,
    session_factory: sessionmaker,
    lease: AdmissionTicket | None = None,
):
    def produce():
        # Retry streams are full generations too — count them as active so
        # background sweeps defer (P3-S1).
        from app.core.database import enter_active_stream, exit_active_stream

        enter_active_stream()
        try:
            with session_factory() as worker_db:
                workspace = worker_db.get(Workspace, context.workspace_id)
                if workspace is None:
                    raise AppError(
                        404,
                        "workspace_not_found",
                        "The workspace no longer exists",
                    )
                worker_context = WorkspaceContext(
                    principal=context.principal,
                    workspace=workspace,
                    permissions=context.permissions,
                )
                worker_service = service(
                    worker_db,
                    worker_context,
                    settings,
                    model_id=payload.model_id,
                    provider_id=payload.provider_id,
                    thinking_mode=payload.thinking_mode,
                    search_route=payload.search_route,
                )
                yield from worker_service.retry_message(
                    session_id,
                    message_id,
                    payload,
                )
        finally:
            exit_active_stream()

    return _detached_sse_transport(
        produce,
        session_id=session_id,
        thread_name=f"learngraph-retry-{message_id[:8]}",
        lease=lease,
    )


def require_session_access(
    session_id: str,
    permission: str,
    db: DB,
    context: CurrentWorkspace,
) -> None:
    if not AuthorizationService(db, context.principal).can_access_resource(
        context.workspace,
        "session",
        session_id,
        permission,
    ):
        raise AppError(404, "not_found", "Resource not found in this workspace")


def service(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    model_id: str | None = None,
    provider_id: str | None = None,
    thinking_mode: str | None = None,
    search_route: str | None = None,
    *,
    agent_mode: bool = True,
) -> ChatService:
    # 单一装配点：委托 chat_service_factory.build_chat_service，避免与
    # subapp 路径的 ChatService / AgentToolRuntime 装配漂移（历史上曾因两处
    # 重复装配漏传 tool_worker_factory，导致并行工具执行器形同虚设）。
    from app.services.chat_service_factory import build_chat_service

    return build_chat_service(
        db,
        workspace_context=context,
        settings=settings,
        model_id=model_id,
        provider_id=provider_id,
        thinking_mode=thinking_mode,
        search_route=search_route,
        agent_mode=agent_mode,
    )



def graph_change_service(db: DB, context: CurrentWorkspace) -> GraphChangeSetService:
    return GraphChangeSetService(
        db,
        context.workspace_id,
        context.principal.user_id,
    )


@router.get("", response_model=list[SessionView])
def list_sessions(db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[SessionView]:
    authz = AuthorizationService(db, context.principal)
    items = service(db, context, settings).list_sessions()
    # B1-4: batch authorization instead of per-item can_access_resource.
    accessible = authz.filter_accessible_ids(
        context.workspace, "session", [item.id for item in items], "read"
    )
    return [
        SessionView.model_validate(item)
        for item in items
        if item.id in accessible
    ]


@router.post("", response_model=SessionView, status_code=status.HTTP_201_CREATED)
def create_session(
    payload: SessionCreateRequest,
    request: Request,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ] = None,
) -> SessionView:
    return SessionView.model_validate(
        service(db, context, settings).create_session(
            payload,
            idempotency_key=idempotency_key,
        )
    )


# Static path registered before the /{session_id}/... routes below so a
# session named "dictation" can never shadow it (and vice versa).
@router.post(
    "/dictation/cleanup",
    response_model=DictationCleanupView,
    responses={
        402: {
            "description": (
                "The workspace hard budget blocks the remote call "
                "(budget_hard_limit_exceeded)"
            )
        },
        409: {
            "description": (
                "Cleanup is disabled, the persisted setting is invalid, or "
                "usage preflight rejected the call (dictation_cleanup_disabled, "
                "dictation_cleanup_setting_invalid, usage_price_required)"
            )
        },
        502: {
            "description": (
                "The remote Provider returned invalid structured output "
                "(dictation_cleanup_failed)"
            )
        },
        503: {
            "description": (
                "The model Provider is unavailable or not remote-capable "
                "(model_provider_unavailable, remote_model_required)"
            )
        },
    },
)
def cleanup_dictation(
    payload: DictationCleanupRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> DictationCleanupView:
    return service(
        db,
        context,
        settings,
        model_id=payload.model_id,
        provider_id=payload.provider_id,
        thinking_mode="off",
    ).cleanup_dictation(payload)


# Static path registered before the /{session_id}/... routes below so a
# session named "dictation" can never shadow it (and vice versa).
@router.post(
    "/dictation/transcriptions",
    response_model=DictationTranscriptionView,
    responses={
        402: {
            "description": (
                "The workspace hard budget blocks the remote call "
                "(budget_hard_limit_exceeded)"
            )
        },
        413: {"description": "The segment exceeds the dictation size bound (audio_segment_too_large)"},
        415: {"description": "The upload is not audio (audio_required)"},
        502: {
            "description": (
                "The remote ASR Provider failed (transcription_provider_failed)"
            )
        },
        503: {
            "description": (
                "No enabled remote ASR Provider matches this request "
                "(transcription_provider_unavailable)"
            )
        },
    },
)
async def transcribe_dictation_segment(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    file: Annotated[UploadFile, File()],
    provider_id: Annotated[str | None, Form(max_length=36)] = None,
    model_id: Annotated[str | None, Form(max_length=160)] = None,
    language: Annotated[str | None, Form(max_length=16)] = None,
) -> DictationTranscriptionView:
    """Transcribe one live microphone segment through the workspace ASR Provider.

    The audio is never stored: segments are cut at natural pauses on the
    client so the Provider's native punctuation inference is preserved and the
    microphone session keeps running while earlier segments upload.
    """

    content = await file.read()
    return DictationTranscriptionView.model_validate(
        DictationService(
            db, context.workspace_id, context.principal.user_id, settings
        ).transcribe_segment(
            content=content,
            mime_type=file.content_type or "application/octet-stream",
            filename=file.filename or "dictation-segment.webm",
            provider_id=provider_id or None,
            model_id=model_id or None,
            language=language or None,
        )
    )


async def _ws_error(websocket: WebSocket, code: str, message: str) -> None:
    try:
        await websocket.send_json({"type": "error", "code": code, "message": message})
        await websocket.close()
    except Exception:
        # 客户端可能已断开;错误通知尽力而为。
        pass


def _passthrough_pcm(chunk: bytes) -> bytes:
    """inference 方言的上行帧就是裸二进制 PCM，不做封装。"""

    return chunk


@router.websocket("/dictation/realtime")
async def dictation_realtime(websocket: WebSocket, db: DB, settings: AppSettings) -> None:
    """Proxy live microphone PCM to the DashScope realtime ASR WebSocket.

    Realtime models (``qwen3-asr-flash-realtime`` / ``paraformer-realtime`` /
    ``gummy-realtime``) are WebSocket-only, so the browser keeps ONE duplex
    connection here and the server bridges it to DashScope with the stored
    Provider secret.  Client protocol: first frame ``{type:"start", token,
    workspace_id, sample_rate}``; then binary PCM16 mono frames; then
    ``{type:"stop"}``.  Server frames: ``ready`` → ``partial``/``final`` text
    events (native punctuation preserved) → ``done`` or ``error``.
    """

    await websocket.accept()
    try:
        raw_start = await asyncio.wait_for(websocket.receive_text(), timeout=10)
        start = json.loads(raw_start)
    except Exception:
        await _ws_error(websocket, "invalid_start", "Expected a start frame within 10s")
        return
    if not isinstance(start, dict) or start.get("type") != "start":
        await _ws_error(websocket, "invalid_start", "The first frame must be a start message")
        return
    token = str(start.get("token") or "")
    workspace_id = str(start.get("workspace_id") or "")
    user_id = authenticate_realtime_dictation(db, token, workspace_id)
    if user_id is None:
        await _ws_error(websocket, "unauthorized", "A valid session token and workspace are required")
        return
    adapter = transcription_provider_for_workspace(
        db,
        workspace_id,
        settings,
        provider_id=str(start.get("provider_id") or "") or None,
        model_id=str(start.get("model_id") or "") or None,
        purpose="realtime",
    )
    if adapter is None:
        await _ws_error(
            websocket,
            "transcription_provider_unavailable",
            "No enabled remote ASR Provider matches this request",
        )
        return
    if not is_realtime_transcription_model(adapter.model_id):
        await _ws_error(
            websocket,
            "realtime_model_required",
            "The configured transcription model is not a realtime model",
        )
        return
    # 端点与协议都由模型家族决定（见 services/dictation.py 的端点说明）：
    # qwen3-asr-flash-realtime 家族走 /api-ws/v1/realtime 的 OpenAI 方言，
    # paraformer/gummy 家族保持 /api-ws/v1/inference 的 run-task 方言。
    openai_realtime = uses_openai_realtime_transport(adapter.model_id)
    upstream_url = dashscope_realtime_ws_url(adapter.base_url, adapter.model_id)
    if upstream_url is None:
        await _ws_error(
            websocket,
            "realtime_unsupported_provider",
            "Realtime dictation requires a DashScope base URL",
        )
        return
    try:
        sample_rate = int(start.get("sample_rate") or 16_000)
    except (TypeError, ValueError):
        sample_rate = 16_000
    if not 8_000 <= sample_rate <= 48_000:
        sample_rate = 16_000

    billing = BillingService(db, workspace_id, user_id)
    try:
        quote = billing.preflight_model_call(
            provider_id=adapter.provider_id,
            model_id=adapter.model_id,
            feature="audio_transcription",
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            remote_capability=True,
        )
        db.commit()
    except AppError as exc:
        await _ws_error(websocket, exc.code, exc.message)
        return

    language = str(start.get("language") or "").strip() or None
    hotwords_raw = start.get("hotwords")
    hotwords = (
        [str(item).strip() for item in hotwords_raw if str(item).strip()]
        if isinstance(hotwords_raw, list)
        else None
    )

    # 两套方言的差异集中在这几个局部变量里，握手/泵送逻辑完全共用：
    # 上行音频的封装方式、就绪事件名、收尾事件名、收尾帧、事件解析器与鉴权头。
    if openai_realtime:
        parse_upstream_event = parse_openai_realtime_upstream_event
        ready_event = "session.updated"
        finished_event = "session.finished"
        open_frame = build_openai_realtime_session_update(
            adapter.model_id, sample_rate, language=language
        )
        finish_frame = build_openai_realtime_finish()
        encode_audio = build_openai_realtime_audio_frame
        upstream_headers = {
            "Authorization": f"Bearer {adapter.api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
    else:
        parse_upstream_event = parse_realtime_upstream_event
        ready_event = "task-started"
        finished_event = "task-finished"
        task_id, open_frame = build_realtime_run_task(
            adapter.model_id, sample_rate, language=language, hotwords=hotwords
        )
        finish_frame = build_realtime_finish_task(task_id)
        encode_audio = _passthrough_pcm
        upstream_headers = {"Authorization": f"bearer {adapter.api_key}"}

    from websockets.asyncio.client import connect as ws_connect

    started_at = asyncio.get_running_loop().time()
    try:
        upstream = await ws_connect(
            upstream_url,
            additional_headers=upstream_headers,
            max_size=2**22,
            open_timeout=15,
        )
    except Exception:
        await _ws_error(
            websocket,
            "upstream_connect_failed",
            "Could not reach the DashScope realtime ASR endpoint",
        )
        return

    finish_sent = False
    handshake_ok = False
    try:
        await upstream.send(open_frame)
        while True:
            event = parse_upstream_event(
                await asyncio.wait_for(upstream.recv(), timeout=20)
            )
            if event.event == ready_event:
                handshake_ok = True
                break
            if event.event == "task-failed":
                await _ws_error(
                    websocket, "asr_task_failed", event.error or "DashScope rejected the task"
                )
                return
    except Exception:
        await _ws_error(
            websocket,
            "asr_task_failed",
            "DashScope did not start the ASR session"
            if openai_realtime
            else "DashScope did not start the ASR task",
        )
        return
    finally:
        # 握手没走到 ready（客户端断开、或上游以 task-failed 拒了这个模型/参数）时，
        # 上游连接必须在这里关掉：这条路径会 return，后面的泵送阶段再也不会接手它。
        # 旧实现只在"客户端已断开"时关闭，于是每次被上游拒绝都漏掉一条到 DashScope
        # 的长连接（修复前 qwen 模型每次都走这条路径）。
        if not handshake_ok or websocket.client_state.name != "CONNECTED":
            await upstream.close()

    if websocket.client_state.name != "CONNECTED":
        # 客户端在等待 ASR 任务启动期间已断开,无需继续。
        return

    try:
        await websocket.send_json({"type": "ready", "sample_rate": sample_rate})
    except Exception:
        # 竞态:状态检查通过后客户端才断开,send_json 会抛
        # WebSocketDisconnect(1006)/InvalidState。连接已不可用,
        # 关闭上游并静默退出,避免 ASGI 层记录无意义的异常栈。
        await upstream.close()
        return

    async def pump_client() -> str:
        nonlocal finish_sent
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return "disconnect"
                data = message.get("bytes")
                if data:
                    # OpenAI 方言要求 base64 的 append 帧，inference 方言直接收二进制。
                    await upstream.send(encode_audio(data))
                    continue
                text = message.get("text")
                if not text:
                    continue
                try:
                    frame = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(frame, dict) and frame.get("type") == "stop":
                    await upstream.send(finish_frame)
                    finish_sent = True
                    return "stop"
        except Exception:
            return "disconnect"

    async def pump_upstream() -> dict[str, int] | None:
        """Relay text events until the task ends; returns usage when finished."""
        usage: dict[str, int] = {}
        try:
            async for raw_event in upstream:
                event = parse_upstream_event(raw_event)
                if event.usage:
                    for key, value in event.usage.items():
                        usage[key] = usage.get(key, 0) + value
                if event.event == "task-failed":
                    await _ws_error(
                        websocket, "asr_task_failed", event.error or "ASR task failed"
                    )
                    return None
                if event.text is not None:
                    await websocket.send_json(
                        {"type": "final" if event.final else "partial", "text": event.text}
                    )
                elif event.event == finished_event:
                    return usage or None
        except Exception:
            return None
        return None

    client_task = asyncio.create_task(pump_client())
    upstream_task = asyncio.create_task(pump_upstream())
    usage: dict[str, int] | None = None
    stop_reason = "disconnect"
    try:
        done, _ = await asyncio.wait(
            {client_task, upstream_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if client_task in done:
            stop_reason = client_task.result()
            if not finish_sent:
                try:
                    await upstream.send(finish_frame)
                    finish_sent = True
                except Exception:
                    pass
            try:
                usage = await asyncio.wait_for(
                    upstream_task, timeout=20 if stop_reason == "stop" else 5
                )
            except (asyncio.TimeoutError, Exception):
                upstream_task.cancel()
        else:
            usage = upstream_task.result()
            client_task.cancel()
    finally:
        for task in (client_task, upstream_task):
            if not task.done():
                task.cancel()
        try:
            await upstream.close()
        except Exception:
            pass

    latency_ms = int((asyncio.get_running_loop().time() - started_at) * 1000)
    try:
        billing.record_usage(
            quote,
            input_tokens=int((usage or {}).get("input_tokens") or 0),
            output_tokens=int((usage or {}).get("output_tokens") or 0),
            cached_input_tokens=int((usage or {}).get("cached_input_tokens") or 0),
            cache_creation_input_tokens=int(
                (usage or {}).get("cache_creation_input_tokens") or 0
            ),
            reasoning_tokens=int((usage or {}).get("reasoning_tokens") or 0),
            attempt=1,
            latency_ms=latency_ms,
            usage_reported=bool(usage),
        )
        db.commit()
    except Exception:
        db.rollback()

    if stop_reason == "stop":
        try:
            await websocket.send_json({"type": "done"})
            await websocket.close()
        except Exception:
            pass


@router.post("/{session_id}/auto-title", response_model=SessionView)
def auto_title_session(
    session_id: str,
    payload: SessionAutoTitleRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SessionView:
    require_session_access(session_id, "write", db, context)
    session = service(
        db,
        context,
        settings,
        model_id=payload.model_id,
        provider_id=payload.provider_id,
        thinking_mode="off",
    ).auto_title_session(session_id, payload)
    return SessionView.model_validate(session)


@router.post("/{session_id}/activity-summary", response_model=SessionView)
def activity_summary_session(
    session_id: str,
    payload: SessionActivitySummaryRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SessionView:
    require_session_access(session_id, "write", db, context)
    session = service(
        db,
        context,
        settings,
        model_id=payload.model_id,
        provider_id=payload.provider_id,
        thinking_mode="off",
    ).activity_summary_session(session_id, payload)
    return SessionView.model_validate(session)


@router.get(
    "/{session_id}/suggested-prompts",
    response_model=SuggestedPromptBatchView,
    responses={
        204: {"description": "No generated batch matches the current context"},
        404: {
            "description": (
                "Session not found, or an inherited Session or linked Project, "
                "Goal, or Graph is not readable by the caller (not_found)"
            )
        },
        409: {
            "description": (
                "The persisted workspace setting is invalid "
                "(suggested_prompts_setting_invalid)"
            )
        },
    },
)
def get_suggested_prompts(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SuggestedPromptBatchView | Response:
    require_session_access(session_id, "read", db, context)
    batch = service(db, context, settings).get_suggested_prompt_batch(session_id)
    if batch is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return batch


@router.post(
    "/{session_id}/suggested-prompts",
    response_model=SuggestedPromptBatchView,
    responses={
        204: {
            "description": (
                "No batch was produced: the conversation has no turn yet (the "
                "new-session opener belongs to the workspace memories) or its "
                "current turn is a full-duplex voice turn"
            )
        },
        402: {
            "description": (
                "The workspace hard budget blocks the remote call "
                "(budget_hard_limit_exceeded)"
            )
        },
        404: {
            "description": (
                "Session not found, or an inherited Session or linked Project, "
                "Goal, or Graph is not readable by the caller (not_found)"
            )
        },
        409: {
            "description": (
                "Generation is disabled, the setting is invalid, the current "
                "assistant anchor is unavailable or stale, the authorized context "
                "changed, or usage preflight rejected the call "
                "(suggested_prompts_disabled, suggested_prompts_setting_invalid, "
                "suggested_prompt_anchor_unavailable, suggested_prompt_anchor_stale, "
                "suggested_prompt_context_stale, usage_price_required)"
            )
        },
        502: {
            "description": (
                "The remote Provider returned invalid structured output or a "
                "different question count (suggested_prompt_generation_failed, "
                "suggested_prompt_count_mismatch)"
            )
        },
        503: {
            "description": (
                "The model or Memory Provider is unavailable, or the selected "
                "model Provider is not remote-capable (model_provider_unavailable, "
                "memory_provider_unavailable, remote_model_required)"
            )
        },
        504: {
            "description": (
                "The remote model Provider timed out "
                "(suggested_prompt_provider_timeout)"
            )
        },
    },
)
def generate_suggested_prompts(
    session_id: str,
    payload: SuggestedPromptGenerateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SuggestedPromptBatchView | Response:
    require_session_access(session_id, "write", db, context)
    batch = service(
        db,
        context,
        settings,
        model_id=payload.model_id,
        provider_id=payload.provider_id,
        thinking_mode="off",
    ).generate_suggested_prompts(session_id, payload)
    if batch is None:
        # Structural refusal (empty session / voice anchor): "no batch" is the
        # answer, not an error. A client that has not learned the gate yet then
        # shows nothing rather than a failure card it cannot resolve.
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return batch


@router.post("/{session_id}/close", response_model=SessionView)
def close_session(session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> SessionView:
    return SessionView.model_validate(service(db, context, settings).close_session(session_id))


@router.get("/{session_id}/graph-change-sets", response_model=list[GraphChangeSetView])
def list_graph_change_sets(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> list[GraphChangeSetView]:
    return [
        GraphChangeSetView.model_validate(item)
        for item in graph_change_service(db, context).list_for_session(session_id)
    ]


@router.post(
    "/{session_id}/graph-change-sets/{change_set_id}/confirm",
    response_model=GraphChangeSetView,
)
def confirm_graph_change_set(
    session_id: str,
    change_set_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> GraphChangeSetView:
    return GraphChangeSetView.model_validate(
        graph_change_service(db, context).confirm(session_id, change_set_id)
    )


@router.post(
    "/{session_id}/graph-change-sets/{change_set_id}/reject",
    response_model=GraphChangeSetView,
)
def reject_graph_change_set(
    session_id: str,
    change_set_id: str,
    payload: RejectGraphChangeSetRequest,
    db: DB,
    context: CurrentWorkspace,
) -> GraphChangeSetView:
    return GraphChangeSetView.model_validate(
        graph_change_service(db, context).reject(session_id, change_set_id, payload.reason)
    )


@router.post(
    "/{session_id}/graph-change-sets/{change_set_id}/undo",
    response_model=GraphChangeSetView,
)
def undo_graph_change_set(
    session_id: str,
    change_set_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> GraphChangeSetView:
    return GraphChangeSetView.model_validate(
        graph_change_service(db, context).undo(session_id, change_set_id)
    )


@router.get("/{session_id}/messages", response_model=MessageListPageView)
def list_messages(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    before_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    compact: bool = True,
) -> MessageListPageView:
    """Return the session timeline, optionally windowed and compact.

    - Default: full timeline with compact parts/provider_trace (list UI).
    - ``limit``: newest N messages (or the window ending just before ``before_id``).
    - ``compact=false``: full durable parts/provider_trace (debug / rare callers).
    Full fidelity for a single message remains on
    ``GET /messages/{message_id}``.
    """

    return MessageListPageView.model_validate(
        service(db, context, settings).list_messages_page(
            session_id,
            limit=limit,
            before_id=before_id,
            compact=compact,
        )
    )


@router.get("/{session_id}/context-usage", response_model=SessionContextUsageView)
def get_session_context_usage(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    model_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    provider_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    agent_mode: bool = False,
) -> SessionContextUsageView:
    return SessionContextUsageView.model_validate(
        service(
            db,
            context,
            settings,
            model_id=model_id,
            provider_id=provider_id,
        ).context_usage(session_id, agent_mode=agent_mode)
    )


@router.post("/{session_id}/compact", response_model=CompactContextResultView)
def compact_session_context(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    model_id: Annotated[str | None, Query(min_length=1, max_length=160)] = None,
    provider_id: Annotated[str | None, Query(min_length=1, max_length=36)] = None,
    agent_mode: bool = False,
) -> CompactContextResultView:
    """Manually compact the session context into a durable summary.

    Mirrors the automatic compaction path: older messages are reduced to a
    summary record that the next prompt build uses instead of the raw
    history. Returns a ``skipped`` result (with reason) when the context is
    still small or there is nothing to compact.
    """
    require_session_access(session_id, "write", db, context)
    return CompactContextResultView.model_validate(
        service(
            db,
            context,
            settings,
            model_id=model_id,
            provider_id=provider_id,
        ).compact_context(session_id, agent_mode=agent_mode)
    )


@router.get("/{session_id}/files", response_model=list[SessionFileView])
def list_session_files(
    session_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> list[SessionFileView]:
    """List every durable file tied to the session (unified file-area view).

    Merges message attachments (FileReference), generated images
    (ImageGenerationTask) and session workspace entries — external downloads
    and agent writes included, which previously only ``sandbox_list_files``
    could see. Same data the Agent ``list_session_files`` tool returns.
    """

    require_session_access(session_id, "read", db, context)
    from app.services.session_files import collect_session_files

    return [
        SessionFileView.model_validate(item)
        for item in collect_session_files(
            db,
            workspace_id=context.workspace_id,
            session_id=session_id,
        )
    ]


@router.get(
    "/{session_id}/messages/{message_id}",
    response_model=MessageSnapshotView,
)
def get_message_snapshot(
    session_id: str,
    message_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    message_version_id: Annotated[
        str | None,
        Query(min_length=1, max_length=36),
    ] = None,
) -> MessageSnapshotView:
    return service(db, context, settings).get_message_snapshot(
        session_id,
        message_id,
        message_version_id=message_version_id,
    )


@router.get("/{session_id}/messages/{message_id}/versions", response_model=list[MessageVersionView])
def list_message_versions(session_id: str, message_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[MessageVersionView]:
    return [MessageVersionView.model_validate(item) for item in service(db, context, settings).list_message_versions(session_id, message_id)]


@router.post("/{session_id}/messages/stream")
def stream_message(
    session_id: str,
    payload: MessageCreateRequest,
    lease: AgentStreamLease,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ] = None,
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", min_length=1, max_length=128),
    ] = None,
    after_event_id: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
) -> StreamingResponse:
    require_session_access(session_id, "write", db, context)
    if after_event_id and last_event_id and after_event_id != last_event_id:
        raise AppError(
            400,
            "conflicting_event_cursor",
            "after_event_id and Last-Event-ID must match when both are provided",
        )
    if payload.generation_mode == "image":
        stream_service = ImageChatService(
            db,
            context.workspace_id,
            context.principal.user_id,
            settings,
            image_provider_for_workspace(
                db,
                context.workspace_id,
                settings,
                model_id=payload.model_id,
                provider_id=payload.provider_id,
            ),
        )
    else:
        stream_service = service(
            db,
            context,
            settings,
            model_id=payload.model_id,
            provider_id=payload.provider_id,
            thinking_mode=payload.thinking_mode,
            search_route=payload.search_route,
            # Preflight only needs validation dependencies. Deferring the
            # Agent runtime avoids MCP/Sandbox setup before the stream can
            # start.
            agent_mode=False,
        )
    stream_service.preflight_create_stream(
        session_id,
        payload,
        idempotency_key=idempotency_key,
        last_event_id=after_event_id or last_event_id,
    )

    # Hand the slot to the transport: from here the worker, the never-started
    # guard or the generator's GC finalizer releases it. Nothing in this
    # handler releases it any more — the admission dependency's ``finally`` is
    # the single lifecycle owner for every non-handoff outcome.
    return StreamingResponse(
        _detached_message_stream(
            context=context,
            settings=settings,
            session_id=session_id,
            payload=payload,
            idempotency_key=idempotency_key,
            last_event_id=after_event_id or last_event_id,
            session_factory=sessionmaker(
                bind=db.get_bind(),
                autoflush=False,
                expire_on_commit=False,
            ),
            lease=lease,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
            "X-SSE-Schema-Version": "1.0",
        },
    )


@router.post("/{session_id}/messages/async")
def async_message(
    session_id: str,
    payload: MessageCreateRequest,
    lease: AgentStreamLease,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=128),
    ] = None,
) -> dict:
    """投递消息到后台生成（手机「异步任务」/ 通知快捷回复用）。

    与 /messages/stream 共用同一套幂等 + 独立 worker 的生成链路
    （_detached_message_stream），但调用方不订阅 SSE：事件照常落库，
    生成在后台线程跑完。客户端通过 GET /sessions/{id}（updated_at）
    或 /sessions/{id}/messages 轮询结果。

    依赖约束与 stream 一致（provider 可用性、agent_mode 前置校验），
    因此先做 preflight，再起后台线程消费 detached 流到完成。
    """
    require_session_access(session_id, "write", db, context)
    if payload.generation_mode == "image":
        stream_service = ImageChatService(
            db,
            context.workspace_id,
            context.principal.user_id,
            settings,
            image_provider_for_workspace(
                db,
                context.workspace_id,
                settings,
                model_id=payload.model_id,
                provider_id=payload.provider_id,
            ),
        )
    else:
        stream_service = service(
            db,
            context,
            settings,
            model_id=payload.model_id,
            provider_id=payload.provider_id,
            thinking_mode=payload.thinking_mode,
            search_route=payload.search_route,
            agent_mode=False,
        )
    stream_service.preflight_create_stream(
        session_id,
        payload,
        idempotency_key=idempotency_key,
        last_event_id=None,
    )

    # The background drain thread below starts the transport worker, which then
    # claims the slot. Building the transport before the thread exists also
    # means a saturated deployment rejects the *submission* rather than
    # accepting a job it cannot run.
    events = _detached_message_stream(
        context=context,
        settings=settings,
        session_id=session_id,
        payload=payload,
        idempotency_key=idempotency_key,
        last_event_id=None,
        session_factory=sessionmaker(
            bind=db.get_bind(),
            autoflush=False,
            expire_on_commit=False,
        ),
        lease=lease,
    )

    def drain() -> None:
        # 消费 detached 流到完成：events() 会启动 worker 线程并持续
        # 持久化事件；这里只是让生成器跑完（含错误上报）。
        try:
            for _ in events:
                pass
        except Exception:
            pass

    threading.Thread(
        target=drain,
        name=f"learngraph-async-{session_id[:8]}",
        daemon=True,
    ).start()

    return {"status": "queued", "session_id": session_id}


@router.get(
    "/{session_id}/messages/{message_id}/events",
    response_model=list[SSEEventEnvelope],
)
def replay_message_events(
    session_id: str,
    message_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    after_event_id: Annotated[
        str | None,
        Query(min_length=1, max_length=128),
    ] = None,
    last_event_id: Annotated[
        str | None,
        Header(alias="Last-Event-ID", min_length=1, max_length=128),
    ] = None,
    message_version_id: Annotated[
        str | None,
        Query(min_length=1, max_length=36),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
) -> list[SSEEventEnvelope]:
    if after_event_id and last_event_id and after_event_id != last_event_id:
        raise AppError(
            400,
            "conflicting_event_cursor",
            "after_event_id and Last-Event-ID must match when both are provided",
        )
    return service(db, context, settings).list_events(
        session_id,
        message_id,
        after_event_id=after_event_id or last_event_id,
        message_version_id=message_version_id,
        limit=limit,
    )


@router.post("/{session_id}/messages/{message_id}/branch", response_model=SessionView, status_code=status.HTTP_201_CREATED)
def branch_session(
    session_id: str,
    message_id: str,
    payload: BranchRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SessionView:
    return SessionView.model_validate(service(db, context, settings).branch(session_id, message_id, payload))


@router.post(
    "/{session_id}/concept-branches",
    response_model=SessionView,
    status_code=status.HTTP_201_CREATED,
)
def create_concept_branch(
    session_id: str,
    payload: ConceptBranchCreateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SessionView:
    require_session_access(session_id, "write", db, context)
    return SessionView.model_validate(
        service(db, context, settings).create_concept_branch(session_id, payload)
    )


@router.post("/{session_id}/promote", response_model=SessionView)
def promote_concept_branch(
    session_id: str,
    payload: ConceptBranchPromoteRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> SessionView:
    require_session_access(session_id, "write", db, context)
    return SessionView.model_validate(
        service(db, context, settings).promote_concept_branch(session_id, payload)
    )


@router.post("/{session_id}/messages/{message_id}/cancel", response_model=ActionResponse)
def cancel_message(session_id: str, message_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> ActionResponse:
    version_id = service(db, context, settings).cancel_message(session_id, message_id)
    return ActionResponse(status="cancelled", message="The message was cancelled", resource_id=version_id)


@router.post("/{session_id}/messages/{message_id}/retry")
def retry_message(
    session_id: str,
    message_id: str,
    lease: AgentStreamLease,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    payload: MessageRetryRequest | None = None,
) -> StreamingResponse:
    retry_payload = payload or MessageRetryRequest()
    require_session_access(session_id, "write", db, context)
    chat_service = service(
        db,
        context,
        settings,
        model_id=retry_payload.model_id,
        provider_id=retry_payload.provider_id,
        thinking_mode=retry_payload.thinking_mode,
        search_route=retry_payload.search_route,
    )
    chat_service.preflight_retry_message(
        session_id,
        message_id,
        retry_payload,
    )

    return StreamingResponse(
        _detached_retry_stream(
            context=context,
            settings=settings,
            session_id=session_id,
            message_id=message_id,
            payload=retry_payload,
            session_factory=sessionmaker(
                bind=db.get_bind(),
                autoflush=False,
                expire_on_commit=False,
            ),
            lease=lease,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
            "X-SSE-Schema-Version": "1.0",
        },
    )
