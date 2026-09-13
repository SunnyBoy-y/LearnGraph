from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.deps import AppSettings, CurrentWorkspace, DB
from app.providers.factory import model_provider_for_workspace
from app.domain.schemas.common import ActionResponse
from app.domain.models import GraphNode
from app.domain.schemas.graphs import (
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
    if graph_items:
        nodes = db.scalars(
            select(GraphNode).where(
                GraphNode.workspace_id == context.workspace_id,
                GraphNode.graph_id.in_([item.id for item in graph_items]),
            ).order_by(GraphNode.graph_id, GraphNode.id)
        ).all()
        for node in nodes:
            node_labels.setdefault(node.graph_id, []).append(node.label)
            if node.mastery_stars >= 3:
                mastered_counts[node.graph_id] = mastered_counts.get(node.graph_id, 0) + 1
    return [
        GraphSummary.model_validate({
            **item.__dict__,
            "cover_svg": item.cover_svg or generate_graph_cover(
                item.title,
                node_labels=node_labels.get(item.id),
                progress=mastered_counts.get(item.id, 0) / max(1, len(node_labels.get(item.id, []))),
            ),
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
