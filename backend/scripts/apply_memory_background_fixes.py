"""One-shot anchored source patch; never imports app or touches runtime data."""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1] / 'app'
changes = {}
def load(name):
    path = ROOT / name
    raw = path.read_bytes()
    changes[name] = (path, raw, raw.decode('utf-8').replace('\r\n', '\n'))
    return changes[name][2]
def store(name, text):
    path, raw, _ = changes[name]
    from normalize_patch_literals import normalize
    text = normalize(text)
    ast.parse(text)
    changes[name] = (path, raw, text)
def replace(text, old, new):
    assert text.count(old) == 1, (old[:140], text.count(old))
    return text.replace(old, new, 1)

s = load('domain/models.py')
s = replace(s, 'class ProviderConfig(Base, TimestampMixin, WorkspaceScopedMixin):', '''class MemoryJobState(Base, TimestampMixin, WorkspaceScopedMixin):
    """Durable fenced background work; new table needs no legacy column rewrite."""
    __tablename__ = "memory_job_states"
    __table_args__ = (UniqueConstraint("workspace_id", "kind", "resource_id", name="uq_memory_job_resource"),)
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    resource_id: Mapped[str] = mapped_column(String(36), index=True)
    window_key: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(32), default="ready")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result_status: Mapped[str] = mapped_column(String(40), default="")
    last_error: Mapped[str] = mapped_column(String(500), default="")
    checkpoint: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ProviderConfig(Base, TimestampMixin, WorkspaceScopedMixin):''')
store('domain/models.py', s)

s = load('services/memory_enhancement.py')
s = replace(s, 'from __future__ import annotations', '''from __future__ import annotations

from app.services.memory_job_guard import guarded_session_job, current_lease, extraction_batch''')
s = replace(s, 'return messages[-_TRANSCRIPT_MESSAGE_CAP:]', 'return messages[:_TRANSCRIPT_MESSAGE_CAP]')
s = replace(s, 'def extract_session_memories(', '@guarded_session_job("extraction")\ndef extract_session_memories(')
s = replace(s, 'def summarize_session_context(', '@guarded_session_job("summarization")\ndef summarize_session_context(')
s = replace(s, '    if state is None:\n        state = MemoryExtractionState', '''    lease = current_lease()
    messages, transcript_slices, complete_messages, next_checkpoint = extraction_batch(
        messages, lease.checkpoint if lease else {}
    )
    if state is None:
        state = MemoryExtractionState''')
s = replace(s, 'f"{item.content[:_TRANSCRIPT_CHARS_PER_MESSAGE]}"', 'f"{transcript_slices[item.id]}"')
s = replace(s, '    known_hashes = {record.content_hash for record in existing_records}', '''    # Exact deduplication is not restricted to the prompt's recent-30 window.
    known_hashes = set(db.scalars(select(MemoryRecord.content_hash).where(
        MemoryRecord.workspace_id == workspace.id, MemoryRecord.state == "active",
        (MemoryRecord.namespace == "workspace") | (MemoryRecord.session_id == session_id),
    )).all())''')
s = replace(s, '                MemoryDraft.status == "PENDING",\n', '                MemoryDraft.status == "PENDING",\n                MemoryDraft.session_id == session_id,\n')
s = replace(s, '    prompt = _extraction_prompt(transcript, existing_lines, session)', '''    prompt = _extraction_prompt(transcript, existing_lines, session)
    if estimate_tokens(prompt + json.dumps(_extraction_schema())) > 12000:
        return {"status": "input_budget_exceeded", "drafts_created": 0}
    output_cap = min(int(getattr(model, "max_output_tokens", 4096) or 4096), 4096)
    if hasattr(model, "max_output_tokens"):
        model.max_output_tokens = output_cap''')
# Match quote to the actual provider cap, for extraction and summary separately.
s = replace(s, '            feature="memory_extraction",\n            estimated_input_tokens=estimate_tokens(prompt),\n            estimated_output_tokens=1_024,', '            feature="memory_extraction",\n            estimated_input_tokens=estimate_tokens(prompt + json.dumps(_extraction_schema())),\n            estimated_output_tokens=output_cap,')
s = replace(s, '    if call_error is not None:\n        state.last_status', '    if lease:\n        lease.fence(db)\n    if call_error is not None:\n        state.last_status')
# Preserve billed usage before entering the all-or-nothing proposals/cursor transaction.
start = s.index('    normalizer = TemporalNormalizer()')
end = s.index('\n\n# ---------------------------------------------------------------------------\n# Background rolling', start)
block = s[start:end]
block = replace(block, '    normalizer = TemporalNormalizer()', '    normalizer = TemporalNormalizer()\n    if lease:\n        lease.fence(db)')
block = replace(block, 'auto_commit=bool(extraction_cfg["auto_commit"]),', 'auto_commit=bool(extraction_cfg["auto_commit"]) and operation == "CREATE",')
block = replace(block, '        except AppError as exc:\n            logger.info(', '        except AppError as exc:\n            raise  # Roll back proposals and cursor together; do not commit partial writes.\n            logger.info(')
block = replace(block, '        pending_titles.add(title)', '        pending_titles.add(title)\n        known_hashes.add(_content_hash(title, content))')
block = replace(block, '    last = messages[-1]\n    state.last_message_id = last.id\n    state.last_message_at = last.created_at', '''    has_more = bool(payload.get("has_more"))
    if not has_more:
        if complete_messages:
            last = complete_messages[-1]
            state.last_message_id = last.id
            state.last_message_at = last.created_at
        if lease:
            lease.save_checkpoint(db, next_checkpoint)''')
block = replace(block, '    db.commit()\n    return {\n        "status": "ok",', '    db.flush()\n    return {\n        "status": "partial" if has_more else "ok",')
block = replace(block, '"completion_reason": "model_completed" if not bool(payload.get("has_more")) else "batch_safety_limit",', '"completion_reason": "continuation_required" if has_more else ("text_chunk_completed" if next_checkpoint else "model_completed"),')
s = s[:start] + '    with memory_service.atomic_write():\n' + ''.join('    ' + line if line.strip() else line for line in block.splitlines(True)) + s[end:]
# Summary invalidation checks content, not just message IDs.
s = replace(s, 'if latest is not None and covered and covered.issubset(set(prefix_ids)):', '''if (latest is not None and covered and covered.issubset(set(prefix_ids))
            and latest.source_hash == hashlib.sha256(
                "\n".join(f"{item.id}:{item.content}" for item in prefix if item.id in covered).encode("utf-8")
            ).hexdigest()):''')
s = replace(s, 'fresh_messages = fresh_messages[-_SUMMARY_BATCH_MESSAGE_CAP:]', 'fresh_messages = fresh_messages[:_SUMMARY_BATCH_MESSAGE_CAP]')
s = replace(s, '    prompt = _summary_prompt(base_summary, transcript)', '''    prompt = _summary_prompt(base_summary, transcript)
    if estimate_tokens(prompt + json.dumps(_summary_schema())) > 12000:
        return {"status": "input_budget_exceeded"}
    output_cap = min(int(getattr(model, "max_output_tokens", 2048) or 2048), 2048)
    if hasattr(model, "max_output_tokens"):
        model.max_output_tokens = output_cap''')
s = replace(s, '            feature="context_summarization",\n            estimated_input_tokens=estimate_tokens(prompt),\n            estimated_output_tokens=1_024,', '            feature="context_summarization",\n            estimated_input_tokens=estimate_tokens(prompt + json.dumps(_summary_schema())),\n            estimated_output_tokens=output_cap,')
s = replace(s, '    summary_text = str(payload.get("summary") or "").strip()[:_SUMMARY_TEXT_CHAR_CAP]', '''    # Usage must survive a lease loss or source-session deletion.
    db.commit()
    lease = current_lease()
    if lease:
        lease.fence(db)
    from app.services.session_memory_deletion import lock_session_memory_sources
    lock_session_memory_sources(db, workspace.id, [session_id])
    summary_text = str(payload.get("summary") or "").strip()[:_SUMMARY_TEXT_CHAR_CAP]''')
s = replace(s, 'totals = {"sessions_summarized": 0}', 'totals = {"sessions_summarized": 0, "sessions_attempted": 0}')
s = replace(s, 'totals = {"sessions_processed": 0, "drafts_created": 0, "auto_committed": 0}', 'totals = {"sessions_processed": 0, "drafts_created": 0, "auto_committed": 0, "sessions_attempted": 0}')
for old in ('sessions_summarized', 'sessions_processed'):
    s = replace(s, f'        if totals["{old}"] >= max(1, sessions_per_sweep):\n            break', '        if totals["sessions_attempted"] >= max(1, sessions_per_sweep):\n            break\n        totals["sessions_attempted"] += 1'.replace('\n','\n')) if False else replace(s, f'        if totals["{old}"] >= max(1, sessions_per_sweep):\n            break', '        if totals["sessions_attempted"] >= max(1, sessions_per_sweep):\n            break\n        totals["sessions_attempted"] += 1')
store('services/memory_enhancement.py', s)

s = load('services/chat.py')
s = replace(s, 'if self.memory_context_loader is not None and settings.memory_read_mode != "events":', 'if self.memory_context_loader is not None and (settings.memory_read_mode != "events" or not settings.memory_context_builder_v2):')
store('services/chat.py', s)

# Validate every anchor and syntax BEFORE writing any file.
for path, original, text in changes.values():
    assert path.read_bytes() == original, f'Concurrent edit: {path}'
for path, original, text in changes.values():
    newline = '\r\n' if b'\r\n' in original else '\n'
    path.write_bytes(text.replace('\n', newline).encode('utf-8'))
    print('updated', path.relative_to(ROOT))
