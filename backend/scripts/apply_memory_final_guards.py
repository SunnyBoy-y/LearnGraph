from pathlib import Path
import ast
from normalize_patch_literals import normalize
ROOT = Path(__file__).resolve().parents[1] / 'app'
changes = {}
def load(name):
    p = ROOT / name
    raw = p.read_bytes()
    changes[name] = (p, raw, '')
    return raw.decode().replace(chr(13)+chr(10), chr(10))
def put(name, s):
    p, raw, _ = changes[name]
    s = normalize(s)
    ast.parse(s)
    changes[name] = (p, raw, s)
def rep(s, old, new):
    expected = 2 if "self._note_refresh_failure(exc)" in old else 1
    assert s.count(old) == expected, (old[:90], s.count(old))
    return s.replace(old,new,expected)

s=load('services/context_builder.py')
s=rep(s, '                StrategyMemory.status == "verified",', '''                StrategyMemory.status == "verified",
                (StrategyMemory.subject_user_id.is_(None)) | (StrategyMemory.subject_user_id == scope.principal_user_id),''')
put('services/context_builder.py',s)

s=load('services/memory_profile.py')
s=rep(s, 'def _profile_atom_query(workspace_id: str):', 'def _profile_atom_query(workspace_id: str, owner_subject_id: str | None = None):')
s=rep(s, 'def _visible_atom_query(workspace_id: str):', 'def _visible_atom_query(workspace_id: str, owner_subject_id: str | None = None):')
a=s.index('def _profile_atom_query('); b=s.index('def _eligible_profile_records(',a)
block=s[a:b]
old='            MemoryRecord.workspace_id == workspace_id,'
assert block.count(old)==2
block=block.replace(old, '''            MemoryRecord.workspace_id == workspace_id,
            MemoryRecord.tenant_id == select(Workspace.tenant_id).where(Workspace.id == workspace_id).scalar_subquery(),
            (MemoryRecord.subject_user_id.is_(None)) | (MemoryRecord.subject_user_id == owner_subject_id),
            MemoryRecord.lifecycle_status == "active",
            MemoryRecord.deleted_at.is_(None), MemoryRecord.content_destroyed_at.is_(None),
            MemoryRecord.auto_recall_suppressed.is_(False),
            MemoryRecord.zone != "archive",
            (MemoryRecord.valid_until.is_(None)) | (MemoryRecord.valid_until >= utc_now()),''')
s=s[:a]+block+s[b:]
s=rep(s, '    db: Session, workspace_id: str\n) -> list[MemoryRecord]:', '    db: Session, workspace_id: str, owner_subject_id: str | None = None\n) -> list[MemoryRecord]:')
s=rep(s, '_profile_atom_query(workspace_id)).all()', '_profile_atom_query(workspace_id, owner_subject_id)).all()')
s=s.replace('_eligible_profile_records(self.db, self.workspace_id)', '_eligible_profile_records(self.db, self.workspace_id, self.actor_id)')
s=rep(s, '_eligible_profile_records(db, workspace_id)', '_eligible_profile_records(db, workspace_id, owner_subject_id)')
s=rep(s, '_visible_atom_query(self.workspace_id)', '_visible_atom_query(self.workspace_id, self.actor_id)')
a=s.index('def current_profile_prompt('); b=s.index('def _atomization_schema',a)
block=s[a:b]
block=rep(block, '            MemoryProfileSnapshot.status == "ready",\n', '')
block=rep(block,'    if snapshot is None or not snapshot.markdown.strip():', '    if snapshot is None or snapshot.status != "ready" or str(snapshot.stale_reason).startswith("refresh_failed") or not snapshot.markdown.strip():')
s=s[:a]+block+s[b:]
a=s.index('    def _note_refresh_failure('); b=s.index('    def refresh_profile(',a)
block=s[a:b]
block=rep(block,'        if snapshot is None:\n            return', '''        if snapshot is None:
            snapshot = MemoryProfileSnapshot(workspace_id=self.workspace_id, owner_subject_id=self.actor_id,
                version=1, status="failed", source_fingerprint="")
            self.db.add(snapshot)
        snapshot.status = "failed"''')
s=s[:a]+block+s[b:]
s=rep(s,'    def refresh_profile(self, *, force: bool = False) -> MemoryProfileView:', '''    def refresh_profile(self, *, force: bool = False) -> MemoryProfileView:
        latest = self.db.scalar(select(MemoryProfileSnapshot).where(
            MemoryProfileSnapshot.workspace_id == self.workspace_id,
            MemoryProfileSnapshot.owner_subject_id == self.actor_id,
        ).order_by(MemoryProfileSnapshot.version.desc()).limit(1))
        if latest is not None and str(latest.stale_reason or "").startswith("refresh_failed|"):
            failed_at = _parse_iso(str(latest.stale_reason).rsplit("|", 1)[-1])
            if failed_at is not None and utc_now() - failed_at < timedelta(seconds=60):
                return self._profile_view(latest)
        try:
            return self._refresh_profile(force=force)
        except Exception as exc:
            self.db.rollback()
            self._note_refresh_failure(exc)
            raise

    def _refresh_profile(self, *, force: bool = False) -> MemoryProfileView:''')
s=rep(s, '        except AppError as exc:\n            self._note_refresh_failure(exc)\n            raise', '        except AppError:\n            raise')
put('services/memory_profile.py',s)

s=load('api/routers/memory_v2.py')
s=rep(s, '            updated = writer.update(memory_id, correction)', '''            updated = writer.update(memory_id, correction)
            if settings.memory_write_mode == "legacy":
                from app.services.memory_projector import MemoryProjector
                event = commands.mirror_legacy_record(memory_scope(context), record,
                    content=content.rstrip(), operation="UPDATE", actor_id=context.principal.user_id)
                MemoryProjector(db).apply(event, commands._payload(record, content.rstrip()))''')
put('api/routers/memory_v2.py',s)

s=load('services/memory_commands.py')
s=rep(s, '            recovery.encrypted_payload = None\n            recovery.recoverable_until = now\n            recovery.destroyed_at = now', '''            from app.services.memory_vault import MemoryRecoveryVault
            from app.core.config import get_settings
            MemoryRecoveryVault(get_settings().memory_root, scope.workspace_id).destroy(memory_id)
            recovery.encrypted_payload = None
            recovery.recoverable_until = now
            recovery.destroyed_at = now''')
put('services/memory_commands.py',s)

s=load('services/chat.py')
s=rep(s,'        uncovered = [item for item in older if item.id not in covered_ids]', '''        import hashlib as summary_hashlib
        source_hash = summary_hashlib.sha256(
            chr(10).join(f"{item.id}:{item.content}" for item in older if item.id in covered_ids).encode("utf-8")
        ).hexdigest()
        if latest.source_hash != source_hash:
            return mechanical(older), "mechanical"
        uncovered = [item for item in older if item.id not in covered_ids]''')
put('services/chat.py',s)

s=load('providers/remote/openai.py')
a=s.index('    def generate_json(', s.index('class OpenAICompatibleChatProvider')); b=s.index('\n\nclass QwenChatProvider',a)
block=s[a:b]
block=rep(block,'        result = _parse_json_object_text(content)\n','')
block=rep(block,'        return self._validate_structured_result(result, schema)', '        result = _parse_json_object_text(content)\n        return self._validate_structured_result(result, schema)')
s=s[:a]+block+s[b:]
put('providers/remote/openai.py',s)

s=load('services/memory_job_guard.py')
s=rep(s, '            delay = min(600, 30 * (2 ** max(0, row.attempt_count - 1)))', '''            if status in {"disabled", "policy_disabled", "too_short", "below_threshold", "fresh", "fresh_enough", "unconfigured"}:
                row.attempt_count = 0
            delay = min(600, 30 * (2 ** max(0, row.attempt_count - 1)))''')
put('services/memory_job_guard.py',s)

for p,raw,s in changes.values():
    assert p.read_bytes()==raw, f'Concurrent edit: {p}'
for p,raw,s in changes.values():
    nl = chr(13)+chr(10) if bytes([13,10]) in raw else chr(10)
    p.write_bytes(s.replace(chr(10),nl).encode())
    print('updated',p.relative_to(ROOT))
