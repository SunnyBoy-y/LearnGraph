from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select, update as sql_update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.memory_event_models import MemorySearchDocument
from app.domain.memory_types import (
    MEMORY_TYPE_REGISTRY,
    compute_memory_strength,
    default_decay_rate,
    get_memory_type,
    normalize_scope,
    validate_not_canonical_state_payload,
)
from app.domain.models import (
    ChatSession,
    Goal,
    Graph,
    GraphNode,
    MemoryDeletionRecovery,
    MemoryDraft,
    MemoryEvidence,
    MemoryJournalEntry,
    MemoryProfileSnapshot,
    MemoryProviderBinding,
    MemoryRecord,
    MemoryRevision,
    Message,
    ProviderConfig,
    Workspace,
    WorkspaceSetting,
    utc_now,
)
from app.domain.schemas.management import (
    EffectiveMemoryPackageView,
    MemoryBindingView,
    MemoryCreateRequest,
    MemoryDraftCreateRequest,
    MemoryDraftDecisionRequest,
    MemoryDraftView,
    MemoryJournalView,
    MemoryPolicyUpdateRequest,
    MemoryPolicyView,
    MemoryProviderStatusView,
    MemoryRevisionRestoreRequest,
    MemoryRevisionView,
    MemoryTypeDefinitionView,
    MemoryUpdateRequest,
    MemoryView,
)
from app.providers.local.memory import LocalWorkspaceMemoryProvider
from app.providers.ports.memory import CanonicalMemory, MemoryProviderPort
from app.repositories.audit import AuditRepository
from app.repositories.domain import (
    GoalRepository,
    GraphNodeRepository,
    GraphRepository,
    MemoryBindingRepository,
    MemoryDraftRepository,
    MemoryJournalRepository,
    MemoryRecoveryRepository,
    MemoryRepository,
    MemoryRevisionRepository,
    SessionRepository,
    SettingRepository,
)
from app.services.memory_vault import MemoryRecoveryVault
from app.services.memory_zones import ReconcileZonesReport, reconcile_memory_zones
from app.services.token_estimate import estimate_tokens


logger = logging.getLogger(__name__)

RECOVERY_WINDOW = timedelta(minutes=30)
DELETION_AUDIT_RETENTION = timedelta(days=7)
MEMORY_POLICY_KEY = "memory.shared_policy"
MEMORY_PROVIDER_EPOCH_KEY = "memory.provider_epoch"

_LEDGER_STATUSES = {"active", "superseded", "retracted", "deleted"}
_TEMPORAL_STATUSES = {
    "timeless",
    "planned",
    "ongoing",
    "completed",
    "cancelled",
    "rescheduled",
    "lapsed_unverified",
    "expired",
}
_SUMMARY_ELIGIBILITY = {
    "durable",
    "current",
    "historical",
    "excluded",
    "legacy_review",
}


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _memory_id() -> str:
    return f"lgm_{uuid4()}"


def _render_markdown(title: str, content: str) -> str:
    return f"# {title.strip()}\n\n{content.rstrip()}\n"


def _content_hash(title: str, content: str) -> str:
    return hashlib.sha256(_render_markdown(title, content).encode("utf-8")).hexdigest()


def _legacy_body(markdown: str) -> str:
    lines = [line for line in markdown.splitlines() if not line.startswith("<!-- lg_memory")]
    if lines and lines[0].startswith("# "):
        lines = lines[1:]
    while lines and not lines[0].strip():
        lines.pop(0)
    return "\n".join(lines).rstrip()


def _structured_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _atomic_fields(
    structured: dict,
    source_ids: list[str],
    *,
    default_eligible: bool = False,
) -> dict:
    """Normalize indexable atom lifecycle fields from validated LLM output."""

    version = int(structured.get("atom_schema_version") or 0)
    ledger_status = str(structured.get("ledger_status") or "active")
    temporal_status = str(structured.get("temporal_status") or "timeless")
    summary_eligibility = str(
        structured.get("summary_eligibility")
        or (
            "durable"
            if default_eligible or (version >= 1 and source_ids)
            else "excluded"
        )
    )
    return {
        "atom_schema_version": max(0, version),
        "canonical_key": str(structured.get("canonical_key") or "")[:240],
        "atom_kind": str(structured.get("atom_kind") or "fact")[:64],
        "ledger_status": (
            ledger_status if ledger_status in _LEDGER_STATUSES else "active"
        ),
        "temporal_status": (
            temporal_status
            if temporal_status in _TEMPORAL_STATUSES
            else "timeless"
        ),
        "summary_eligibility": (
            summary_eligibility
            if summary_eligibility in _SUMMARY_ELIGIBILITY
            else "excluded"
        ),
        "valid_from": _structured_datetime(structured.get("valid_from")),
        "valid_until": _structured_datetime(structured.get("valid_until")),
        "event_at": _structured_datetime(structured.get("event_at")),
        "next_review_at": _structured_datetime(structured.get("next_review_at")),
        "last_verified_at": _structured_datetime(
            structured.get("last_verified_at")
        ),
        "timezone_name": str(
            structured.get("timezone_name") or "Asia/Shanghai"
        )[:80],
        "evidence_ids": list(
            dict.fromkeys(
                str(value)
                for value in (structured.get("evidence_ids") or source_ids)
                if value
            )
        ),
    }


class MemoryService:
    def __init__(
        self,
        db: Session,
        workspace: Workspace,
        actor_id: str,
        provider: MemoryProviderPort,
        memory_root: Path,
    ) -> None:
        self.db = db
        self.workspace = workspace
        self.workspace_id = workspace.id
        self.actor_id = actor_id
        self.provider = provider
        self.local_projection = LocalWorkspaceMemoryProvider(memory_root, workspace.id)
        self.vault = MemoryRecoveryVault(memory_root, workspace.id)
        self.memories = MemoryRepository(db, workspace.id)
        self.drafts = MemoryDraftRepository(db, workspace.id)
        self.revisions = MemoryRevisionRepository(db, workspace.id)
        self.journal = MemoryJournalRepository(db, workspace.id)
        self.bindings = MemoryBindingRepository(db, workspace.id)
        self.recoveries = MemoryRecoveryRepository(db, workspace.id)
        self.sessions = SessionRepository(db, workspace.id)
        self.settings = SettingRepository(db, workspace.id)
        self.goals = GoalRepository(db, workspace.id)
        self.graphs = GraphRepository(db, workspace.id)
        self.nodes = GraphNodeRepository(db, workspace.id)
        self.audit = AuditRepository(db, workspace.id)

    def _event_store(self):
        from app.core.config import get_settings
        from app.services.memory_event_ingestor import event_cipher_from_settings
        from app.services.memory_event_store import MemoryEventStore

        return MemoryEventStore(self.db, event_cipher_from_settings(get_settings()))

    def _event_scope(self, record: MemoryRecord | None = None):
        from app.domain.memory_event_models import MemoryScopeContext

        return MemoryScopeContext(
            tenant_id=self.workspace.tenant_id,
            principal_user_id=(
                record.subject_user_id
                if record is not None and record.subject_user_id
                else self.actor_id
            ),
            workspace_id=self.workspace_id,
            task_id=record.task_id if record is not None else None,
            project_id=record.project_id if record is not None else None,
            conversation_id=(
                record.conversation_id or record.session_id if record is not None else None
            ),
            goal_id=record.goal_id if record is not None else None,
        )

    def _mirror_event(self, record: MemoryRecord, content: str, operation: str) -> None:
        from app.core.config import get_settings
        from app.services.memory_commands import MemoryCommandService

        if get_settings().memory_write_mode == "legacy":
            return
        command_service = MemoryCommandService(self.db, self._event_store())
        event = command_service.mirror_legacy_record(
            self._event_scope(record),
            record,
            content=content,
            operation=operation,
            actor_id=self.actor_id,
            idempotency_key=f"memory:{record.id}:{record.revision}:{operation}",
        )
        from app.services.memory_projector import MemoryProjector

        MemoryProjector(self.db).apply(
            event,
            command_service._payload(record, content),
        )

    def _project_after_commit(
        self,
        record: MemoryRecord,
        canonical: CanonicalMemory,
        *,
        previous_provider_id: str | None = None,
        previous_binding_id: str | None = None,
    ) -> None:
        """Best-effort synchronous projection after the canonical commit.

        The durable Outbox remains queued as the retry source. This fast path
        preserves the existing UI/readback behavior when the provider is healthy.
        """

        try:
            binding_id = (
                record.provider_binding_id
                if record.provider_id == self.provider.provider_id
                else None
            )
            result = self.provider.upsert(canonical, provider_record_id=binding_id)
            if (
                previous_provider_id == self.local_projection.provider_id
                and previous_binding_id
                and previous_binding_id != result.provider_record_id
            ):
                try:
                    self.local_projection.delete(previous_binding_id)
                except Exception:
                    logger.warning(
                        "Stale local memory projection %s could not be removed",
                        previous_binding_id,
                        exc_info=True,
                    )
            if previous_provider_id and previous_provider_id != self.provider.provider_id:
                prior_bindings = self.db.scalars(
                    self.bindings.query().where(
                        MemoryProviderBinding.memory_id == record.id,
                        MemoryProviderBinding.provider_instance_id == previous_provider_id,
                        MemoryProviderBinding.binding_status == "verified",
                    )
                ).all()
                for binding in prior_bindings:
                    binding.binding_status = (
                        "superseded"
                        if previous_provider_id == self.local_projection.provider_id
                        else "orphaned"
                    )
            record.provider_id = self.provider.provider_id
            record.provider_binding_id = result.provider_record_id
            record.relative_path = result.relative_path
            self._add_binding(
                record=record,
                revision=canonical.revision,
                result=result,
                now=utc_now(),
            )
            journal = self.db.scalar(
                self.journal.query()
                .where(
                    MemoryJournalEntry.memory_id == record.id,
                    MemoryJournalEntry.revision == canonical.revision,
                )
                .order_by(MemoryJournalEntry.created_at.desc())
            )
            if journal is not None:
                journal.provider_id = self.provider.provider_id
                journal.provider_record_id = result.provider_record_id
            from app.domain.memory_event_models import MemoryProjectionOutbox

            completed_kind = (
                "mem0" if self.provider.remote_capability else "markdown"
            )
            queued_jobs = self.db.scalars(
                select(MemoryProjectionOutbox).where(
                    MemoryProjectionOutbox.aggregate_id == record.id,
                    MemoryProjectionOutbox.projection_kind == completed_kind,
                    MemoryProjectionOutbox.status.in_(("queued", "failed")),
                )
            ).all()
            for job in queued_jobs:
                job.status = "succeeded"
                job.last_error = ""
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.warning(
                "Memory %s committed locally; provider projection remains queued",
                record.id,
                exc_info=True,
            )

    def _delete_projection_after_commit(
        self,
        *,
        memory_id: str,
        provider_id: str,
        provider_binding_id: str | None,
    ) -> None:
        if not provider_binding_id:
            return
        try:
            if provider_id == self.provider.provider_id:
                self.provider.delete(provider_binding_id)
            elif provider_id == self.local_projection.provider_id:
                self.local_projection.delete(provider_binding_id)
            bindings = self.db.scalars(
                self.bindings.query().where(MemoryProviderBinding.memory_id == memory_id)
            ).all()
            for binding in bindings:
                binding.binding_status = "deleted"
            from app.domain.memory_event_models import MemoryProjectionOutbox

            completed_kind = (
                "mem0"
                if provider_id == self.provider.provider_id
                and self.provider.remote_capability
                else "markdown"
            )
            jobs = self.db.scalars(
                select(MemoryProjectionOutbox).where(
                    MemoryProjectionOutbox.aggregate_id == memory_id,
                    MemoryProjectionOutbox.projection_kind == completed_kind,
                    MemoryProjectionOutbox.status.in_(("queued", "failed")),
                )
            ).all()
            for job in jobs:
                job.status = "succeeded"
                job.last_error = ""
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.warning(
                "Memory %s was deleted locally; provider deletion remains queued",
                memory_id,
                exc_info=True,
            )

    def _provider_epoch(self) -> int:
        setting = self.db.scalar(
            self.settings.query().where(WorkspaceSetting.key == MEMORY_PROVIDER_EPOCH_KEY)
        )
        try:
            return max(1, int(setting.value)) if setting is not None else 1
        except (TypeError, ValueError):
            return 1

    def _latest_revision(self, memory_id: str) -> MemoryRevision | None:
        return self.db.scalar(
            self.revisions.query()
            .where(MemoryRevision.memory_id == memory_id)
            .order_by(MemoryRevision.revision.desc())
            .limit(1)
        )

    def _ensure_revision(self, record: MemoryRecord) -> MemoryRevision | None:
        revision = self._latest_revision(record.id)
        if revision is not None or record.state != "active":
            return revision
        markdown = self.local_projection.read_legacy(record.relative_path)
        body = _legacy_body(markdown)
        digest = _content_hash(record.title, body)
        record.content_hash = digest
        record.provider_id = record.provider_id or self.local_projection.provider_id
        revision = self.revisions.add(
            MemoryRevision(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=record.revision,
                base_revision=None,
                operation="ADD",
                title=record.title,
                content=body,
                content_hash=digest,
                namespace=record.namespace,
                session_id=record.session_id,
                record_kind=record.record_kind,
                zone=record.zone,
                source=record.source,
                source_ids=list(record.source_ids or []),
                actor_id="legacy_migration",
                reason="lazy_provider_neutral_journal_bootstrap",
                is_active=True,
            )
        )
        self.journal.add(
            MemoryJournalEntry(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=record.revision,
                operation="ADD",
                provider_id=record.provider_id,
                provider_epoch=self._provider_epoch(),
                provider_record_id=record.provider_binding_id or record.relative_path,
                content_hash=digest,
                payload=self._journal_payload(record, body),
            )
        )
        return revision

    @staticmethod
    def _journal_payload(record: MemoryRecord, content: str) -> dict:
        return {
            "lg_memory_id": record.id,
            "revision": record.revision,
            "title": record.title,
            "content": content,
            "namespace": record.namespace,
            "session_id": record.session_id,
            "scope_type": getattr(record, "scope_type", "workspace") or "workspace",
            "scope_id": getattr(record, "scope_id", None),
            "goal_id": getattr(record, "goal_id", None),
            "node_id": getattr(record, "node_id", None),
            "record_kind": record.record_kind,
            "zone": record.zone,
            "source": record.source,
            "source_ids": list(record.source_ids or []),
            "structured_payload": dict(getattr(record, "structured_payload", None) or {}),
            "resolution_status": getattr(record, "resolution_status", "none") or "none",
            "atom_schema_version": int(
                getattr(record, "atom_schema_version", 0) or 0
            ),
            "canonical_key": getattr(record, "canonical_key", "") or "",
            "atom_kind": getattr(record, "atom_kind", "fact") or "fact",
            "ledger_status": getattr(record, "ledger_status", "active") or "active",
            "temporal_status": getattr(record, "temporal_status", "timeless")
            or "timeless",
            "summary_eligibility": getattr(
                record, "summary_eligibility", "legacy_review"
            )
            or "legacy_review",
            "evidence_ids": list(getattr(record, "evidence_ids", None) or []),
        }

    def _mark_profile_stale(self, reason: str) -> None:
        snapshots = self.db.scalars(
            select(MemoryProfileSnapshot).where(
                MemoryProfileSnapshot.workspace_id == self.workspace_id,
                MemoryProfileSnapshot.status == "ready",
            )
        ).all()
        for snapshot in snapshots:
            snapshot.status = "stale"
            snapshot.stale_reason = reason[:240]

    def _apply_type_defaults(self, record: MemoryRecord) -> None:
        type_def = get_memory_type(record.record_kind)
        if not getattr(record, "merge_strategy", None):
            record.merge_strategy = type_def.merge_strategy
        if not getattr(record, "decay_policy", None) or record.decay_policy == "SLOW":
            # Keep explicit non-default policies; seed registry default on create.
            pass
        if not getattr(record, "scope_type", None):
            record.scope_type = type_def.default_scope

    def _canonical(
        self,
        record: MemoryRecord,
        *,
        title: str,
        content: str,
        revision: int,
        zone: str,
        source_ids: list[str],
        now: datetime,
    ) -> CanonicalMemory:
        return CanonicalMemory(
            memory_id=record.id,
            revision=revision,
            title=title,
            content=content,
            content_hash=_content_hash(title, content),
            namespace=record.namespace,
            session_id=record.session_id,
            record_kind=record.record_kind,
            zone=zone,
            state="active",
            source=record.source,
            source_ids=tuple(source_ids),
            origin_created_at=_as_utc(record.created_at or now),
            origin_updated_at=now,
        )

    def _view(
        self,
        record: MemoryRecord,
        *,
        include_content: bool = False,
        retrieval_score: float | None = None,
    ) -> MemoryView:
        revision = self._ensure_revision(record) if record.state == "active" else self._latest_revision(record.id)
        content = None
        if include_content and revision is not None and revision.content is not None:
            content = _render_markdown(revision.title, revision.content)
        now = utc_now()
        restore_available = bool(
            record.state == "deleted"
            and record.recoverable_until is not None
            and _as_utc(record.recoverable_until) > now
            and self.vault.key_exists(record.id)
        )
        payload = {
            **{key: value for key, value in record.__dict__.items() if not key.startswith("_")},
            "lg_memory_id": record.id,
            "scope_type": getattr(record, "scope_type", None) or "workspace",
            "scope_id": getattr(record, "scope_id", None),
            "goal_id": getattr(record, "goal_id", None),
            "node_id": getattr(record, "node_id", None),
            "merge_strategy": getattr(record, "merge_strategy", None) or "UNION",
            "structured_payload": dict(getattr(record, "structured_payload", None) or {}),
            "atom_schema_version": int(
                getattr(record, "atom_schema_version", 0) or 0
            ),
            "canonical_key": getattr(record, "canonical_key", "") or "",
            "atom_kind": getattr(record, "atom_kind", "fact") or "fact",
            "ledger_status": getattr(record, "ledger_status", "active") or "active",
            "temporal_status": getattr(record, "temporal_status", "timeless")
            or "timeless",
            "summary_eligibility": getattr(
                record, "summary_eligibility", "legacy_review"
            )
            or "legacy_review",
            "valid_from": getattr(record, "valid_from", None),
            "valid_until": getattr(record, "valid_until", None),
            "event_at": getattr(record, "event_at", None),
            "next_review_at": getattr(record, "next_review_at", None),
            "last_verified_at": getattr(record, "last_verified_at", None),
            "timezone_name": getattr(record, "timezone_name", "Asia/Shanghai")
            or "Asia/Shanghai",
            "evidence_ids": list(getattr(record, "evidence_ids", None) or []),
            "confidence": float(getattr(record, "confidence", 0.7) or 0.7),
            "importance": float(getattr(record, "importance", 0.5) or 0.5),
            "strength": float(getattr(record, "strength", 0.5) or 0.5),
            "access_count": int(getattr(record, "access_count", 0) or 0),
            "confirmation_count": int(getattr(record, "confirmation_count", 0) or 0),
            "successful_use_count": int(getattr(record, "successful_use_count", 0) or 0),
            "last_accessed_at": getattr(record, "last_accessed_at", None),
            "resolution_status": getattr(record, "resolution_status", None) or "none",
            "decay_policy": getattr(record, "decay_policy", None) or "SLOW",
            "supersedes_id": getattr(record, "supersedes_id", None),
            "restore_available": restore_available,
            "content": content,
            "retrieval_score": retrieval_score,
        }
        return MemoryView.model_validate(payload)

    def _draft_view(self, draft: MemoryDraft) -> MemoryDraftView:
        return MemoryDraftView.model_validate(
            {key: value for key, value in draft.__dict__.items() if not key.startswith("_")}
        )

    def _validate_structured_payload(self, payload: dict | None) -> dict:
        data = dict(payload or {})
        try:
            validate_not_canonical_state_payload(data)
        except ValueError as exc:
            raise AppError(422, "memory_canonical_state_forbidden", str(exc)) from exc
        return data

    def _validate_session_scope(self, namespace: str, session_id: str | None) -> None:
        if namespace == "session":
            self.sessions.require(session_id or "", "session")

    def _require_workspace_goal(self, goal_id: str) -> Goal:
        goal = self.db.scalar(
            self.goals.query().where(Goal.id == goal_id)
        )
        if goal is None:
            raise AppError(
                404,
                "goal_not_found",
                "goal_id is not a Goal in this workspace",
                {"goal_id": goal_id},
            )
        return goal

    def _require_workspace_node(self, node_id: str) -> GraphNode:
        node = self.db.scalar(
            self.nodes.query().where(GraphNode.id == node_id)
        )
        if node is None:
            raise AppError(
                404,
                "node_not_found",
                "node_id is not a GraphNode in this workspace",
                {"node_id": node_id},
            )
        return node

    def _validate_knowledge_scope(
        self,
        *,
        scope_type: str,
        scope_id: str | None,
        goal_id: str | None,
        node_id: str | None,
    ) -> tuple[str | None, str | None]:
        """Ensure goal/node references exist in this workspace.

        Returns normalized (goal_id, node_id). Workspace/session scopes may omit both.
        """

        resolved_goal = goal_id
        resolved_node = node_id
        if scope_type == "goal":
            target = scope_id or goal_id
            if not target:
                raise AppError(422, "goal_scope_requires_id", "goal scope requires goal_id or scope_id")
            self._require_workspace_goal(target)
            resolved_goal = target
        elif scope_type == "node":
            target = scope_id or node_id
            if not target:
                raise AppError(422, "node_scope_requires_id", "node scope requires node_id or scope_id")
            node = self._require_workspace_node(target)
            resolved_node = target
            graph = self.db.scalar(self.graphs.query().where(Graph.id == node.graph_id))
            if graph is None:
                raise AppError(
                    409,
                    "node_graph_missing",
                    "GraphNode is not attached to a workspace graph",
                    {"node_id": target},
                )
            if goal_id and goal_id != graph.goal_id:
                raise AppError(
                    422,
                    "node_goal_mismatch",
                    "node_id does not belong to the provided goal_id",
                    {"node_id": target, "goal_id": goal_id, "node_goal_id": graph.goal_id},
                )
            resolved_goal = goal_id or graph.goal_id
            if resolved_goal:
                self._require_workspace_goal(resolved_goal)
        else:
            if goal_id:
                self._require_workspace_goal(goal_id)
            if node_id:
                node = self._require_workspace_node(node_id)
                graph = self.db.scalar(self.graphs.query().where(Graph.id == node.graph_id))
                if graph is not None and goal_id and goal_id != graph.goal_id:
                    raise AppError(
                        422,
                        "node_goal_mismatch",
                        "node_id does not belong to the provided goal_id",
                        {"node_id": node_id, "goal_id": goal_id, "node_goal_id": graph.goal_id},
                    )
        return resolved_goal, resolved_node

    def _require_active(self, memory_id: str) -> MemoryRecord:
        record = self.memories.require(memory_id, "memory")
        if record.state != "active":
            raise AppError(
                409,
                "memory_not_active",
                "Only an active memory can be modified",
                {"memory_id": memory_id, "state": record.state},
            )
        return record

    def _check_revision(self, record: MemoryRecord, expected_revision: int | None) -> None:
        if expected_revision is None or expected_revision == record.revision:
            return
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.revision_conflict",
            resource_type="memory",
            resource_id=record.id,
            outcome="conflict",
            details={"expected_revision": expected_revision, "current_revision": record.revision},
        )
        self.db.commit()
        raise AppError(
            409,
            "memory_revision_conflict",
            "The memory changed after this editor loaded it",
            {
                "memory_id": record.id,
                "expected_revision": expected_revision,
                "current_revision": record.revision,
            },
        )

    def _migrate_record_to_active_provider(self, record: MemoryRecord) -> None:
        """Adopt a record from a frozen provider generation into the active one.

        Re-projects the current revision into the active provider so the
        mutation that follows operates on a binding the provider owns, then
        commits immediately: a later failure in the caller must not roll the
        adoption back, or the provider-side copy would be orphaned. The stale
        local Markdown projection is removed best-effort; a remote projection
        owned by a now-disabled provider is left in place and its bindings are
        marked orphaned — the local journal stays authoritative either way.
        """

        if record.provider_id == self.provider.provider_id:
            return
        revision = self._ensure_revision(record)
        if revision is None or revision.content is None:
            raise AppError(410, "memory_content_destroyed", "Memory content is no longer available")
        now = utc_now()
        canonical = self._canonical(
            record,
            title=revision.title,
            content=revision.content,
            revision=record.revision,
            zone=record.zone,
            source_ids=list(record.source_ids or []),
            now=now,
        )
        result = self.provider.upsert(canonical)
        previous_provider_id = record.provider_id
        previous_binding_id = record.provider_binding_id
        previous_was_local = previous_provider_id == self.local_projection.provider_id
        if previous_was_local and previous_binding_id:
            try:
                self.local_projection.delete(previous_binding_id)
            except Exception:
                logger.warning(
                    "Stale local memory projection %s could not be removed during provider migration",
                    previous_binding_id,
                    exc_info=True,
                )
        for binding in self.db.scalars(
            self.bindings.query().where(
                MemoryProviderBinding.memory_id == record.id,
                MemoryProviderBinding.provider_instance_id == previous_provider_id,
                MemoryProviderBinding.binding_status == "verified",
            )
        ).all():
            binding.binding_status = "superseded" if previous_was_local else "orphaned"
        record.provider_id = self.provider.provider_id
        record.provider_binding_id = result.provider_record_id
        record.relative_path = result.relative_path
        record.content_hash = canonical.content_hash
        self._add_binding(record=record, revision=record.revision, result=result, now=now)
        self.journal.add(
            MemoryJournalEntry(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=record.revision,
                operation="MIGRATE",
                provider_id=self.provider.provider_id,
                provider_epoch=self._provider_epoch(),
                provider_record_id=result.provider_record_id,
                content_hash=record.content_hash,
                payload=self._journal_payload(record, revision.content),
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.provider_migrate",
            resource_type="memory",
            resource_id=record.id,
            details={
                "from_provider_id": previous_provider_id,
                "to_provider_id": self.provider.provider_id,
                "revision": record.revision,
                "previous_projection": "removed" if previous_was_local else "orphaned",
            },
        )
        self.db.commit()

    def migrate_provider_generation(self, *, limit: int = 200) -> dict[str, int]:
        """Bulk-adopt active frozen-generation records into the active provider.

        Lazy per-mutation migration only covers records that get touched; this
        sweep completes the active provider's projection for the rest.
        """

        capped = max(1, min(int(limit), 500))
        records = list(
            self.db.scalars(
                self.memories.query()
                .where(
                    MemoryRecord.state == "active",
                    MemoryRecord.provider_id != self.provider.provider_id,
                )
                .order_by(MemoryRecord.updated_at.desc())
                .limit(capped)
            ).all()
        )
        migrated = 0
        failed = 0
        for record in records:
            try:
                self._migrate_record_to_active_provider(record)
                migrated += 1
            except Exception:
                self.db.rollback()
                failed += 1
                logger.warning(
                    "Memory %s could not be migrated to provider %s",
                    record.id,
                    self.provider.provider_id,
                    exc_info=True,
                )
        return {
            "migrated": migrated,
            "failed": failed,
            "remaining": self._frozen_generation_count(),
        }

    def _frozen_generation_count(self) -> int:
        return len(
            self.db.scalars(
                select(MemoryRecord.id).where(
                    MemoryRecord.workspace_id == self.workspace_id,
                    MemoryRecord.state == "active",
                    MemoryRecord.provider_id != self.provider.provider_id,
                )
            ).all()
        )

    def _add_binding(
        self,
        *,
        record: MemoryRecord,
        revision: int,
        result,
        now: datetime,
    ) -> MemoryProviderBinding:
        return self.bindings.add(
            MemoryProviderBinding(
                workspace_id=self.workspace_id,
                provider_instance_id=self.provider.provider_id,
                memory_id=record.id,
                revision=revision,
                provider_record_id=result.provider_record_id,
                provider_entity_kind=result.provider_entity_kind,
                provider_entity_value=result.provider_entity_value,
                source_content_hash=record.content_hash,
                target_readback_hash=result.target_readback_hash,
                import_event_id=result.import_event_id,
                binding_status="verified",
                verified_at=now,
            )
        )

    def purge_expired(self, *, now: datetime | None = None) -> dict[str, int]:
        current = _as_utc(now or utc_now())
        destroyed = 0
        removed_journal = 0
        recoveries = self.db.scalars(
            self.recoveries.query().where(MemoryDeletionRecovery.destroyed_at.is_(None))
        ).all()
        for recovery in recoveries:
            if _as_utc(recovery.recoverable_until) > current:
                continue
            self.vault.destroy(recovery.memory_id)
            recovery.encrypted_payload = None
            recovery.key_relative_path = None
            recovery.destroyed_at = current
            record = self.memories.get(recovery.memory_id)
            if record is not None and record.state == "deleted":
                record.state = "destroyed"
                record.content_destroyed_at = current
                record.provider_binding_id = None
            for revision in self.db.scalars(
                self.revisions.query().where(MemoryRevision.memory_id == recovery.memory_id)
            ).all():
                revision.content = None
                revision.is_active = False
            for entry in self.db.scalars(
                self.journal.query().where(MemoryJournalEntry.memory_id == recovery.memory_id)
            ).all():
                entry.payload = {}
                entry.content_scrubbed_at = entry.content_scrubbed_at or current
            self.audit.record(
                actor_id="retention_worker",
                action="memory.content_key_destroyed",
                resource_type="memory",
                resource_id=recovery.memory_id,
                details={"recoverable_until": recovery.recoverable_until.isoformat()},
            )
            destroyed += 1
        expired_journal = self.db.scalars(
            self.journal.query().where(
                MemoryJournalEntry.audit_retention_until.is_not(None),
                MemoryJournalEntry.audit_retention_until <= current,
            )
        ).all()
        for entry in expired_journal:
            self.db.delete(entry)
            removed_journal += 1
        expired_recoveries = self.db.scalars(
            self.recoveries.query().where(
                MemoryDeletionRecovery.destroyed_at.is_not(None),
                MemoryDeletionRecovery.audit_retention_until <= current,
            )
        ).all()
        for recovery in expired_recoveries:
            self.db.delete(recovery)
        if destroyed or removed_journal or expired_recoveries:
            self.db.commit()
        return {"content_keys_destroyed": destroyed, "journal_entries_removed": removed_journal}

    def list(
        self,
        *,
        zone: str | None = None,
        state: str = "active",
        namespace: str | None = None,
        session_id: str | None = None,
        include_content: bool = False,
    ) -> list[MemoryView]:
        self.purge_expired()
        statement = self.memories.query().where(MemoryRecord.state == state)
        if zone:
            statement = statement.where(MemoryRecord.zone == zone)
        if namespace:
            statement = statement.where(MemoryRecord.namespace == namespace)
        if session_id:
            statement = statement.where(MemoryRecord.session_id == session_id)
        records = self.db.scalars(statement.order_by(MemoryRecord.updated_at.desc())).all()
        views = [
            self._view(item, include_content=include_content and item.state == "active")
            for item in records
        ]
        if any(self.db.is_modified(item) for item in records):
            self.db.commit()
        return views

    def list_views(
        self,
        *,
        zone: str | None = None,
        state: str = "active",
        include_content: bool = False,
    ) -> list[MemoryView]:
        """Unified visible memory list: v1 records plus v2 event projections.

        v2-only memories are read-only views surfaced so the user can see every
        active memory block even before a legacy record exists.
        """

        self.purge_expired()
        views = self.list(
            zone=zone,
            state=state,
            include_content=include_content,
        )
        if state != "active":
            return views
        record_ids = {view.id for view in views}
        statement = select(MemorySearchDocument).where(
            MemorySearchDocument.target_type == "memory",
            MemorySearchDocument.workspace_id == self.workspace_id,
            MemorySearchDocument.status == "active",
        )
        if record_ids:
            statement = statement.where(~MemorySearchDocument.target_id.in_(record_ids))
        if zone:
            statement = statement.where(MemorySearchDocument.zone == zone)
        documents = self.db.scalars(
            statement.order_by(MemorySearchDocument.updated_at.desc())
        ).all()
        views.extend(
            self._search_document_view(document, include_content=include_content)
            for document in documents
        )
        return views

    def reconcile_zones(self) -> ReconcileZonesReport:
        report = reconcile_memory_zones(self.db, self.workspace_id)
        self.db.commit()
        return report

    def _search_document_view(
        self,
        document: MemorySearchDocument,
        *,
        include_content: bool = False,
    ) -> MemoryView:
        conversation_id = document.conversation_id
        namespace = "session" if conversation_id else "workspace"
        return MemoryView(
            id=str(document.target_id),
            lg_memory_id=str(document.target_id),
            workspace_id=str(document.workspace_id or self.workspace_id),
            namespace=namespace,
            session_id=conversation_id,
            scope_type="session" if conversation_id else "workspace",
            scope_id=conversation_id if conversation_id else None,
            goal_id=None,
            node_id=document.knowledge_node_id,
            record_kind=document.memory_type,
            merge_strategy="UNION",
            zone=document.zone or "recent",
            state="active",
            title=document.subject,
            content_hash=document.content_hash,
            relative_path="",
            revision=document.target_version,
            source="event",
            source_ids=[document.source_event_id],
            structured_payload={},
            atom_schema_version=1,
            canonical_key=document.slot_key,
            atom_kind="fact",
            ledger_status="active",
            temporal_status="timeless",
            summary_eligibility="current",
            valid_from=document.valid_from,
            valid_until=document.valid_until,
            event_at=document.updated_at,
            next_review_at=None,
            last_verified_at=None,
            timezone_name="Asia/Shanghai",
            evidence_ids=[],
            confidence=float(document.confidence or 0.7),
            importance=float(document.importance or 0.5),
            strength=0.5,
            access_count=0,
            confirmation_count=0,
            successful_use_count=0,
            last_accessed_at=None,
            resolution_status="none",
            decay_policy="SLOW",
            supersedes_id=None,
            provider_id="event-source",
            provider_binding_id=None,
            deleted_at=None,
            recoverable_until=None,
            content_destroyed_at=None,
            tenant_id=document.tenant_id,
            subject_user_id=document.subject_user_id,
            audience_type="workspace",
            task_id=document.task_id,
            project_id=document.project_id,
            conversation_id=conversation_id,
            file_id=document.file_id,
            memory_layer=document.memory_layer,
            assertion_type="inferred",
            sensitivity=document.sensitivity,
            lifecycle_status="active",
            superseded_by_id=None,
            head_event_id=document.source_event_id,
            view_source="event",
            restore_available=False,
            created_at=document.created_at,
            updated_at=document.updated_at,
            content=document.content if include_content else None,
            retrieval_score=None,
        )

    def get(self, memory_id: str) -> MemoryView:
        self.purge_expired()
        record = self.memories.require(memory_id, "memory")
        view = self._view(record, include_content=record.state == "active")
        if self.db.is_modified(record):
            self.db.commit()
        return view

    def create(self, payload: MemoryCreateRequest) -> MemoryView:
        self.purge_expired()
        self._validate_session_scope(payload.namespace, payload.session_id)
        structured = self._validate_structured_payload(payload.structured_payload)
        type_def = get_memory_type(payload.record_kind)
        scope_type, scope_id, goal_id, node_id = normalize_scope(
            scope_type=payload.scope_type,
            scope_id=payload.scope_id,
            namespace=payload.namespace,
            session_id=payload.session_id,
            goal_id=payload.goal_id,
            node_id=payload.node_id,
            memory_type=payload.record_kind,
        )
        if scope_type == "session" and not payload.session_id:
            raise AppError(422, "session_scope_requires_session", "session scope requires session_id")
        goal_id, node_id = self._validate_knowledge_scope(
            scope_type=scope_type,
            scope_id=scope_id,
            goal_id=goal_id,
            node_id=node_id,
        )
        if scope_type == "goal":
            scope_id = goal_id
        elif scope_type == "node":
            scope_id = node_id
        now = utc_now()
        atomic = _atomic_fields(
            structured,
            list(payload.source_ids),
            default_eligible=payload.source.startswith("user"),
        )
        importance = float(payload.importance)
        strength = compute_memory_strength(
            base_importance=importance,
            access_count=0,
            confirmation_count=1 if payload.source.startswith("user") else 0,
            successful_use_count=0,
            active_goal_bonus=0.15 if goal_id or scope_type == "goal" else 0.0,
            elapsed_days=0.0,
            decay_rate=default_decay_rate(type_def.decay_policy),
        )
        record = MemoryRecord(
            id=_memory_id(),
            workspace_id=self.workspace_id,
            tenant_id=self.workspace.tenant_id,
            subject_user_id=self.actor_id if payload.namespace == "session" else None,
            audience_type="user" if payload.namespace == "session" else "workspace",
            conversation_id=payload.session_id,
            memory_layer="L3" if payload.namespace == "session" else "L4",
            assertion_type="explicit" if payload.source.startswith("user") else "inferred",
            sensitivity="normal",
            lifecycle_status="active",
            namespace=payload.namespace,
            session_id=payload.session_id,
            scope_type=scope_type,
            scope_id=scope_id,
            goal_id=goal_id,
            node_id=node_id,
            record_kind=payload.record_kind,
            merge_strategy=type_def.merge_strategy,
            zone=payload.zone,
            state="active",
            title=payload.title.strip(),
            content_hash="",
            relative_path="",
            revision=1,
            source=payload.source,
            source_ids=list(dict.fromkeys(payload.source_ids)),
            structured_payload=structured,
            **atomic,
            confidence=float(payload.confidence),
            importance=importance,
            strength=strength,
            confirmation_count=1 if payload.source.startswith("user") else 0,
            resolution_status=payload.resolution_status,
            decay_policy=type_def.decay_policy,
            provider_id=self.provider.provider_id,
            created_at=now,
            updated_at=now,
        )
        canonical = self._canonical(
            record,
            title=record.title,
            content=payload.content,
            revision=1,
            zone=record.zone,
            source_ids=record.source_ids,
            now=now,
        )
        # Canonical DB state commits before any external projection. Markdown,
        # Mem0 and embeddings are rebuildable Outbox targets, never transaction
        # participants in the memory fact write.
        record.content_hash = canonical.content_hash
        self.memories.add(record)
        self.revisions.add(
            MemoryRevision(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=1,
                base_revision=None,
                operation="ADD",
                title=record.title,
                content=payload.content.rstrip(),
                content_hash=record.content_hash,
                namespace=record.namespace,
                session_id=record.session_id,
                record_kind=record.record_kind,
                zone=record.zone,
                source=record.source,
                source_ids=record.source_ids,
                actor_id=self.actor_id,
                reason="user_create",
                is_active=True,
            )
        )
        self.journal.add(
            MemoryJournalEntry(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=1,
                operation="ADD",
                provider_id=self.provider.provider_id,
                provider_epoch=self._provider_epoch(),
                provider_record_id=None,
                content_hash=record.content_hash,
                payload=self._journal_payload(record, payload.content.rstrip()),
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.create",
            resource_type="memory",
            resource_id=record.id,
            details={
                "lg_memory_id": record.id,
                "revision": 1,
                "zone": record.zone,
                "namespace": record.namespace,
                "scope_type": record.scope_type,
                "scope_id": record.scope_id,
                "record_kind": record.record_kind,
                "provider_id": record.provider_id,
            },
        )
        self._mark_profile_stale("memory_created")
        self._mirror_event(record, payload.content.rstrip(), "ADD")
        self.db.commit()
        self._project_after_commit(record, canonical)
        record = self.memories.require(record.id, "memory")
        self.db.refresh(record)
        return self._view(record, include_content=True)

    def update(self, memory_id: str, payload: MemoryUpdateRequest) -> MemoryView:
        self.purge_expired()
        record = self._require_active(memory_id)
        self._check_revision(record, payload.expected_revision)
        previous_provider_id = record.provider_id
        previous_binding_id = record.provider_binding_id
        current = self._ensure_revision(record)
        if current is None or current.content is None:
            raise AppError(410, "memory_content_destroyed", "Memory content is no longer available")
        title = payload.title.strip() if payload.title is not None else current.title
        content = payload.content.rstrip() if payload.content is not None else current.content
        zone = payload.zone or current.zone
        source_ids = (
            list(dict.fromkeys(payload.source_ids))
            if payload.source_ids is not None
            else list(current.source_ids or [])
        )
        structured = (
            self._validate_structured_payload(payload.structured_payload)
            if payload.structured_payload is not None
            else dict(getattr(record, "structured_payload", None) or {})
        )
        if (
            payload.reason == "user_edit"
            and int(getattr(record, "atom_schema_version", 0) or 0) >= 1
            and (title != current.title or content != current.content)
        ):
            evidence = MemoryEvidence(
                workspace_id=self.workspace_id,
                source_kind="user_memory_edit",
                source_id=f"{record.id}:revision:{record.revision + 1}",
                authorship="user",
                observed_at=utc_now(),
                content_hash=_content_hash(title, content),
                excerpt=content[:1_200],
                profile_eligible=True,
                eligibility_reason="explicit_user_memory_edit",
            )
            self.db.add(evidence)
            self.db.flush()
            source_ids = list(dict.fromkeys([*source_ids, evidence.id]))
            structured = {
                **structured,
                "evidence_ids": list(
                    dict.fromkeys(
                        [
                            *list(structured.get("evidence_ids") or []),
                            evidence.id,
                        ]
                    )
                ),
                "last_verified_at": evidence.observed_at.isoformat(),
                "provenance": {
                    "authorship": "user",
                    "source_kinds": ["user_memory_edit"],
                    "profile_eligible": True,
                },
            }
        atomic = _atomic_fields(
            structured,
            source_ids,
            default_eligible=str(getattr(record, "source", "")).startswith("user"),
        )
        if payload.scope_type is not None or payload.scope_id is not None or payload.goal_id is not None or payload.node_id is not None:
            scope_type, scope_id, goal_id, node_id = normalize_scope(
                scope_type=payload.scope_type or record.scope_type,
                scope_id=payload.scope_id if payload.scope_id is not None else record.scope_id,
                namespace=record.namespace,
                session_id=record.session_id,
                goal_id=payload.goal_id if payload.goal_id is not None else record.goal_id,
                node_id=payload.node_id if payload.node_id is not None else record.node_id,
                memory_type=record.record_kind,
            )
            goal_id, node_id = self._validate_knowledge_scope(
                scope_type=scope_type,
                scope_id=scope_id,
                goal_id=goal_id,
                node_id=node_id,
            )
            if scope_type == "goal":
                scope_id = goal_id
            elif scope_type == "node":
                scope_id = node_id
        else:
            scope_type = getattr(record, "scope_type", None) or "workspace"
            scope_id = getattr(record, "scope_id", None)
            goal_id = payload.goal_id if payload.goal_id is not None else getattr(record, "goal_id", None)
            node_id = payload.node_id if payload.node_id is not None else getattr(record, "node_id", None)
        confidence = (
            float(payload.confidence)
            if payload.confidence is not None
            else float(getattr(record, "confidence", 0.7) or 0.7)
        )
        importance = (
            float(payload.importance)
            if payload.importance is not None
            else float(getattr(record, "importance", 0.5) or 0.5)
        )
        resolution_status = (
            payload.resolution_status
            if payload.resolution_status is not None
            else (getattr(record, "resolution_status", None) or "none")
        )
        next_revision = record.revision + 1
        now = utc_now()
        strength = self._recompute_strength(record, importance=importance, now=now, goal_id=goal_id)
        canonical = self._canonical(
            record,
            title=title,
            content=content,
            revision=next_revision,
            zone=zone,
            source_ids=source_ids,
            now=now,
        )
        current_revision = record.revision
        cas = self.db.execute(
            sql_update(MemoryRecord)
            .where(
                MemoryRecord.workspace_id == self.workspace_id,
                MemoryRecord.id == record.id,
                MemoryRecord.state == "active",
                MemoryRecord.revision == current_revision,
            )
            .values(
                title=title,
                content_hash=canonical.content_hash,
                revision=next_revision,
                zone=zone,
                source_ids=source_ids,
                structured_payload=structured,
                **atomic,
                confidence=confidence,
                importance=importance,
                strength=strength,
                resolution_status=resolution_status,
                scope_type=scope_type,
                scope_id=scope_id,
                goal_id=goal_id,
                node_id=node_id,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        if cas.rowcount != 1:
            self.db.rollback()
            fresh = self.memories.require(memory_id, "memory")
            self.audit.record(
                actor_id=self.actor_id,
                action="memory.revision_conflict",
                resource_type="memory",
                resource_id=memory_id,
                outcome="conflict",
                details={
                    "expected_revision": current_revision,
                    "current_revision": fresh.revision,
                    "detected_at": "atomic_compare_and_swap",
                },
            )
            self.db.commit()
            raise AppError(
                409,
                "memory_revision_conflict",
                "A concurrent writer committed this memory first",
                {
                    "memory_id": memory_id,
                    "expected_revision": current_revision,
                    "current_revision": fresh.revision,
                },
            )
        current.is_active = False
        record.title = title
        record.content_hash = canonical.content_hash
        record.revision = next_revision
        record.zone = zone
        record.source_ids = source_ids
        record.structured_payload = structured
        for field, value in atomic.items():
            setattr(record, field, value)
        record.confidence = confidence
        record.importance = importance
        record.strength = strength
        record.resolution_status = resolution_status
        record.scope_type = scope_type
        record.scope_id = scope_id
        record.goal_id = goal_id
        record.node_id = node_id
        record.updated_at = now
        revision = self.revisions.add(
            MemoryRevision(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=next_revision,
                base_revision=current.revision,
                operation="UPDATE",
                title=title,
                content=content,
                content_hash=record.content_hash,
                namespace=record.namespace,
                session_id=record.session_id,
                record_kind=record.record_kind,
                zone=zone,
                source=record.source,
                source_ids=source_ids,
                actor_id=self.actor_id,
                reason=payload.reason,
                is_active=True,
            )
        )
        self.journal.add(
            MemoryJournalEntry(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=next_revision,
                operation="UPDATE",
                provider_id=self.provider.provider_id,
                provider_epoch=self._provider_epoch(),
                provider_record_id=record.provider_binding_id,
                content_hash=record.content_hash,
                payload=self._journal_payload(record, content),
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.update",
            resource_type="memory",
            resource_id=record.id,
            details={
                "from_revision": current.revision,
                "to_revision": revision.revision,
                "expected_revision": payload.expected_revision,
                "zone": zone,
            },
        )
        self._mark_profile_stale("memory_updated")
        self._mirror_event(record, content, "UPDATE")
        self.db.commit()
        self._project_after_commit(
            record,
            canonical,
            previous_provider_id=previous_provider_id,
            previous_binding_id=previous_binding_id,
        )
        record = self.memories.require(record.id, "memory")
        self.db.refresh(record)
        return self._view(record, include_content=True)

    def list_revisions(self, memory_id: str) -> list[MemoryRevisionView]:
        self.purge_expired()
        self.memories.require(memory_id, "memory")
        revisions = self.db.scalars(
            self.revisions.query()
            .where(MemoryRevision.memory_id == memory_id)
            .order_by(MemoryRevision.revision.desc())
        ).all()
        return [MemoryRevisionView.model_validate(item) for item in revisions]

    def restore_revision(
        self,
        memory_id: str,
        revision_number: int,
        payload: MemoryRevisionRestoreRequest,
    ) -> MemoryView:
        record = self._require_active(memory_id)
        self._check_revision(record, payload.expected_revision)
        target = self.db.scalar(
            self.revisions.query().where(
                MemoryRevision.memory_id == memory_id,
                MemoryRevision.revision == revision_number,
            )
        )
        if target is None:
            raise AppError(404, "memory_revision_not_found", "Memory revision was not found")
        if target.content is None:
            raise AppError(
                410,
                "memory_revision_content_destroyed",
                "This revision no longer has recoverable content",
            )
        restored = self.update(
            memory_id,
            MemoryUpdateRequest(
                expected_revision=payload.expected_revision,
                title=target.title,
                content=target.content,
                zone=target.zone,
                source_ids=list(target.source_ids or []),
                reason=payload.reason,
            ),
        )
        newest = self._latest_revision(memory_id)
        if newest is not None:
            newest.operation = "RESTORE_REVISION"
            newest_journal = self.db.scalar(
                self.journal.query().where(
                    MemoryJournalEntry.memory_id == memory_id,
                    MemoryJournalEntry.revision == newest.revision,
                )
            )
            if newest_journal is not None:
                newest_journal.operation = "RESTORE_REVISION"
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.revision_restore",
            resource_type="memory",
            resource_id=memory_id,
            details={"restored_revision": revision_number, "new_revision": restored.revision},
        )
        self.db.commit()
        return restored

    def _recovery_snapshot(self, record: MemoryRecord) -> dict:
        revisions = self.db.scalars(
            self.revisions.query()
            .where(MemoryRevision.memory_id == record.id)
            .order_by(MemoryRevision.revision)
        ).all()
        return {
            "record": {
                "id": record.id,
                "namespace": record.namespace,
                "session_id": record.session_id,
                "record_kind": record.record_kind,
                "zone": record.zone,
                "title": record.title,
                "revision": record.revision,
                "source": record.source,
                "source_ids": list(record.source_ids or []),
            },
            "revisions": [
                {
                    "revision": item.revision,
                    "base_revision": item.base_revision,
                    "operation": item.operation,
                    "title": item.title,
                    "content": item.content,
                    "content_hash": item.content_hash,
                    "namespace": item.namespace,
                    "session_id": item.session_id,
                    "record_kind": item.record_kind,
                    "zone": item.zone,
                    "source": item.source,
                    "source_ids": list(item.source_ids or []),
                    "actor_id": item.actor_id,
                    "reason": item.reason,
                }
                for item in revisions
            ],
        }

    def delete(self, memory_id: str) -> MemoryView:
        self.purge_expired()
        record = self._require_active(memory_id)
        current = self._ensure_revision(record)
        if current is None or current.content is None:
            raise AppError(410, "memory_content_destroyed", "Memory content is no longer available")
        now = utc_now()
        recoverable_until = now + RECOVERY_WINDOW
        audit_until = now + DELETION_AUDIT_RETENTION
        snapshot = self._recovery_snapshot(record)
        encrypted_payload, key_path = self.vault.encrypt(record.id, snapshot)
        previous_provider_id = record.provider_id
        previous_binding_id = record.provider_binding_id
        recovery = self.db.scalar(
            self.recoveries.query().where(MemoryDeletionRecovery.memory_id == record.id)
        )
        if recovery is None:
            recovery = self.recoveries.add(
                MemoryDeletionRecovery(
                    workspace_id=self.workspace_id,
                    memory_id=record.id,
                    encrypted_payload=encrypted_payload,
                    key_relative_path=key_path,
                    recoverable_until=recoverable_until,
                    audit_retention_until=audit_until,
                )
            )
        else:
            recovery.encrypted_payload = encrypted_payload
            recovery.key_relative_path = key_path
            recovery.recoverable_until = recoverable_until
            recovery.audit_retention_until = audit_until
            recovery.destroyed_at = None
        record.state = "deleted"
        record.deleted_at = now
        record.recoverable_until = recoverable_until
        record.content_destroyed_at = None
        record.provider_binding_id = None
        for revision in self.db.scalars(
            self.revisions.query().where(MemoryRevision.memory_id == record.id)
        ).all():
            revision.content = None
            revision.is_active = False
        for entry in self.db.scalars(
            self.journal.query().where(MemoryJournalEntry.memory_id == record.id)
        ).all():
            entry.payload = {}
            entry.content_scrubbed_at = now
            entry.audit_retention_until = audit_until
        self.journal.add(
            MemoryJournalEntry(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=record.revision,
                operation="DELETE",
                provider_id=self.provider.provider_id,
                provider_epoch=self._provider_epoch(),
                provider_record_id=None,
                content_hash=record.content_hash,
                payload={},
                tombstone=True,
                recoverable_until=recoverable_until,
                audit_retention_until=audit_until,
                content_scrubbed_at=now,
            )
        )
        bindings = self.db.scalars(
            self.bindings.query().where(MemoryProviderBinding.memory_id == record.id)
        ).all()
        for binding in bindings:
            binding.binding_status = "pending_delete"
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.soft_delete",
            resource_type="memory",
            resource_id=record.id,
            details={
                "recoverable_until": recoverable_until.isoformat(),
                "audit_retention_until": audit_until.isoformat(),
                "provider_id": self.provider.provider_id,
                "projection_delete_pending": bool(previous_binding_id),
            },
        )
        self._mark_profile_stale("memory_deleted")
        self._mirror_event(record, "", "DELETE")
        self.db.commit()
        self._delete_projection_after_commit(
            memory_id=record.id,
            provider_id=previous_provider_id,
            provider_binding_id=previous_binding_id,
        )
        record = self.memories.require(record.id, "memory")
        self.db.refresh(record)
        return self._view(record)

    def restore_deleted(self, memory_id: str) -> MemoryView:
        self.purge_expired()
        record = self.memories.require(memory_id, "memory")
        if record.state == "destroyed":
            raise AppError(
                410,
                "memory_recovery_window_expired",
                "The 30 minute recovery window has expired and content was destroyed",
            )
        if record.state != "deleted":
            raise AppError(409, "memory_not_deleted", "Only a deleted memory can be restored")
        recovery = self.db.scalar(
            self.recoveries.query().where(MemoryDeletionRecovery.memory_id == record.id)
        )
        now = utc_now()
        if (
            recovery is None
            or recovery.encrypted_payload is None
            or _as_utc(recovery.recoverable_until) <= now
        ):
            self.purge_expired(now=now)
            raise AppError(
                410,
                "memory_recovery_window_expired",
                "The 30 minute recovery window has expired and content was destroyed",
            )
        snapshot = self.vault.decrypt(record.id, recovery.encrypted_payload)
        revision_payloads = snapshot.get("revisions")
        if not isinstance(revision_payloads, list) or not revision_payloads:
            raise AppError(409, "memory_recovery_payload_invalid", "Recovery has no revisions")
        by_number = {
            item.revision: item
            for item in self.db.scalars(
                self.revisions.query().where(MemoryRevision.memory_id == record.id)
            ).all()
        }
        for item in revision_payloads:
            if not isinstance(item, dict) or not isinstance(item.get("revision"), int):
                raise AppError(409, "memory_recovery_payload_invalid", "Recovery revision is invalid")
            revision = by_number.get(item["revision"])
            if revision is not None:
                revision.content = item.get("content") if isinstance(item.get("content"), str) else None
        last = max(revision_payloads, key=lambda item: int(item["revision"]))
        content = last.get("content")
        if not isinstance(content, str):
            raise AppError(409, "memory_recovery_payload_invalid", "Recovery content is invalid")
        next_revision = record.revision + 1
        canonical = self._canonical(
            record,
            title=str(last.get("title") or record.title),
            content=content,
            revision=next_revision,
            zone=str(last.get("zone") or record.zone),
            source_ids=[str(value) for value in last.get("source_ids") or []],
            now=now,
        )
        record.title = canonical.title
        record.zone = canonical.zone
        record.content_hash = canonical.content_hash
        record.revision = next_revision
        # Restore re-projects into whichever provider is active now, so the
        # record adopts the current generation even across a provider switch.
        record.provider_id = self.provider.provider_id
        record.state = "active"
        record.deleted_at = None
        record.recoverable_until = None
        record.content_destroyed_at = None
        record.provider_binding_id = None
        record.relative_path = ""
        record.source_ids = list(canonical.source_ids)
        self.revisions.add(
            MemoryRevision(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=next_revision,
                base_revision=int(last["revision"]),
                operation="RESTORE_DELETE",
                title=canonical.title,
                content=content,
                content_hash=canonical.content_hash,
                namespace=record.namespace,
                session_id=record.session_id,
                record_kind=record.record_kind,
                zone=record.zone,
                source=record.source,
                source_ids=record.source_ids,
                actor_id=self.actor_id,
                reason="user_restore_within_30_minutes",
                is_active=True,
            )
        )
        self.journal.add(
            MemoryJournalEntry(
                workspace_id=self.workspace_id,
                memory_id=record.id,
                revision=next_revision,
                operation="RESTORE_DELETE",
                provider_id=self.provider.provider_id,
                provider_epoch=self._provider_epoch(),
                provider_record_id=None,
                content_hash=record.content_hash,
                payload=self._journal_payload(record, content),
            )
        )
        self.vault.destroy(record.id)
        recovery.encrypted_payload = None
        recovery.key_relative_path = None
        recovery.destroyed_at = now
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.restore_deleted",
            resource_type="memory",
            resource_id=record.id,
            details={"new_revision": next_revision, "provider_id": self.provider.provider_id},
        )
        self._mark_profile_stale("memory_restored")
        self._mirror_event(record, content, "RESTORE_DELETE")
        self.db.commit()
        self._project_after_commit(record, canonical)
        record = self.memories.require(record.id, "memory")
        self.db.refresh(record)
        return self._view(record, include_content=True)

    def list_journal(self, memory_id: str) -> list[MemoryJournalView]:
        self.purge_expired()
        self.memories.require(memory_id, "memory")
        entries = self.db.scalars(
            self.journal.query()
            .where(MemoryJournalEntry.memory_id == memory_id)
            .order_by(MemoryJournalEntry.created_at.desc())
        ).all()
        return [MemoryJournalView.model_validate(item) for item in entries]

    def list_bindings(self, memory_id: str) -> list[MemoryBindingView]:
        self.memories.require(memory_id, "memory")
        bindings = self.db.scalars(
            self.bindings.query()
            .where(MemoryProviderBinding.memory_id == memory_id)
            .order_by(MemoryProviderBinding.revision.desc())
        ).all()
        return [MemoryBindingView.model_validate(item) for item in bindings]

    def _workspace_policy_values(self) -> dict[str, bool]:
        setting = self.db.scalar(
            self.settings.query().where(WorkspaceSetting.key == MEMORY_POLICY_KEY)
        )
        value = setting.value if setting is not None else {}
        data = value if isinstance(value, dict) else {}
        return {
            "workspace_enabled": bool(data.get("workspace_enabled", False)),
            "workspace_recall_enabled": bool(
                data.get("workspace_recall_enabled", True)
            ),
            "workspace_learning_enabled": bool(
                data.get("workspace_learning_enabled", True)
            ),
        }

    def _workspace_policy(self) -> bool:
        return self._workspace_policy_values()["workspace_enabled"]

    def policy(self, session_id: str | None = None) -> MemoryPolicyView:
        workspace_policy = self._workspace_policy_values()
        workspace_enabled = workspace_policy["workspace_enabled"]
        session_enabled: bool | None = None
        session_recall_enabled: bool | None = None
        session_learning_enabled: bool | None = None
        if session_id is not None:
            session = self.sessions.require(session_id, "session")
            session_enabled = bool(session.memory_enabled)
            session_recall_enabled = bool(
                getattr(session, "memory_recall_enabled", True)
            )
            session_learning_enabled = bool(
                getattr(session, "memory_learning_enabled", True)
            )
        effective_recall = bool(
            workspace_enabled
            and workspace_policy["workspace_recall_enabled"]
            and session_enabled
            and session_recall_enabled
        )
        effective_learning = bool(
            workspace_enabled
            and workspace_policy["workspace_learning_enabled"]
            and session_enabled
            and session_learning_enabled
        )
        return MemoryPolicyView(
            workspace_id=self.workspace_id,
            workspace_enabled=workspace_enabled,
            session_id=session_id,
            session_enabled=session_enabled,
            effective_enabled=effective_recall,
            workspace_recall_enabled=workspace_policy[
                "workspace_recall_enabled"
            ],
            workspace_learning_enabled=workspace_policy[
                "workspace_learning_enabled"
            ],
            session_recall_enabled=session_recall_enabled,
            session_learning_enabled=session_learning_enabled,
            effective_recall_enabled=effective_recall,
            effective_learning_enabled=effective_learning,
        )

    def update_policy(self, payload: MemoryPolicyUpdateRequest) -> MemoryPolicyView:
        setting = self.db.scalar(
            self.settings.query().where(WorkspaceSetting.key == MEMORY_POLICY_KEY)
        )
        current = dict(setting.value or {}) if setting is not None and isinstance(setting.value, dict) else {}
        if payload.workspace_enabled is not None:
            current["workspace_enabled"] = payload.workspace_enabled
        if payload.workspace_recall_enabled is not None:
            current["workspace_recall_enabled"] = (
                payload.workspace_recall_enabled
            )
        if payload.workspace_learning_enabled is not None:
            current["workspace_learning_enabled"] = (
                payload.workspace_learning_enabled
            )
        if any(
            value is not None
            for value in (
                payload.workspace_enabled,
                payload.workspace_recall_enabled,
                payload.workspace_learning_enabled,
            )
        ):
            if setting is None:
                setting = self.settings.add(
                    WorkspaceSetting(
                        workspace_id=self.workspace_id,
                        key=MEMORY_POLICY_KEY,
                        value=current,
                    )
                )
            else:
                setting.value = current
        session = None
        bulk_affected = 0
        if payload.all_sessions_shared is not None:
            sessions = self.db.scalars(
                select(ChatSession).where(ChatSession.workspace_id == self.workspace_id)
            ).all()
            enabled = payload.all_sessions_shared
            for candidate in sessions:
                candidate.memory_enabled = enabled
                candidate.memory_recall_enabled = enabled
                candidate.memory_learning_enabled = enabled
            bulk_affected = len(sessions)
        if payload.session_enabled is not None:
            session = self.sessions.require(payload.session_id or "", "session")
            session.memory_enabled = payload.session_enabled
        elif (
            payload.session_recall_enabled is not None
            or payload.session_learning_enabled is not None
        ):
            session = self.sessions.require(payload.session_id or "", "session")
        else:
            session = None
        if session is not None and payload.session_recall_enabled is not None:
            session.memory_recall_enabled = payload.session_recall_enabled
        if session is not None and payload.session_learning_enabled is not None:
            session.memory_learning_enabled = payload.session_learning_enabled
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.policy_update",
            resource_type="memory_policy",
            resource_id=payload.session_id or self.workspace_id,
            details={
                "workspace_enabled": current.get("workspace_enabled", False),
                "workspace_recall_enabled": current.get(
                    "workspace_recall_enabled", True
                ),
                "workspace_learning_enabled": current.get(
                    "workspace_learning_enabled", True
                ),
                "session_id": payload.session_id,
                "session_enabled": payload.session_enabled,
                "session_recall_enabled": payload.session_recall_enabled,
                "session_learning_enabled": payload.session_learning_enabled,
                "all_sessions_shared": payload.all_sessions_shared,
                "bulk_affected": bulk_affected,
            },
        )
        self.db.commit()
        return self.policy(payload.session_id)

    def provider_status(self, *, probe: bool = False) -> MemoryProviderStatusView:
        if probe:
            health = self.provider.health()
        else:
            health = self.provider.health() if not self.provider.remote_capability else None
        config = self.db.get(ProviderConfig, self.provider.provider_id)
        if health is None:
            status = config.status if config is not None else "enabled_unverified"
            available = bool(self.provider.available)
            details = dict(config.capabilities or {}) if config is not None else {}
        else:
            status = health.status
            available = health.available
            details = health.details
        if probe and config is not None and health is not None:
            config.status = health.status
            capabilities = dict(config.capabilities or {})
            capabilities["last_probe_result"] = health.status
            capabilities["last_probe_details"] = health.details
            config.capabilities = capabilities
            self.audit.record(
                actor_id=self.actor_id,
                action="memory.provider_probe",
                resource_type="provider",
                resource_id=config.id,
                details={"status": health.status},
            )
            self.db.commit()
        return MemoryProviderStatusView(
            provider_id=self.provider.provider_id,
            provider_type=config.provider_type if config is not None else "local_workspace_markdown",
            display_name=config.display_name if config is not None else "本地工作区 Markdown",
            available=available,
            remote_capability=bool(self.provider.remote_capability),
            status=status,
            provider_epoch=self._provider_epoch(),
            frozen_memories=self._frozen_generation_count(),
            details=details,
        )

    def _recompute_strength(
        self,
        record: MemoryRecord,
        *,
        importance: float | None = None,
        now: datetime | None = None,
        goal_id: str | None = None,
    ) -> float:
        current = now or utc_now()
        updated = _as_utc(record.updated_at) if record.updated_at else current
        elapsed_days = max(0.0, (current - updated).total_seconds() / 86_400.0)
        decay_policy = getattr(record, "decay_policy", None) or "SLOW"
        active_goal = goal_id or getattr(record, "goal_id", None)
        resolution = getattr(record, "resolution_status", None) or "none"
        conflict_penalty = 0.35 if resolution == "resolved" else 0.0
        if resolution == "recurring":
            conflict_penalty = 0.0
        return compute_memory_strength(
            base_importance=float(
                importance
                if importance is not None
                else (getattr(record, "importance", None) or 0.5)
            ),
            access_count=int(getattr(record, "access_count", 0) or 0),
            confirmation_count=int(getattr(record, "confirmation_count", 0) or 0),
            successful_use_count=int(getattr(record, "successful_use_count", 0) or 0),
            active_goal_bonus=0.2 if active_goal else 0.0,
            elapsed_days=elapsed_days,
            decay_rate=default_decay_rate(decay_policy),
            conflict_penalty=conflict_penalty,
        )

    def _scope_proximity(
        self,
        record: MemoryRecord,
        *,
        goal_id: str | None,
        node_ids: set[str],
        session_id: str | None,
    ) -> float:
        scope_type = getattr(record, "scope_type", None) or "workspace"
        if scope_type == "node" and record.node_id and record.node_id in node_ids:
            return 1.0
        if scope_type == "goal" and goal_id and record.goal_id == goal_id:
            return 0.85
        if scope_type == "session" and session_id and record.session_id == session_id:
            return 0.8
        if scope_type == "workspace":
            return 0.55
        # Inherit parent scopes when record is broader than current focus.
        if goal_id and record.goal_id == goal_id:
            return 0.7
        if record.node_id and record.node_id in node_ids:
            return 0.9
        return 0.2

    def _merge_effective(
        self,
        records: list[MemoryRecord],
        *,
        goal_id: str | None,
        node_ids: set[str],
        session_id: str | None,
        now: datetime,
        limit: int = 8,
        semantic_boosts: dict[str, float] | None = None,
    ) -> tuple[list[tuple[MemoryRecord, float]], list[dict]]:
        """Assemble effective memories with type-aware merge (no parent copy)."""

        scored: list[tuple[MemoryRecord, float]] = []
        conflicts: list[dict] = []
        for record in records:
            resolution = getattr(record, "resolution_status", None) or "none"
            if resolution == "resolved":
                # Resolved misconceptions stay low-visibility unless recurring.
                default_boost = 0.15
            else:
                default_boost = 1.0
            strength = self._recompute_strength(record, now=now, goal_id=goal_id)
            record.strength = strength
            proximity = self._scope_proximity(
                record, goal_id=goal_id, node_ids=node_ids, session_id=session_id
            )
            reliability = float(getattr(record, "confidence", 0.7) or 0.7)
            zone_factor = {"hot": 1.0, "recent": 0.9, "topics": 0.8, "archive": 0.35}.get(
                record.zone, 0.5
            )
            score = strength * proximity * reliability * zone_factor * default_boost
            if semantic_boosts:
                # Optional embedding plugin: similarity amplifies the heuristic
                # score, it never replaces it (and never demotes on absence).
                score *= 1.0 + max(0.0, semantic_boosts.get(record.id, 0.0))
            if score <= 0:
                continue
            scored.append((record, score))

        # Type-aware merge: OVERRIDE / INHERIT_UNTIL_OVERRIDE keep nearest scope.
        by_type: dict[str, list[tuple[MemoryRecord, float]]] = {}
        for item in scored:
            by_type.setdefault(item[0].record_kind, []).append(item)

        selected: list[tuple[MemoryRecord, float]] = []
        for memory_type, items in by_type.items():
            type_def = get_memory_type(memory_type)
            strategy = type_def.merge_strategy
            items_sorted = sorted(
                items,
                key=lambda pair: (
                    -pair[1],
                    -{"node": 3, "goal": 2, "session": 2, "workspace": 1}.get(
                        getattr(pair[0], "scope_type", None) or "workspace", 0
                    ),
                ),
            )
            if strategy in {"OVERRIDE", "INHERIT_UNTIL_OVERRIDE"}:
                winner = items_sorted[0]
                selected.append(winner)
                for loser, _ in items_sorted[1:]:
                    if loser.scope_type != winner[0].scope_type:
                        conflicts.append(
                            {
                                "memory_type": memory_type,
                                "strategy": strategy,
                                "kept": winner[0].id,
                                "hidden": loser.id,
                                "reason": "nearer_scope_override",
                            }
                        )
            elif strategy == "LOCAL_ONLY":
                for record, score in items_sorted:
                    if (
                        (record.scope_type == "node" and record.node_id in node_ids)
                        or (record.scope_type == "session" and record.session_id == session_id)
                        or (record.scope_type == "goal" and record.goal_id == goal_id)
                        or record.scope_type == "workspace"
                    ):
                        selected.append((record, score))
            else:
                selected.extend(items_sorted)

        selected.sort(key=lambda pair: -pair[1])
        return selected[:limit], conflicts

    def effective_memory_package(
        self,
        *,
        session_id: str | None = None,
        goal_id: str | None = None,
        node_ids: list[str] | None = None,
        limit: int = 8,
        mark_access: bool = False,
        query_text: str | None = None,
        prompt_token_budget: int | None = None,
    ) -> EffectiveMemoryPackageView:
        # Recall reads only local records/revisions — it never contacts the
        # semantic provider, so provider availability must not gate (or add
        # remote-probe latency to) the chat hot path. Provider health surfaces
        # through /memory/provider and the write paths instead.
        self.purge_expired()
        node_id_set = set(node_ids or [])
        if session_id:
            policy = self.policy(session_id)
            if not policy.effective_enabled:
                return EffectiveMemoryPackageView(session_id=session_id, goal_id=goal_id, node_ids=list(node_id_set))
            session = self.sessions.get(session_id)
            if session is not None and goal_id is None:
                goal_id = session.goal_id
        now = utc_now()
        statement = self.memories.query().where(
            MemoryRecord.state == "active",
            MemoryRecord.lifecycle_status == "active",
            MemoryRecord.auto_recall_suppressed.is_(False),
            MemoryRecord.zone.in_(["hot", "recent", "topics"]),
        )
        if session_id:
            statement = statement.where(
                (MemoryRecord.namespace == "workspace")
                | (
                    (MemoryRecord.namespace == "session")
                    & (MemoryRecord.session_id == session_id)
                )
            )
        else:
            statement = statement.where(MemoryRecord.namespace == "workspace")
        records = list(self.db.scalars(statement).all())
        # Filter by knowledge scope inheritance: workspace always; goal/node when matching or broader.
        eligible: list[MemoryRecord] = []
        for record in records:
            if (getattr(record, "ledger_status", None) or "active") != "active":
                continue
            if (getattr(record, "temporal_status", None) or "timeless") in {
                "cancelled",
                "rescheduled",
                "lapsed_unverified",
                "expired",
            }:
                continue
            if int(getattr(record, "atom_schema_version", 0) or 0) >= 1 and (
                getattr(record, "summary_eligibility", None) or "excluded"
            ) in {"historical", "excluded"}:
                continue
            scope_type = getattr(record, "scope_type", None) or "workspace"
            if scope_type == "workspace":
                eligible.append(record)
            elif scope_type == "goal" and goal_id and record.goal_id == goal_id:
                eligible.append(record)
            elif scope_type == "node" and (
                (record.node_id and record.node_id in node_id_set)
                or (goal_id and record.goal_id == goal_id)
            ):
                eligible.append(record)
            elif scope_type == "session" and session_id and record.session_id == session_id:
                eligible.append(record)
            elif not goal_id and not node_id_set and scope_type in {"workspace", "session"}:
                eligible.append(record)
        semantic_boosts: dict[str, float] = {}
        if query_text and eligible:
            # Optional embedding plugin. Failure or absence of configuration
            # silently keeps the heuristic (no-embedding) recall pipeline.
            from app.core.config import get_settings
            from app.services.memory_enhancement import semantic_boosts_for_records

            semantic_boosts = semantic_boosts_for_records(
                self.db,
                self.workspace_id,
                get_settings(),
                query_text,
                eligible,
            )
        selected, conflicts = self._merge_effective(
            eligible,
            goal_id=goal_id,
            node_ids=node_id_set,
            session_id=session_id,
            now=now,
            limit=limit,
            semantic_boosts=semantic_boosts or None,
        )
        views: list[MemoryView] = []
        entries: list[str] = []
        used_tokens = 0
        accessed = 0
        for record, score in selected:
            view = self._view(record, include_content=True, retrieval_score=score)
            views.append(view)
            body = (view.content or record.title)[:2000]
            entry = (
                f"- memory_id={record.id} type={record.record_kind} "
                f"scope={record.scope_type}:{record.scope_id or '-'} "
                f"zone={record.zone} strength={record.strength:.2f} score={score:.2f}\n"
                f"  {record.title}: {body}"
            )
            if prompt_token_budget is not None:
                # The prompt block competes with history for the model input
                # budget; drop lower-ranked entries instead of overflowing.
                entry_tokens = estimate_tokens(entry)
                if entries and used_tokens + entry_tokens > prompt_token_budget:
                    continue
                used_tokens += entry_tokens
            entries.append(entry)
            if mark_access:
                record.access_count = int(getattr(record, "access_count", 0) or 0) + 1
                record.last_accessed_at = now
                record.strength = self._recompute_strength(record, now=now, goal_id=goal_id)
                accessed += 1
        if mark_access and accessed:
            self.db.commit()
        prompt_block = (
            "经工作区与 Session 策略授权的作用域记忆（动态继承，非父副本）：\n"
            + "\n".join(entries)
            if entries
            else ""
        )
        return EffectiveMemoryPackageView(
            session_id=session_id,
            goal_id=goal_id,
            node_ids=list(node_id_set),
            effective_memories=views,
            conflicts=conflicts,
            prompt_block=prompt_block,
            token_estimate=estimate_tokens(prompt_block) if prompt_block else 0,
        )

    def context_for_session(
        self,
        session_id: str,
        *,
        goal_id: str | None = None,
        node_ids: list[str] | None = None,
        query_text: str | None = None,
        prompt_token_budget: int | None = None,
    ) -> str:
        # The Profile is a human-readable current-cognition projection, not a
        # per-turn default injection block (记忆文档2-升级 §二). The recallable
        # package below ranks hot/recent/topics atoms by scope and relevance
        # instead of always pasting the whole user summary.
        if not self.policy(session_id).effective_recall_enabled:
            return ""
        package = self.effective_memory_package(
            session_id=session_id,
            goal_id=goal_id,
            node_ids=node_ids,
            limit=8,
            mark_access=True,
            query_text=query_text,
            prompt_token_budget=prompt_token_budget,
        )
        return package.prompt_block

    def list_memory_types(self) -> list[MemoryTypeDefinitionView]:
        return [
            MemoryTypeDefinitionView(
                memory_type=item.memory_type,
                default_scope=item.default_scope,
                merge_strategy=item.merge_strategy,
                decay_policy=item.decay_policy,
                requires_confirmation=item.requires_confirmation,
                description=item.description,
                payload_schema=dict(item.schema or {}),
            )
            for item in MEMORY_TYPE_REGISTRY.values()
        ]

    def create_draft(self, payload: MemoryDraftCreateRequest) -> MemoryDraftView:
        self.purge_expired()
        structured = self._validate_structured_payload(payload.structured_payload)
        auto_commit_allowed = bool(payload.auto_commit)
        if payload.created_by == "learning_agent":
            # The live agent can see assistant output, tool results, and parsed
            # files. Those mixed-authority inputs must never become user memory
            # by implication. Explicit user statements are handled separately
            # by the evidence-bound background extractor/profile intent flow.
            structured = {
                **structured,
                "atom_schema_version": 1,
                "atom_kind": str(
                    structured.get("atom_kind") or "ai_observation"
                )[:64],
                "ledger_status": "active",
                "temporal_status": str(
                    structured.get("temporal_status") or "timeless"
                ),
                "summary_eligibility": "excluded",
                "evidence_ids": [],
                "provenance": {
                    "authorship": "assistant",
                    "derived_from": "mixed_agent_context",
                    "profile_eligible": False,
                    "ineligible_reason": "untrusted_agent_context",
                },
            }
            auto_commit_allowed = False
        if payload.operation in {
            "UPDATE",
            "CORRECT",
            "CONFIRM",
            "COMPLETE",
            "CANCEL",
            "RESCHEDULE",
            "MERGE",
            "SUPERSEDE",
            "RETRACT",
            "PROMOTE",
            "DEMOTE",
            "ARCHIVE",
        }:
            if not payload.target_memory_id:
                raise AppError(
                    422,
                    "draft_target_required",
                    "This draft operation requires target_memory_id",
                )
            self.memories.require(payload.target_memory_id, "memory")
        if payload.session_id:
            source_session = self.sessions.require(payload.session_id, "session")
            if payload.created_by in {"learning_agent", "memory_extraction"}:
                workspace_policy = self._workspace_policy_values()
                # Only the workspace memory master switch and the session memory
                # switch gate contribution; learning sub-switches were removed
                # so extraction is fully automatic when memory is on.
                if (
                    not workspace_policy["workspace_enabled"]
                    or not bool(source_session.memory_enabled)
                ):
                    raise AppError(
                        409,
                        "memory_learning_disabled",
                        "This session is not allowed to contribute new memory",
                    )
        if payload.branch_session_id:
            self.sessions.require(payload.branch_session_id, "branch session")
        type_def = get_memory_type(payload.memory_type)
        proposed_scope_type = payload.proposed_scope_type or type_def.default_scope
        proposed_goal, proposed_node = self._validate_knowledge_scope(
            scope_type=proposed_scope_type,
            scope_id=payload.proposed_scope_id,
            goal_id=payload.goal_id,
            node_id=payload.node_id,
        )
        proposed_scope_id = payload.proposed_scope_id
        if proposed_scope_type == "goal":
            proposed_scope_id = proposed_goal
        elif proposed_scope_type == "node":
            proposed_scope_id = proposed_node
        draft = self.drafts.add(
            MemoryDraft(
                workspace_id=self.workspace_id,
                operation=payload.operation,
                status="PENDING",
                memory_type=payload.memory_type,
                target_memory_id=payload.target_memory_id,
                proposed_scope_type=proposed_scope_type,
                proposed_scope_id=proposed_scope_id,
                goal_id=proposed_goal,
                node_id=proposed_node,
                session_id=payload.session_id,
                branch_session_id=payload.branch_session_id,
                title=payload.title.strip() or payload.content[:80].strip() or payload.memory_type,
                content=payload.content.rstrip(),
                structured_payload=structured,
                source_refs=list(payload.source_refs or []),
                confidence=float(payload.confidence),
                importance=float(payload.importance),
                suggested_decay_policy=payload.suggested_decay_policy or type_def.decay_policy,
                conflicts_with=list(payload.conflicts_with or []),
                created_by=payload.created_by or self.actor_id,
            )
        )
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.draft.create",
            resource_type="memory_draft",
            resource_id=draft.id,
            details={
                "operation": draft.operation,
                "memory_type": draft.memory_type,
                "auto_commit_requested": payload.auto_commit,
                "auto_commit_allowed": auto_commit_allowed,
            },
        )
        self.db.commit()
        self.db.refresh(draft)
        if auto_commit_allowed and not type_def.requires_confirmation and draft.confidence >= 0.75:
            return self.decide_draft(
                draft.id,
                MemoryDraftDecisionRequest(decision="commit", reason="auto_commit_threshold"),
            )
        return self._draft_view(draft)

    def list_drafts(
        self,
        *,
        status: str | None = "PENDING",
        session_id: str | None = None,
        goal_id: str | None = None,
    ) -> list[MemoryDraftView]:
        statement = self.drafts.query()
        if status:
            statement = statement.where(MemoryDraft.status == status)
        if session_id:
            statement = statement.where(
                (MemoryDraft.session_id == session_id)
                | (MemoryDraft.branch_session_id == session_id)
            )
        if goal_id:
            statement = statement.where(MemoryDraft.goal_id == goal_id)
        drafts = self.db.scalars(statement.order_by(MemoryDraft.created_at.desc())).all()
        return [self._draft_view(item) for item in drafts]

    def get_draft(self, draft_id: str) -> MemoryDraftView:
        draft = self.drafts.require(draft_id, "memory draft")
        return self._draft_view(draft)

    def decide_draft(
        self,
        draft_id: str,
        payload: MemoryDraftDecisionRequest,
    ) -> MemoryDraftView:
        draft = self.drafts.require(draft_id, "memory draft")
        if draft.status != "PENDING":
            raise AppError(
                409,
                "draft_not_pending",
                "Only pending drafts can be decided",
                {"draft_id": draft_id, "status": draft.status},
            )
        now = utc_now()
        if payload.decision == "reject":
            draft.status = "REJECTED"
            draft.reviewed_by = self.actor_id
            draft.reviewed_at = now
            draft.rejection_reason = payload.reason or "rejected"
            self.audit.record(
                actor_id=self.actor_id,
                action="memory.draft.reject",
                resource_type="memory_draft",
                resource_id=draft.id,
                details={"reason": draft.rejection_reason},
            )
            self.db.commit()
            self.db.refresh(draft)
            return self._draft_view(draft)

        # commit path
        source_ids = []
        for ref in draft.source_refs or []:
            if isinstance(ref, dict) and ref.get("id"):
                source_ids.append(str(ref["id"]))
            elif isinstance(ref, str):
                source_ids.append(ref)

        if draft.operation == "CREATE":
            created = self.create(
                MemoryCreateRequest(
                    title=draft.title or draft.memory_type,
                    content=draft.content or draft.title or draft.memory_type,
                    namespace="session" if draft.proposed_scope_type == "session" else "workspace",
                    session_id=draft.session_id if draft.proposed_scope_type == "session" else None,
                    scope_type=draft.proposed_scope_type,  # type: ignore[arg-type]
                    scope_id=draft.proposed_scope_id,
                    goal_id=draft.goal_id,
                    node_id=draft.node_id,
                    zone="topics",
                    record_kind=draft.memory_type,
                    structured_payload=dict(draft.structured_payload or {}),
                    confidence=float(draft.confidence),
                    importance=float(draft.importance),
                    source=f"draft:{draft.created_by}",
                    source_ids=source_ids,
                )
            )
            draft.result_memory_id = created.id
            draft.result_revision = created.revision
        elif draft.operation in {
            "UPDATE",
            "CORRECT",
            "CONFIRM",
            "COMPLETE",
            "CANCEL",
            "MERGE",
        }:
            assert draft.target_memory_id
            structured_payload = dict(draft.structured_payload or {})
            if draft.operation == "COMPLETE":
                structured_payload["temporal_status"] = "completed"
                structured_payload.setdefault(
                    "summary_eligibility", "historical"
                )
                structured_payload["last_verified_at"] = now.isoformat()
            elif draft.operation == "CANCEL":
                structured_payload["temporal_status"] = "cancelled"
                structured_payload["summary_eligibility"] = "excluded"
                structured_payload["last_verified_at"] = now.isoformat()
            elif draft.operation == "CONFIRM":
                structured_payload["last_verified_at"] = now.isoformat()
            updated = self.update(
                draft.target_memory_id,
                MemoryUpdateRequest(
                    content=draft.content or None,
                    title=draft.title or None,
                    structured_payload=structured_payload or None,
                    source_ids=source_ids or None,
                    scope_type=draft.proposed_scope_type,  # type: ignore[arg-type]
                    scope_id=draft.proposed_scope_id,
                    goal_id=draft.goal_id,
                    node_id=draft.node_id,
                    importance=float(draft.importance),
                    confidence=float(draft.confidence),
                    reason=f"draft_commit:{draft.operation.lower()}",
                ),
            )
            draft.result_memory_id = updated.id
            draft.result_revision = updated.revision
        elif draft.operation in {"SUPERSEDE", "RESCHEDULE"}:
            # A supersession keeps the old record as auditable history: the
            # draft content becomes a NEW memory that records its lineage via
            # ``supersedes_id`` while the superseded record moves to the cold
            # archive zone (out of recall, still exportable/restorable).
            assert draft.target_memory_id
            created = self.create(
                MemoryCreateRequest(
                    title=draft.title or draft.memory_type,
                    content=draft.content or draft.title or draft.memory_type,
                    namespace="session" if draft.proposed_scope_type == "session" else "workspace",
                    session_id=draft.session_id if draft.proposed_scope_type == "session" else None,
                    scope_type=draft.proposed_scope_type,  # type: ignore[arg-type]
                    scope_id=draft.proposed_scope_id,
                    goal_id=draft.goal_id,
                    node_id=draft.node_id,
                    zone="topics",
                    record_kind=draft.memory_type,
                    structured_payload=dict(draft.structured_payload or {}),
                    confidence=float(draft.confidence),
                    importance=float(draft.importance),
                    source=f"draft:{draft.created_by}",
                    source_ids=source_ids,
                )
            )
            successor = self.memories.require(created.id, "memory")
            successor.supersedes_id = draft.target_memory_id
            target = self.memories.require(draft.target_memory_id, "memory")
            target_structured = dict(target.structured_payload or {})
            target_structured["ledger_status"] = "superseded"
            target_structured["summary_eligibility"] = "historical"
            if draft.operation == "RESCHEDULE":
                target_structured["temporal_status"] = "rescheduled"
            self.update(
                draft.target_memory_id,
                MemoryUpdateRequest(
                    zone="archive",
                    structured_payload=target_structured,
                    reason=f"superseded_by:{created.id}",
                ),
            )
            draft.result_memory_id = created.id
            draft.result_revision = created.revision
        elif draft.operation == "RETRACT":
            assert draft.target_memory_id
            self.delete(draft.target_memory_id)
            draft.result_memory_id = draft.target_memory_id
        elif draft.operation == "ARCHIVE":
            assert draft.target_memory_id
            updated = self.update(
                draft.target_memory_id,
                MemoryUpdateRequest(zone="archive", reason="draft_archive"),
            )
            draft.result_memory_id = updated.id
            draft.result_revision = updated.revision
        elif draft.operation == "PROMOTE":
            assert draft.target_memory_id
            # Promote node→goal→workspace one level.
            target = self.memories.require(draft.target_memory_id, "memory")
            next_scope = "workspace"
            next_scope_id = None
            next_goal = target.goal_id
            next_node = None
            if (target.scope_type or "workspace") == "node":
                next_scope = "goal"
                next_scope_id = target.goal_id
                next_goal = target.goal_id
            elif target.scope_type == "goal":
                next_scope = "workspace"
                next_scope_id = None
                next_goal = None
            updated = self.update(
                draft.target_memory_id,
                MemoryUpdateRequest(
                    scope_type=next_scope,  # type: ignore[arg-type]
                    scope_id=next_scope_id,
                    goal_id=next_goal,
                    node_id=next_node,
                    reason="draft_promote",
                ),
            )
            draft.result_memory_id = updated.id
            draft.result_revision = updated.revision
        elif draft.operation == "DEMOTE":
            assert draft.target_memory_id
            updated = self.update(
                draft.target_memory_id,
                MemoryUpdateRequest(
                    scope_type=draft.proposed_scope_type,  # type: ignore[arg-type]
                    scope_id=draft.proposed_scope_id,
                    goal_id=draft.goal_id,
                    node_id=draft.node_id,
                    reason="draft_demote",
                ),
            )
            draft.result_memory_id = updated.id
            draft.result_revision = updated.revision
        else:
            raise AppError(422, "unsupported_draft_operation", f"Unsupported operation {draft.operation}")

        draft.status = "COMMITTED"
        draft.reviewed_by = self.actor_id
        draft.reviewed_at = now
        draft.rejection_reason = payload.reason or ""
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.draft.commit",
            resource_type="memory_draft",
            resource_id=draft.id,
            details={
                "operation": draft.operation,
                "result_memory_id": draft.result_memory_id,
                "result_revision": draft.result_revision,
            },
        )
        self.db.commit()
        self.db.refresh(draft)
        return self._draft_view(draft)

    def resolve_misconception(
        self,
        memory_id: str,
        *,
        resolution_status: str,
        evidence_note: str = "",
    ) -> MemoryView:
        if resolution_status not in {
            "active_misconception",
            "improving",
            "resolved",
            "recurring",
            "none",
        }:
            raise AppError(422, "invalid_resolution_status", "Invalid resolution_status")
        record = self._require_active(memory_id)
        if record.record_kind != "misconception" and resolution_status != "none":
            # Allow any memory to carry resolution metadata, but prefer misconception type.
            pass
        payload_data = dict(getattr(record, "structured_payload", None) or {})
        if evidence_note:
            notes = list(payload_data.get("resolution_evidence") or [])
            notes.append(evidence_note)
            payload_data["resolution_evidence"] = notes[-20:]
        return self.update(
            memory_id,
            MemoryUpdateRequest(
                resolution_status=resolution_status,  # type: ignore[arg-type]
                structured_payload=payload_data,
                reason=f"resolution:{resolution_status}",
            ),
        )

    def archive_goal_memories(self, goal_id: str) -> dict[str, int]:
        """Phase 4: cold-archive goal-lifecycle memories when a goal closes."""

        self.purge_expired()
        records = self.db.scalars(
            self.memories.query().where(
                MemoryRecord.state == "active",
                MemoryRecord.goal_id == goal_id,
                MemoryRecord.zone != "archive",
            )
        ).all()
        archived = 0
        for record in records:
            type_def = get_memory_type(record.record_kind)
            if type_def.decay_policy not in {"GOAL_LIFECYCLE", "FAST_VALIDATION_DECAY", "SLOW"}:
                continue
            if record.record_kind in {"learning_preference"} and record.scope_type == "workspace":
                continue
            self.update(
                record.id,
                MemoryUpdateRequest(zone="archive", reason=f"goal_closed:{goal_id}"),
            )
            archived += 1
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.goal_archive",
            resource_type="goal",
            resource_id=goal_id,
            details={"archived": archived},
        )
        self.db.commit()
        return {"archived": archived}

    def build_goal_overview(self, goal_id: str, *, max_chars: int = 9_000) -> str:
        """Phase 4: ~3k token hot overview text for one goal."""

        package = self.effective_memory_package(
            goal_id=goal_id,
            limit=24,
            mark_access=False,
        )
        lines = [f"# Goal Overview ({goal_id})", ""]
        for view in package.effective_memories:
            if view.zone == "archive":
                continue
            lines.append(f"## {view.title} ({view.record_kind}/{view.scope_type})")
            body = (view.content or "").strip()
            if body.startswith("# "):
                body = "\n".join(body.splitlines()[1:]).strip()
            lines.append(body[:1_200])
            lines.append("")
        text = "\n".join(lines).strip() + "\n"
        if len(text) > max_chars:
            text = text[: max_chars - 20].rstrip() + "\n\n…\n"
        return text

    def search_conversation_history(
        self,
        *,
        query: str,
        goal_id: str | None = None,
        session_id: str | None = None,
        include_descendant_branches: bool = True,
        top_k: int = 8,
        max_chars: int = 2_500,
    ) -> dict:
        """Phase 2 tool: keyword search over persisted messages (no silent full dump)."""

        q = (query or "").strip().casefold()
        if not q:
            raise AppError(422, "empty_query", "query is required")
        sessions = list(self.db.scalars(self.sessions.query()).all())
        if goal_id:
            sessions = [item for item in sessions if item.goal_id == goal_id]
        if session_id:
            root_ids = {session_id}
            if include_descendant_branches:
                for item in sessions:
                    if item.parent_session_id == session_id or item.id == session_id:
                        root_ids.add(item.id)
            sessions = [item for item in sessions if item.id in root_ids]
        session_ids = {item.id for item in sessions}
        if not session_ids:
            return {"summary": "No matching sessions.", "hits": [], "source_refs": []}
        messages = self.db.scalars(
            select(Message)
            .where(
                Message.workspace_id == self.workspace_id,
                Message.session_id.in_(session_ids),
                Message.status == "completed",
            )
            .order_by(Message.created_at.desc())
            .limit(400)
        ).all()
        hits: list[dict] = []
        for message in messages:
            content = (message.content or "").strip()
            if not content:
                continue
            if q not in content.casefold() and q not in (message.role or "").casefold():
                # loose token match
                tokens = [token for token in q.split() if token]
                if not tokens or not all(token in content.casefold() for token in tokens):
                    continue
            snippet = content[:400]
            hits.append(
                {
                    "session_id": message.session_id,
                    "message_id": message.id,
                    "role": message.role,
                    "snippet": snippet,
                    "created_at": message.created_at.isoformat() if message.created_at else None,
                }
            )
            if len(hits) >= top_k:
                break
        summary_parts = [f"{item['role']}: {item['snippet'][:160]}" for item in hits[:3]]
        summary = "；".join(summary_parts) if summary_parts else "No matching history messages."
        if len(summary) > max_chars:
            summary = summary[: max_chars - 1] + "…"
        return {
            "summary": summary,
            "hits": hits,
            "source_refs": [
                {"type": "message", "id": item["message_id"], "session_id": item["session_id"]}
                for item in hits
            ],
            "relevance": 0.9 if hits else 0.0,
        }

    def read_conversation_segment(
        self,
        *,
        session_id: str,
        around_message_id: str | None = None,
        limit: int = 12,
    ) -> dict:
        self.sessions.require(session_id, "session")
        messages = list(
            self.db.scalars(
                select(Message)
                .where(
                    Message.workspace_id == self.workspace_id,
                    Message.session_id == session_id,
                    Message.status == "completed",
                )
                .order_by(Message.created_at.asc())
            ).all()
        )
        if not messages:
            return {"session_id": session_id, "messages": []}
        if around_message_id:
            index = next((i for i, item in enumerate(messages) if item.id == around_message_id), None)
            if index is None:
                raise AppError(404, "message_not_found", "Message not found in session")
            start = max(0, index - limit // 2)
            window = messages[start : start + limit]
        else:
            window = messages[-limit:]
        return {
            "session_id": session_id,
            "messages": [
                {
                    "message_id": item.id,
                    "role": item.role,
                    "content": (item.content or "")[:2_000],
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                }
                for item in window
            ],
        }

    def get_memory_evidence(self, memory_id: str) -> dict:
        record = self.memories.require(memory_id, "memory")
        revision = self._ensure_revision(record) if record.state == "active" else self._latest_revision(record.id)
        evidence: list[dict] = []
        for source_id in list(record.source_ids or []):
            typed = self.db.scalar(
                select(MemoryEvidence).where(
                    MemoryEvidence.workspace_id == self.workspace_id,
                    MemoryEvidence.id == source_id,
                )
            )
            if typed is not None:
                evidence.append(
                    {
                        "type": typed.source_kind,
                        "id": typed.id,
                        "source_id": typed.source_id,
                        "message_id": typed.message_id,
                        "file_id": typed.file_id,
                        "authorship": typed.authorship,
                        "profile_eligible": typed.profile_eligible,
                        "eligibility_reason": typed.eligibility_reason,
                        "snippet": typed.excerpt[:500],
                        "derived_from": list(typed.derived_from or []),
                    }
                )
                continue
            message = self.db.get(Message, source_id)
            if message is not None and message.workspace_id == self.workspace_id:
                evidence.append(
                    {
                        "type": "message",
                        "id": message.id,
                        "session_id": message.session_id,
                        "snippet": (message.content or "")[:500],
                    }
                )
            else:
                evidence.append({"type": "source_ref", "id": source_id})
        return {
            "memory_id": record.id,
            "revision": record.revision,
            "title": record.title,
            "content": revision.content if revision is not None else None,
            "source_ids": list(record.source_ids or []),
            "evidence": evidence,
            "scope": {
                "scope_type": getattr(record, "scope_type", "workspace"),
                "scope_id": getattr(record, "scope_id", None),
                "goal_id": getattr(record, "goal_id", None),
                "node_id": getattr(record, "node_id", None),
                "session_id": record.session_id,
            },
        }

    def export_markdown(self) -> bytes:
        self.purge_expired()
        records = self.db.scalars(
            self.memories.query()
            .where(MemoryRecord.state == "active")
            .order_by(MemoryRecord.zone, MemoryRecord.updated_at.desc())
        ).all()
        manifest_records: list[dict] = []
        buffer = BytesIO()
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
            for record in records:
                revision = self._ensure_revision(record)
                if revision is None or revision.content is None:
                    continue
                export_path = f"{record.zone}/{record.id}.md"
                archive.writestr(export_path, _render_markdown(revision.title, revision.content))
                manifest_records.append(
                    {
                        "lg_memory_id": record.id,
                        "revision": record.revision,
                        "content_sha256": record.content_hash,
                        "namespace": record.namespace,
                        "session_id": record.session_id,
                        "record_kind": record.record_kind,
                        "zone": record.zone,
                        "source_ids": list(record.source_ids or []),
                        "provider_id": record.provider_id,
                        "path": export_path,
                    }
                )
            manifest = {
                "schema_version": "1.0",
                "workspace_id": self.workspace_id,
                "active_provider_id": self.provider.provider_id,
                "provider_epoch": self._provider_epoch(),
                "exported_at": utc_now().isoformat(),
                "records": manifest_records,
            }
            archive.writestr(
                "memory-manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        self.audit.record(
            actor_id=self.actor_id,
            action="memory.export",
            resource_type="memory_export",
            resource_id=self.workspace_id,
            details={"record_count": len(manifest_records), "format": "markdown_zip"},
        )
        self.db.commit()
        return buffer.getvalue()
