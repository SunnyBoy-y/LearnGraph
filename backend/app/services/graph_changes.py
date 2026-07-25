from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import (
    ChatSession,
    Goal,
    Graph,
    GraphChangeSet,
    GraphEdge,
    GraphNode,
    GraphRevision,
    Message,
    MessagePartRecord,
)
from app.domain.schemas.graphs import ModelConversationGraphProposal
from app.repositories.audit import AuditRepository
from app.repositories.domain import GraphChangeSetRepository


class GraphChangeSetService:
    """Persist, review, and atomically apply conversation graph proposals."""

    def __init__(self, db: Session, workspace_id: str, actor_id: str) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.change_sets = GraphChangeSetRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)

    @staticmethod
    def _normalize_label(label: str) -> str:
        """Collapse trivial label variants so near-duplicate adds are rejected."""
        return " ".join((label or "").casefold().split())

    def _session(self, session_id: str) -> ChatSession:
        session = self.db.scalar(
            select(ChatSession).where(
                ChatSession.workspace_id == self.workspace_id,
                ChatSession.id == session_id,
            )
        )
        if session is None:
            raise AppError(404, "not_found", "session not found in this workspace")
        return session

    def _change_set(self, session_id: str, change_set_id: str) -> GraphChangeSet:
        self._session(session_id)
        item = self.change_sets.require(change_set_id, "graph change set")
        if item.session_id != session_id:
            raise AppError(
                404,
                "graph_change_set_not_in_session",
                "Graph change set does not belong to this session",
            )
        return item

    def ensure_can_propose(self, session_id: str) -> None:
        self._session(session_id)
        pending = self.db.scalar(
            self.change_sets.query().where(
                GraphChangeSet.session_id == session_id,
                GraphChangeSet.status == "proposed",
            )
        )
        if pending is not None:
            raise AppError(
                409,
                "graph_change_pending_review",
                "Confirm or reject the current graph proposal before generating another one",
                {"proposal_id": pending.id},
            )

    def list_for_session(self, session_id: str) -> list[GraphChangeSet]:
        self._session(session_id)
        return list(
            self.db.scalars(
                self.change_sets.query()
                .where(GraphChangeSet.session_id == session_id)
                .order_by(GraphChangeSet.created_at.desc())
            ).all()
        )

    def validate_proposal(
        self,
        proposal: ModelConversationGraphProposal,
        *,
        mode: str,
        graph: Graph | None,
    ) -> None:
        proposal_refs = {node.ref for node in proposal.nodes}
        if mode == "create":
            if graph is not None:
                raise AppError(409, "graph_create_target_invalid", "A create proposal cannot target an existing graph")
            if len(proposal.nodes) < 2:
                raise AppError(502, "graph_proposal_invalid", "A new candidate graph requires at least two nodes")
            if any(node.change != "add" for node in proposal.nodes):
                raise AppError(502, "graph_proposal_invalid", "A new graph proposal may only add nodes")
            roots = [node for node in proposal.nodes if node.node_type == "root"]
            if len(roots) != 1:
                raise AppError(502, "graph_proposal_invalid", "A new graph proposal requires exactly one root node")
            allowed_refs = proposal_refs
        elif mode == "update":
            if graph is None:
                raise AppError(409, "graph_update_target_required", "An update proposal requires a graph")
            existing_nodes = list(
                self.db.scalars(
                    select(GraphNode).where(
                        GraphNode.workspace_id == self.workspace_id,
                        GraphNode.graph_id == graph.id,
                    )
                ).all()
            )
            existing_ids = {node.id for node in existing_nodes}
            existing_labels = {
                self._normalize_label(node.label): node.id for node in existing_nodes
            }
            projected_node_types = {node.id: node.node_type for node in existing_nodes}
            projected_labels: dict[str, str] = dict(existing_labels)
            updated_node_ids: set[str] = set()
            for node in proposal.nodes:
                if node.change == "update" and node.node_id not in existing_ids:
                    raise AppError(
                        502,
                        "graph_proposal_out_of_scope",
                        "The model attempted to update a node outside the target graph",
                        {"node_id": node.node_id},
                    )
                if node.change == "update":
                    assert node.node_id is not None
                    if node.node_id in updated_node_ids:
                        raise AppError(
                            502,
                            "graph_proposal_invalid",
                            "A proposal cannot update the same graph node more than once",
                            {"node_id": node.node_id},
                        )
                    updated_node_ids.add(node.node_id)
                    projected_node_types[node.node_id] = node.node_type
                    normalized = self._normalize_label(node.label)
                    owner = projected_labels.get(normalized)
                    if owner is not None and owner != node.node_id:
                        raise AppError(
                            502,
                            "graph_proposal_duplicate_label",
                            "An update would create a duplicate label already present on another node",
                            {"node_id": node.node_id, "label": node.label, "existing_node_id": owner},
                        )
                    # Drop the previous normalized label of this node so renames free the old key.
                    for key, owner_id in list(projected_labels.items()):
                        if owner_id == node.node_id:
                            del projected_labels[key]
                    projected_labels[normalized] = node.node_id
                if node.change == "add" and node.node_type == "root":
                    raise AppError(502, "graph_proposal_invalid", "An incremental update cannot add another root node")
                if node.change == "add":
                    normalized = self._normalize_label(node.label)
                    owner = projected_labels.get(normalized)
                    if owner is not None:
                        raise AppError(
                            502,
                            "graph_proposal_duplicate_label",
                            "An added node duplicates an existing concept; update that node instead of re-adding it",
                            {"label": node.label, "existing_node_id": owner},
                        )
                    projected_labels[normalized] = node.ref
                if node.ref in existing_ids:
                    raise AppError(502, "graph_proposal_invalid", "A proposal ref cannot shadow an existing node ID")
            if sum(node_type == "root" for node_type in projected_node_types.values()) != 1:
                raise AppError(
                    502,
                    "graph_proposal_invalid",
                    "An incremental update must preserve exactly one root node",
                )
            allowed_refs = proposal_refs | existing_ids
        else:
            raise AppError(422, "graph_change_mode_invalid", "Unsupported graph change mode")

        edge_keys: set[tuple[str, str, str]] = set()
        for edge in proposal.edges:
            if edge.source_ref not in allowed_refs or edge.target_ref not in allowed_refs:
                raise AppError(
                    502,
                    "graph_proposal_out_of_scope",
                    "A proposed edge references a node outside the proposal and target graph",
                    {"source_ref": edge.source_ref, "target_ref": edge.target_ref},
                )
            key = (edge.source_ref, edge.target_ref, edge.relation)
            if key in edge_keys:
                raise AppError(502, "graph_proposal_invalid", "A graph proposal contains a duplicate edge")
            edge_keys.add(key)

    def create_proposal(
        self,
        *,
        session: ChatSession,
        goal: Goal,
        graph: Graph | None,
        source_user_message: Message,
        source_assistant_message: Message,
        mode: str,
        base_revision: int,
        proposal: ModelConversationGraphProposal,
        provider_trace: dict[str, Any],
    ) -> GraphChangeSet:
        self.validate_proposal(proposal, mode=mode, graph=graph)
        item = self.change_sets.add(
            GraphChangeSet(
                workspace_id=self.workspace_id,
                session_id=session.id,
                goal_id=goal.id,
                graph_id=graph.id if graph else None,
                source_user_message_id=source_user_message.id,
                source_assistant_message_id=source_assistant_message.id,
                mode=mode,
                status="proposed",
                base_revision=base_revision,
                proposal=proposal.model_dump(mode="json"),
                provider_trace=dict(provider_trace),
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="graph.change_set_proposed",
            resource_type="graph_change_set",
            resource_id=item.id,
            details={
                "session_id": session.id,
                "goal_id": goal.id,
                "graph_id": graph.id if graph else None,
                "mode": mode,
                "base_revision": base_revision,
                "source_user_message_id": source_user_message.id,
                "source_assistant_message_id": source_assistant_message.id,
                "provider_id": provider_trace.get("provider_id"),
            },
        )
        return item

    @staticmethod
    def component_data(item: GraphChangeSet) -> dict[str, Any]:
        proposal = ModelConversationGraphProposal.model_validate(item.proposal)
        result = dict(item.result or {})
        props = {
            "proposal_id": item.id,
            "mode": item.mode,
            "graph_id": item.graph_id,
            "goal_id": item.goal_id,
            "base_revision": item.base_revision,
            "confirmed_revision": item.confirmed_revision,
            "title": proposal.graph_title,
            "summary": proposal.summary,
            "status": item.status,
            "nodes": [
                {
                    "id": node.node_id or node.ref,
                    "ref": node.ref,
                    "node_id": node.node_id,
                    "label": node.label,
                    "description": node.description,
                    "node_type": node.node_type,
                    "change": node.change,
                    "rationale": node.rationale,
                }
                for node in proposal.nodes
            ],
            "edges": [edge.model_dump(mode="json") for edge in proposal.edges],
            "confirmation_required": item.status == "proposed",
            "confirmed_node_ids": result.get("node_ids", {}),
            "rejection_reason": item.rejection_reason,
        }
        return {
            "component_type": "graph_update_proposal",
            "schema_version": "1.0",
            "props": props,
            "allowed_events": ["confirm", "reject"] if item.status == "proposed" else [],
        }

    def bind_component(self, item: GraphChangeSet, part: MessagePartRecord) -> None:
        item.component_part_id = part.id

    @staticmethod
    def _node_snapshot(node: GraphNode) -> dict[str, Any]:
        return {
            "id": node.id,
            "label": node.label,
            "description": node.description,
            "node_type": node.node_type,
        }

    def _sync_component_snapshot(self, item: GraphChangeSet) -> None:
        if not item.component_part_id:
            return
        part = self.db.scalar(
            select(MessagePartRecord).where(
                MessagePartRecord.workspace_id == self.workspace_id,
                MessagePartRecord.id == item.component_part_id,
            )
        )
        if part is None:
            return
        part.data = self.component_data(item)
        assistant = self.db.scalar(
            select(Message).where(
                Message.workspace_id == self.workspace_id,
                Message.id == item.source_assistant_message_id,
            )
        )
        if assistant is None:
            return
        updated_parts: list[dict[str, Any]] = []
        for snapshot in assistant.parts or []:
            if snapshot.get("id") == part.id:
                updated_parts.append(
                    {
                        **snapshot,
                        "status": "completed",
                        "data": dict(part.data),
                    }
                )
            else:
                updated_parts.append(snapshot)
        assistant.parts = updated_parts

    def confirm(self, session_id: str, change_set_id: str) -> GraphChangeSet:
        item = self._change_set(session_id, change_set_id)
        if item.status == "confirmed":
            return item
        if item.status == "rejected":
            raise AppError(409, "graph_change_set_rejected", "A rejected graph change set cannot be confirmed")

        session = self._session(session_id)
        goal = self.db.scalar(
            select(Goal).where(
                Goal.workspace_id == self.workspace_id,
                Goal.id == item.goal_id,
            )
        )
        if goal is None:
            raise AppError(404, "goal_not_found", "The proposal goal no longer exists")
        proposal = ModelConversationGraphProposal.model_validate(item.proposal)
        graph: Graph | None = None
        if item.mode == "update":
            graph = self.db.scalar(
                select(Graph).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.id == item.graph_id,
                )
            )
            if graph is None:
                raise AppError(404, "graph_not_found", "The proposal graph no longer exists")
            if graph.goal_id != goal.id:
                raise AppError(409, "graph_goal_mismatch", "The target graph no longer belongs to the proposal goal")
            if graph.revision != item.base_revision:
                raise AppError(
                    409,
                    "graph_revision_conflict",
                    "The graph changed after this proposal was generated; generate a fresh proposal",
                    {"expected_revision": item.base_revision, "current_revision": graph.revision},
                )
        elif goal.status not in {"confirmed", "candidate_ready", "approved"}:
            raise AppError(
                409,
                "goal_not_confirmed_for_graph",
                "Confirm the Goal before materializing a candidate graph",
            )

        self.validate_proposal(proposal, mode=item.mode, graph=graph)
        before_revision = graph.revision if graph else 0
        before_nodes: list[dict[str, Any]] = []
        if graph is None:
            graph = Graph(
                workspace_id=self.workspace_id,
                goal_id=goal.id,
                title=proposal.graph_title,
                status="candidate",
                revision=1,
            )
            self.db.add(graph)
            self.db.flush()
            confirmed_revision = 1
            if goal.status == "confirmed":
                goal.status = "candidate_ready"
        else:
            graph.revision += 1
            confirmed_revision = graph.revision

        ref_to_node_id: dict[str, str] = {}
        changed_nodes: list[GraphNode] = []
        for change in proposal.nodes:
            if change.change == "add":
                node = GraphNode(
                    workspace_id=self.workspace_id,
                    graph_id=graph.id,
                    label=change.label,
                    description=change.description,
                    node_type=change.node_type,
                    retrieval_state="unverified",
                    evidence_state="none",
                )
                self.db.add(node)
                self.db.flush()
            else:
                node = self.db.scalar(
                    select(GraphNode).where(
                        GraphNode.workspace_id == self.workspace_id,
                        GraphNode.graph_id == graph.id,
                        GraphNode.id == change.node_id,
                    )
                )
                if node is None:
                    raise AppError(409, "graph_node_changed", "A proposed graph node no longer exists")
                before_nodes.append(self._node_snapshot(node))
                node.label = change.label
                node.description = change.description
                node.node_type = change.node_type
            ref_to_node_id[change.ref] = node.id
            changed_nodes.append(node)

        existing_node_ids = set(
            self.db.scalars(
                select(GraphNode.id).where(
                    GraphNode.workspace_id == self.workspace_id,
                    GraphNode.graph_id == graph.id,
                )
            ).all()
        )

        def resolve_ref(ref: str) -> str:
            if ref in ref_to_node_id:
                return ref_to_node_id[ref]
            if ref in existing_node_ids:
                return ref
            raise AppError(409, "graph_edge_target_changed", "A proposed graph edge target no longer exists")

        existing_edge_keys = set(
            self.db.execute(
                select(GraphEdge.source_node_id, GraphEdge.target_node_id, GraphEdge.relation).where(
                    GraphEdge.workspace_id == self.workspace_id,
                    GraphEdge.graph_id == graph.id,
                )
            ).all()
        )
        created_edges: list[GraphEdge] = []
        for edge_change in proposal.edges:
            source_id = resolve_ref(edge_change.source_ref)
            target_id = resolve_ref(edge_change.target_ref)
            key = (source_id, target_id, edge_change.relation)
            if key in existing_edge_keys:
                continue
            edge = GraphEdge(
                workspace_id=self.workspace_id,
                graph_id=graph.id,
                source_node_id=source_id,
                target_node_id=target_id,
                relation=edge_change.relation,
            )
            self.db.add(edge)
            self.db.flush()
            created_edges.append(edge)
            existing_edge_keys.add(key)

        after_nodes = [self._node_snapshot(node) for node in changed_nodes]
        revision = GraphRevision(
            workspace_id=self.workspace_id,
            graph_id=graph.id,
            revision=confirmed_revision,
            change_type=(
                "conversation_graph_create" if item.mode == "create" else "conversation_graph_update"
            ),
            resource_id=item.id,
            before={
                "graph_revision": before_revision,
                "nodes": before_nodes,
            },
            after={
                "graph_revision": confirmed_revision,
                "summary": proposal.summary,
                "nodes": after_nodes,
                "edges": [
                    {
                        "id": edge.id,
                        "source_node_id": edge.source_node_id,
                        "target_node_id": edge.target_node_id,
                        "relation": edge.relation,
                    }
                    for edge in created_edges
                ],
                "evidence_refs": {
                    "session_id": session.id,
                    "user_message_id": item.source_user_message_id,
                    "assistant_message_id": item.source_assistant_message_id,
                    "graph_change_set_id": item.id,
                },
                "mastery_evidence_created": False,
            },
            actor_id=self.actor_id,
        )
        self.db.add(revision)

        item.graph_id = graph.id
        item.status = "confirmed"
        item.confirmed_revision = confirmed_revision
        item.reviewed_by = self.actor_id
        item.reviewed_at = datetime.now(timezone.utc)
        item.result = {
            "node_ids": ref_to_node_id,
            "edge_ids": [edge.id for edge in created_edges],
        }
        session.graph_id = graph.id
        if session.goal_id is None:
            session.goal_id = goal.id
        self._sync_component_snapshot(item)
        self.audit.record(
            actor_id=self.actor_id,
            action="graph.change_set_confirmed",
            resource_type="graph_change_set",
            resource_id=item.id,
            details={
                "session_id": session.id,
                "goal_id": goal.id,
                "graph_id": graph.id,
                "revision": confirmed_revision,
                "node_change_count": len(proposal.nodes),
                "edge_add_count": len(created_edges),
                "source_user_message_id": item.source_user_message_id,
                "source_assistant_message_id": item.source_assistant_message_id,
                "mastery_evidence_created": False,
            },
        )
        self.db.commit()
        self.db.refresh(item)
        return item

    def reject(self, session_id: str, change_set_id: str, reason: str = "") -> GraphChangeSet:
        item = self._change_set(session_id, change_set_id)
        if item.status == "rejected":
            return item
        if item.status == "confirmed":
            raise AppError(409, "graph_change_set_confirmed", "A confirmed graph change set cannot be rejected")
        item.status = "rejected"
        item.reviewed_by = self.actor_id
        item.reviewed_at = datetime.now(timezone.utc)
        item.rejection_reason = reason.strip()
        self._sync_component_snapshot(item)
        self.audit.record(
            actor_id=self.actor_id,
            action="graph.change_set_rejected",
            resource_type="graph_change_set",
            resource_id=item.id,
            details={"session_id": session_id, "reason": item.rejection_reason},
        )
        self.db.commit()
        self.db.refresh(item)
        return item
