from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.providers.factory import model_provider_for_workspace
from app.domain.schemas.common import ActionResponse
from app.domain.models import GraphNode
from app.domain.graph_cover_models import COVER_ACTIVE_STATUSES, GraphCoverJob
from app.domain.schemas.graphs import (
    GraphCoverAIJobView,
    GraphCoverAIRequest,
    GraphCoverDraftRequest,
    GraphCoverDraftView,
    GraphCoverUpdateRequest,
    GraphCoverView,
    GraphNodeView,
    GraphRevisionView,
    GraphSummary,
    GraphView,
    MultiNodeStudyRequest,
    MultiNodeStudyResponse,
    NodeMergeDecisionRequest,
    NodeMergePreview,
    NodeMergePreviewRequest,
    NodeMergeView,
    NodeQuestionView,
    RetryNodeRequest,
    UpdateNodeRequest,
)
from app.services.graphs import GraphService
from app.services.authorization import AuthorizationService
from app.services.graph_cover import generate_graph_cover
from app.services.graph_cover_ai import GraphCoverAIService
from app.services.graph_cover_management import GraphCoverService


router = APIRouter(prefix="/graphs", tags=["graphs"])


def service(db: DB, context: CurrentWorkspace, settings: AppSettings) -> GraphService:
    authorization = AuthorizationService(db, context.principal)
    return GraphService(
        db,
        context.workspace_id,
        context.principal.user_id,
        model_provider_for_workspace(db, context.workspace_id, settings),
        graph_access_checker=lambda graph_id, permission: authorization.can_access_bindings(
            context.workspace,
            permission,
            graph_id=graph_id,
        ),
    )


@router.get("", response_model=list[GraphSummary])
def list_graphs(db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[GraphSummary]:
    """列出当前工作区的目标图谱。无请求体，输出图谱 ID、名称、状态和节点统计。"""
    authz = AuthorizationService(db, context.principal)
    graph_items = [
        item for item in service(db, context, settings).list()
        if authz.can_access_resource(context.workspace, "graph", item.id, "read")
    ]
    node_labels: dict[str, list[str]] = {}
    mastered_counts: dict[str, int] = {}
    ai_cover_active: set[str] = set()
    if graph_items:
        graph_ids = [item.id for item in graph_items]
        nodes = db.scalars(
            select(GraphNode).where(
                GraphNode.workspace_id == context.workspace_id,
                GraphNode.graph_id.in_(graph_ids),
            ).order_by(GraphNode.graph_id, GraphNode.id)
        ).all()
        for node in nodes:
            node_labels.setdefault(node.graph_id, []).append(node.label)
            if node.mastery_stars >= 3:
                mastered_counts[node.graph_id] = mastered_counts.get(node.graph_id, 0) + 1
        # One batched lookup keeps the shelf able to show an in-flight AI cover
        # after a reload, instead of polling every card.
        ai_cover_active = set(
            db.scalars(
                select(GraphCoverJob.graph_id).where(
                    GraphCoverJob.workspace_id == context.workspace_id,
                    GraphCoverJob.graph_id.in_(graph_ids),
                    GraphCoverJob.status.in_(COVER_ACTIVE_STATUSES),
                )
            ).all()
        )
    return [
        GraphSummary.model_validate({
            **item.__dict__,
            "cover_svg": item.cover_svg or generate_graph_cover(
                item.title,
                node_labels=node_labels.get(item.id),
                progress=mastered_counts.get(item.id, 0) / max(1, len(node_labels.get(item.id, []))),
            ),
            "cover_ai_active": item.id in ai_cover_active,
        })
        for item in graph_items
    ]


@router.patch("/{graph_id}/cover", response_model=GraphCoverView)
def update_graph_cover(
    graph_id: str,
    payload: GraphCoverUpdateRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> GraphCoverView:
    """Select a generated/template/image cover without changing graph revision."""
    authz = AuthorizationService(db, context.principal)
    service = GraphCoverService(
        db, context.workspace_id, context.principal.user_id,
        can_access=lambda target_id, permission: authz.can_access_resource(context.workspace, "graph", target_id, permission),
    )
    return service.update(graph_id, payload)


@router.get("/{graph_id}/cover", response_model=GraphCoverView)
def graph_cover(graph_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> GraphCoverView:
    authz = AuthorizationService(db, context.principal)
    return GraphCoverService(
        db, context.workspace_id, context.principal.user_id,
        can_access=lambda target_id, permission: authz.can_access_resource(context.workspace, "graph", target_id, permission),
    ).read(graph_id)


def _cover_ai_service(db, context) -> GraphCoverAIService:
    authz = AuthorizationService(db, context.principal)
    return GraphCoverAIService(
        db, context.workspace_id, context.principal.user_id,
        can_access=lambda target_id, permission: authz.can_access_resource(context.workspace, "graph", target_id, permission),
    )


@router.post("/{graph_id}/cover/ai/draft", response_model=GraphCoverDraftView)
def draft_ai_graph_cover(
    graph_id: str,
    payload: GraphCoverDraftRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> GraphCoverDraftView:
    """Phase 1: let the model propose a cover brief the user can edit.

    Synchronous on purpose — the draft is one short text response, and the user
    is waiting to *read* it before deciding whether to spend image-model money.
    """
    return GraphCoverDraftView.model_validate(
        _cover_ai_service(db, context).draft(
            graph_id,
            engine=payload.engine,
            hint=payload.hint,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
        )
    )


@router.post("/{graph_id}/cover/ai", response_model=GraphCoverAIJobView, status_code=202)
def start_ai_graph_cover(
    graph_id: str,
    payload: GraphCoverAIRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> GraphCoverAIJobView:
    """Phase 2: submit the confirmed brief; the worker draws it in the background."""
    return GraphCoverAIJobView.model_validate(
        _cover_ai_service(db, context).submit(
            graph_id,
            engine=payload.engine,
            prompt=payload.prompt,
            prompt_source=payload.prompt_source,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
        )
    )


@router.get("/{graph_id}/cover/ai", response_model=GraphCoverAIJobView)
def ai_graph_cover_status(graph_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> GraphCoverAIJobView:
    return GraphCoverAIJobView.model_validate(_cover_ai_service(db, context).status(graph_id))


@router.post("/{graph_id}/cover/ai/cancel", response_model=GraphCoverAIJobView)
def cancel_ai_graph_cover(graph_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> GraphCoverAIJobView:
    return GraphCoverAIJobView.model_validate(_cover_ai_service(db, context).cancel(graph_id))


@router.get("/merges", response_model=list[NodeMergeView])
def list_node_merges(db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[NodeMergeView]:
    return [NodeMergeView.model_validate(item) for item in service(db, context, settings).list_node_merges()]


@router.post("/merges/preview", response_model=NodeMergePreview)
def preview_node_merge(
    payload: NodeMergePreviewRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> NodeMergePreview:
    return service(db, context, settings).preview_node_merge(payload)


@router.post("/merges", response_model=NodeMergeView, status_code=201)
def decide_node_merge(
    payload: NodeMergeDecisionRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> NodeMergeView:
    return NodeMergeView.model_validate(service(db, context, settings).decide_node_merge(payload))


@router.post("/merges/{merge_id}/undo", response_model=NodeMergeView)
def undo_node_merge(
    merge_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> NodeMergeView:
    return NodeMergeView.model_validate(service(db, context, settings).undo_node_merge(merge_id))


@router.get("/{graph_id}", response_model=GraphView)
def graph_detail(graph_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> GraphView:
    """读取图谱详情。输入图谱 ID，输出图谱状态、节点、边和审核相关信息。"""
    return service(db, context, settings).detail(graph_id)


@router.get("/{graph_id}/revisions", response_model=list[GraphRevisionView])
def graph_revisions(graph_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[GraphRevisionView]:
    return [GraphRevisionView.model_validate(item) for item in service(db, context, settings).revisions(graph_id)]


@router.get("/{graph_id}/nodes/{node_id}/questions", response_model=list[NodeQuestionView])
def node_questions(graph_id: str, node_id: str, db: DB, context: CurrentWorkspace, settings: AppSettings) -> list[NodeQuestionView]:
    return [NodeQuestionView.model_validate(item) for item in service(db, context, settings).node_questions(graph_id, node_id)]


@router.patch("/{graph_id}/nodes/{node_id}", response_model=GraphNodeView)
def update_node(
    graph_id: str,
    node_id: str,
    payload: UpdateNodeRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> GraphNodeView:
    """编辑候选图谱节点。输入图谱 ID、节点 ID 和允许修改的字段，输出更新后的节点；已发布图谱不会被静默修改。"""
    return GraphNodeView.model_validate(service(db, context, settings).update_node(graph_id, node_id, payload))


@router.post("/{graph_id}/nodes/{node_id}/retry", response_model=GraphNodeView)
def retry_node(
    graph_id: str,
    node_id: str,
    payload: RetryNodeRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> GraphNodeView:
    return GraphNodeView.model_validate(
        service(db, context, settings).retry_node(graph_id, node_id, payload)
    )


@router.delete("/{graph_id}/nodes/{node_id}", response_model=ActionResponse)
def delete_node(
    graph_id: str,
    node_id: str,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
    expected_revision: int = Query(ge=1),
) -> ActionResponse:
    deleted_id = service(db, context, settings).delete_node(
        graph_id,
        node_id,
        expected_revision,
    )
    return ActionResponse(
        status="deleted",
        message="Candidate graph node and its connected edges were deleted",
        resource_id=deleted_id,
    )


@router.post("/{graph_id}/multi-node-study", response_model=MultiNodeStudyResponse)
def multi_node_study(
    graph_id: str,
    payload: MultiNodeStudyRequest,
    db: DB,
    context: CurrentWorkspace,
    settings: AppSettings,
) -> MultiNodeStudyResponse:
    """创建多节点学习关联。输入图谱 ID 与节点 ID 列表，输出可供学习会话使用的节点关系说明。"""
    return service(db, context, settings).multi_node_study(graph_id, payload)
