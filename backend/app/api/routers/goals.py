from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, status
from fastapi.responses import StreamingResponse

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.schemas.common import ActionResponse
from app.providers.factory import model_provider_for_workspace
from app.domain.schemas.goals import (
    CandidateGraphRequest,
    CandidateGraphStreamRequest,
    GoalClarifyRequest,
    GoalClarifyResponse,
    GoalConfirmRequest,
    GoalPlanningUpdate,
    GoalView,
    PublishGoalRequest,
    PublishGoalResponse,
)
from app.domain.schemas.graphs import GraphSummary
from app.domain.schemas.workflow import DeleteConfirm, DeleteImpact
from app.services.goals import GoalService
from app.services.authorization import AuthorizationService


router = APIRouter(prefix="/goals", tags=["goals"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> GoalService:
    return GoalService(
        db,
        context.workspace_id,
        context.principal.user_id,
        model_provider_for_workspace(db, context.workspace_id, settings),
        provider_factory=lambda: model_provider_for_workspace(db, context.workspace_id, settings),
    )


def service_with_model(
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    *,
    provider_id: str | None,
    model_id: str | None,
    thinking_mode: str | None,
) -> GoalService:
    """Build a GoalService that uses the chat-selected model when provided.

    The Goal wizard and the unified Agent chat should agree on the same
    provider/model; without this the wizard silently falls back to the
    workspace's implicit provider-priority default, which is how a model that
    works in normal chat could still fail the goal/graph endpoints.

    The ``provider_factory`` closure lets the parallel branch expansion build
    one dedicated provider instance per worker thread (usage metadata such as
    ``last_usage`` is per-instance, so workers must never share one).
    """

    return GoalService(
        db,
        context.workspace_id,
        context.principal.user_id,
        model_provider_for_workspace(
            db,
            context.workspace_id,
            settings,
            provider_id=provider_id,
            model_id=model_id,
            thinking_mode=thinking_mode,
        ),
        provider_factory=lambda: model_provider_for_workspace(
            db,
            context.workspace_id,
            settings,
            provider_id=provider_id,
            model_id=model_id,
            thinking_mode=thinking_mode,
        ),
    )


def require_goal_access(
    goal_id: str,
    permission: str,
    db: DB,
    context: CurrentWorkspace,
) -> None:
    if not AuthorizationService(db, context.principal).can_access_resource(
        context.workspace,
        "goal",
        goal_id,
        permission,
    ):
        raise AppError(404, "not_found", "Goal not found in this workspace")


def require_publish_access(
    goal_id: str,
    graph_id: str,
    db: DB,
    context: CurrentWorkspace,
) -> None:
    if not AuthorizationService(db, context.principal).can_access_bindings(
        context.workspace,
        "write",
        goal_id=goal_id,
        graph_id=graph_id,
    ):
        raise AppError(404, "not_found", "Goal or Graph not found in this workspace")


@router.get("", response_model=list[GoalView])
def list_goals(db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[GoalView]:
    authz = AuthorizationService(db, context.principal)
    items = service(db, context, settings).list()
    # B1-4: batch authorization instead of per-item can_access_resource.
    accessible = authz.filter_accessible_ids(
        context.workspace, "goal", [item.id for item in items], "read"
    )
    return [
        GoalView.model_validate(item)
        for item in items
        if item.id in accessible
    ]


@router.post("/clarify", response_model=GoalClarifyResponse, status_code=status.HTTP_201_CREATED)
def clarify_goal(payload: GoalClarifyRequest, db: DB, context: CurrentWorkspace, settings: AppSettings) -> GoalClarifyResponse:
    return service_with_model(
        db,
        context,
        settings,
        provider_id=payload.provider_id,
        model_id=payload.model_id,
        thinking_mode=payload.thinking_mode,
    ).clarify(payload)


@router.get("/{goal_id}/delete-impact", response_model=DeleteImpact)
def goal_delete_impact(
    goal_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> DeleteImpact:
    require_goal_access(goal_id, "read", db, context)
    return service(db, context, settings).goal_impact(goal_id)


@router.post("/{goal_id}/delete", response_model=ActionResponse)
def delete_goal(
    goal_id: str,
    payload: DeleteConfirm,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> ActionResponse:
    require_goal_access(goal_id, "delete", db, context)
    service(db, context, settings).delete_goal(goal_id, payload.confirmation_text)
    return ActionResponse(
        status="deleted",
        message="Goal and owned graph resources were deleted",
        resource_id=goal_id,
    )


@router.put("/{goal_id}/confirm", response_model=GoalView)
def confirm_goal(goal_id: str, payload: GoalConfirmRequest, db: DB, context: CurrentWorkspace, settings: AppSettings) -> GoalView:
    require_goal_access(goal_id, "write", db, context)
    return GoalView.model_validate(service(db, context, settings).confirm(goal_id, payload))


@router.patch("/{goal_id}/planning", response_model=GoalView)
def update_goal_planning(
    goal_id: str,
    payload: GoalPlanningUpdate,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> GoalView:
    """Persist explicit scheduling inputs without rewriting Goal intent or text."""

    require_goal_access(goal_id, "write", db, context)
    return GoalView.model_validate(service(db, context, settings).update_planning(goal_id, payload))


@router.post("/{goal_id}/candidate-graph", response_model=GraphSummary, status_code=status.HTTP_201_CREATED)
def candidate_graph(
    goal_id: str,
    payload: CandidateGraphRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> GraphSummary:
    require_goal_access(goal_id, "write", db, context)
    return GraphSummary.model_validate(
        service_with_model(
            db,
            context,
            settings,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            thinking_mode=payload.thinking_mode,
        ).generate_candidate_graph(goal_id, payload)
    )


def _sse_encode(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{goal_id}/candidate-graph/stream")
def candidate_graph_stream(
    goal_id: str,
    payload: CandidateGraphStreamRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> StreamingResponse:
    """Stream a candidate graph trunk-first: root preview, then trunk, then
    per-trunk two-layer expansion generated concurrently across branches.

    Event order: ``graph.root`` (single root), ``graph.nodes_added`` (trunk),
    then per trunk node two ``graph.nodes_added`` events (layer-1 children,
    layer-2 grandchildren) in branch completion order, then
    ``graph.complete`` (full snapshot). Every stage is persisted before it is
    emitted, so the review flow can start from the already-visible root.
    Branch expansions run in parallel worker threads; persistence and SSE
    emission stay on a single thread so SQLite writes remain short and
    serialized. ``mode=fast`` keeps thinking off with a compact trunk and
    narrow branches; ``mode=thinking`` keeps the provider thinking budget
    with a fuller trunk and wider branches.
    """
    require_goal_access(goal_id, "write", db, context)
    thinking_mode = payload.thinking_mode
    if thinking_mode is None and payload.mode == "fast":
        thinking_mode = "off"

    def generate() -> Any:
        from queue import Empty, Queue
        from threading import Thread

        events: Queue[str | None] = Queue(maxsize=64)

        def emit(event: str, data: dict[str, Any]) -> None:
            events.put(_sse_encode(event, data))

        def produce() -> None:
            try:
                svc = service_with_model(
                    db,
                    context,
                    settings,
                    provider_id=payload.provider_id,
                    model_id=payload.model_id,
                    thinking_mode=thinking_mode,
                )
                svc.stream_candidate_graph(goal_id, payload, emit)
            except AppError as exc:
                events.put(
                    _sse_encode(
                        "graph.error",
                        {"code": exc.code, "message": str(exc.message)},
                    )
                )
            except BaseException as exc:  # noqa: BLE001 -- stream must terminate
                events.put(
                    _sse_encode(
                        "graph.error",
                        {"code": "graph_stream_failed", "message": str(exc)[:500]},
                    )
                )
            finally:
                events.put(None)

        Thread(
            target=produce,
            name=f"learngraph-candidate-graph-{goal_id[:8]}",
            daemon=True,
        ).start()
        yield ": graph-stream-ready\n\n"
        while True:
            item = events.get()
            if item is None:
                break
            yield item

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/{goal_id}/publish", response_model=PublishGoalResponse)
def publish_goal(
    goal_id: str,
    payload: PublishGoalRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> PublishGoalResponse:
    require_publish_access(goal_id, payload.graph_id, db, context)
    return service(db, context, settings).publish(goal_id, payload)
