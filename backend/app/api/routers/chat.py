from __future__ import annotations

import queue
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Header, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import sessionmaker

from app.api.deps import AppSettings, CurrentWorkspace, DB, WorkspaceContext
from app.core.errors import AppError
from app.domain.models import Workspace
from app.domain.schemas.chat import (
    BranchRequest,
    ConceptBranchCreateRequest,
    ConceptBranchPromoteRequest,
    MessageCreateRequest,
    MessageRetryRequest,
    MessageSnapshotView,
    MessageView,
    MessageVersionView,
    SSEEventEnvelope,
    SessionAutoTitleRequest,
    SessionCreateRequest,
    SessionView,
    SuggestedPromptBatchView,
    SuggestedPromptGenerateRequest,
)
from app.domain.schemas.common import ActionResponse
from app.domain.schemas.graphs import GraphChangeSetView, RejectGraphChangeSetRequest
from app.providers.factory import (
    image_provider_for_workspace,
    memory_provider_for_workspace,
    model_provider_for_workspace,
    search_provider_for_workspace,
    vision_provider_for_workspace,
)
from app.services.chat import ChatService
from app.services.graph_changes import GraphChangeSetService
from app.services.image_chat import ImageChatService
from app.services.memory import MemoryService
from app.services.authorization import AuthorizationService
from app.services.agent_runtime import AgentToolRuntime
from app.services.mcp_skills import MCPAndSkillService
from app.services.sandbox import SandboxAgentWorkspaceService, agent_sandbox_readiness
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
    )
    return ChatService(
        db,
        context.workspace_id,
        context.principal.user_id,
        model_provider_for_workspace(db, context.workspace_id, settings, **model_kwargs),
        search_provider=search_provider,
        memory_context_loader=memory_service.context_for_session,
        memory_cache_context_loader=lambda session_id: memory_service.context_for_session(
            session_id,
            require_provider_health=False,
        ),
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


@router.get("/{session_id}/messages", response_model=list[MessageView])
def list_messages(session_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[MessageView]:
    return [MessageView.model_validate(item) for item in service(db, context, settings).list_messages(session_id)]


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
    if payload.agent_mode:
        sandbox_readiness = agent_sandbox_readiness(
            settings,
            authorized="workspace.manage" in context.permissions,
        )
        if not sandbox_readiness["available"]:
            raise AppError(
                403
                if sandbox_readiness["code"] == "sandbox_permission_required"
                else 503,
                str(sandbox_readiness["code"] or "sandbox_backend_unavailable"),
                str(sandbox_readiness["message"]),
                {
                    "backend_id": sandbox_readiness["backend_id"],
                    "remediation_steps": sandbox_readiness["remediation_steps"],
                },
            )
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
    chat_service = service(
        db,
        context,
        settings,
        model_id=retry_payload.model_id,
        provider_id=retry_payload.provider_id,
        thinking_mode=retry_payload.thinking_mode,
        search_route=retry_payload.search_route,
    )
    retry_agent_mode = chat_service.preflight_retry_message(
        session_id,
        message_id,
        retry_payload,
    )
    if retry_agent_mode:
        sandbox_readiness = agent_sandbox_readiness(
            settings,
            authorized="workspace.manage" in context.permissions,
        )
        if not sandbox_readiness["available"]:
            raise AppError(
                409,
                str(sandbox_readiness["code"]),
                str(sandbox_readiness["message"]),
                sandbox_readiness,
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
