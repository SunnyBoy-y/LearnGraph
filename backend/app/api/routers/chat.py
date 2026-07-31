from __future__ import annotations

import asyncio
import json
import queue
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Annotated

from fastapi import (
    APIRouter,
    File,
    Form,
    Header,
    Query,
    Response,
    UploadFile,
    WebSocket,
    status,
)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import sessionmaker

from app.api.deps import AppSettings, CurrentWorkspace, DB, WorkspaceContext
from app.core.errors import AppError
from app.domain.models import Workspace
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
    SessionCreateRequest,
    SessionView,
    SuggestedPromptBatchView,
    SuggestedPromptGenerateRequest,
)
from app.domain.schemas.common import ActionResponse
from app.domain.schemas.graphs import GraphChangeSetView, RejectGraphChangeSetRequest
from app.providers.factory import (
    fetch_provider_for_workspace,
    image_provider_for_workspace,
    memory_provider_for_workspace,
    model_provider_for_workspace,
    search_provider_for_workspace,
    transcription_provider_for_workspace,
    vision_provider_for_workspace,
)
from app.services.chat import ChatService
from app.services.context_builder import ContextBuilder
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_router import MemoryRouter
from app.services.dictation import (
    DictationService,
    authenticate_realtime_dictation,
    build_realtime_finish_task,
    build_realtime_run_task,
    dashscope_realtime_ws_url,
    is_realtime_transcription_model,
    parse_realtime_upstream_event,
)
from app.services.billing import BillingService
from app.services.graph_changes import GraphChangeSetService
from app.services.image_chat import ImageChatService
from app.services.memory import MemoryService
from app.services.authorization import AuthorizationService
from app.services.agent_runtime import AgentToolRuntime
from app.services.mcp_skills import MCPAndSkillService
from app.services.sandbox import SandboxAgentWorkspaceService
from app.services.session_retrieval import SessionRetrievalService


router = APIRouter(prefix="/sessions", tags=["chat"])
SSE_TRANSPORT_READY_COMMENT = ": learngraph-stream-ready\n\n"


@dataclass(frozen=True, slots=True)
class _DetachedStreamFailure:
    error: BaseException


_DETACHED_STREAM_END = object()


def _detached_sse_transport(
    producer: Callable[[], Iterable[str]],
    *,
    thread_name: str,
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
            publish(_DetachedStreamFailure(exc))
        finally:
            publish(_DETACHED_STREAM_END)

    worker = threading.Thread(
        target=produce,
        name=thread_name,
        daemon=True,
    )

    def events():
        worker.start()
        try:
            yield SSE_TRANSPORT_READY_COMMENT
            while True:
                item = output.get()
                if item is _DETACHED_STREAM_END:
                    return
                if isinstance(item, _DetachedStreamFailure):
                    raise item.error
                yield item
        finally:
            # Do not stop the worker. Clearing this flag only disables the
            # abandoned transport queue, preventing disconnect backpressure.
            subscriber_active.clear()

    return events()


def _detached_message_stream(
    *,
    context: WorkspaceContext,
    settings,
    session_id: str,
    payload: MessageCreateRequest,
    idempotency_key: str | None,
    last_event_id: str | None,
    session_factory: sessionmaker,
):
    """Run generation independently from the HTTP subscriber.

    A browser refresh closes the StreamingResponse iterator. The provider
    generator must keep its own database session and continue persisting
    events so a later subscriber can follow the same idempotent submission.
    """

    def produce():
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
                )
            yield from worker_service.create_stream(
                session_id,
                payload,
                idempotency_key=idempotency_key,
                last_event_id=last_event_id,
            )

    return _detached_sse_transport(
        produce,
        thread_name=f"learngraph-message-{session_id[:8]}",
    )


def _detached_retry_stream(
    *,
    context: WorkspaceContext,
    settings,
    session_id: str,
    message_id: str,
    payload: MessageRetryRequest,
    session_factory: sessionmaker,
):
    def produce():
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

    return _detached_sse_transport(
        produce,
        thread_name=f"learngraph-retry-{message_id[:8]}",
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
) -> ChatService:
    authorization = AuthorizationService(db, context.principal)
    search_provider = search_provider_for_workspace(
        db, context.workspace_id, settings, route=search_route
    )
    memory_service = MemoryService(
        db,
        context.workspace,
        context.principal.user_id,
        memory_provider_for_workspace(
            db,
            context.workspace,
            context.principal.user_id,
            settings,
        ),
        settings.memory_root,
    )
    model_kwargs: dict[str, str] = {}
    if model_id is not None:
        model_kwargs["model_id"] = model_id
    if provider_id is not None:
        model_kwargs["provider_id"] = provider_id
    if thinking_mode is not None:
        model_kwargs["thinking_mode"] = thinking_mode
    if search_route not in {None, "disabled"}:
        model_kwargs["search_route"] = search_route
    sandbox_authorized = "workspace.manage" in authorization.workspace_permissions(
        context.workspace
    )
    extension_service = MCPAndSkillService(
        db,
        context.workspace_id,
        context.principal.user_id,
        settings,
        workspace=context.workspace,
        principal=context.principal,
    )
    sandbox = (
        SandboxAgentWorkspaceService(
            db,
            context.workspace_id,
            context.principal.user_id,
            settings,
            workspace=context.workspace,
            principal=context.principal,
        )
        if sandbox_authorized and settings.sandbox_agent_enabled
        else None
    )
    agent_tool_runtime = AgentToolRuntime(
        workspace_id=context.workspace_id,
        actor_id=context.principal.user_id,
        search_provider=search_provider,
        extensions=extension_service,
        sandbox=sandbox,
        sandbox_authorized=sandbox_authorized,
        memory_tools=memory_service,
        session_retrieval=SessionRetrievalService(
            db,
            context.workspace,
            context.principal.user_id,
            authorization,
        ),
        image_provider=image_provider_for_workspace(
            db, context.workspace_id, settings
        ),
        image_provider_resolver=lambda image_provider_id, image_model_id: (
            image_provider_for_workspace(
                db,
                context.workspace_id,
                settings,
                provider_id=image_provider_id,
                model_id=image_model_id,
            )
        ),
        settings=settings,
        can_manage_providers="workspace.manage" in context.permissions,
        fetch_provider=fetch_provider_for_workspace(db, context.workspace_id, settings),
    )
    return ChatService(
        db,
        context.workspace_id,
        context.principal.user_id,
        model_provider_for_workspace(db, context.workspace_id, settings, **model_kwargs),
        tenant_id=context.principal.tenant_id,
        context_builder=(
            ContextBuilder(db, MemoryRouter(MemoryHybridRetriever(db)))
            if settings.memory_context_builder_v2
            else None
        ),
        search_provider=search_provider,
        memory_context_loader=(
            None
            if settings.memory_context_builder_v2
            else memory_service.context_for_session
        ),
        memory_cache_context_loader=memory_service.context_for_session,
        suggested_prompt_context_access_checker=lambda session, session_permission: (
            authorization.can_access_resource(
                context.workspace,
                "session",
                session.id,
                session_permission,
            )
            and authorization.can_access_bindings(
                context.workspace,
                "read",
                project_id=session.project_id,
                goal_id=session.goal_id,
                graph_id=session.graph_id,
            )
        ),
        learning_context_access_checker=lambda resource_type, resource_id: (
            authorization.can_access_bindings(
                context.workspace,
                "read",
                node_id=resource_id,
            )
            if resource_type == "node"
            else authorization.can_access_resource(
                context.workspace,
                resource_type,
                resource_id,
                "read",
            )
        ),
        session_binding_access_checker=lambda project_id, goal_id, graph_id: (
            authorization.can_access_bindings(
                context.workspace,
                "read",
                project_id=project_id,
                goal_id=goal_id,
                graph_id=graph_id,
            )
        ),
        agent_tool_runtime=agent_tool_runtime,
        vision_provider=vision_provider_for_workspace(
            db, context.workspace_id, settings
        ),
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
    return [
        SessionView.model_validate(item)
        for item in service(db, context, settings).list_sessions()
        if authz.can_access_resource(context.workspace, "session", item.id, "read")
    ]


@router.post("", response_model=SessionView, status_code=status.HTTP_201_CREATED)
def create_session(payload: SessionCreateRequest, db: DB, context: CurrentWorkspace, settings: AppSettings) -> SessionView:
    return SessionView.model_validate(service(db, context, settings).create_session(payload))


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
    upstream_url = dashscope_realtime_ws_url(adapter.base_url)
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

    from websockets.asyncio.client import connect as ws_connect

    started_at = asyncio.get_running_loop().time()
    try:
        upstream = await ws_connect(
            upstream_url,
            additional_headers={"Authorization": f"bearer {adapter.api_key}"},
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

    task_id, run_task = build_realtime_run_task(adapter.model_id, sample_rate)
    finish_sent = False
    try:
        await upstream.send(run_task)
        while True:
            event = parse_realtime_upstream_event(
                await asyncio.wait_for(upstream.recv(), timeout=20)
            )
            if event.event == "task-started":
                break
            if event.event == "task-failed":
                await _ws_error(
                    websocket, "asr_task_failed", event.error or "DashScope rejected the task"
                )
                return
    except Exception:
        await _ws_error(websocket, "asr_task_failed", "DashScope did not start the ASR task")
        return
    finally:
        if websocket.client_state.name != "CONNECTED":
            await upstream.close()

    await websocket.send_json({"type": "ready", "sample_rate": sample_rate})

    async def pump_client() -> str:
        nonlocal finish_sent
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return "disconnect"
                data = message.get("bytes")
                if data:
                    await upstream.send(data)
                    continue
                text = message.get("text")
                if not text:
                    continue
                try:
                    frame = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(frame, dict) and frame.get("type") == "stop":
                    await upstream.send(build_realtime_finish_task(task_id))
                    finish_sent = True
                    return "stop"
        except Exception:
            return "disconnect"

    async def pump_upstream() -> dict[str, int] | None:
        """Relay text events until the task ends; returns usage when finished."""
        try:
            async for raw_event in upstream:
                event = parse_realtime_upstream_event(raw_event)
                if event.event == "result-generated" and event.text is not None:
                    await websocket.send_json(
                        {"type": "final" if event.final else "partial", "text": event.text}
                    )
                elif event.event == "task-finished":
                    return event.usage
                elif event.event == "task-failed":
                    await _ws_error(
                        websocket, "asr_task_failed", event.error or "ASR task failed"
                    )
                    return None
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
                    await upstream.send(build_realtime_finish_task(task_id))
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
) -> SuggestedPromptBatchView:
    require_session_access(session_id, "write", db, context)
    return service(
        db,
        context,
        settings,
        model_id=payload.model_id,
        provider_id=payload.provider_id,
        thinking_mode="off",
    ).generate_suggested_prompts(session_id, payload)


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
        )
    stream_service.preflight_create_stream(
        session_id,
        payload,
        idempotency_key=idempotency_key,
        last_event_id=after_event_id or last_event_id,
    )

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
