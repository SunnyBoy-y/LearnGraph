"""Source-only retrieval and request consistency patch; run once."""
from pathlib import Path
import ast
base = Path(__file__).with_name('apply_memory_consistency_fixes.py').read_text(encoding='utf-8')
exec(compile(base.split("p = 'services/memory.py'")[0], '<patch-helpers>', 'exec'))

p = 'services/memory_retrieval.py'
replace(p, 'from sqlalchemy import select, text', '''from sqlalchemy import and_, or_, select, text
from app.domain.models import MemoryRecord''')
s = get(p)
a = s.index('        documents = self.db.scalars(')
b = s.index('        semantic_boosts:', a)
put(p, s[:a] + '''        if top_k <= 0:
            return RetrievalResult((), excluded)
        fts_scores = self._fts_scores(scope, query, limit=max(top_k * 5, 20))
        D, R = MemorySearchDocument, MemoryRecord
        canonical_ok = and_(
            R.tenant_id == scope.tenant_id,
            R.workspace_id.is_not_distinct_from(D.workspace_id),
            or_(R.subject_user_id.is_(None), R.subject_user_id == scope.principal_user_id),
            R.state == "active", R.lifecycle_status == "active", R.ledger_status == "active",
            R.zone != "archive", R.deleted_at.is_(None), R.content_destroyed_at.is_(None),
            R.auto_recall_suppressed.is_(False),
            R.sensitivity.in_(scope.allowed_sensitivity), R.memory_layer.in_(allowed_layers),
            or_(R.atom_schema_version < 1, R.summary_eligibility.in_(("durable", "current"))),
            R.temporal_status.not_in(("cancelled", "rescheduled", "lapsed_unverified", "expired")),
            or_(R.valid_until.is_(None), R.valid_until >= now),
        )
        statement = (select(D).outerjoin(R, and_(D.target_type == "memory", R.id == D.target_id))
            .where(*hard_filters, or_(R.id.is_(None), canonical_ok),
                   or_(D.valid_until.is_(None), D.valid_until >= now), D.confidence >= min_confidence)
            .order_by(D.id).execution_options(yield_per=128))
        # Bound memory/embedding cost AFTER every eligible document can compete.
        pool_limit = max(top_k, min(200, max(24, top_k * 8)))
        pool = {}
        stream = self.db.scalars(statement)
        try:
            for document in stream:
                tokens = _tokens(f"{document.subject} {document.content}")
                lexical = len(query_tokens & tokens) / math.sqrt(len(query_tokens) * len(tokens)) if query_tokens and tokens else 0.0
                lexical = max(lexical, fts_scores.get(document.id, 0.0))
                entity = float(bool(query_tokens & _tokens(document.entity_aliases_text)))
                scope_score = 1.0 if document.task_id and document.task_id == scope.task_id else 0.85 if document.workspace_id == scope.workspace_id else 0.70 if document.workspace_id is None else 0.45
                score = 0.20 * lexical + 0.15 * scope_score + 0.15 * entity + 0.05 + 0.10 * document.importance + 0.05 * document.confidence
                external = (document.metadata_json or {}).get("external_source") or {}
                try:
                    observed = datetime.fromisoformat(str(external.get("observed_at") or "").replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError, OverflowError):
                    observed = 0.0
                rank = (-score, -observed, document.target_id, document.id)
                key = (document.target_type, " ".join(document.content.casefold().split()))
                previous = pool.get(key)
                if previous is not None:
                    excluded["duplicate"] += 1
                if previous is None or rank < previous[0]:
                    pool[key] = (rank, document)
                if len(pool) > pool_limit:
                    del pool[max(pool, key=lambda value: pool[value][0])]
        finally:
            stream.close()
        documents = [item[1] for item in sorted(pool.values(), key=lambda item: item[0])]
''' + s[b:])
replace(p, '''            if document.valid_until is not None and document.valid_until < now:
                excluded["expired"] += 1
                continue
''', '')

p = 'services/context_builder.py'
replace(p, '            cost = estimate_tokens(serialized)', '''            cost = estimate_tokens(
                chr(10) * 2 + f"<HOST_DATA_BLOCK kind={name}>" + chr(10)
                + serialized + chr(10) + "</HOST_DATA_BLOCK>"
            )''')
replace(p, '''        for name, serialized, raw in sections:
''', '''        for name, serialized, raw in sections:
            if name == "memories" and isinstance(raw, list):
                kept = []
                for item in raw:
                    trial = json.dumps([*kept, item], ensure_ascii=False, sort_keys=True)
                    cost = estimate_tokens(chr(10) * 2 + f"<HOST_DATA_BLOCK kind={name}>" + chr(10) + trial + chr(10) + "</HOST_DATA_BLOCK>")
                    if used + cost <= request.token_budget:
                        kept.append(item)
                    else:
                        excluded["budget"] += 1
                if kept:
                    _try_section(name, json.dumps(kept, ensure_ascii=False, sort_keys=True), kept)
                continue
''')
replace(p, '''        memories_injected = any(name == "memories" for name, _, _ in selected_sections)
        injected_memories = [item for item in evidence if memories_injected]
        excluded_memories = [item for item in evidence if not memories_injected]''', '''        included_ids = {item["memory_id"] for name, _, raw in selected_sections if name == "memories" for item in raw}
        memories_injected = bool(included_ids)
        injected_memories = [item for item in evidence if item.target_id in included_ids]
        excluded_memories = [item for item in evidence if item.target_id not in included_ids]''')
replace(p, '            item.manifest_status = "injected" if memories_injected else "excluded"', '            item.manifest_status = "injected" if item.target_id in included_ids else "excluded"')
replace(p, '            selected_memories=evidence if memories_injected else [],', '            selected_memories=injected_memories,')
method(p, '_strategies', lambda s: s.replace('                VerifiedStrategy.workspace_id == scope.workspace_id,', '''                VerifiedStrategy.workspace_id == scope.workspace_id,
                (VerifiedStrategy.subject_user_id.is_(None)) | (VerifiedStrategy.subject_user_id == scope.principal_user_id),'''))

p = 'services/chat_service_factory.py'
s = get(p)
# Only the memory loader conditional, not construction of ContextBuilder.
needle = '            if settings.memory_context_builder_v2'
positions = [i for i in range(len(s)) if s.startswith(needle, i)]
assert len(positions) == 2
i = positions[1]
put(p, s[:i] + s[i:].replace(needle, needle + ' and settings.memory_read_mode == "events"', 1))

p = 'services/chat.py'
replace(p, '''        settings = get_settings()
        if self.context_builder is None or not settings.memory_context_builder_v2:''', '''        settings = get_settings()
        if not self._session_memory_policy_enabled(session_id):
            return None, {"manifest_status": "policy_disabled", "request_sent": False}, None
        if self.context_builder is None or not settings.memory_context_builder_v2:''')
old = 'if self.memory_context_loader is not None and get_settings().memory_read_mode != "events":'
new = 'if self.memory_context_loader is not None and (get_settings().memory_read_mode != "events" or not get_settings().memory_context_builder_v2):'
replace(p, old, new, count=2)
method(p, '_build_model_prompt', lambda s: s.replace('        agent_id: str | None = None,', '''        agent_id: str | None = None,
        include_memory: bool = True,
        include_history: bool = True,''', 1).replace('        history = self._session_timeline(session_id)', '        history = self._session_timeline(session_id) if include_history else []', 1).replace('        if self.memory_context_loader is not None', '        if include_memory and self.memory_context_loader is not None', 1).replace('''        v2_block, _v2_telemetry = self._build_v2_memory_context(
            session_id, current_content, node_ids=node_ids, agent_id=agent_id
        )''', '''        v2_block, _v2_telemetry = (self._build_v2_memory_context(
            session_id, current_content, node_ids=node_ids, agent_id=agent_id
        ) if include_memory else (None, {}))''').replace('        if not history:', '''        if not include_history and not include_memory:
            return style_instructions + chr(10) * 2 + authorized_context, None
        if not history:''', 1))
# A construction manifest is not transport proof.
replace(p, '                "manifest_status": built.view.manifest_status,', '''                "manifest_status": built.view.manifest_status,
                "request_sent": False,
                "stage": "context_built",''')

p = 'services/voice_context.py'
replace(p, '            agent_id=VOICE_MEMORY_AGENT_ID,', '''            agent_id=VOICE_MEMORY_AGENT_ID,
            include_memory=False,
            include_history=False,''', count=2) if False else None
method(p, 'load_snapshot', lambda s: s.replace('            agent_id=VOICE_MEMORY_AGENT_ID,', '''            agent_id=VOICE_MEMORY_AGENT_ID,
            include_memory=False,
            include_history=False,''', 1))
s=get(p);a=s.index('        try:', s.index('    def load_snapshot('));b=s.index('        # Keep the snapshot', a)
put(p, s[:a] + '''        # Long-term memory is ephemeral per-turn input, never pinned in a
        # reconnect snapshot. This also avoids duplicate startup retrieval.
        telemetry = {"stage": "per_turn_recall", "request_sent": False}
''' + s[b:])

p='services/memory_profile.py'
s=get(p);a=s.index('    def _existing_atom_context(');b=s.index('        return [',a)
put(p,s[:a]+'''    def _existing_atom_context(self, selected_atom_ids: list[str] | None = None) -> list[dict[str, Any]]:
        query = select(MemoryRecord).where(
            MemoryRecord.workspace_id == self.workspace_id, MemoryRecord.tenant_id == self.workspace.tenant_id,
            MemoryRecord.state == "active", MemoryRecord.atom_schema_version >= 1,
            MemoryRecord.deleted_at.is_(None), MemoryRecord.content_destroyed_at.is_(None),
            (MemoryRecord.subject_user_id.is_(None)) | (MemoryRecord.subject_user_id == self.actor_id),
        )
        records = list(self.db.scalars(query.order_by(MemoryRecord.updated_at.desc(), MemoryRecord.id).limit(80)))
        selected = set(selected_atom_ids or [])
        if selected:
            explicit = list(self.db.scalars(query.where(MemoryRecord.id.in_(selected))))
            if {record.id for record in explicit} != selected:
                raise AppError(404, "memory_profile_target_unavailable", "Selected memory unavailable")
            records = list({record.id: record for record in [*explicit, *records]}.values())
''' + s[b:])
replace(p, '        evidence = self._create_user_evidence(text, observed_at=now)', '''        existing_atoms = self._existing_atom_context(request.selected_atom_ids)
        evidence = self._create_user_evidence(text, observed_at=now)''')
replace(p, 'json.dumps(self._existing_atom_context(), ensure_ascii=False)', 'json.dumps(existing_atoms, ensure_ascii=False)')
replace(p, '        existing_atoms = self._existing_atom_context()', '        existing_atoms = self._existing_atom_context(request.selected_atom_ids)')
replace(p, '        stored = list(snapshot.structured_sections or [])', '''        if snapshot.status != "ready" or str(snapshot.stale_reason or "").startswith("refresh_failed"):
            return MemoryProfileView(
                id=snapshot.id, workspace_id=snapshot.workspace_id, owner_subject_id=snapshot.owner_subject_id,
                version=snapshot.version, status=snapshot.status, stale_reason=snapshot.stale_reason,
                generated_at=snapshot.generated_at, updated_at=snapshot.updated_at,
            )
        stored = list(snapshot.structured_sections or [])''')
method(p, '_model_json', lambda s: s.replace('        provider_id, model_id = self._model_selection()', '''        if estimate_tokens(prompt + json.dumps(schema, ensure_ascii=False)) > 12000:
            raise AppError(413, "memory_profile_input_budget_exceeded", "Memory operation exceeds its input budget; select fewer atoms")
        provider_id, model_id = self._model_selection()''', 1))
for path, source in changes.items():
    ast.parse(source)
for path, source in changes.items():
    target=ROOT/path
    raw=target.read_bytes()
    newline=chr(13)+chr(10) if bytes([13,10]) in raw else chr(10)
    target.write_bytes(source.replace(chr(10),newline).encode('utf-8'))
    print('updated',path)
