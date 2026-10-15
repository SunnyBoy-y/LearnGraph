"""Guarded, source-only repair of memory lifecycle consistency (no database access)."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1] / 'app'
changes = {}
def get(path):
    if path not in changes:
        changes[path] = (ROOT / path).read_text(encoding='utf-8')
    return changes[path]
def put(path, text):
    ast.parse(text)
    changes[path] = text
def replace(path, old, new, count=1):
    text = get(path)
    assert text.count(old) == count, (path, old[:100], text.count(old))
    put(path, text.replace(old, new, count))
def method(path, name, transform):
    text = get(path)
    node = next(n for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
    lines = text.splitlines(keepends=True)
    original = ''.join(lines[node.lineno-1:node.end_lineno])
    updated = transform(original)
    put(path, ''.join(lines[:node.lineno-1]) + updated + ''.join(lines[node.end_lineno:]))

p = 'services/memory.py'
replace(p, 'from __future__ import annotations', '''from __future__ import annotations

from contextlib import contextmanager
from sqlalchemy import update as atomic_sql_update''')
replace(p, '        self.audit = AuditRepository(db, workspace.id)', '''        self.audit = AuditRepository(db, workspace.id)
        self._atomic_write_active = False
        self._after_commit = []''')
replace(p, '    def _event_store(self):', '''    @contextmanager
    def atomic_write(self):
        if self._atomic_write_active:
            yield
            return
        self._atomic_write_active = True
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self._after_commit.clear()
            raise
        finally:
            self._atomic_write_active = False
        callbacks, self._after_commit = self._after_commit, []
        for callback in callbacks:
            callback()

    def _commit_write(self):
        if self._atomic_write_active:
            self.db.flush()
        else:
            self.db.commit()

    def _event_store(self):''')
for name in ('_check_revision', 'create', 'update', 'delete', 'create_draft', 'decide_draft'):
    method(p, name, lambda s: s.replace('self.db.commit()', 'self._commit_write()'))
method(p, 'purge_expired', lambda s: s.replace('        now = ', '''        if self._atomic_write_active:
            return {"content_keys_destroyed": 0, "journal_entries_removed": 0}
        now = ''', 1))
method(p, '_project_after_commit', lambda s: s.replace('''        try:
''', '''        if self._atomic_write_active:
            self._after_commit.append(lambda: self._project_after_commit(
                record, canonical, previous_provider_id=previous_provider_id,
                previous_binding_id=previous_binding_id))
            return
        try:
''', 1))
method(p, '_delete_projection_after_commit', lambda s: s.replace('        if not provider_binding_id:', '''        if self._atomic_write_active:
            self._after_commit.append(lambda: self._delete_projection_after_commit(
                memory_id=memory_id, provider_id=provider_id,
                provider_binding_id=provider_binding_id))
            return
        if not provider_binding_id:''', 1).replace('self.bindings.query().where(MemoryProviderBinding.memory_id == memory_id)', '''self.bindings.query().where(
                    MemoryProviderBinding.memory_id == memory_id,
                    MemoryProviderBinding.provider_instance_id == provider_id,
                    MemoryProviderBinding.provider_record_id == provider_binding_id,
                )''').replace('            from app.domain.memory_event_models import MemoryProjectionOutbox', '''            # Remaining bindings are deliberately left for the durable projector.
            self.db.commit()
            return
            from app.domain.memory_event_models import MemoryProjectionOutbox'''))
# Drop unreachable completion code rather than falsely completing a provider kind.
method(p, '_delete_projection_after_commit', lambda s: s[:s.index('            from app.domain.memory_event_models import MemoryProjectionOutbox')] + s[s.index('        except Exception:'):])
replace(p, '''    def decide_draft(
''', '''    def decide_draft(self, draft_id: str, payload: MemoryDraftDecisionRequest) -> MemoryDraftView:
        with self.atomic_write():
            return self._decide_draft(draft_id, payload)

    def _decide_draft(
''')
method(p, '_decide_draft', lambda s: s.replace('        draft = self.drafts.require(draft_id, "memory draft")', '''        claimed = self.db.execute(atomic_sql_update(MemoryDraft).where(
            MemoryDraft.workspace_id == self.workspace_id,
            MemoryDraft.id == draft_id, MemoryDraft.status == "PENDING",
        ).values(status="PENDING").execution_options(synchronize_session=False)).rowcount
        if not claimed:
            existing = self.drafts.require(draft_id, "memory draft")
            self.db.refresh(existing)
            if existing.status == "COMMITTED" and payload.decision == "commit":
                return self._draft_view(existing)
            raise AppError(409, "draft_not_pending", "Only pending drafts can be decided")
        draft = self.drafts.require(draft_id, "memory draft")
        self.db.refresh(draft)''', 1))
replace(p, 'if auto_commit_allowed and not type_def.requires_confirmation and draft.confidence >= 0.75:', 'if auto_commit_allowed and (draft.created_by != "memory_extraction" or draft.operation == "CREATE") and not type_def.requires_confirmation and draft.confidence >= 0.75:')
# Explicitly accepted MERGE retires only named, same-scope source records.
replace(p, '''            draft.result_memory_id = updated.id
            draft.result_revision = updated.revision
        elif draft.operation in {"SUPERSEDE", "RESCHEDULE"}:''', '''            if draft.operation == "MERGE":
                target = self.memories.require(updated.id, "memory")
                for conflict_id in dict.fromkeys(draft.conflicts_with or []):
                    if conflict_id == target.id:
                        continue
                    other = self._require_active(conflict_id)
                    if (other.subject_user_id, other.scope_type, other.scope_id) != (
                        target.subject_user_id, target.scope_type, target.scope_id
                    ):
                        raise AppError(409, "memory_merge_scope_conflict", "Merge targets must have the same owner and scope")
                    structured = dict(other.structured_payload or {})
                    structured.update(ledger_status="superseded", summary_eligibility="historical")
                    other.superseded_by_id = target.id
                    self.update(other.id, MemoryUpdateRequest(
                        zone="archive", structured_payload=structured, reason=f"merged_into:{target.id}"))
            draft.result_memory_id = updated.id
            draft.result_revision = updated.revision
        elif draft.operation in {"SUPERSEDE", "RESCHEDULE"}:''')

p = 'services/memory_commands.py'
method(p, 'feedback', lambda s: s.replace('        payload: dict[str, Any],', '''        payload: dict[str, Any],
        commit: bool = True,''', 1).replace('            expected_version=None,', '''            expected_version=None,
            stream_subject_user_id=None if feedback_type == "project_only" else record.subject_user_id,''', 1).replace('        self.db.commit()', '''        if commit:
            self.db.commit()
        else:
            self.db.flush()''', 1))
# Live feedback and replay use exactly the same projection implementation.
method(p, 'feedback', lambda s: s[:s.index('        document = self.db.scalar(')] + '''        from app.services.memory_projector import MemoryProjector
        MemoryProjector(self.db).apply(result.event, {
            "memory_id": memory_id, "feedback_type": feedback_type,
        })
        record.head_event_id = result.event.event_id
        record.projection_version = result.event.stream_version
''' + s[s.index('        if commit:'):])
replace(p, '            "memory_id": record.id,', '''            "memory_id": record.id,
            "subject_user_id": record.subject_user_id,
            "state": record.state,
            "zone": record.zone,
            "atom_schema_version": record.atom_schema_version,
            "ledger_status": record.ledger_status,
            "temporal_status": record.temporal_status,
            "summary_eligibility": record.summary_eligibility,
            "supersedes_id": record.supersedes_id,
            "superseded_by_id": record.superseded_by_id,
            "auto_recall_suppressed": record.auto_recall_suppressed,
            "child_agent_denied": record.child_agent_denied,
            "valid_from": record.valid_from.isoformat() if record.valid_from else None,
            "valid_until": record.valid_until.isoformat() if record.valid_until else None,''')
# Scrub recoverable copies in the same transaction as the forget tombstone.
method(p, 'forget', lambda s: s.replace('        record.title = "[forgotten]"', '''        from app.domain.models import MemoryJournalEntry, MemoryDeletionRecovery, MemoryProfileSnapshot, MemoryDraft
        now = utc_now()
        for journal in self.db.scalars(select(MemoryJournalEntry).where(
            MemoryJournalEntry.workspace_id == scope.workspace_id, MemoryJournalEntry.memory_id == memory_id
        )):
            journal.payload = {}
            journal.content_hash = ""
            journal.tombstone = True
            journal.recoverable_until = None
            journal.content_scrubbed_at = now
        for recovery in self.db.scalars(select(MemoryDeletionRecovery).where(
            MemoryDeletionRecovery.workspace_id == scope.workspace_id, MemoryDeletionRecovery.memory_id == memory_id
        )):
            recovery.encrypted_payload = None
            recovery.recoverable_until = now
            recovery.destroyed_at = now
        for feedback in self.db.scalars(select(MemoryFeedback).where(
            MemoryFeedback.workspace_id == scope.workspace_id, MemoryFeedback.memory_id == memory_id
        )):
            feedback.payload_json = {}
        for draft in self.db.scalars(select(MemoryDraft).where(
            MemoryDraft.workspace_id == scope.workspace_id,
            (MemoryDraft.target_memory_id == memory_id) | (MemoryDraft.result_memory_id == memory_id),
        )):
            draft.title = "[forgotten]"
            draft.content = ""
            draft.structured_payload = {}
            draft.source_refs = []
            draft.status = "REJECTED"
            draft.rejection_reason = "source_forgotten"
        for snapshot in self.db.scalars(select(MemoryProfileSnapshot).where(
            MemoryProfileSnapshot.workspace_id == scope.workspace_id
        )):
            refs = set(snapshot.source_atom_ids or []) | {i for ids in (snapshot.claim_atom_map or {}).values() for i in ids}
            if memory_id in refs:
                snapshot.markdown = ""
                snapshot.structured_sections = []
                snapshot.source_atom_ids = []
                snapshot.claim_atom_map = {}
                snapshot.source_fingerprint = ""
                snapshot.status = "stale"
                snapshot.stale_reason = "source_forgotten"
        record.title = "[forgotten]"''', 1))

p = 'services/memory_event_store.py'
method(p, 'append', lambda s: s.replace('subject_user_id=scope.principal_user_id,', 'subject_user_id=stream.subject_user_id,'))

p = 'services/memory_projector.py'
replace(p, 'from __future__ import annotations', '''from __future__ import annotations

from datetime import datetime''')
replace(p, '        stream_status = self.db.scalar(select(MemoryStream.status).where(', '        stream = self.db.scalar(select(MemoryStream).where(')
replace(p, '        if stream_status == "forgotten":', '        if stream is not None and stream.status == "forgotten":')
replace(p, '        if event.event_type in _PATCH_EVENTS:', '''        if event.event_type == MemoryEventType.MEMORY_FEEDBACK_RECORDED:
            document = self.db.scalar(select(MemorySearchDocument).where(
                MemorySearchDocument.tenant_id == event.tenant_id,
                MemorySearchDocument.workspace_id == event.workspace_id,
                MemorySearchDocument.target_type == "memory", MemorySearchDocument.target_id == memory_id,
            ))
            if document is None:
                return
            kind = (payload or {}).get("feedback_type")
            if kind in {"wrong", "stale", "suppress_auto_recall"}:
                document.status = {"wrong": "disputed", "stale": "stale", "suppress_auto_recall": "suppressed"}[kind]
            elif kind == "project_only":
                document.subject_user_id = None
                document.memory_layer = "L4"
            elif kind == "deny_child" and document.sensitivity in {"public", "normal"}:
                document.sensitivity = "private"
            document.source_event_id = event.event_id
            document.target_version = event.stream_version
            self.db.flush()
            if document.status == "active":
                self._upsert_fts(document)
            else:
                self._delete_fts_row(document.id)
            return

        if event.event_type in _PATCH_EVENTS:''')
replace(p, '        title = str(payload.get("title") or "")', '''        hidden = (payload.get("state", "active") != "active"
                  or payload.get("ledger_status", "active") != "active"
                  or payload.get("zone") == "archive"
                  or bool(payload.get("auto_recall_suppressed"))
                  or (int(payload.get("atom_schema_version") or 0) >= 1
                      and payload.get("summary_eligibility", "durable") in {"historical", "excluded"}))
        if hidden:
            self._remove_search_document(memory_id)
            return
        subject_user_id = payload.get("subject_user_id", stream.subject_user_id if stream is not None else event.subject_user_id)
        title = str(payload.get("title") or "")''')
method(p, '_apply_memory', lambda s: s.replace('subject_user_id=event.subject_user_id,', 'subject_user_id=subject_user_id,').replace('existing.subject_user_id = event.subject_user_id', 'existing.subject_user_id = subject_user_id').replace('        existing.importance = ', '''        if payload.get("child_agent_denied") and existing.sensitivity in {"public", "normal"}:
            existing.sensitivity = "private"
        for field in ("valid_from", "valid_until"):
            if field in payload:
                value = payload[field]
                setattr(existing, field, datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None)
        existing.importance = ''', 1))

p = 'api/routers/memory_v2.py'
replace(p, '    row = MemoryCommandService(db, event_store(db, settings)).feedback(', '''    if payload.feedback_type == "wrong" and (
        "replacement_content" in payload.payload or "replacement_title" in payload.payload
    ):
        from pydantic import ValidationError
        from app.api.routers.memory import service
        from app.domain.schemas.management import MemoryUpdateRequest
        title = payload.payload.get("replacement_title", record.title)
        content = payload.payload.get("replacement_content")
        if not isinstance(title, str) or not title.strip() or not isinstance(content, str) or not content.strip():
            raise AppError(422, "memory_replacement_invalid", "Replacement title and content are required")
        try:
            correction = MemoryUpdateRequest(title=title, content=content,
                expected_revision=payload.payload.get("expected_revision", record.revision), reason="user_edit")
        except ValidationError as exc:
            raise AppError(422, "memory_replacement_invalid", "Invalid replacement fields") from exc
        writer = service(db, context, settings)
        commands = MemoryCommandService(db, event_store(db, settings))
        with writer.atomic_write():
            row = commands.feedback(memory_scope(context), memory_id, actor_id=context.principal.user_id,
                feedback_type="wrong", payload=payload.payload, commit=False)
            record.lifecycle_status = "active"
            updated = writer.update(memory_id, correction)
        return {"feedback_id": row.id, "applied_event_id": record.head_event_id,
                "memory_id": updated.id, "revision": updated.revision}
    row = MemoryCommandService(db, event_store(db, settings)).feedback(''')

for path, source in changes.items():
    ast.parse(source)
for path, source in changes.items():
    destination = ROOT / path
    raw = destination.read_bytes()
    newline = '\r\n' if b'\r\n' in raw else '\n'
    destination.write_bytes(source.replace('\n', newline).encode('utf-8'))
    print('updated', path)
