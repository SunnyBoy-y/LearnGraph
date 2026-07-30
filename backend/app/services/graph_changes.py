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

    @staticmethod
    def _has_prerequisite_cycle(prereq_edges: list[tuple[str, str]]) -> bool:
        """Detect directed cycles over prerequisite edges (source -> target)."""

        adjacency: dict[str, list[str]] = {}
        for source, target in prereq_edges:
            adjacency.setdefault(source, []).append(target)
            adjacency.setdefault(target, [])
        visiting: set[str] = set()
        visited: set[str] = set()

        def dfs(node: str) -> bool:
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            for nxt in adjacency.get(node, []):
                if dfs(nxt):
                    return True
            visiting.remove(node)
            visited.add(node)
            return False

        return any(dfs(node) for node in list(adjacency))

    def validate_proposal(
        self,
        proposal: ModelConversationGraphProposal,
        *,
        mode: str,
        graph: Graph | None,
    ) -> None:
        proposal_refs = {node.ref for node in proposal.nodes}
        existing_ids: set[str] = set()
        existing_nodes: list[GraphNode] = []
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
            projected_node_types = {node.ref: node.node_type for node in proposal.nodes}
            projected_labels = {
                self._normalize_label(node.label): node.ref for node in proposal.nodes
            }
            if len(projected_labels) != len(proposal.nodes):
                raise AppError(
                    502,
                    "graph_proposal_duplicate_label",
                    "A new graph proposal contains duplicate or near-duplicate node labels",
                )
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
                    projected_node_types[node.ref] = node.node_type
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

        # Added nodes must attach via at least one edge so the tree does not fragment.
        added_refs = {node.ref for node in proposal.nodes if node.change == "add"}
        if added_refs:
            attached: set[str] = set()
            for edge in proposal.edges:
                if edge.source_ref in added_refs:
                    attached.add(edge.source_ref)
                if edge.target_ref in added_refs:
                    attached.add(edge.target_ref)
            orphaned = sorted(added_refs - attached)
            if orphaned:
                raise AppError(
                    502,
                    "graph_proposal_orphaned_nodes",
                    "Every added node must connect through at least one edge so the graph stays connected",
                    {"orphaned_refs": orphaned},
                )

        # Project prerequisite edges (existing + proposed) and reject cycles.
        prereq_edges: list[tuple[str, str]] = []
        if mode == "update" and graph is not None:
            existing_edges = list(
                self.db.scalars(
                    select(GraphEdge).where(
                        GraphEdge.workspace_id == self.workspace_id,
                        GraphEdge.graph_id == graph.id,
                    )
                ).all()
            )
            for edge in existing_edges:
                if edge.relation == "prerequisite":
                    prereq_edges.append((edge.source_node_id, edge.target_node_id))
        for edge in proposal.edges:
            if edge.relation == "prerequisite":
                prereq_edges.append((edge.source_ref, edge.target_ref))
        if self._has_prerequisite_cycle(prereq_edges):
            raise AppError(
                502,
                "graph_proposal_prerequisite_cycle",
                "The proposal would introduce a prerequisite cycle and break the learning order",
            )

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
    def component_data(item: GraphChangeSet, *, can_undo: bool | None = None) -> dict[str, Any]:
        proposal = ModelConversationGraphProposal.model_validate(item.proposal)
        result = dict(item.result or {})
        status = item.status
        undo_allowed = bool(can_undo) if can_undo is not None else status == "confirmed"
        if status == "proposed":
            allowed_events = ["confirm", "reject"]
        elif status == "confirmed" and undo_allowed:
            # Confirmed proposals keep an undo affordance only while the applied
            # revision is still the graph tip (checked again at undo time).
            allowed_events = ["undo"]
        else:
            allowed_events = []
        props = {
            "proposal_id": item.id,
            "mode": item.mode,
            "graph_id": item.graph_id,
            "goal_id": item.goal_id,
            "base_revision": item.base_revision,
            "confirmed_revision": item.confirmed_revision,
            "title": proposal.graph_title,
            "summary": proposal.summary,
            "status": status,
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
            "confirmation_required": status == "proposed",
            "confirmed_node_ids": result.get("node_ids", {}),
            "rejection_reason": item.rejection_reason,
            "can_undo": status == "confirmed" and undo_allowed,
        }
        return {
            "component_type": "graph_update_proposal",
            "schema_version": "1.0",
            "props": props,
            "allowed_events": allowed_events,
        }

    def bind_component(self, item: GraphChangeSet, part: MessagePartRecord) -> None:
        item.component_part_id = part.id

    def ensure_component_bound(self, item: GraphChangeSet) -> None:
        """Locate and bind the proposal component part when the agent path skipped it.

        Agent tool emission creates the MessagePart via the generic artifact
        path and historically left ``component_part_id`` null. Confirm/reject
        then could not rewrite the durable card snapshot, so the UI still
        showed confirm buttons after acceptance. Resolve by proposal_id.
        """

        if item.component_part_id:
            return
        assistant = self.db.scalar(
            select(Message).where(
                Message.workspace_id == self.workspace_id,
                Message.id == item.source_assistant_message_id,
            )
        )
        if assistant is not None:
            for snapshot in assistant.parts or []:
                if not isinstance(snapshot, dict):
                    continue
                if snapshot.get("type") != "component":
                    continue
                data = snapshot.get("data")
                if not isinstance(data, dict):
                    continue
                if data.get("component_type") != "graph_update_proposal":
                    continue
                props = data.get("props") if isinstance(data.get("props"), dict) else {}
                if props.get("proposal_id") != item.id:
                    continue
                part_id = snapshot.get("id")
                if isinstance(part_id, str) and part_id:
                    item.component_part_id = part_id
                    return
        # Fall back: scan component MessagePart rows for this proposal_id.
        candidates = list(
            self.db.scalars(
                select(MessagePartRecord).where(
                    MessagePartRecord.workspace_id == self.workspace_id,
                    MessagePartRecord.part_type == "component",
                )
            ).all()
        )
        for candidate in candidates:
            data = candidate.data if isinstance(candidate.data, dict) else {}
            if data.get("component_type") != "graph_update_proposal":
                continue
            props = data.get("props") if isinstance(data.get("props"), dict) else {}
            if props.get("proposal_id") == item.id:
                item.component_part_id = candidate.id
                return

    @staticmethod
    def _node_snapshot(node: GraphNode) -> dict[str, Any]:
        return {
            "id": node.id,
            "label": node.label,
            "description": node.description,
            "node_type": node.node_type,
        }

    def _sync_component_snapshot(self, item: GraphChangeSet) -> None:
        self.ensure_component_bound(item)
        can_undo = False
        if item.status == "confirmed" and item.graph_id and item.confirmed_revision is not None:
            graph = self.db.scalar(
                select(Graph).where(
                    Graph.workspace_id == self.workspace_id,
                    Graph.id == item.graph_id,
                )
            )
            can_undo = graph is not None and graph.revision == item.confirmed_revision
        component = self.component_data(item, can_undo=can_undo)
        if item.component_part_id:
            part = self.db.scalar(
                select(MessagePartRecord).where(
                    MessagePartRecord.workspace_id == self.workspace_id,
                    MessagePartRecord.id == item.component_part_id,
                )
            )
            if part is not None:
                part.data = component
        assistant = self.db.scalar(
            select(Message).where(
                Message.workspace_id == self.workspace_id,
                Message.id == item.source_assistant_message_id,
            )
        )
        if assistant is None:
            return
        updated_parts: list[dict[str, Any]] = []
        matched = False
        for snapshot in assistant.parts or []:
            if not isinstance(snapshot, dict):
                updated_parts.append(snapshot)
                continue
            data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else None
            props = data.get("props") if isinstance(data, dict) and isinstance(data.get("props"), dict) else {}
            is_target = False
            if item.component_part_id and snapshot.get("id") == item.component_part_id:
                is_target = True
            elif (
                snapshot.get("type") == "component"
                and isinstance(data, dict)
                and data.get("component_type") == "graph_update_proposal"
                and props.get("proposal_id") == item.id
            ):
                is_target = True
                if not item.component_part_id and isinstance(snapshot.get("id"), str):
                    item.component_part_id = snapshot["id"]
            if is_target:
                matched = True
                updated_parts.append(
                    {
                        **snapshot,
                        "status": "completed",
                        "data": dict(component),
                    }
                )
            else:
                updated_parts.append(snapshot)
        if matched:
            assistant.parts = updated_parts

    def confirm(self, session_id: str, change_set_id: str) -> GraphChangeSet:
        item = self._change_set(session_id, change_set_id)
        self.ensure_component_bound(item)
        if item.status == "confirmed":
            # Still re-sync the durable card so older agent-emitted proposals
            # that never bound component_part_id pick up the accepted UI.
            self._sync_component_snapshot(item)
            self.db.commit()
            self.db.refresh(item)
            return item
        if item.status == "undone":
            raise AppError(409, "graph_change_set_undone", "An undone graph change set cannot be confirmed again; generate a fresh proposal")
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
        self.ensure_component_bound(item)
        if item.status == "rejected":
            self._sync_component_snapshot(item)
            self.db.commit()
            self.db.refresh(item)
            return item
        if item.status == "confirmed":
            raise AppError(409, "graph_change_set_confirmed", "A confirmed graph change set cannot be rejected; use undo instead")
        if item.status == "undone":
            raise AppError(409, "graph_change_set_undone", "An undone graph change set cannot be rejected")
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

    def undo(self, session_id: str, change_set_id: str) -> GraphChangeSet:
        """Reverse a confirmed conversation graph change set if it is still the tip.

        - create mode: delete nodes/edges created by this proposal when the
          resulting graph would otherwise only contain those nodes, then drop
          the empty candidate graph.
        - update mode: delete edges created by this proposal, delete nodes
          added by this proposal, and restore updated node fields from the
          revision before-snapshot. Only allowed when the graph revision is
          still the confirmed tip so later edits are not silently discarded.
        """

        item = self._change_set(session_id, change_set_id)
        self.ensure_component_bound(item)
        if item.status == "undone":
            self._sync_component_snapshot(item)
            self.db.commit()
            self.db.refresh(item)
            return item
        if item.status != "confirmed":
            raise AppError(
                409,
                "graph_change_set_not_confirmed",
                "Only a confirmed graph change set can be undone",
                {"status": item.status},
            )
        if not item.graph_id or item.confirmed_revision is None:
            raise AppError(
                409,
                "graph_change_set_missing_result",
                "This confirmed proposal is missing the applied graph revision metadata",
            )

        graph = self.db.scalar(
            select(Graph).where(
                Graph.workspace_id == self.workspace_id,
                Graph.id == item.graph_id,
            )
        )
        if graph is None:
            raise AppError(404, "graph_not_found", "The proposal graph no longer exists")
        if graph.revision != item.confirmed_revision:
            raise AppError(
                409,
                "graph_revision_conflict",
                "The graph has newer revisions; undo is only available for the latest applied proposal",
                {
                    "expected_revision": item.confirmed_revision,
                    "current_revision": graph.revision,
                },
            )

        revision = self.db.scalar(
            select(GraphRevision).where(
                GraphRevision.workspace_id == self.workspace_id,
                GraphRevision.graph_id == graph.id,
                GraphRevision.revision == item.confirmed_revision,
                GraphRevision.resource_id == item.id,
            )
        )
        if revision is None:
            raise AppError(
                409,
                "graph_revision_missing",
                "The applied graph revision for this proposal could not be found",
            )

        result = dict(item.result or {})
        created_node_ids = {
            node_id
            for ref, node_id in dict(result.get("node_ids") or {}).items()
            if isinstance(node_id, str)
            and any(
                change.change == "add" and change.ref == ref
                for change in ModelConversationGraphProposal.model_validate(item.proposal).nodes
            )
        }
        # Prefer explicit edge ids recorded at confirm time.
        created_edge_ids = {
            edge_id for edge_id in (result.get("edge_ids") or []) if isinstance(edge_id, str)
        }

        before = dict(revision.before or {})
        before_nodes = {
            snap["id"]: snap
            for snap in (before.get("nodes") or [])
            if isinstance(snap, dict) and isinstance(snap.get("id"), str)
        }

        # 1) Delete edges created by this proposal.
        if created_edge_ids:
            edges = list(
                self.db.scalars(
                    select(GraphEdge).where(
                        GraphEdge.workspace_id == self.workspace_id,
                        GraphEdge.graph_id == graph.id,
                        GraphEdge.id.in_(created_edge_ids),
                    )
                ).all()
            )
            for edge in edges:
                self.db.delete(edge)

        # 2) Restore updated nodes from before-snapshot.
        for node_id, snap in before_nodes.items():
            node = self.db.scalar(
                select(GraphNode).where(
                    GraphNode.workspace_id == self.workspace_id,
                    GraphNode.graph_id == graph.id,
                    GraphNode.id == node_id,
                )
            )
            if node is None:
                continue
            if "label" in snap:
                node.label = str(snap["label"] or node.label)
            if "description" in snap:
                node.description = str(snap.get("description") or "")
            if "node_type" in snap and snap["node_type"]:
                node.node_type = str(snap["node_type"])

        # 3) Delete nodes added by this proposal (and any remaining edges on them).
        if created_node_ids:
            dangling_edges = list(
                self.db.scalars(
                    select(GraphEdge).where(
                        GraphEdge.workspace_id == self.workspace_id,
                        GraphEdge.graph_id == graph.id,
                    ).where(
                        (GraphEdge.source_node_id.in_(created_node_ids))
                        | (GraphEdge.target_node_id.in_(created_node_ids))
                    )
                ).all()
            )
            for edge in dangling_edges:
                self.db.delete(edge)
            for node_id in created_node_ids:
                node = self.db.scalar(
                    select(GraphNode).where(
                        GraphNode.workspace_id == self.workspace_id,
                        GraphNode.graph_id == graph.id,
                        GraphNode.id == node_id,
                    )
                )
                if node is not None:
                    self.db.delete(node)

        before_revision = int(before.get("graph_revision") or item.base_revision or 0)
        remaining_nodes = list(
            self.db.scalars(
                select(GraphNode).where(
                    GraphNode.workspace_id == self.workspace_id,
                    GraphNode.graph_id == graph.id,
                )
            ).all()
        )
        session = self._session(session_id)
        deleted_graph = False
        restored_tip: int | None = None
        if item.mode == "create" and not remaining_nodes:
            residual_edges = list(
                self.db.scalars(
                    select(GraphEdge).where(
                        GraphEdge.workspace_id == self.workspace_id,
                        GraphEdge.graph_id == graph.id,
                    )
                ).all()
            )
            for edge in residual_edges:
                self.db.delete(edge)
            # Drop tip revision rows owned by this change set, then the empty graph.
            tip_revisions = list(
                self.db.scalars(
                    select(GraphRevision).where(
                        GraphRevision.workspace_id == self.workspace_id,
                        GraphRevision.graph_id == graph.id,
                        GraphRevision.resource_id == item.id,
                    )
                ).all()
            )
            for tip in tip_revisions:
                self.db.delete(tip)
            self.db.delete(graph)
            deleted_graph = True
            if session.graph_id == item.graph_id:
                session.graph_id = None
            item.graph_id = None
            item.confirmed_revision = None
        else:
            # Advance tip with an undo revision so history stays monotonic and
            # subsequent proposals target the restored tip cleanly.
            undo_revision_number = graph.revision + 1
            self.db.add(
                GraphRevision(
                    workspace_id=self.workspace_id,
                    graph_id=graph.id,
                    revision=undo_revision_number,
                    change_type="conversation_graph_undo",
                    resource_id=item.id,
                    before={
                        "graph_revision": graph.revision,
                        "undone_change_set_id": item.id,
                        "undone_confirmed_revision": item.confirmed_revision,
                    },
                    after={
                        "graph_revision": undo_revision_number,
                        "content_restored_to_revision": before_revision,
                        "restored_nodes": list(before_nodes.values()),
                        "deleted_node_ids": sorted(created_node_ids),
                        "deleted_edge_ids": sorted(created_edge_ids),
                    },
                    actor_id=self.actor_id,
                )
            )
            graph.revision = undo_revision_number
            restored_tip = undo_revision_number
            item.confirmed_revision = None

        item.status = "undone"
        item.reviewed_by = self.actor_id
        item.reviewed_at = datetime.now(timezone.utc)
        item.result = {
            **result,
            "undone": True,
            "deleted_graph": deleted_graph,
            "restored_tip_revision": restored_tip,
            "content_restored_to_revision": before_revision if not deleted_graph else None,
        }
        self._sync_component_snapshot(item)
        self.audit.record(
            actor_id=self.actor_id,
            action="graph.change_set_undone",
            resource_type="graph_change_set",
            resource_id=item.id,
            details={
                "session_id": session_id,
                "graph_id": graph.id if not deleted_graph else None,
                "deleted_graph": deleted_graph,
                "restored_tip_revision": restored_tip,
                "content_restored_to_revision": before_revision if not deleted_graph else None,
                "deleted_node_count": len(created_node_ids),
                "deleted_edge_count": len(created_edge_ids),
            },
        )
        self.db.commit()
        self.db.refresh(item)
        return item
