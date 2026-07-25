from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.schemas.common import ActionResponse
from app.providers.factory import model_provider_for_workspace
from app.domain.schemas.goals import (
    CandidateGraphRequest,
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
    return GoalService(db, context.workspace_id, context.principal.user_id, model_provider_for_workspace(db, context.workspace_id, settings))


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
    return [
        GoalView.model_validate(item)
        for item in service(db, context, settings).list()
        if authz.can_access_resource(context.workspace, "goal", item.id, "read")
    ]


@router.post("/clarify", response_model=GoalClarifyResponse, status_code=status.HTTP_201_CREATED)
def clarify_goal(payload: GoalClarifyRequest, db: DB, context: CurrentWorkspace, settings: AppSettings) -> GoalClarifyResponse:
    return service(db, context, settings).clarify(payload)


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
    return GraphSummary.model_validate(service(db, context, settings).generate_candidate_graph(goal_id, payload))


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
