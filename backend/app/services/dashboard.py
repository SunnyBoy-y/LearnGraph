from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.security import Principal
from app.domain.models import ActionItem, Evidence, Goal, Graph, ProviderConfig, ResearchJob, Roadmap, Workspace
from app.domain.schemas.auth import DashboardAction, DashboardMetric, DashboardResponse
from app.providers.catalog import SEARCH_PROVIDER_TYPES
from app.services.authorization import AuthorizationService


class DashboardService:
    def __init__(self, db: Session, workspace: Workspace, principal: Principal) -> None:
        self.db = db
        self.workspace = workspace
        self.workspace_id = workspace.id
        self.authz = AuthorizationService(db, principal)

    def _count(self, model: type, *conditions: object) -> int:
        statement = select(func.count()).select_from(model).where(model.workspace_id == self.workspace_id, *conditions)
        return int(self.db.scalar(statement) or 0)

    def _accessible_count(
        self,
        model: type,
        resource_type: str,
        *conditions: object,
    ) -> int:
        items = self.db.scalars(
            select(model).where(model.workspace_id == self.workspace_id, *conditions)
        )
        return sum(
            1
            for item in items
            if self.authz.can_access_resource(
                self.workspace, resource_type, item.id, "read"
            )
        )

    def _provider_status(self, provider_types: tuple[str, ...]) -> str:
        providers = list(
            self.db.scalars(
                select(ProviderConfig).where(
                    ProviderConfig.workspace_id == self.workspace_id,
                    ProviderConfig.enabled.is_(True),
                    ProviderConfig.provider_type.in_(provider_types),
                ).order_by(
                    ProviderConfig.remote_capability.desc(),
                    ProviderConfig.updated_at.desc(),
                )
            )
        )
        if not providers:
            return "unavailable"
        provider = providers[0]
        return f"{provider.display_name} · {provider.status}"

    def _next_actions(self) -> list[DashboardAction]:
        candidates = self.db.scalars(
            select(ActionItem)
            .outerjoin(Roadmap, ActionItem.roadmap_id == Roadmap.id)
            .where(
                ActionItem.workspace_id == self.workspace_id,
                ActionItem.status.in_(["pending", "in_progress"]),
                or_(
                    ActionItem.roadmap_id.is_(None),
                    Roadmap.status == "published",
                ),
            )
            .order_by(ActionItem.priority.desc(), ActionItem.due_at)
        )
        visible = [
            item
            for item in candidates
            if self.authz.can_access_action_record(
                self.workspace, item, "read"
            )
        ][:8]
        return [
            DashboardAction.model_validate(
                {
                    "id": item.id,
                    "title": item.title,
                    "description": item.description,
                    "status": item.status,
                    "source": item.source,
                    "action_type": item.action_type,
                    "project_id": item.project_id,
                    "goal_id": item.goal_id,
                    "graph_id": item.graph_id,
                    "node_id": item.node_id,
                    "due_at": item.due_at,
                    "priority": item.priority,
                }
            )
            for item in visible
        ]

    def get(self) -> DashboardResponse:
        active_goals = self._accessible_count(
            Goal,
            "goal",
            Goal.status.in_(["clarifying", "confirmed", "approved"]),
        )
        graphs = self._accessible_count(Graph, "graph")
        pending_evidence = self._count(Evidence, Evidence.status == "pending")
        running_research = self._count(ResearchJob, ResearchJob.status.in_(["queued", "running"]))
        return DashboardResponse(
            workspace_id=self.workspace_id,
            metrics=[
                DashboardMetric(key="active_goals", label="活跃目标", value=active_goals),
                DashboardMetric(key="graphs", label="学习图谱", value=graphs),
                DashboardMetric(key="pending_evidence", label="证据待审", value=pending_evidence, status="attention" if pending_evidence else "normal"),
                DashboardMetric(key="research_running", label="研究任务", value=running_research),
            ],
            next_actions=self._next_actions(),
            system_status={
                "database": "healthy",
                "local_storage": "healthy",
                "model_provider": self._provider_status(
                    ("openai_responses", "openai_compatible_chat", "local_mock")
                ),
                "search_provider": self._provider_status(
                    tuple(sorted(SEARCH_PROVIDER_TYPES))
                ),
            },
        )
