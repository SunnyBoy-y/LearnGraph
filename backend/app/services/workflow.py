from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.security import Principal
from app.domain.models import (
    ActionItem, AuditEvent, ChatSession, CompositeDraft, ContextSummary, FetchAuthorizationRequest, FileReference, Goal, Graph, GraphChangeSet, GraphEdge, GraphNode,
    ImageGenerationTask, Message, MessageControl, MessagePartRecord, MessageStreamEvent, MessageSubmission,
    MessageVersion, Project, ProviderAttempt, ProviderResponseState, Roadmap, SourceLink, SourceRecord, Workspace,
    SuggestedPromptBatch,
)
from app.domain.schemas.chat import SessionUpdateRequest
from app.domain.schemas.workflow import (
    ActionCreate,
    ActionUpdate,
    CompositeCreate,
    DeleteImpact,
    ImpactItem,
    ProjectCreate,
    ProjectUpdate,
    RoadmapItemReschedule,
    RoadmapReject,
    SessionBatchDeleteImpact,
    SessionBatchDeleteResponse,
    SourceLinkCreate,
)
from app.repositories.audit import AuditRepository
from app.services.authorization import AuthorizationService


def now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowService:
    """Workspace-scoped project, action, and versioned planning use cases."""

    PLANNER_RULE_VERSION = "action_planner_v1"
    MAX_MASTERY_STARS = 15
    PREREQUISITE_MIN_STARS = 1

    def __init__(self, db: Session, workspace: Workspace, principal: Principal) -> None:
        self.db = db
        self.workspace = workspace
        self.workspace_id = workspace.id
        self.actor_id = principal.user_id
        self.authz = AuthorizationService(db, principal)
        self.audit = AuditRepository(db, workspace.id)

    def projects(self, include_archived: bool = False) -> list[Project]:
        stmt = select(Project).where(Project.workspace_id == self.workspace_id)
        if not include_archived:
            stmt = stmt.where(Project.status != "archived")
        return list(self.db.scalars(stmt.order_by(Project.position, Project.created_at)))

    def _project(self, project_id: str) -> Project:
        item = self.db.scalar(select(Project).where(Project.workspace_id == self.workspace_id, Project.id == project_id))
        if not item:
            raise AppError(404, "project_not_found", "Project was not found")
        return item

    def _check_binding(self, goal_id: str | None, graph_id: str | None) -> None:
        if goal_id and not self.db.scalar(select(Goal.id).where(Goal.workspace_id == self.workspace_id, Goal.id == goal_id)):
            raise AppError(404, "goal_not_found", "Primary goal was not found")
        if graph_id and not self.db.scalar(select(Graph.id).where(Graph.workspace_id == self.workspace_id, Graph.id == graph_id)):
            raise AppError(404, "graph_not_found", "Primary graph was not found")

    def create_project(self, payload: ProjectCreate) -> Project:
        self._check_binding(payload.primary_goal_id, payload.primary_graph_id)
        item = Project(workspace_id=self.workspace_id, **payload.model_dump())
        self.db.add(item); self.db.flush()
        self.audit.record(actor_id=self.actor_id, action="project.create", resource_type="project", resource_id=item.id)
        self.db.commit(); self.db.refresh(item)
        return item

    def update_project(self, project_id: str, payload: ProjectUpdate) -> Project:
        item = self._project(project_id); values = payload.model_dump(exclude_unset=True)
        self._check_binding(values.get("primary_goal_id"), values.get("primary_graph_id"))
        for key, value in values.items(): setattr(item, key, value)
        self.audit.record(actor_id=self.actor_id, action="project.update", resource_type="project", resource_id=item.id, details={"fields": list(values)})
        self.db.commit(); self.db.refresh(item)
        return item

    def archive_project(self, project_id: str, archived: bool) -> Project:
        item = self._project(project_id)
        item.status = "archived" if archived else "active"; item.archived_at = now() if archived else None
        self.audit.record(actor_id=self.actor_id, action="project.archive" if archived else "project.restore", resource_type="project", resource_id=item.id)
        self.db.commit(); self.db.refresh(item)
        return item

    def project_impact(self, project_id: str) -> DeleteImpact:
        item = self._project(project_id)
        session_ids = select(ChatSession.id).where(
            ChatSession.workspace_id == self.workspace_id,
            ChatSession.project_id == project_id,
        )
        message_ids = select(Message.id).where(
            Message.workspace_id == self.workspace_id,
            Message.session_id.in_(session_ids),
        )
        source_link_ids = select(SourceLink.id).where(
            SourceLink.workspace_id == self.workspace_id,
            SourceLink.target_type == "project",
            SourceLink.target_id == project_id,
        )
        sessions = self.db.scalar(select(func.count()).select_from(ChatSession).where(ChatSession.workspace_id == self.workspace_id, ChatSession.project_id == project_id)) or 0
        actions = self.db.scalar(select(func.count()).select_from(ActionItem).where(ActionItem.workspace_id == self.workspace_id, ActionItem.project_id == project_id)) or 0
        links = self.db.scalar(select(func.count()).select_from(SourceLink).where(SourceLink.workspace_id == self.workspace_id, SourceLink.target_type == "project", SourceLink.target_id == project_id)) or 0
        proposals = self.db.scalar(select(func.count()).select_from(GraphChangeSet).where(GraphChangeSet.workspace_id == self.workspace_id, GraphChangeSet.session_id.in_(select(ChatSession.id).where(ChatSession.workspace_id == self.workspace_id, ChatSession.project_id == project_id)))) or 0
        file_references = self.db.scalar(
            select(func.count()).select_from(FileReference).where(
                FileReference.workspace_id == self.workspace_id,
                or_(
                    and_(FileReference.target_type == "project", FileReference.target_id == project_id),
                    and_(FileReference.target_type == "session", FileReference.target_id.in_(session_ids)),
                    and_(FileReference.target_type == "message", FileReference.target_id.in_(message_ids)),
                    and_(FileReference.target_type == "source_link", FileReference.target_id.in_(source_link_ids)),
                ),
            )
        ) or 0
        return DeleteImpact(resource_type="project", resource_id=item.id, title=item.title, confirmation_text=item.title, impacts=[ImpactItem(resource_type="session", count=sessions, action="delete"), ImpactItem(resource_type="graph_change_set", count=proposals, action="delete"), ImpactItem(resource_type="action_item", count=actions, action="delete"), ImpactItem(resource_type="source_link", count=links, action="delete"), ImpactItem(resource_type="file_reference", count=file_references, action="delete")])

    def delete_project(self, project_id: str, confirmation: str) -> None:
        impact = self.project_impact(project_id)
        if confirmation != impact.confirmation_text:
            raise AppError(409, "confirmation_mismatch", "Confirmation text does not match the project title")
        session_ids = list(self.db.scalars(select(ChatSession.id).where(ChatSession.workspace_id == self.workspace_id, ChatSession.project_id == project_id)))
        message_ids = list(self.db.scalars(select(Message.id).where(Message.workspace_id == self.workspace_id, Message.session_id.in_(session_ids)))) if session_ids else []
        source_link_ids = list(self.db.scalars(select(SourceLink.id).where(SourceLink.workspace_id == self.workspace_id, SourceLink.target_type == "project", SourceLink.target_id == project_id)))
        version_ids = list(self.db.scalars(select(MessageVersion.id).where(MessageVersion.workspace_id == self.workspace_id, MessageVersion.message_id.in_(message_ids)))) if message_ids else []
        reference_conditions = [
            and_(FileReference.target_type == "project", FileReference.target_id == project_id),
        ]
        if session_ids:
            reference_conditions.append(and_(FileReference.target_type == "session", FileReference.target_id.in_(session_ids)))
        if message_ids:
            reference_conditions.append(and_(FileReference.target_type == "message", FileReference.target_id.in_(message_ids)))
        if source_link_ids:
            reference_conditions.append(and_(FileReference.target_type == "source_link", FileReference.target_id.in_(source_link_ids)))
        self.db.execute(
            delete(FileReference).where(
                FileReference.workspace_id == self.workspace_id,
                or_(*reference_conditions),
            )
        )
        if session_ids:
            self.db.execute(delete(GraphChangeSet).where(GraphChangeSet.workspace_id == self.workspace_id, GraphChangeSet.session_id.in_(session_ids)))
        if version_ids:
            for model in (
                MessageControl,
                ImageGenerationTask,
                MessageStreamEvent,
                MessagePartRecord,
                ProviderAttempt,
                ProviderResponseState,
            ):
                column = MessageControl.message_version_id if model is MessageControl else model.message_version_id
                self.db.execute(delete(model).where(column.in_(version_ids)))
        if session_ids:
            self.db.execute(delete(MessageSubmission).where(MessageSubmission.workspace_id == self.workspace_id, MessageSubmission.session_id.in_(session_ids)))
            self.db.execute(delete(ContextSummary).where(ContextSummary.workspace_id == self.workspace_id, ContextSummary.session_id.in_(session_ids)))
        if message_ids: self.db.execute(delete(MessageVersion).where(MessageVersion.workspace_id == self.workspace_id, MessageVersion.message_id.in_(message_ids)))
        if session_ids:
            if message_ids: self.db.execute(delete(CompositeDraft).where(CompositeDraft.workspace_id == self.workspace_id, CompositeDraft.target_message_id.in_(message_ids)))
            self.db.execute(delete(Message).where(Message.workspace_id == self.workspace_id, Message.session_id.in_(session_ids)))
            self.db.execute(delete(ChatSession).where(ChatSession.workspace_id == self.workspace_id, ChatSession.id.in_(session_ids)))
        self.db.execute(delete(ActionItem).where(ActionItem.workspace_id == self.workspace_id, ActionItem.project_id == project_id))
        self.db.execute(delete(SourceLink).where(SourceLink.workspace_id == self.workspace_id, SourceLink.target_type == "project", SourceLink.target_id == project_id))
        self.db.execute(delete(Project).where(Project.workspace_id == self.workspace_id, Project.id == project_id))
        self.audit.record(actor_id=self.actor_id, action="project.delete", resource_type="project", resource_id=project_id, details={"impacts": [i.model_dump() for i in impact.impacts]})
        self.db.commit()

    def assign_session(self, session_id: str, project_id: str | None) -> ChatSession:
        session = self.db.scalar(select(ChatSession).where(ChatSession.workspace_id == self.workspace_id, ChatSession.id == session_id))
        if not session: raise AppError(404, "session_not_found", "Session was not found")
        if project_id: self._project(project_id)
        session.project_id = project_id
        self.audit.record(actor_id=self.actor_id, action="session.assign_project", resource_type="session", resource_id=session.id, details={"project_id": project_id})
        self.db.commit(); self.db.refresh(session)
        return session

    def update_session(self, session_id: str, payload: SessionUpdateRequest) -> ChatSession:
        session = self._session(session_id)
        if not self.authz.can_access_resource(
            self.workspace,
            "session",
            session.id,
            "write",
        ):
            raise AppError(404, "session_not_found", "Session was not found")
        values = payload.model_dump(exclude_unset=True)
        if not values:
            return session
        binding_fields = {"goal_id", "graph_id"}.intersection(values)
        requested_goal_id = values.get("goal_id", session.goal_id)
        requested_graph_id = values.get("graph_id", session.graph_id)
        if binding_fields and not self.authz.can_access_bindings(
            self.workspace,
            "read",
            goal_id=requested_goal_id,
            graph_id=requested_graph_id,
        ):
            raise AppError(
                404,
                "session_binding_not_found",
                "One or more Session bindings were not found",
            )
        if binding_fields and session.status != "active":
            raise AppError(
                409,
                "session_binding_closed",
                "Only an active Session can be bound to a Goal or Graph",
            )

        goal = None
        if requested_goal_id:
            goal = self.db.scalar(
                select(Goal).where(
                    Goal.workspace_id == self.workspace_id,
                    Goal.id == requested_goal_id,
                )
            )
            if goal is None:
                raise AppError(404, "goal_not_found", "Session Goal was not found")
        if "goal_id" in values and session.goal_id not in {None, values["goal_id"]}:
            raise AppError(
                409,
                "session_goal_rebind_forbidden",
                "A Session cannot be rebound to a different Goal",
            )

        graph = None
        if requested_graph_id:
            graph = self.db.scalar(
                select(Graph).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.id == requested_graph_id,
                )
            )
            if graph is None:
                raise AppError(404, "graph_not_found", "Session Graph was not found")
        if "graph_id" in values and session.graph_id not in {None, values["graph_id"]}:
            raise AppError(
                409,
                "session_graph_rebind_forbidden",
                "A Session cannot be rebound to a different Graph",
            )
        if graph is not None:
            if graph.status != "published":
                raise AppError(
                    409,
                    "session_graph_not_published",
                    "A learning Session can only bind a published Graph",
                )
            if not requested_goal_id or graph.goal_id != requested_goal_id:
                raise AppError(
                    409,
                    "session_graph_goal_mismatch",
                    "Session Goal and Graph must belong to the same target",
                )
        if binding_fields and goal is not None and goal.status != "approved":
            raise AppError(
                409,
                "session_goal_not_approved",
                "A learning Session can only bind an approved Goal",
            )
        changed_values = {
            key: value
            for key, value in values.items()
            if getattr(session, key) != value
        }
        if not changed_values:
            return session
        changed_binding_fields = binding_fields.intersection(changed_values)
        if changed_binding_fields:
            supplied_binding_fields = sorted(binding_fields)
            compatible_bindings = [
                or_(
                    getattr(ChatSession, field).is_(None),
                    getattr(ChatSession, field) == values[field],
                )
                for field in supplied_binding_fields
            ]
            has_unbound_field = or_(
                *(getattr(ChatSession, field).is_(None) for field in supplied_binding_fields)
            )
            result = self.db.execute(
                update(ChatSession)
                .where(
                    ChatSession.workspace_id == self.workspace_id,
                    ChatSession.id == session.id,
                    ChatSession.status == "active",
                    *compatible_bindings,
                    has_unbound_field,
                )
                .values(**changed_values)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                self.db.rollback()
                current = self._session(session_id)
                if current.status != "active":
                    raise AppError(
                        409,
                        "session_binding_closed",
                        "Only an active Session can be bound to a Goal or Graph",
                    )
                for field in supplied_binding_fields:
                    current_value = getattr(current, field)
                    if current_value not in {None, values[field]}:
                        code = (
                            "session_goal_rebind_forbidden"
                            if field == "goal_id"
                            else "session_graph_rebind_forbidden"
                        )
                        raise AppError(
                            409,
                            code,
                            f"A Session cannot be rebound to a different {field[:-3].title()}",
                        )
                remaining_values = {
                    key: value
                    for key, value in changed_values.items()
                    if key not in binding_fields and getattr(current, key) != value
                }
                if any(
                    getattr(current, field) is None for field in supplied_binding_fields
                ):
                    raise AppError(
                        409,
                        "session_binding_conflict",
                        "The Session binding changed concurrently; reload and retry",
                    )
                if not remaining_values:
                    return current
                for key, value in remaining_values.items():
                    setattr(current, key, value)
                self.audit.record(
                    actor_id=self.actor_id,
                    action="session.update",
                    resource_type="session",
                    resource_id=current.id,
                    details={"fields": sorted(remaining_values)},
                )
                self.db.commit()
                self.db.refresh(current)
                return current
            # Avoid a second unconditional ORM write after the conditional CAS.
            self.db.refresh(session)
        else:
            for key, value in changed_values.items():
                setattr(session, key, value)
        self.audit.record(
            actor_id=self.actor_id,
            action="session.update",
            resource_type="session",
            resource_id=session.id,
            details={"fields": sorted(changed_values)},
        )
        if changed_binding_fields:
            self.audit.record(
                actor_id=self.actor_id,
                action="session.bind_learning_context",
                resource_type="session",
                resource_id=session.id,
                details={
                    "fields": sorted(changed_binding_fields),
                    "goal_id": session.goal_id,
                    "graph_id": session.graph_id,
                },
            )
        self.db.commit()
        self.db.refresh(session)
        return session

    def _session(self, session_id: str) -> ChatSession:
        item = self.db.scalar(select(ChatSession).where(ChatSession.workspace_id == self.workspace_id, ChatSession.id == session_id))
        if not item: raise AppError(404, "session_not_found", "Session was not found")
        return item

    def archive_session(self, session_id: str, archived: bool) -> ChatSession:
        item = self._session(session_id)
        item.status = "archived" if archived else "active"; item.archived_at = now() if archived else None
        self.audit.record(actor_id=self.actor_id, action="session.archive" if archived else "session.restore", resource_type="session", resource_id=item.id)
        self.db.commit(); self.db.refresh(item); return item

    def _suggested_prompt_batch_ids_for_session_deletion(
        self,
        session_ids: list[str],
        *,
        message_ids: list[str] | None = None,
        version_ids: list[str] | None = None,
    ) -> list[str]:
        selected_session_ids = set(session_ids)
        if message_ids is None:
            message_ids = list(
                self.db.scalars(
                    select(Message.id).where(
                        Message.workspace_id == self.workspace_id,
                        Message.session_id.in_(session_ids),
                    )
                )
            )
        if version_ids is None:
            version_ids = (
                list(
                    self.db.scalars(
                        select(MessageVersion.id).where(
                            MessageVersion.workspace_id == self.workspace_id,
                            MessageVersion.message_id.in_(message_ids),
                        )
                    )
                )
                if message_ids
                else []
            )
        deleted_message_ids = set(message_ids)
        deleted_version_ids = set(version_ids)
        affected: list[str] = []
        batches = self.db.scalars(
            select(SuggestedPromptBatch).where(
                SuggestedPromptBatch.workspace_id == self.workspace_id
            )
        ).all()
        for batch in batches:
            source_message_ids = (
                set(batch.source_message_ids)
                if isinstance(batch.source_message_ids, list)
                else set()
            )
            if (
                batch.session_id in selected_session_ids
                or batch.anchor_message_id in deleted_message_ids
                or batch.anchor_message_version_id in deleted_version_ids
                or bool(source_message_ids & deleted_message_ids)
            ):
                affected.append(batch.id)
        return affected

    def session_impact(self, session_id: str) -> DeleteImpact:
        item = self._session(session_id)
        message_ids = select(Message.id).where(
            Message.workspace_id == self.workspace_id,
            Message.session_id == session_id,
        )
        messages = self.db.scalar(select(func.count()).select_from(Message).where(Message.workspace_id == self.workspace_id, Message.session_id == session_id)) or 0
        children = self.db.scalar(select(func.count()).select_from(ChatSession).where(ChatSession.workspace_id == self.workspace_id, ChatSession.parent_session_id == session_id)) or 0
        proposals = self.db.scalar(select(func.count()).select_from(GraphChangeSet).where(GraphChangeSet.workspace_id == self.workspace_id, GraphChangeSet.session_id == session_id)) or 0
        file_references = self.db.scalar(
            select(func.count()).select_from(FileReference).where(
                FileReference.workspace_id == self.workspace_id,
                or_(
                    and_(FileReference.target_type == "session", FileReference.target_id == session_id),
                    and_(FileReference.target_type == "message", FileReference.target_id.in_(message_ids)),
                ),
            )
        ) or 0
        suggested_prompt_batches = len(
            self._suggested_prompt_batch_ids_for_session_deletion([session_id])
        )
        return DeleteImpact(resource_type="session", resource_id=item.id, title=item.title, confirmation_text=item.title, impacts=[ImpactItem(resource_type="message", count=messages, action="delete"), ImpactItem(resource_type="graph_change_set", count=proposals, action="delete"), ImpactItem(resource_type="suggested_prompt_batch", count=suggested_prompt_batches, action="delete"), ImpactItem(resource_type="child_session", count=children, action="detach"), ImpactItem(resource_type="file_reference", count=file_references, action="delete")])

    def _sessions(self, session_ids: list[str]) -> list[ChatSession]:
        items = list(
            self.db.scalars(
                select(ChatSession).where(
                    ChatSession.workspace_id == self.workspace_id,
                    ChatSession.id.in_(session_ids),
                )
            )
        )
        by_id = {item.id: item for item in items}
        if len(by_id) != len(session_ids):
            # Missing and foreign-workspace IDs intentionally share one response so
            # callers cannot use a batch preflight as a cross-workspace oracle.
            raise AppError(
                404,
                "session_not_found",
                "One or more sessions were not found",
            )
        return [by_id[session_id] for session_id in session_ids]

    def _session_batch_digest(self, session_ids: list[str]) -> str:
        canonical = "\0".join(sorted(session_ids))
        return hashlib.sha256(
            f"{self.workspace_id}\0{canonical}".encode("utf-8")
        ).hexdigest()[:16]

    def _session_batch_confirmation(self, session_ids: list[str]) -> str:
        return f"delete-sessions:{len(session_ids)}:{self._session_batch_digest(session_ids)}"

    def session_batch_impact(self, session_ids: list[str]) -> SessionBatchDeleteImpact:
        self._sessions(session_ids)
        message_count = self.db.scalar(
            select(func.count()).select_from(Message).where(
                Message.workspace_id == self.workspace_id,
                Message.session_id.in_(session_ids),
            )
        ) or 0
        detached_child_count = self.db.scalar(
            select(func.count()).select_from(ChatSession).where(
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.parent_session_id.in_(session_ids),
                ChatSession.id.notin_(session_ids),
            )
        ) or 0
        change_set_count = self.db.scalar(
            select(func.count()).select_from(GraphChangeSet).where(
                GraphChangeSet.workspace_id == self.workspace_id,
                GraphChangeSet.session_id.in_(session_ids),
            )
        ) or 0
        file_reference_count = self.db.scalar(
            select(func.count()).select_from(FileReference).where(
                FileReference.workspace_id == self.workspace_id,
                or_(
                    and_(FileReference.target_type == "session", FileReference.target_id.in_(session_ids)),
                    and_(
                        FileReference.target_type == "message",
                        FileReference.target_id.in_(
                            select(Message.id).where(
                                Message.workspace_id == self.workspace_id,
                                Message.session_id.in_(session_ids),
                            )
                        ),
                    ),
                ),
            )
        ) or 0
        suggested_prompt_batch_count = len(
            self._suggested_prompt_batch_ids_for_session_deletion(session_ids)
        )
        digest = self._session_batch_digest(session_ids)
        return SessionBatchDeleteImpact(
            resource_type="session_batch",
            resource_id=f"batch-{digest}",
            title=f"{len(session_ids)} 个会话",
            confirmation_text=self._session_batch_confirmation(session_ids),
            session_ids=session_ids,
            impacts=[
                ImpactItem(resource_type="message", count=message_count, action="delete"),
                ImpactItem(
                    resource_type="graph_change_set",
                    count=change_set_count,
                    action="delete",
                ),
                ImpactItem(
                    resource_type="suggested_prompt_batch",
                    count=suggested_prompt_batch_count,
                    action="delete",
                ),
                ImpactItem(
                    resource_type="child_session",
                    count=detached_child_count,
                    action="detach",
                ),
                ImpactItem(
                    resource_type="file_reference",
                    count=file_reference_count,
                    action="delete",
                ),
            ],
        )

    def _delete_session_records(self, session_ids: list[str]) -> None:
        message_ids = list(
            self.db.scalars(
                select(Message.id).where(
                    Message.workspace_id == self.workspace_id,
                    Message.session_id.in_(session_ids),
                )
            )
        )
        version_ids = (
            list(
                self.db.scalars(
                    select(MessageVersion.id).where(
                        MessageVersion.workspace_id == self.workspace_id,
                        MessageVersion.message_id.in_(message_ids),
                    )
                )
            )
            if message_ids
            else []
        )
        suggested_prompt_batch_ids = (
            self._suggested_prompt_batch_ids_for_session_deletion(
                session_ids,
                message_ids=message_ids,
                version_ids=version_ids,
            )
        )

        reference_conditions = [
            and_(FileReference.target_type == "session", FileReference.target_id.in_(session_ids)),
        ]
        if message_ids:
            reference_conditions.append(
                and_(FileReference.target_type == "message", FileReference.target_id.in_(message_ids))
            )
        self.db.execute(
            delete(FileReference).where(
                FileReference.workspace_id == self.workspace_id,
                or_(*reference_conditions),
            )
        )

        self.db.execute(
            delete(GraphChangeSet).where(
                GraphChangeSet.workspace_id == self.workspace_id,
                GraphChangeSet.session_id.in_(session_ids),
            )
        )
        self.db.execute(
            delete(FetchAuthorizationRequest).where(
                FetchAuthorizationRequest.workspace_id == self.workspace_id,
                FetchAuthorizationRequest.chat_session_id.in_(session_ids),
            )
        )
        self.db.execute(
            delete(ImageGenerationTask).where(
                ImageGenerationTask.workspace_id == self.workspace_id,
                ImageGenerationTask.session_id.in_(session_ids),
            )
        )
        if suggested_prompt_batch_ids:
            self.db.execute(
                delete(SuggestedPromptBatch).where(
                    SuggestedPromptBatch.workspace_id == self.workspace_id,
                    SuggestedPromptBatch.id.in_(suggested_prompt_batch_ids),
                )
            )
        if version_ids:
            for model in (
                MessageControl,
                MessageStreamEvent,
                MessagePartRecord,
                ProviderAttempt,
                ProviderResponseState,
            ):
                self.db.execute(
                    delete(model).where(
                        model.workspace_id == self.workspace_id,
                        model.message_version_id.in_(version_ids),
                    )
                )
        self.db.execute(
            delete(MessageSubmission).where(
                MessageSubmission.workspace_id == self.workspace_id,
                MessageSubmission.session_id.in_(session_ids),
            )
        )
        self.db.execute(
            delete(ContextSummary).where(
                ContextSummary.workspace_id == self.workspace_id,
                ContextSummary.session_id.in_(session_ids),
            )
        )
        if message_ids:
            self.db.execute(
                delete(CompositeDraft).where(
                    CompositeDraft.workspace_id == self.workspace_id,
                    CompositeDraft.target_message_id.in_(message_ids),
                )
            )
            self.db.execute(
                delete(MessageVersion).where(
                    MessageVersion.workspace_id == self.workspace_id,
                    MessageVersion.message_id.in_(message_ids),
                )
            )
            self.db.execute(
                delete(Message).where(
                    Message.workspace_id == self.workspace_id,
                    Message.id.in_(message_ids),
                )
            )

        # Branches included in the batch are deleted. Only surviving branches
        # are detached, matching the single-session deletion rule.
        self.db.execute(
            ChatSession.__table__.update()
            .where(
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.parent_session_id.in_(session_ids),
                ChatSession.id.notin_(session_ids),
            )
            .values(parent_session_id=None)
        )
        self.db.execute(
            delete(ChatSession).where(
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.id.in_(session_ids),
            )
        )

    def delete_session(self, session_id: str, confirmation: str) -> None:
        impact = self.session_impact(session_id)
        if confirmation != impact.confirmation_text: raise AppError(409, "confirmation_mismatch", "Confirmation text does not match the session title")
        self._delete_session_records([session_id])
        self.audit.record(actor_id=self.actor_id, action="session.delete", resource_type="session", resource_id=session_id, details={"impacts": [i.model_dump() for i in impact.impacts]})
        self.db.commit()

    def delete_sessions(
        self,
        session_ids: list[str],
        confirmation: str,
    ) -> SessionBatchDeleteResponse:
        try:
            impact = self.session_batch_impact(session_ids)
        except AppError as error:
            if error.code != "session_not_found":
                raise
            replay = self._deleted_session_batch(session_ids, confirmation)
            if replay is not None:
                return replay
            raise
        if confirmation != impact.confirmation_text:
            raise AppError(
                409,
                "confirmation_mismatch",
                "Confirmation token does not match the selected sessions",
            )
        self._delete_session_records(session_ids)
        self.audit.record(
            actor_id=self.actor_id,
            action="session.batch_delete",
            resource_type="session_batch",
            resource_id=impact.resource_id,
            details={
                "session_ids": session_ids,
                "impacts": [item.model_dump() for item in impact.impacts],
            },
        )
        self.db.commit()
        return SessionBatchDeleteResponse(
            deleted_session_ids=session_ids,
            deleted_count=len(session_ids),
            impacts=impact.impacts,
        )

    def _deleted_session_batch(
        self,
        session_ids: list[str],
        confirmation: str,
    ) -> SessionBatchDeleteResponse | None:
        """Return the prior success for an exact retry without exposing absence.

        A missing/foreign selection still receives the same 404 unless the
        current workspace already has a successful audit event for this exact
        ID set and the caller presents its matching confirmation token.
        """

        if confirmation != self._session_batch_confirmation(session_ids):
            return None
        resource_id = f"batch-{self._session_batch_digest(session_ids)}"
        event = self.db.scalar(
            select(AuditEvent).where(
                AuditEvent.workspace_id == self.workspace_id,
                AuditEvent.action == "session.batch_delete",
                AuditEvent.resource_type == "session_batch",
                AuditEvent.resource_id == resource_id,
                AuditEvent.outcome == "success",
            )
        )
        if event is None:
            return None
        audited_ids = list(event.details.get("session_ids") or [])
        if sorted(audited_ids) != sorted(session_ids):
            return None
        impacts = [
            ImpactItem.model_validate(item)
            for item in list(event.details.get("impacts") or [])
        ]
        return SessionBatchDeleteResponse(
            deleted_session_ids=session_ids,
            deleted_count=len(session_ids),
            impacts=impacts,
        )

    def create_source_link(self, source_id: str, payload: SourceLinkCreate) -> SourceLink:
        if not self.db.scalar(select(SourceRecord.id).where(SourceRecord.workspace_id == self.workspace_id, SourceRecord.id == source_id)):
            raise AppError(404, "source_not_found", "Source was not found")
        models = {"project": Project, "goal": Goal, "graph": Graph, "node": GraphNode}; model = models[payload.target_type]
        if not self.db.scalar(select(model.id).where(model.workspace_id == self.workspace_id, model.id == payload.target_id)):
            raise AppError(404, "source_target_not_found", "Source link target was not found")
        existing = self.db.scalar(select(SourceLink).where(SourceLink.workspace_id == self.workspace_id, SourceLink.source_id == source_id, SourceLink.target_type == payload.target_type, SourceLink.target_id == payload.target_id))
        if existing: return existing
        item = SourceLink(workspace_id=self.workspace_id, source_id=source_id, **payload.model_dump())
        self.db.add(item); self.db.flush(); self.audit.record(actor_id=self.actor_id, action="source.link", resource_type="source", resource_id=source_id, details=payload.model_dump())
        self.db.commit(); self.db.refresh(item); return item

    def source_links(self, source_id: str) -> list[SourceLink]:
        return list(self.db.scalars(select(SourceLink).where(SourceLink.workspace_id == self.workspace_id, SourceLink.source_id == source_id).order_by(SourceLink.created_at)))

    def actions(self, status: str | None = None) -> list[ActionItem]:
        stmt = (
            select(ActionItem)
            .outerjoin(Roadmap, ActionItem.roadmap_id == Roadmap.id)
            .where(
                ActionItem.workspace_id == self.workspace_id,
                or_(
                    ActionItem.roadmap_id.is_(None),
                    and_(
                        Roadmap.workspace_id == self.workspace_id,
                        Roadmap.status == "published",
                    ),
                ),
            )
        )
        if status: stmt = stmt.where(ActionItem.status == status)
        items = list(self.db.scalars(stmt.order_by(ActionItem.status, ActionItem.priority.desc(), ActionItem.due_at, ActionItem.position)))
        return [
            item
            for item in items
            if self.authz.can_access_action_record(
                self.workspace, item, "read"
            )
        ]

    def create_action(self, payload: ActionCreate) -> ActionItem:
        if not self.authz.can_access_bindings(
            self.workspace,
            "write",
            project_id=payload.project_id,
            goal_id=payload.goal_id,
            graph_id=payload.graph_id,
            node_id=payload.node_id,
        ):
            raise AppError(
                404,
                "action_binding_not_found",
                "One or more action bindings were not found",
            )
        item = ActionItem(workspace_id=self.workspace_id, source="user", **payload.model_dump())
        self.db.add(item); self.db.flush(); self.audit.record(actor_id=self.actor_id, action="action.create", resource_type="action_item", resource_id=item.id)
        self.db.commit(); self.db.refresh(item); return item

    def update_action(self, action_id: str, payload: ActionUpdate) -> ActionItem:
        item = self.db.scalar(select(ActionItem).where(ActionItem.workspace_id == self.workspace_id, ActionItem.id == action_id))
        if not item or not self.authz.can_access_action_record(
            self.workspace, item, "write"
        ):
            raise AppError(404, "action_not_found", "Action item was not found")
        values = payload.model_dump(exclude_unset=True)
        if item.roadmap_id is not None:
            roadmap = self.db.scalar(
                select(Roadmap).where(
                    Roadmap.workspace_id == self.workspace_id,
                    Roadmap.id == item.roadmap_id,
                )
            )
            # Versionless mode: the active roadmap is always `published`.
            # Legacy draft rows remain non-actionable until regenerated.
            if roadmap is None or roadmap.status != "published":
                raise AppError(
                    409,
                    "action_roadmap_not_active",
                    "Only actions from the active roadmap can be updated",
                )
            plan_fields = sorted(set(values) - {"status"})
            if plan_fields:
                raise AppError(
                    409,
                    "roadmap_action_plan_immutable",
                    "Roadmap action content and schedule must be changed through roadmap reschedule/replan",
                    {"fields": plan_fields, "roadmap_id": item.roadmap_id},
                )
            target_status = values.get("status")
            allowed_transitions = {
                "pending": {"pending", "in_progress", "completed"},
                "in_progress": {"in_progress", "completed"},
                "completed": {"completed"},
                "archived": {"archived"},
                "blocked": {"blocked"},
            }
            if (
                not isinstance(target_status, str)
                or target_status not in allowed_transitions.get(item.status, set())
            ):
                raise AppError(
                    409,
                    "roadmap_action_invalid_transition",
                    "Roadmap actions only allow forward progress on the active plan",
                    {
                        "current_status": item.status,
                        "requested_status": target_status,
                        "roadmap_id": item.roadmap_id,
                    },
                )
            if target_status == item.status:
                return item
        for key, value in values.items(): setattr(item, key, value)
        if "status" in values: item.completed_at = now() if values["status"] == "completed" else None
        self.audit.record(actor_id=self.actor_id, action="action.update", resource_type="action_item", resource_id=item.id, details={"fields": list(values)})
        self.db.commit(); self.db.refresh(item); return item

    def create_composite(self, payload: CompositeCreate) -> CompositeDraft:
        target = self.db.scalar(select(Message).where(Message.workspace_id == self.workspace_id, Message.id == payload.target_message_id, Message.role == "assistant"))
        if not target: raise AppError(404, "target_message_not_found", "Target assistant message was not found")
        versions = list(self.db.scalars(select(MessageVersion).where(MessageVersion.workspace_id == self.workspace_id, MessageVersion.id.in_(payload.source_version_ids))))
        if len({item.id for item in versions}) != len(set(payload.source_version_ids)):
            raise AppError(404, "source_version_not_found", "One or more source versions were not found")
        ordered = sorted(versions, key=lambda item: payload.source_version_ids.index(item.id))
        sections: list[str] = []
        source_meta: list[dict] = []
        for version in ordered:
            message = self.db.scalar(select(Message).where(Message.workspace_id == self.workspace_id, Message.id == version.message_id))
            parts = list(self.db.scalars(select(MessagePartRecord).where(MessagePartRecord.workspace_id == self.workspace_id, MessagePartRecord.message_version_id == version.id).order_by(MessagePartRecord.ordinal)))
            text = "\n".join(part.content for part in parts if part.part_type == "text" and part.content.strip()) or (message.content if message else "")
            if text and text not in sections: sections.append(text)
            source_meta.append({"message_id": version.message_id, "message_version_id": version.id, "version": version.version})
        content = "\n\n---\n\n".join(sections)
        if not content: raise AppError(409, "composite_sources_empty", "Selected versions contain no mergeable text")
        draft = CompositeDraft(workspace_id=self.workspace_id, target_message_id=target.id, source_version_ids=payload.source_version_ids, content=content, parts=[{"type": "text", "status": "completed", "content": content, "data": {"composite_sources": source_meta}}])
        self.db.add(draft); self.db.flush(); self.audit.record(actor_id=self.actor_id, action="message.composite_draft", resource_type="message", resource_id=target.id, details={"draft_id": draft.id, "source_version_ids": payload.source_version_ids})
        self.db.commit(); self.db.refresh(draft); return draft

    def confirm_composite(self, draft_id: str) -> CompositeDraft:
        draft = self.db.scalar(select(CompositeDraft).where(CompositeDraft.workspace_id == self.workspace_id, CompositeDraft.id == draft_id))
        if not draft: raise AppError(404, "composite_draft_not_found", "Composite draft was not found")
        if draft.status == "confirmed": return draft
        message = self.db.scalar(select(Message).where(Message.workspace_id == self.workspace_id, Message.id == draft.target_message_id))
        if not message: raise AppError(404, "target_message_not_found", "Target message was not found")
        next_version = (self.db.scalar(select(func.max(MessageVersion.version)).where(MessageVersion.workspace_id == self.workspace_id, MessageVersion.message_id == message.id)) or 0) + 1
        version = MessageVersion(workspace_id=self.workspace_id, message_id=message.id, version=next_version, status="completed", provider_trace={"provider_id": "composite", "source_version_ids": draft.source_version_ids})
        self.db.add(version); self.db.flush()
        for ordinal, part in enumerate(draft.parts):
            self.db.add(MessagePartRecord(workspace_id=self.workspace_id, message_version_id=version.id, ordinal=ordinal, part_type=str(part.get("type", "text")), status="completed", content=str(part.get("content", "")), data=dict(part.get("data") or {})))
        message.version = next_version; message.content = draft.content; message.parts = draft.parts; message.provider_trace = version.provider_trace
        draft.status = "confirmed"; draft.confirmed_version_id = version.id
        self.audit.record(actor_id=self.actor_id, action="message.composite_confirm", resource_type="message", resource_id=message.id, details={"draft_id": draft.id, "version_id": version.id})
        self.db.commit(); self.db.refresh(draft); return draft

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @staticmethod
    def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, parsed))

    def _planning_graph(self, goal_id: str) -> tuple[Graph, str]:
        """Return the graph fact a draft is allowed to use.

        The first draft may be attached to a candidate graph for joint review.
        Once a graph is published, all later re-plans use only that published
        graph.  Research artifacts are intentionally absent from this choice.
        """

        published = self.db.scalar(
            select(Graph)
            .where(
                Graph.workspace_id == self.workspace_id,
                Graph.goal_id == goal_id,
                Graph.status == "published",
            )
            .order_by(Graph.revision.desc(), Graph.updated_at.desc())
        )
        if published is not None:
            return published, "published_replan"

        candidate = self.db.scalar(
            select(Graph)
            .where(
                Graph.workspace_id == self.workspace_id,
                Graph.goal_id == goal_id,
                Graph.status == "candidate",
            )
            .order_by(Graph.revision.desc(), Graph.updated_at.desc())
        )
        if candidate is not None:
            return candidate, "initial_candidate"
        raise AppError(
            409,
            "planning_graph_missing",
            "Generate and review a candidate graph before creating a roadmap draft",
        )

    @staticmethod
    def _action_type_for_node(node: GraphNode) -> str:
        if node.node_type == "practice":
            return "practice"
        if node.node_type == "assessment":
            return "assessment"
        if node.retrieval_state in {"due", "relearning"}:
            return "review"
        return "learn"

    @staticmethod
    def _acceptance_criteria(node: GraphNode, action_type: str) -> list[str]:
        label = node.label.strip() or "当前节点"
        if action_type == "practice":
            return [
                f"完成一项与“{label}”直接相关的练习",
                "提交可追溯答案，由证据规则判断结果而不是按浏览行为授予掌握",
            ]
        if action_type == "assessment":
            return [
                f"完成“{label}”验收题",
                "达到题目声明的通过条件，并保留答案与评分证据",
            ]
        if action_type == "review":
            return [
                f"在不查看资料时主动回忆“{label}”的关键概念",
                "记录回忆结果；遗忘只改变复习状态，不扣减既有成长星级",
            ]
        return [
            f"能用自己的话解释“{label}”及其在目标图谱中的作用",
            "完成至少一次可追溯的回答、解释或练习产出",
        ]

    def _graph_planning_hash(
        self,
        graph: Graph,
        nodes: list[GraphNode],
        edges: list[GraphEdge],
    ) -> str:
        """Hash only facts that can change a generated action plan.

        Graph publication state is deliberately excluded: publishing the
        reviewed candidate must not invalidate the separately reviewed draft.
        A node or prerequisite edit does invalidate it.
        """

        payload = {
            "graph_id": graph.id,
            "graph_revision": graph.revision,
            "nodes": [
                {
                    "id": node.id,
                    "label": node.label,
                    "description": node.description,
                    "node_type": node.node_type,
                    "target_weight": node.target_weight,
                    "mastery_stars": node.mastery_stars,
                    "retrieval_state": node.retrieval_state,
                    "evidence_state": node.evidence_state,
                }
                for node in sorted(nodes, key=lambda item: item.id)
            ],
            "edges": [
                {
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                    "relation": edge.relation,
                }
                for edge in sorted(
                    edges,
                    key=lambda item: (
                        item.source_node_id,
                        item.target_node_id,
                        item.relation,
                    ),
                )
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @classmethod
    def _evidence_gap(cls, node: GraphNode) -> float:
        return {
            "robust": 0.0,
            "cross_time": 0.1,
            "multi": 0.25,
            "single": 0.5,
            "interest_only": 0.8,
            "none": 1.0,
            "conflicted": 1.0,
        }.get(node.evidence_state, 0.75)

    @staticmethod
    def _retrieval_urgency(node: GraphNode) -> float:
        return {
            "relearning": 1.0,
            "due": 0.9,
            "due_soon": 0.6,
            "fresh": 0.1,
            "unverified": 0.5,
        }.get(node.retrieval_state, 0.35)

    @staticmethod
    def _deadline_urgency(deadline_at: datetime | None, plan_started_at: datetime) -> float:
        if deadline_at is None:
            return 0.0
        days_remaining = (deadline_at - plan_started_at).total_seconds() / 86_400
        if days_remaining <= 0:
            return 1.0
        if days_remaining <= 1:
            return 0.95
        if days_remaining <= 7:
            return 0.75
        if days_remaining <= 14:
            return 0.55
        if days_remaining <= 30:
            return 0.35
        return 0.15

    @classmethod
    def _prerequisite_satisfied(cls, node: GraphNode) -> bool:
        return (
            max(0, int(node.mastery_stars)) >= cls.PREREQUISITE_MIN_STARS
            and node.evidence_state not in {"none", "interest_only", "conflicted"}
            and node.retrieval_state != "relearning"
        )

    def _prerequisite_entries(
        self,
        node: GraphNode,
        prerequisites: dict[str, list[GraphNode]],
    ) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for prerequisite in prerequisites.get(node.id, []):
            satisfied = self._prerequisite_satisfied(prerequisite)
            entries.append(
                {
                    "node_id": prerequisite.id,
                    "label": prerequisite.label,
                    "mastery_stars": max(0, int(prerequisite.mastery_stars)),
                    "retrieval_state": prerequisite.retrieval_state,
                    "evidence_state": prerequisite.evidence_state,
                    "satisfied": satisfied,
                    "reason": (
                        "prerequisite_satisfied"
                        if satisfied
                        else "needs_verified_prerequisite"
                    ),
                }
            )
        return entries

    def _prerequisite_blockers(
        self,
        node: GraphNode,
        prerequisites: dict[str, list[GraphNode]],
    ) -> list[dict[str, Any]]:
        return [
            entry
            for entry in self._prerequisite_entries(node, prerequisites)
            if not entry.get("satisfied")
        ]

    def _score_action(
        self,
        goal: Goal,
        node: GraphNode,
        action_type: str,
        deadline_at: datetime | None,
        plan_started_at: datetime,
        preferred_action_types: set[str],
    ) -> tuple[int, dict[str, float]]:
        importance = (max(1, min(100, int(goal.target_weight))) / 100) * (
            max(1, min(100, int(node.target_weight))) / 100
        )
        mastery_gap = 1 - min(
            self.MAX_MASTERY_STARS,
            max(0, int(node.mastery_stars)),
        ) / self.MAX_MASTERY_STARS
        retrieval_urgency = self._retrieval_urgency(node)
        evidence_gap = self._evidence_gap(node)
        deadline_urgency = self._deadline_urgency(deadline_at, plan_started_at)
        preference_match = 1.0 if action_type in preferred_action_types else 0.0
        score = round(
            100
            * (
                0.30 * importance
                + 0.24 * mastery_gap
                + 0.18 * retrieval_urgency
                + 0.14 * evidence_gap
                + 0.10 * deadline_urgency
                + 0.04 * preference_match
            )
        )
        return max(0, min(100, score)), {
            "importance": round(importance, 4),
            "mastery_gap": round(mastery_gap, 4),
            "retrieval_urgency": round(retrieval_urgency, 4),
            "evidence_gap": round(evidence_gap, 4),
            "deadline_urgency": round(deadline_urgency, 4),
            "preference_match": preference_match,
        }

    @staticmethod
    def _scheduled_due_at(
        plan_started_at: datetime,
        day_index: int,
        days_per_week: int,
    ) -> datetime:
        # `day_index` means an available learning day, not a calendar-day
        # promise.  Spreading it across the declared weekly availability makes
        # five available days behave differently from seven without inventing
        # specific weekdays the user never supplied.
        offset_days = ((day_index - 1) * 7 + days_per_week - 1) // days_per_week
        return plan_started_at + timedelta(days=offset_days)

    def _normalized_goal_planning_inputs(
        self,
        goal: Goal,
    ) -> tuple[dict[str, Any], datetime | None, int, int, int, set[str]]:
        """Return the exact normalized facts persisted with a roadmap draft."""

        deadline_at = self._as_utc(goal.deadline_at)
        availability = dict(goal.availability or {})
        preferences = dict(goal.preferences or {})
        available_minutes_per_day = self._bounded_int(
            availability.get("minutes_per_day"), 60, 15, 1_440
        )
        days_per_week = self._bounded_int(availability.get("days_per_week"), 5, 1, 7)
        preferred_session_minutes = self._bounded_int(
            preferences.get("session_minutes"), 30, 15, 240
        )
        preferred_action_types = {
            str(value)
            for value in list(preferences.get("preferred_action_types") or [])
            if str(value) in {"learn", "review", "practice", "assessment"}
        }
        return (
            {
                "target_weight": self._bounded_int(goal.target_weight, 50, 1, 100),
                "deadline_at": deadline_at.isoformat() if deadline_at else None,
                "availability": {
                    "minutes_per_day": available_minutes_per_day,
                    "days_per_week": days_per_week,
                },
                "preferences": {
                    "preferred_action_types": sorted(preferred_action_types),
                    "session_minutes": preferred_session_minutes,
                },
            },
            deadline_at,
            available_minutes_per_day,
            days_per_week,
            min(preferred_session_minutes, available_minutes_per_day),
            preferred_action_types,
        )

    def _goal(self, goal_id: str, permission: str = "read") -> Goal:
        goal = self.db.scalar(
            select(Goal).where(
                Goal.workspace_id == self.workspace_id,
                Goal.id == goal_id,
            )
        )
        if goal is None or not self.authz.can_access_resource(
            self.workspace, "goal", goal_id, permission
        ):
            raise AppError(404, "goal_not_found", "Goal was not found")
        return goal

    def _roadmap_record(self, roadmap_id: str, permission: str = "read") -> Roadmap:
        roadmap = self.db.scalar(
            select(Roadmap).where(
                Roadmap.workspace_id == self.workspace_id,
                Roadmap.id == roadmap_id,
            )
        )
        if roadmap is None or not self.authz.can_access_roadmap_record(
            self.workspace, roadmap, permission
        ):
            raise AppError(404, "roadmap_not_found", "Roadmap was not found")
        return roadmap

    def _roadmap_items(self, roadmap_id: str) -> list[ActionItem]:
        items = list(
            self.db.scalars(
                select(ActionItem)
                .where(
                    ActionItem.workspace_id == self.workspace_id,
                    ActionItem.roadmap_id == roadmap_id,
                )
                .order_by(ActionItem.day_index, ActionItem.position)
            )
        )
        # Blocked actions are kept as transparent plan facts, but never lead
        # the actionable list merely because their unscheduled day is zero.
        items.sort(key=lambda item: (item.status == "blocked", item.day_index, item.position))
        return items

    def _roadmap_data(self, roadmap: Roadmap) -> dict[str, Any]:
        return {
            **roadmap.__dict__,
            "items": [
                item
                for item in self._roadmap_items(roadmap.id)
                if self.authz.can_access_action_record(
                    self.workspace, item, "read"
                )
            ],
        }

    def roadmaps(self, goal_id: str) -> list[Roadmap]:
        self._goal(goal_id)
        roadmaps = list(
            self.db.scalars(
                select(Roadmap)
                .where(
                    Roadmap.workspace_id == self.workspace_id,
                    Roadmap.goal_id == goal_id,
                )
                .order_by(Roadmap.version.desc())
            )
        )
        return [
            roadmap
            for roadmap in roadmaps
            if self.authz.can_access_roadmap_record(
                self.workspace, roadmap, "read"
            )
        ]

    def roadmap_by_id(self, roadmap_id: str) -> dict[str, Any]:
        return self._roadmap_data(self._roadmap_record(roadmap_id))

    def roadmap(self, goal_id: str) -> dict[str, Any]:
        self._goal(goal_id)
        # Prefer the active roadmap; fall back to the latest row for legacy data.
        roadmap = self.db.scalar(
            select(Roadmap)
            .where(
                Roadmap.workspace_id == self.workspace_id,
                Roadmap.goal_id == goal_id,
                Roadmap.status == "published",
            )
            .order_by(Roadmap.version.desc())
        )
        if roadmap is None:
            roadmap = self.db.scalar(
                select(Roadmap)
                .where(
                    Roadmap.workspace_id == self.workspace_id,
                    Roadmap.goal_id == goal_id,
                )
                .order_by(Roadmap.version.desc())
            )
        if roadmap is None:
            # A read must not manufacture a plan. Explicit replan creates it.
            raise AppError(
                404,
                "roadmap_not_found",
                "No roadmap exists; explicitly create one before reading it",
            )
        if not self.authz.can_access_roadmap_record(
            self.workspace, roadmap, "read"
        ):
            raise AppError(404, "roadmap_not_found", "Roadmap was not found")
        # Versionless migration: promote leftover draft rows so actions and
        # dashboard immediately treat them as the active plan.
        if roadmap.status == "draft":
            roadmap.status = "published"
            roadmap.published_at = roadmap.published_at or now()
            self.db.commit()
            self.db.refresh(roadmap)
        return self._roadmap_data(roadmap)

    def replan_roadmap(self, goal_id: str) -> Roadmap:
        goal = self._goal(goal_id, "write")
        graph, review_scope = self._planning_graph(goal_id)
        if not self.authz.can_access_resource(
            self.workspace, "graph", graph.id, "write"
        ):
            raise AppError(404, "planning_graph_not_found", "Planning graph was not found")
        plan_started_at = now()
        (
            goal_planning_inputs,
            deadline_at,
            available_minutes_per_day,
            days_per_week,
            duration_minutes,
            preferred_action_types,
        ) = self._normalized_goal_planning_inputs(goal)
        raw_availability = dict(goal.availability or {})
        raw_preferences = dict(goal.preferences or {})
        assumptions: list[str] = []
        if raw_availability.get("minutes_per_day") is None:
            assumptions.append("未提供每日可用时间，规划按 60 分钟/天计算")
        if raw_availability.get("days_per_week") is None:
            assumptions.append("未提供每周学习天数，规划按 5 天/周计算")
        if raw_preferences.get("session_minutes") is None:
            assumptions.append("未提供单次时长偏好，规划按 30 分钟时间盒计算")
        if deadline_at is None:
            assumptions.append("目标没有固定截止时间，排序不增加期限紧迫度")
        nodes = list(
            self.db.scalars(
                select(GraphNode).where(
                    GraphNode.workspace_id == self.workspace_id,
                    GraphNode.graph_id == graph.id,
                )
            )
        )
        edges = list(
            self.db.scalars(
                select(GraphEdge).where(
                    GraphEdge.workspace_id == self.workspace_id,
                    GraphEdge.graph_id == graph.id,
                    GraphEdge.relation == "prerequisite",
                )
            )
        )
        by_node_id = {node.id: node for node in nodes}
        prerequisites: dict[str, list[GraphNode]] = {}
        for edge in edges:
            source = by_node_id.get(edge.source_node_id)
            target = by_node_id.get(edge.target_node_id)
            if source is not None and target is not None:
                prerequisites.setdefault(target.id, []).append(source)

        graph_snapshot_hash = self._graph_planning_hash(graph, nodes, edges)
        planning_snapshot = {
            "rule_version": self.PLANNER_RULE_VERSION,
            "plan_started_at": plan_started_at.isoformat(),
            "review_scope": review_scope,
            "graph_status_at_generation": graph.status,
            "graph_revision": graph.revision,
            "graph_snapshot_hash": graph_snapshot_hash,
            "goal": goal_planning_inputs,
            "assumptions": assumptions,
            "replan_triggers": [
                "graph_revision_changed",
                "goal_constraints_changed",
                "mastery_or_evidence_changed",
                "prerequisite_state_changed",
            ],
            "prerequisite_edges": [
                {
                    "source_node_id": edge.source_node_id,
                    "target_node_id": edge.target_node_id,
                }
                for edge in sorted(edges, key=lambda item: (item.source_node_id, item.target_node_id))
            ],
        }
        latest = self.db.scalar(select(func.max(Roadmap.version)).where(Roadmap.workspace_id == self.workspace_id, Roadmap.goal_id == goal_id)) or 0
        # Versionless mode: replan immediately becomes the active roadmap.
        # Prior active/draft rows are superseded so only one plan is actionable.
        for prior in self.db.scalars(
            select(Roadmap).where(
                Roadmap.workspace_id == self.workspace_id,
                Roadmap.goal_id == goal_id,
                Roadmap.status.in_(("draft", "published")),
            )
        ):
            prior.status = "superseded"
        roadmap = Roadmap(
            workspace_id=self.workspace_id,
            goal_id=goal_id,
            graph_id=graph.id,
            graph_revision=graph.revision,
            title=f"{goal.title}路线",
            version=latest + 1,
            status="published",
            published_at=plan_started_at,
            rationale="基于目标权重、掌握缺口、期限、可用时间、偏好和前置条件生成；路线即生效，无需单独发布。",
            planning_snapshot=planning_snapshot,
        )
        self.db.add(roadmap); self.db.flush()
        ranked: list[tuple[GraphNode, str, int, dict[str, float], list[dict[str, Any]]]] = []
        blocked: list[tuple[GraphNode, str, int, dict[str, float], list[dict[str, Any]]]] = []
        excluded: list[dict[str, str]] = []
        completed_node_ids = set(
            self.db.scalars(
                select(ActionItem.node_id).where(
                    ActionItem.workspace_id == self.workspace_id,
                    ActionItem.goal_id == goal_id,
                    ActionItem.source == "roadmap",
                    ActionItem.status == "completed",
                    ActionItem.node_id.is_not(None),
                )
            )
        )
        for node in nodes:
            action_type = self._action_type_for_node(node)
            if node.node_type == "root":
                excluded.append({"node_id": node.id, "reason": "goal_root_is_not_actionable"})
                continue
            if (
                node.id in completed_node_ids
                and node.retrieval_state not in {"due", "due_soon", "relearning"}
            ):
                excluded.append({"node_id": node.id, "reason": "completed_and_not_due"})
                continue
            if (
                max(0, int(node.mastery_stars)) >= self.MAX_MASTERY_STARS
                and node.retrieval_state == "fresh"
                and node.evidence_state in {"multi", "cross_time", "robust"}
            ):
                excluded.append({"node_id": node.id, "reason": "mastered_and_fresh"})
                continue
            score, score_breakdown = self._score_action(
                goal,
                node,
                action_type,
                deadline_at,
                plan_started_at,
                preferred_action_types,
            )
            prereq_entries = self._prerequisite_entries(node, prerequisites)
            blockers = [entry for entry in prereq_entries if not entry.get("satisfied")]
            if blockers:
                blocked.append((node, action_type, score, score_breakdown, prereq_entries))
            else:
                ranked.append((node, action_type, score, score_breakdown, prereq_entries))

        ranked.sort(key=lambda item: (-item[2], -item[0].target_weight, item[0].created_at, item[0].id))
        minutes_on_day = 0
        day_index = 1
        position = 0
        scheduled_after_deadline_count = 0
        for node, action_type, score, score_breakdown, prereq_entries in ranked:
            if minutes_on_day and minutes_on_day + duration_minutes > available_minutes_per_day:
                day_index += 1
                minutes_on_day = 0
            due_at = self._scheduled_due_at(plan_started_at, day_index, days_per_week)
            scheduled_after_deadline = bool(deadline_at and due_at > deadline_at)
            if scheduled_after_deadline:
                scheduled_after_deadline_count += 1
            self.db.add(
                ActionItem(
                    workspace_id=self.workspace_id,
                    title=f"学习：{node.label}",
                    description=node.description,
                    source="roadmap",
                    action_type=action_type,
                    goal_id=goal_id,
                    graph_id=graph.id,
                    node_id=node.id,
                    roadmap_id=roadmap.id,
                    day_index=day_index,
                    duration_minutes=duration_minutes,
                    due_at=due_at,
                    priority=score,
                    position=position,
                    metadata_json={
                        "roadmap_version": roadmap.version,
                        "planner_rule_version": self.PLANNER_RULE_VERSION,
                        "review_scope": review_scope,
                        "graph_revision": graph.revision,
                        "score": score,
                        "score_breakdown": score_breakdown,
                        "ranking_reason": "eligible_after_prerequisite_check",
                        "acceptance_criteria": self._acceptance_criteria(node, action_type),
                        "prerequisites": {
                            "items": prereq_entries,
                            "blocked_by": [],
                        },
                        "schedule": {
                            "available_minutes_per_day": available_minutes_per_day,
                            "days_per_week": days_per_week,
                            "session_minutes": duration_minutes,
                            "scheduled_after_deadline": scheduled_after_deadline,
                        },
                    },
                )
            )
            minutes_on_day += duration_minutes
            position += 1

        # Keep blocked items visible inside the plan timeline rather than a
        # detached queue: place them after scheduled work so each day can show
        # prerequisite context next to actionable tasks.
        blocked_start_day = day_index if ranked else 1
        for offset, (node, action_type, score, score_breakdown, prereq_entries) in enumerate(
            sorted(
                blocked,
                key=lambda item: (-item[2], -item[0].target_weight, item[0].created_at, item[0].id),
            )
        ):
            blocked_day = blocked_start_day + (offset // max(1, available_minutes_per_day // max(duration_minutes, 1)))
            blockers = [entry for entry in prereq_entries if not entry.get("satisfied")]
            self.db.add(
                ActionItem(
                    workspace_id=self.workspace_id,
                    title=f"待解锁：{node.label}",
                    description=node.description,
                    status="blocked",
                    source="roadmap",
                    action_type=action_type,
                    goal_id=goal_id,
                    graph_id=graph.id,
                    node_id=node.id,
                    roadmap_id=roadmap.id,
                    day_index=blocked_day,
                    duration_minutes=duration_minutes,
                    due_at=self._scheduled_due_at(plan_started_at, blocked_day, days_per_week),
                    priority=0,
                    position=position,
                    metadata_json={
                        "roadmap_version": roadmap.version,
                        "planner_rule_version": self.PLANNER_RULE_VERSION,
                        "review_scope": review_scope,
                        "graph_revision": graph.revision,
                        "score": score,
                        "score_breakdown": score_breakdown,
                        "ranking_reason": "blocked_by_prerequisite",
                        "acceptance_criteria": self._acceptance_criteria(node, action_type),
                        "prerequisites": {
                            "items": prereq_entries,
                            "blocked_by": blockers,
                        },
                        "schedule": {
                            "available_minutes_per_day": available_minutes_per_day,
                            "days_per_week": days_per_week,
                            "session_minutes": duration_minutes,
                        },
                    },
                )
            )
            position += 1
        updated_snapshot = dict(roadmap.planning_snapshot or {})
        scheduled_day_candidates = [0]
        if ranked:
            scheduled_day_candidates.append(day_index)
        if blocked:
            last_blocked_day = blocked_start_day + max(
                0,
                (len(blocked) - 1)
                // max(1, available_minutes_per_day // max(duration_minutes, 1)),
            )
            scheduled_day_candidates.append(last_blocked_day)
        scheduled_days = max(scheduled_day_candidates)
        updated_snapshot["capacity_summary"] = {
            "scheduled_item_count": len(ranked),
            "blocked_item_count": len(blocked),
            "scheduled_days": scheduled_days if (ranked or blocked) else 0,
            "total_minutes": len(ranked) * duration_minutes,
            "available_minutes_per_day": available_minutes_per_day,
            "scheduled_after_deadline_count": scheduled_after_deadline_count,
        }
        updated_snapshot["excluded_nodes"] = excluded
        updated_snapshot["unresolved_gaps"] = (
            [
                f"{scheduled_after_deadline_count} 个行动超出目标截止时间；请调整约束或重新规划"
            ]
            if scheduled_after_deadline_count
            else []
        )
        roadmap.planning_snapshot = updated_snapshot
        self.audit.record(
            actor_id=self.actor_id,
            action="roadmap.replan",
            resource_type="roadmap",
            resource_id=roadmap.id,
            details={
                "goal_id": goal_id,
                "version": roadmap.version,
                "graph_id": graph.id,
                "graph_revision": graph.revision,
                "review_scope": review_scope,
                "planner_rule_version": self.PLANNER_RULE_VERSION,
                "eligible_item_count": len(ranked),
                "blocked_item_count": len(blocked),
                "excluded_node_count": len(excluded),
                "scheduled_after_deadline_count": scheduled_after_deadline_count,
                "mode": "versionless_active",
            },
        )
        self.db.commit(); self.db.refresh(roadmap); return roadmap

    def reschedule_roadmap_item(
        self,
        roadmap_id: str,
        action_id: str,
        payload: RoadmapItemReschedule,
    ) -> Roadmap:
        roadmap = self._roadmap_record(roadmap_id, "write")
        if payload.base_version != roadmap.version:
            raise AppError(
                409,
                "roadmap_version_conflict",
                "The roadmap changed after this editor was opened",
                {"expected_version": payload.base_version, "current_version": roadmap.version},
            )
        latest_version = self.db.scalar(
            select(func.max(Roadmap.version)).where(
                Roadmap.workspace_id == self.workspace_id,
                Roadmap.goal_id == roadmap.goal_id,
            )
        )
        if latest_version is not None and roadmap.version != latest_version:
            raise AppError(
                409,
                "roadmap_not_latest",
                "Only the active roadmap can be revised",
                {"requested_version": roadmap.version, "latest_version": latest_version},
            )
        if roadmap.status != "published":
            # Auto-activate legacy draft rows so reschedule stays versionless.
            if roadmap.status == "draft":
                for prior in self.db.scalars(
                    select(Roadmap).where(
                        Roadmap.workspace_id == self.workspace_id,
                        Roadmap.goal_id == roadmap.goal_id,
                        Roadmap.status == "published",
                        Roadmap.id != roadmap.id,
                    )
                ):
                    prior.status = "superseded"
                roadmap.status = "published"
                roadmap.published_at = roadmap.published_at or now()
            else:
                raise AppError(
                    409,
                    "roadmap_not_revisionable",
                    "Only the active roadmap can be revised",
                    {"status": roadmap.status},
                )

        items = self._roadmap_items(roadmap.id)
        moving = next((item for item in items if item.id == action_id), None)
        if moving is None:
            raise AppError(404, "roadmap_item_not_found", "Roadmap item was not found")
        if moving.status == "blocked":
            raise AppError(
                409,
                "roadmap_item_blocked",
                "Resolve the persisted prerequisite blockers before scheduling this item",
            )
        if moving.status in {"completed", "archived"}:
            raise AppError(
                409,
                "roadmap_item_not_schedulable",
                "Completed or archived roadmap items cannot be rescheduled",
                {"status": moving.status},
            )

        scheduled_by_day: dict[int, list[ActionItem]] = {}
        blocked_items: list[ActionItem] = []
        for item in items:
            if item.status == "blocked":
                blocked_items.append(item)
            elif item.id != moving.id:
                scheduled_by_day.setdefault(item.day_index, []).append(item)
        target_items = scheduled_by_day.setdefault(payload.day_index, [])
        target_position = min(payload.position, len(target_items))
        target_items.insert(target_position, moving)
        ordered_scheduled = [
            item
            for day in sorted(scheduled_by_day)
            for item in scheduled_by_day[day]
        ]

        snapshot = copy.deepcopy(roadmap.planning_snapshot or {})
        plan_started_at = self._as_utc(roadmap.created_at) or now()
        raw_plan_started_at = snapshot.get("plan_started_at")
        if isinstance(raw_plan_started_at, str):
            try:
                parsed_plan_started_at = datetime.fromisoformat(raw_plan_started_at)
                plan_started_at = self._as_utc(parsed_plan_started_at) or plan_started_at
            except ValueError:
                pass
        goal_snapshot = dict(snapshot.get("goal") or {})
        availability = dict(goal_snapshot.get("availability") or {})
        available_minutes_per_day = self._bounded_int(
            availability.get("minutes_per_day"), 60, 15, 1_440
        )
        days_per_week = self._bounded_int(availability.get("days_per_week"), 5, 1, 7)
        deadline_at: datetime | None = None
        raw_deadline_at = goal_snapshot.get("deadline_at")
        if isinstance(raw_deadline_at, str):
            try:
                deadline_at = self._as_utc(datetime.fromisoformat(raw_deadline_at))
            except ValueError:
                deadline_at = None

        duration_by_source_id = {
            item.id: (
                payload.duration_minutes
                if item.id == moving.id and payload.duration_minutes is not None
                else item.duration_minutes
            )
            for item in items
        }
        day_total_minutes = {
            day: sum(duration_by_source_id[item.id] for item in day_items)
            for day, day_items in scheduled_by_day.items()
        }
        scheduled_after_deadline_count = 0
        scheduled_position = 0
        new_day_by_source_id = {
            item.id: day
            for day, day_items in scheduled_by_day.items()
            for item in day_items
        }
        from_day_index = moving.day_index
        from_duration = moving.duration_minutes
        for item in ordered_scheduled:
            day_index = new_day_by_source_id[item.id]
            duration_minutes = duration_by_source_id[item.id]
            due_at = self._scheduled_due_at(plan_started_at, day_index, days_per_week)
            scheduled_after_deadline = bool(deadline_at and due_at > deadline_at)
            if scheduled_after_deadline:
                scheduled_after_deadline_count += 1
            metadata = copy.deepcopy(item.metadata_json or {})
            metadata["roadmap_version"] = roadmap.version
            metadata["ranking_reason"] = (
                "user_rescheduled" if item.id == moving.id else metadata.get("ranking_reason")
            )
            schedule = dict(metadata.get("schedule") or {})
            schedule.update(
                {
                    "available_minutes_per_day": available_minutes_per_day,
                    "days_per_week": days_per_week,
                    "session_minutes": duration_minutes,
                    "day_total_minutes": day_total_minutes[day_index],
                    "exceeds_daily_capacity": day_total_minutes[day_index]
                    > available_minutes_per_day,
                    "scheduled_after_deadline": scheduled_after_deadline,
                }
            )
            metadata["schedule"] = schedule
            if item.id == moving.id:
                metadata["manual_adjustment"] = {
                    "source_action_id": item.id,
                    "from_day_index": from_day_index,
                    "to_day_index": day_index,
                    "from_duration_minutes": from_duration,
                    "to_duration_minutes": duration_minutes,
                    "rationale": payload.rationale,
                }
            item.day_index = day_index
            item.duration_minutes = duration_minutes
            item.due_at = due_at
            item.position = scheduled_position
            item.metadata_json = metadata
            scheduled_position += 1

        # Keep blocked items attached to their existing day (or after schedule).
        fallback_day = max(scheduled_by_day.keys(), default=1)
        for item in blocked_items:
            metadata = copy.deepcopy(item.metadata_json or {})
            metadata["roadmap_version"] = roadmap.version
            day_index = item.day_index if item.day_index > 0 else fallback_day
            item.day_index = day_index
            item.due_at = self._scheduled_due_at(plan_started_at, day_index, days_per_week)
            item.position = scheduled_position
            item.metadata_json = metadata
            scheduled_position += 1

        adjustments = list(snapshot.get("manual_adjustments") or [])
        adjustments.append(
            {
                "source_roadmap_id": roadmap.id,
                "source_version": roadmap.version,
                "source_action_id": moving.id,
                "node_id": moving.node_id,
                "from_day_index": from_day_index,
                "to_day_index": payload.day_index,
                "from_duration_minutes": from_duration,
                "to_duration_minutes": duration_by_source_id[moving.id],
                "rationale": payload.rationale,
                "actor_id": self.actor_id,
                "created_at": now().isoformat(),
            }
        )
        snapshot.update(
            {
                "revision_kind": "manual_reschedule",
                "manual_adjustments": adjustments,
                "capacity_summary": {
                    "scheduled_item_count": len(ordered_scheduled),
                    "blocked_item_count": len(blocked_items),
                    "scheduled_days": len(
                        {
                            *scheduled_by_day.keys(),
                            *(item.day_index for item in blocked_items if item.day_index > 0),
                        }
                    ),
                    "total_minutes": sum(
                        duration_by_source_id[item.id] for item in ordered_scheduled
                    ),
                    "available_minutes_per_day": available_minutes_per_day,
                    "over_capacity_days": sorted(
                        day
                        for day, total in day_total_minutes.items()
                        if total > available_minutes_per_day
                    ),
                    "scheduled_after_deadline_count": scheduled_after_deadline_count,
                },
            }
        )
        unresolved_gaps = [
            f"Day {day} 超出每日 {available_minutes_per_day} 分钟容量"
            for day, total in sorted(day_total_minutes.items())
            if total > available_minutes_per_day
        ]
        if scheduled_after_deadline_count:
            unresolved_gaps.append(
                f"{scheduled_after_deadline_count} 个行动超出目标截止时间"
            )
        snapshot["unresolved_gaps"] = unresolved_gaps
        roadmap.planning_snapshot = snapshot
        roadmap.rationale = payload.rationale or roadmap.rationale
        self.audit.record(
            actor_id=self.actor_id,
            action="roadmap.item_reschedule",
            resource_type="roadmap",
            resource_id=roadmap.id,
            details={
                "version": roadmap.version,
                "source_action_id": moving.id,
                "node_id": moving.node_id,
                "from_day_index": from_day_index,
                "to_day_index": payload.day_index,
                "duration_minutes": duration_by_source_id[moving.id],
                "mode": "versionless_inplace",
            },
        )
        self.db.commit()
        self.db.refresh(roadmap)
        return roadmap

    def reject_roadmap(self, roadmap_id: str, payload: RoadmapReject) -> Roadmap:
        """Compatibility shim: versionless mode has no reject-review step.

        Legacy clients may still call this endpoint. Replan remains the way to
        replace an unwanted plan.
        """
        roadmap = self._roadmap_record(roadmap_id, "write")
        if payload.base_version != roadmap.version:
            raise AppError(
                409,
                "roadmap_version_conflict",
                "The roadmap changed after this review was opened",
                {"expected_version": payload.base_version, "current_version": roadmap.version},
            )
        raise AppError(
            409,
            "roadmap_versionless_mode",
            "Roadmaps are active without draft review; replan instead of rejecting",
            {"status": roadmap.status, "rationale": payload.rationale},
        )

    def publish_roadmap(self, roadmap_id: str) -> Roadmap:
        """Compatibility shim: replan already activates the roadmap.

        Existing draft rows are promoted in place so older clients keep working.
        """
        roadmap = self._roadmap_record(roadmap_id, "write")
        if roadmap.status == "published":
            return roadmap
        latest_version = self.db.scalar(
            select(func.max(Roadmap.version)).where(
                Roadmap.workspace_id == self.workspace_id,
                Roadmap.goal_id == roadmap.goal_id,
            )
        )
        if latest_version is not None and roadmap.version != latest_version:
            raise AppError(
                409,
                "roadmap_not_latest",
                "Only the latest roadmap can become active",
                {"requested_version": roadmap.version, "latest_version": latest_version},
            )
        if roadmap.status not in {"draft", "published"}:
            raise AppError(
                409,
                "roadmap_not_publishable",
                "Only a draft or active roadmap can be activated",
                {"status": roadmap.status},
            )
        for prior in self.db.scalars(
            select(Roadmap).where(
                Roadmap.workspace_id == self.workspace_id,
                Roadmap.goal_id == roadmap.goal_id,
                Roadmap.status == "published",
                Roadmap.id != roadmap.id,
            )
        ):
            prior.status = "superseded"
        roadmap.status = "published"
        roadmap.published_at = now()
        self.audit.record(
            actor_id=self.actor_id,
            action="roadmap.activate",
            resource_type="roadmap",
            resource_id=roadmap.id,
            details={
                "version": roadmap.version,
                "mode": "versionless_compat_publish",
            },
        )
        self.db.commit()
        self.db.refresh(roadmap)
        return roadmap
