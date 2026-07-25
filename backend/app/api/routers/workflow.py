from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.api.deps import CurrentWorkspace, DB
from app.core.errors import AppError
from app.domain.models import ChatSession, GraphNode, Roadmap
from app.domain.schemas.chat import SessionUpdateRequest, SessionView
from app.domain.schemas.common import ActionResponse
from app.domain.schemas.workflow import (
    ActionCreate,
    ActionUpdate,
    ActionView,
    CompositeCreate,
    CompositeView,
    DeleteConfirm,
    DeleteImpact,
    ProjectCreate,
    ProjectUpdate,
    ProjectView,
    RoadmapItemReschedule,
    RoadmapReject,
    RoadmapVersionView,
    RoadmapView,
    SessionBatchDeleteConfirm,
    SessionBatchDeleteImpact,
    SessionBatchDeleteResponse,
    SessionBatchSelection,
    SessionProjectUpdate,
    SourceLinkCreate,
    SourceLinkView,
)
from app.services.workflow import WorkflowService
from app.services.authorization import AuthorizationService

router = APIRouter(tags=["projects-actions"])

def service(db: DB, context: CurrentWorkspace) -> WorkflowService:
    return WorkflowService(db, context.workspace, context.principal)

def assert_batch_session_access(session_ids: list[str], db: DB, context: CurrentWorkspace) -> None:
    authz = AuthorizationService(db, context.principal)
    for session_id in session_ids:
        session = db.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.workspace_id == context.workspace_id,
            )
        )
        if session is not None and not authz.can_access_resource(
            context.workspace, "session", session_id, "delete"
        ):
            raise AppError(404, "session_not_found", "One or more sessions were not found")


def assert_source_link_target_access(
    payload: SourceLinkCreate,
    db: DB,
    context: CurrentWorkspace,
) -> None:
    """Require write access to the target before adding a SourceLink.

    A node inherits its access boundary from its owning graph because nodes are
    not independently ACL-managed resources.  Keep the outward error opaque so
    a caller cannot probe restricted target IDs in the current workspace.
    """

    target_type = payload.target_type
    target_id = payload.target_id
    if target_type == "node":
        node = db.scalar(
            select(GraphNode).where(
                GraphNode.id == target_id,
                GraphNode.workspace_id == context.workspace_id,
            )
        )
        if node is None:
            raise AppError(404, "source_target_not_found", "Source link target was not found")
        target_type = "graph"
        target_id = node.graph_id
    if not AuthorizationService(db, context.principal).can_access_resource(
        context.workspace,
        target_type,
        target_id,
        "write",
    ):
        raise AppError(404, "source_target_not_found", "Source link target was not found")


def assert_goal_access(
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
        raise AppError(404, "goal_not_found", "Goal was not found")


def assert_roadmap_access(
    roadmap_id: str,
    permission: str,
    db: DB,
    context: CurrentWorkspace,
) -> Roadmap:
    roadmap = db.scalar(
        select(Roadmap).where(
            Roadmap.id == roadmap_id,
            Roadmap.workspace_id == context.workspace_id,
        )
    )
    if roadmap is None or not AuthorizationService(
        db, context.principal
    ).can_access_roadmap_record(context.workspace, roadmap, permission):
        raise AppError(404, "roadmap_not_found", "Roadmap was not found")
    return roadmap

@router.get("/projects", response_model=list[ProjectView])
def projects(db: DB, context: CurrentWorkspace, include_archived: Annotated[bool, Query()] = False):
    authz = AuthorizationService(db, context.principal)
    return [
        item
        for item in service(db, context).projects(include_archived)
        if authz.can_access_resource(context.workspace, "project", item.id, "read")
    ]

@router.post("/projects", response_model=ProjectView, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: DB, context: CurrentWorkspace): return service(db, context).create_project(payload)

@router.patch("/projects/{project_id}", response_model=ProjectView)
def update_project(project_id: str, payload: ProjectUpdate, db: DB, context: CurrentWorkspace): return service(db, context).update_project(project_id, payload)

@router.post("/projects/{project_id}/archive", response_model=ProjectView)
def archive_project(project_id: str, db: DB, context: CurrentWorkspace): return service(db, context).archive_project(project_id, True)

@router.post("/projects/{project_id}/restore", response_model=ProjectView)
def restore_project(project_id: str, db: DB, context: CurrentWorkspace): return service(db, context).archive_project(project_id, False)

@router.get("/projects/{project_id}/delete-impact", response_model=DeleteImpact)
def project_delete_impact(project_id: str, db: DB, context: CurrentWorkspace): return service(db, context).project_impact(project_id)

@router.post("/projects/{project_id}/delete", response_model=ActionResponse)
def delete_project(project_id: str, payload: DeleteConfirm, db: DB, context: CurrentWorkspace):
    service(db, context).delete_project(project_id, payload.confirmation_text)
    return ActionResponse(status="deleted", message="Project and owned impacts were deleted", resource_id=project_id)

@router.put("/sessions/{session_id}/project", response_model=SessionView)
def assign_session(session_id: str, payload: SessionProjectUpdate, db: DB, context: CurrentWorkspace): return service(db, context).assign_session(session_id, payload.project_id)

@router.patch("/sessions/{session_id}", response_model=SessionView)
def update_session(session_id: str, payload: SessionUpdateRequest, db: DB, context: CurrentWorkspace):
    return service(db, context).update_session(session_id, payload)

@router.post("/sessions/{session_id}/archive", response_model=SessionView)
def archive_session(session_id: str, db: DB, context: CurrentWorkspace): return service(db, context).archive_session(session_id, True)

@router.post("/sessions/{session_id}/restore", response_model=SessionView)
def restore_session(session_id: str, db: DB, context: CurrentWorkspace): return service(db, context).archive_session(session_id, False)

@router.post("/sessions/batch-delete-impact", response_model=SessionBatchDeleteImpact)
def session_batch_delete_impact(payload: SessionBatchSelection, db: DB, context: CurrentWorkspace):
    assert_batch_session_access(payload.session_ids, db, context)
    return service(db, context).session_batch_impact(payload.session_ids)

@router.post("/sessions/batch-delete", response_model=SessionBatchDeleteResponse)
def delete_sessions(payload: SessionBatchDeleteConfirm, db: DB, context: CurrentWorkspace):
    assert_batch_session_access(payload.session_ids, db, context)
    return service(db, context).delete_sessions(payload.session_ids, payload.confirmation_text)

@router.get("/sessions/{session_id}/delete-impact", response_model=DeleteImpact)
def session_delete_impact(session_id: str, db: DB, context: CurrentWorkspace): return service(db, context).session_impact(session_id)

@router.post("/sessions/{session_id}/delete", response_model=ActionResponse)
def delete_session(session_id: str, payload: DeleteConfirm, db: DB, context: CurrentWorkspace):
    service(db, context).delete_session(session_id, payload.confirmation_text)
    return ActionResponse(status="deleted", message="Session and owned impacts were deleted", resource_id=session_id)

@router.get("/sources/{source_id}/links", response_model=list[SourceLinkView])
def source_links(source_id: str, db: DB, context: CurrentWorkspace): return service(db, context).source_links(source_id)

@router.post("/sources/{source_id}/links", response_model=SourceLinkView, status_code=status.HTTP_201_CREATED)
def create_source_link(source_id: str, payload: SourceLinkCreate, db: DB, context: CurrentWorkspace):
    assert_source_link_target_access(payload, db, context)
    return service(db, context).create_source_link(source_id, payload)

@router.get("/actions", response_model=list[ActionView])
def actions(db: DB, context: CurrentWorkspace, action_status: Annotated[str | None, Query(alias="status")] = None): return service(db, context).actions(action_status)

@router.post("/actions", response_model=ActionView, status_code=status.HTTP_201_CREATED)
def create_action(payload: ActionCreate, db: DB, context: CurrentWorkspace): return service(db, context).create_action(payload)

@router.patch("/actions/{action_id}", response_model=ActionView)
def update_action(action_id: str, payload: ActionUpdate, db: DB, context: CurrentWorkspace): return service(db, context).update_action(action_id, payload)

@router.post("/message-composites", response_model=CompositeView, status_code=status.HTTP_201_CREATED)
def create_composite(payload: CompositeCreate, db: DB, context: CurrentWorkspace): return service(db, context).create_composite(payload)

@router.post("/message-composites/{draft_id}/confirm", response_model=CompositeView)
def confirm_composite(draft_id: str, db: DB, context: CurrentWorkspace): return service(db, context).confirm_composite(draft_id)

@router.get("/goals/{goal_id}/roadmap", response_model=RoadmapView)
def get_roadmap(goal_id: str, db: DB, context: CurrentWorkspace):
    assert_goal_access(goal_id, "read", db, context)
    return service(db, context).roadmap(goal_id)


@router.get("/goals/{goal_id}/roadmaps", response_model=list[RoadmapVersionView])
def list_roadmaps(goal_id: str, db: DB, context: CurrentWorkspace):
    assert_goal_access(goal_id, "read", db, context)
    return service(db, context).roadmaps(goal_id)


@router.get("/roadmaps/{roadmap_id}", response_model=RoadmapView)
def get_roadmap_version(roadmap_id: str, db: DB, context: CurrentWorkspace):
    assert_roadmap_access(roadmap_id, "read", db, context)
    return service(db, context).roadmap_by_id(roadmap_id)

@router.post("/goals/{goal_id}/roadmap/replan", response_model=RoadmapView, status_code=status.HTTP_201_CREATED)
def replan_roadmap(goal_id: str, db: DB, context: CurrentWorkspace):
    assert_goal_access(goal_id, "write", db, context)
    item = service(db, context).replan_roadmap(goal_id)
    return service(db, context).roadmap_by_id(item.id)

@router.post("/roadmaps/{roadmap_id}/publish", response_model=RoadmapView)
def publish_roadmap(roadmap_id: str, db: DB, context: CurrentWorkspace):
    assert_roadmap_access(roadmap_id, "write", db, context)
    item = service(db, context).publish_roadmap(roadmap_id)
    return service(db, context).roadmap_by_id(item.id)


@router.post(
    "/roadmaps/{roadmap_id}/items/{action_id}/reschedule",
    response_model=RoadmapView,
    status_code=status.HTTP_201_CREATED,
)
def reschedule_roadmap_item(
    roadmap_id: str,
    action_id: str,
    payload: RoadmapItemReschedule,
    db: DB,
    context: CurrentWorkspace,
):
    assert_roadmap_access(roadmap_id, "write", db, context)
    item = service(db, context).reschedule_roadmap_item(
        roadmap_id,
        action_id,
        payload,
    )
    return service(db, context).roadmap_by_id(item.id)


@router.post("/roadmaps/{roadmap_id}/reject", response_model=RoadmapView)
def reject_roadmap(
    roadmap_id: str,
    payload: RoadmapReject,
    db: DB,
    context: CurrentWorkspace,
):
    assert_roadmap_access(roadmap_id, "write", db, context)
    item = service(db, context).reject_roadmap(roadmap_id, payload)
    return service(db, context).roadmap_by_id(item.id)
