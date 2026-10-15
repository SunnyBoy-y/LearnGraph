"""Offline regression suite. Only run via run_memory_regressions_isolated.py."""
from __future__ import annotations
import hashlib
import inspect
import json
import os
from pathlib import Path
import threading
import unittest
from contextlib import ExitStack
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

assert os.environ.get('LEARNGRAPH_ENV') == 'test'
assert os.environ.get('LEARNGRAPH_DATABASE_URL', '').startswith('sqlite:////tmp/')
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from app.core.database import Base
from app.core.config import get_settings
from app.core.errors import AppError
from app.domain.models import (Workspace, ChatSession, Message, MemoryExtractionState,
    MemoryJobState, MemoryRecord, MemoryRevision, MemoryProfileSnapshot, ContextSummary, utc_now)
from app.domain.memory_event_models import MemoryScopeContext, MemorySearchDocument, StrategyMemory
from app.services import memory_enhancement as enhancement
from app.services.memory_job_guard import claim_memory_job, extraction_batch
from app.services.memory_retrieval import MemoryHybridRetriever
from app.services.memory_profile import MemoryProfileService, _profile_atom_query, _visible_atom_query
from app.services.context_builder import ContextBuilder
from app.services.memory import MemoryService
from app.services.memory_projector import ensure_memory_search_fts
from app.services.chat import ChatService

assert Path(inspect.getfile(enhancement)).as_posix().startswith('/src/'), 'Testing stale image code'

def make_row(model, **values):
    """Supply irrelevant mandatory fixture columns without relying on production data."""
    for column in model.__table__.columns:
        if column.name in values or column.nullable or column.default is not None or column.server_default is not None:
            continue
        if column.primary_key and column.autoincrement is True:
            continue
        try:
            kind = column.type.python_type
        except NotImplementedError:
            kind = str
        if kind is str:
            values[column.name] = 'fixture-' + uuid4().hex[:12]
        elif kind in (int, float):
            values[column.name] = 0
        elif kind is bool:
            values[column.name] = False
        elif kind is datetime:
            values[column.name] = utc_now()
        elif kind is bytes:
            values[column.name] = b'fixture'
        else:
            values[column.name] = {}
    return model(**values)

class MemoryRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_engine = create_engine('sqlite:////tmp/regression-schema.db')
        Base.metadata.create_all(cls.seed_engine)

    @classmethod
    def tearDownClass(cls):
        cls.seed_engine.dispose()

    def setUp(self):
        self.engine = create_engine('sqlite:////tmp/case-' + uuid4().hex + '.db', connect_args={'check_same_thread': False})
        source = self.seed_engine.raw_connection()
        target = self.engine.raw_connection()
        source.driver_connection.backup(target.driver_connection)
        source.close(); target.close()
        self.db = Session(self.engine, expire_on_commit=False)
        self.workspace = make_row(Workspace, id='workspace', tenant_id='tenant')
        self.session = make_row(ChatSession, id='session', workspace_id='workspace', memory_enabled=True)
        self.db.add_all([self.workspace, self.session]); self.db.commit()
        self.settings = get_settings()
        self.scope = MemoryScopeContext(tenant_id='tenant', workspace_id='workspace', principal_user_id='alice')
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(enhancement, 'semantic_boosts_for_documents', return_value={}))

    def tearDown(self):
        self.stack.close(); self.db.close(); self.engine.dispose()

    def message(self, i, content='ordinary user statement'):
        m = make_row(Message, id=f'm{i:04}', workspace_id='workspace', session_id='session',
            role='user', status='completed', content=content, created_at=utc_now() + timedelta(seconds=i))
        self.db.add(m)
        return m

    def record(self, id, subject='alice', **kwargs):
        r = make_row(MemoryRecord, id=id, tenant_id='tenant', workspace_id='workspace', subject_user_id=subject,
            state='active', lifecycle_status='active', ledger_status='active', zone='recent',
            namespace='workspace', memory_layer='L4', atom_schema_version=1, summary_eligibility='durable',
            temporal_status='timeless', sensitivity='normal', auto_recall_suppressed=False, **kwargs)
        self.db.add(r)
        return r

    def doc(self, id, subject='alice', marker='ordinary unrelated', **kwargs):
        d = make_row(MemorySearchDocument, id=id, tenant_id='tenant', workspace_id='workspace',
            subject_user_id=subject, target_type='memory', target_id=id, status='active',
            subject=marker, content=marker, memory_layer='L4', sensitivity='normal', confidence=.9, importance=.5, **kwargs)
        self.db.add(d)
        return d

    def claim(self, window='w', force=False, db=None):
        return claim_memory_job(db or self.db, workspace_id='workspace', kind='test',
            resource_id='session', window_key=window, force=force)

    def expire_backoff(self, kind='test'):
        self.db.rollback(); self.db.expire_all()
        row = self.db.scalar(select(MemoryJobState).where(MemoryJobState.kind == kind))
        row.next_attempt_at = utc_now() - timedelta(seconds=1)
        self.db.commit()

    def mock_extraction(self, payload=None, error=None):
        config = {'extraction': {'enabled': True, 'model_id': 'fake', 'provider_id': 'fake', 'auto_commit': False},
                  'summarization': {'enabled': False}}
        self.model = SimpleNamespace(available=True, provider_id='fake', model_id='fake', remote_capability=False,
            max_output_tokens=10000, last_usage={'input_tokens': 20, 'output_tokens': 7})
        self.prompts, self.usages = [], []
        def generate(prompt, *args):
            self.prompts.append(prompt)
            # Independent writer succeeds while the mock model is running.
            with Session(self.engine) as other:
                other.execute(text('CREATE TABLE IF NOT EXISTS model_call_write_probe (id INTEGER)')); other.commit()
            if error:
                raise error
            return payload if payload is not None else {'memories': [], 'has_more': False}
        self.model.generate_json = generate
        bill = SimpleNamespace(preflight_model_call=Mock(return_value=SimpleNamespace()),
            record_usage=lambda *args, **kwargs: self.usages.append(kwargs))
        for name, value in [('load_enhancement_config', lambda *args: config),
            ('_workspace_memory_enabled', lambda *args: True), ('model_provider_for_workspace', lambda *a, **k: self.model),
            ('memory_provider_for_workspace', lambda *a, **k: SimpleNamespace(provider_id='local', remote_capability=False)),
            ('BillingService', lambda *args: bill)]:
            self.stack.enter_context(patch.object(enhancement, name, value))
        return bill

    def test_01_new_table_upgrade_preserves_existing_cursor(self):
        state = MemoryExtractionState(workspace_id='workspace', session_id='session', last_message_id='old')
        self.db.add(state); self.db.commit(); MemoryJobState.__table__.drop(self.engine); Base.metadata.create_all(self.engine)
        self.assertEqual(self.db.get(MemoryExtractionState, state.id).last_message_id, 'old')
        self.assertIn('memory_job_states', Base.metadata.tables)

    def test_02_oldest_eighty_do_not_skip_first_message(self):
        for i in range(81): self.message(i)
        self.db.commit()
        batch = enhancement._new_messages_since(self.db, 'workspace', 'session', None)
        self.assertEqual([m.id for m in batch], [f'm{i:04}' for i in range(80)])
        state = SimpleNamespace(last_message_id=batch[-1].id, last_message_at=batch[-1].created_at)
        self.assertEqual([m.id for m in enhancement._new_messages_since(self.db,'workspace','session',state)], ['m0080'])

    def test_03_long_message_reassembled_without_loss(self):
        m = SimpleNamespace(id='long', content='a'*12000 + 'TAIL'*4000)
        checkpoint, parts = {}, []
        for _ in range(4):
            selected, texts, complete, checkpoint = extraction_batch([m], checkpoint)
            parts.append(texts[m.id])
            if not checkpoint: break
            self.assertEqual(complete, [])
        self.assertEqual(''.join(parts), m.content)
        self.assertEqual(complete, [m])

    def test_04_busy_and_force_never_steal_lease(self):
        lease, status = self.claim(); self.assertEqual(status, 'claimed')
        try:
            self.assertEqual(self.claim()[1], 'busy')
            self.assertEqual(self.claim('new', force=True)[1], 'busy')
        finally:
            lease.finish(success=True, status='ok')
        self.assertEqual(self.claim()[1], 'already_processed')

    def test_05_retry_backoff_and_three_attempt_limit(self):
        for attempt in range(1,4):
            lease, status = self.claim(); self.assertEqual(status, 'claimed')
            self.assertEqual(lease.attempt, attempt)
            lease.finish(success=False, status='model_error')
            self.assertEqual(self.claim(force=True)[1], 'retry_backoff')
            self.expire_backoff()
        self.assertEqual(self.claim()[1], 'retry_exhausted')
        lease, status = self.claim(force=True); self.assertEqual(status,'claimed')
        lease.finish(success=True, status='ok')

    def test_06_stale_worker_cannot_write(self):
        lease, _ = self.claim()
        row = self.db.get(MemoryJobState, lease.job_id)
        row.lease_until=utc_now()-timedelta(seconds=1); self.db.commit()
        with self.assertRaises(RuntimeError): lease.fence(self.db)
        self.db.rollback(); lease.finish(success=False,status='lost')

    def test_07_concurrent_claim_has_one_winner(self):
        barrier=threading.Barrier(2); results=[]; errors=[]
        def worker():
            try:
                with Session(self.engine) as db:
                    barrier.wait(); results.append(self.claim(db=db))
            except Exception as exc: errors.append(exc)
        threads=[threading.Thread(target=worker) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(15)
        for lease,_ in results:
            if lease: lease.finish(success=True,status='ok')
        self.assertFalse(errors); self.assertEqual(sum(lease is not None for lease,_ in results),1)

    def test_08_failed_extraction_is_bounded_with_new_turns(self):
        self.message(0); self.db.commit(); self.mock_extraction(error=RuntimeError('offline mock failure'))
        for i in range(3):
            result=enhancement.extract_session_memories(self.db,self.workspace,'session',self.settings)
            self.assertEqual(result['status'],'model_error')
            self.expire_backoff('extraction')
            self.message(i+1); self.db.commit()
        result=enhancement.extract_session_memories(self.db,self.workspace,'session',self.settings)
        self.assertEqual(result['status'],'retry_exhausted')
        self.assertEqual(len(self.prompts),3); self.assertEqual(len(self.usages),3)
        self.assertEqual(self.usages[0]['input_tokens'],20)

    def test_09_chunk_cursor_waits_until_entire_message_reviewed(self):
        m=self.message(0,'x'*24010); self.db.commit(); self.mock_extraction()
        for n in range(3):
            result=enhancement.extract_session_memories(self.db,self.workspace,'session',self.settings)
            self.assertEqual(result['status'],'ok')
            state=self.db.scalar(select(MemoryExtractionState))
            self.assertEqual(state.last_message_id,m.id if n==2 else None)
        self.assertEqual(len(self.prompts),3)
        self.assertEqual(self.model.max_output_tokens,4096)

    def test_10_model_has_more_does_not_advance_cursor(self):
        self.message(0); self.db.commit(); self.mock_extraction({'memories':[],'has_more':True})
        result=enhancement.extract_session_memories(self.db,self.workspace,'session',self.settings)
        self.assertEqual(result['status'],'partial')
        self.assertIsNone(self.db.scalar(select(MemoryExtractionState)).last_message_id)

    def test_11_failed_sweep_obeys_attempt_quota(self):
        for n in range(8):
            self.db.add(make_row(ChatSession,id='idle'+str(n),workspace_id='workspace',memory_enabled=True,
                updated_at=utc_now()-timedelta(hours=1)))
        self.db.commit(); self.mock_extraction()
        with patch.object(enhancement,'extract_session_memories',return_value={'status':'model_error'}) as run:
            totals=enhancement.run_workspace_extraction_sweep(self.db,self.workspace,self.settings,idle_seconds=30,sessions_per_sweep=3)
        self.assertEqual(run.call_count,3); self.assertEqual(totals['sessions_attempted'],3)

    def test_12_recall_beyond_two_hundred_without_fts(self):
        for i in range(260): self.doc(f'd{i:03}')
        self.doc('z-old-needle', marker='uniquequasar')
        self.doc('z-private',subject='bob', marker='uniquequasar')
        self.db.commit()
        retriever=MemoryHybridRetriever(self.db)
        with patch.object(retriever,'_fts_scores',return_value={}):
            result=retriever.search(self.scope,'uniquequasar',top_k=4)
        ids={c.target_id for c in result.candidates}
        self.assertIn('z-old-needle',ids); self.assertNotIn('z-private',ids)

    def test_13_canonical_governance_overrides_stale_document(self):
        self.record('suppressed',auto_recall_suppressed_override=True) if False else None
        r=self.record('suppressed'); r.auto_recall_suppressed=True
        self.doc('suppressed',marker='uniquequasar'); self.db.commit()
        result=MemoryHybridRetriever(self.db).search(self.scope,'uniquequasar',min_score=0)
        self.assertNotIn('suppressed',{c.target_id for c in result.candidates})

    def test_14_profile_source_queries_enforce_owner(self):
        self.record('mine'); self.record('shared',subject=None); self.record('other',subject='bob')
        stale=self.record('stale'); stale.lifecycle_status='disputed'
        self.db.commit()
        for query in (_profile_atom_query,_visible_atom_query):
            ids={r.id for r in self.db.scalars(query('workspace','alice'))}
            self.assertEqual(ids,{'mine','shared'})

    def test_15_strategy_prompt_sources_enforce_owner(self):
        for owner, id in [('alice','mine'),('bob','private'),(None,'shared')]:
            self.db.add(make_row(StrategyMemory,id=id,tenant_id='tenant',workspace_id='workspace',
                subject_user_id=owner,status='verified',confidence=.9,title=id,reproducible_steps_json=[id]))
        self.db.commit()
        builder=ContextBuilder.__new__(ContextBuilder); builder.db=self.db
        self.assertEqual({x['strategy_id'] for x in builder._strategies(self.scope)},{'mine','shared'})

    def test_16_first_profile_failure_persists_and_force_cools_down(self):
        service=MemoryProfileService.__new__(MemoryProfileService)
        service.db=self.db; service.workspace_id='workspace'; service.actor_id='alice'
        service.workspace=self.workspace; service.settings=self.settings
        service._profile_view=lambda row: SimpleNamespace(status=row.status,markdown=row.markdown)
        with patch.object(service,'_refresh_profile',side_effect=RuntimeError('mock fail')) as refresh:
            with self.assertRaises(RuntimeError): service.refresh_profile(force=True)
            row=self.db.scalar(select(MemoryProfileSnapshot))
            self.assertEqual(row.status,'failed')
            self.assertEqual(service.refresh_profile(force=True).status,'failed')
            self.assertEqual(refresh.call_count,1)

    def test_17_atomic_retention_does_not_commit_feedback(self):
        service=MemoryService(self.db,self.workspace,'alice',SimpleNamespace(),Path('/tmp/regression-memory'))
        with self.assertRaises(RuntimeError):
            with service.atomic_write():
                self.db.add(MemoryExtractionState(workspace_id='workspace',session_id='rollback'))
                service.purge_expired()
                raise RuntimeError('force rollback')
        with Session(self.engine) as db:
            self.assertIsNone(db.scalar(select(MemoryExtractionState).where(MemoryExtractionState.session_id=='rollback')))

    def test_18_invalid_json_keeps_actual_usage(self):
        from app.providers.remote.openai import OpenAICompatibleChatProvider
        provider=OpenAICompatibleChatProvider.__new__(OpenAICompatibleChatProvider)
        provider.capabilities={}; provider.structured_output_mode='json_schema'; provider.model_id='fake'
        provider._apply_call_options=lambda value,**kw:value
        provider._apply_output_budget=lambda value,**kw:value
        provider._post_json=lambda *args:{'choices':[{'message':{'content':'invalid json'}}],
            'usage':{'prompt_tokens':123,'completion_tokens':45}}
        with self.assertRaises(Exception): provider.generate_json('prompt','test',{'type':'object','properties':{}})
        self.assertEqual(provider.last_usage['input_tokens'],123)
        self.assertEqual(provider.last_usage['output_tokens'],45)

if __name__=='__main__':
    print('SOURCE', inspect.getfile(enhancement), flush=True)
    from memory_regression_extended import install_tests
    install_tests(MemoryRegression)
    unittest.main(verbosity=2)
