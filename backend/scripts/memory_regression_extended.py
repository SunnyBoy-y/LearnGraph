"""Additional offline end-to-end memory checks; installed into the main suite."""
from pathlib import Path
from types import SimpleNamespace
from datetime import timedelta
import hashlib
from unittest.mock import Mock, patch
from sqlalchemy import select, func
from app.domain.models import (MemoryRecord, MemoryRevision, MemoryJournalEntry, MemoryDeletionRecovery,
    MemoryProfileSnapshot, ContextSummary, utc_now)
from app.domain.memory_event_models import MemoryEvent, MemoryFeedback, MemoryProjectionOutbox, MemorySearchDocument
from app.services.memory import MemoryService, LocalWorkspaceMemoryProvider
from app.services.memory_commands import MemoryCommandService
from app.services.memory_projector import MemoryProjector, ensure_memory_search_fts
from app.services.memory_event_ingestor import event_cipher_from_settings
from app.api.memory_deps import event_store
from app.services.chat import ChatService
from app.services.memory_outbox import MemoryOutboxWorker, PermanentProjectionError
from app.core.errors import AppError

def install_tests(cls):
    def writer_fixture(self, mode='dual'):
        from app.domain.schemas.management import MemoryCreateRequest
        settings=self.settings.model_copy(update={'memory_write_mode':mode})
        self.stack.enter_context(patch('app.core.config.get_settings',return_value=settings))
        provider=LocalWorkspaceMemoryProvider(settings.memory_root,'workspace')
        writer=MemoryService(self.db,self.workspace,'alice',provider,settings.memory_root)
        writer._project_after_commit=lambda *a,**k:None
        ensure_memory_search_fts(self.db); self.db.commit()
        created=writer.create(MemoryCreateRequest(title='Old title',content='old-fact-marker',namespace='session',session_id='session'))
        context=SimpleNamespace(workspace=self.workspace,principal=SimpleNamespace(user_id='alice',tenant_id='tenant'),
            require_permission=lambda *a:None)
        self.stack.enter_context(patch('app.api.routers.memory.service',return_value=writer))
        return writer,settings,created,context

    def test_19_correction_reaches_recall_and_survives_rebuild(self):
        from app.api.routers.memory_v2 import record_feedback, MemoryFeedbackRequest
        writer,settings,created,context=writer_fixture(self)
        result=record_feedback(created.id,MemoryFeedbackRequest(feedback_type='wrong',payload={
            'replacement_title':'New title','replacement_content':'newquasar factual correction',
            'expected_revision':created.revision}),self.db,context,settings)
        self.assertEqual(result['revision'],created.revision+1)
        from app.services.memory_retrieval import MemoryHybridRetriever
        for _ in range(2):
            candidates=MemoryHybridRetriever(self.db).search(self.scope,'newquasar',min_score=0).candidates
            self.assertTrue(any(c.target_id==created.id and 'newquasar' in c.content for c in candidates))
            self.assertFalse(any('old-fact-marker' in c.content for c in candidates))
            MemoryProjector(self.db,cipher=event_cipher_from_settings(settings)).rebuild_search_projection()
            self.db.commit()

    def test_20_legacy_correction_also_writes_replayable_event(self):
        from app.api.routers.memory_v2 import record_feedback, MemoryFeedbackRequest
        writer,settings,created,context=writer_fixture(self,'legacy')
        record_feedback(created.id,MemoryFeedbackRequest(feedback_type='wrong',payload={
            'replacement_title':'New legacy','replacement_content':'legacyquasar corrected'}),self.db,context,settings)
        doc=self.db.scalar(select(MemorySearchDocument).where(MemorySearchDocument.target_id==created.id))
        self.assertEqual(doc.content,'legacyquasar corrected'); self.assertEqual(doc.status,'active')
        MemoryProjector(self.db,cipher=event_cipher_from_settings(settings)).rebuild_search_projection(); self.db.commit()
        self.db.expire_all()
        doc=self.db.scalar(select(MemorySearchDocument).where(MemorySearchDocument.target_id==created.id))
        self.assertEqual(doc.content,'legacyquasar corrected')

    def test_21_correction_conflict_rolls_back_feedback(self):
        from app.api.routers.memory_v2 import record_feedback, MemoryFeedbackRequest
        writer,settings,created,context=writer_fixture(self)
        before=self.db.scalar(select(func.count()).select_from(MemoryEvent))
        with self.assertRaises(AppError) as caught:
            record_feedback(created.id,MemoryFeedbackRequest(feedback_type='wrong',payload={
                'replacement_title':'Conflict','replacement_content':'must-not-write','expected_revision':created.revision+1}),
                self.db,context,settings)
        self.assertEqual(caught.exception.status_code,409)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(MemoryFeedback)),0)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(MemoryEvent)),before)
        self.assertEqual(self.db.get(MemoryRecord,created.id).lifecycle_status,'active')

    def test_22_feedback_state_survives_repeated_rebuild(self):
        writer,settings,created,context=writer_fixture(self)
        commands=MemoryCommandService(self.db,event_store(self.db,settings))
        commands.feedback(self.scope,created.id,actor_id='alice',feedback_type='suppress_auto_recall',payload={})
        for _ in range(2):
            MemoryProjector(self.db,cipher=event_cipher_from_settings(settings)).rebuild_search_projection(); self.db.commit()
            doc=self.db.scalar(select(MemorySearchDocument).where(MemorySearchDocument.target_id==created.id))
            self.assertTrue(doc is None or doc.status!='active')

    def test_23_forget_scrubs_journal_revision_and_profile(self):
        writer,settings,created,context=writer_fixture(self)
        from __main__ import make_row
        ciphertext,_=writer.vault.encrypt(created.id,{'content':'recovery-secret-marker'})
        self.assertTrue(writer.vault.key_exists(created.id))
        self.db.add(make_row(MemoryDeletionRecovery,workspace_id='workspace',memory_id=created.id,
            encrypted_payload=ciphertext,recoverable_until=utc_now()+timedelta(days=1)))
        self.db.add(MemoryProfileSnapshot(workspace_id='workspace',owner_subject_id='alice',version=1,
            source_fingerprint='x',source_atom_ids=[created.id],markdown='old-fact-marker'))
        self.db.commit()
        MemoryCommandService(self.db,event_store(self.db,settings)).forget(self.scope,created.id,
            actor_id='alice',confirmation='FORGET',reason='isolated test')
        self.assertTrue(all(not r.content for r in self.db.scalars(select(MemoryRevision).where(MemoryRevision.memory_id==created.id))))
        self.assertTrue(all(not r.payload for r in self.db.scalars(select(MemoryJournalEntry).where(MemoryJournalEntry.memory_id==created.id))))
        self.assertEqual(self.db.scalar(select(MemoryProfileSnapshot)).markdown,'')
        self.assertFalse(writer.vault.key_exists(created.id))
        with self.assertRaises(Exception):writer.vault.decrypt(created.id,ciphertext)
        MemoryProjector(self.db,cipher=event_cipher_from_settings(settings)).rebuild_search_projection(); self.db.commit()
        self.assertIsNone(self.db.scalar(select(MemorySearchDocument).where(MemorySearchDocument.target_id==created.id)))

    def test_24_voice_events_disabled_v2_uses_legacy_once(self):
        chat=ChatService.__new__(ChatService)
        chat.memory_context_loader=Mock(return_value='corrected-memory-marker')
        chat._build_v2_memory_context=Mock(return_value=('',{}))
        with patch('app.services.chat.get_settings',return_value=SimpleNamespace(memory_read_mode='events',memory_context_builder_v2=False)):
            block,telemetry=chat.voice_memory_block('session','question',token_budget=200)
        self.assertEqual(block,'corrected-memory-marker')
        self.assertEqual(chat.memory_context_loader.call_count,1)
        chat._build_v2_memory_context.assert_not_called()

    def test_25_changed_same_id_invalidates_summary_consumption(self):
        older=[SimpleNamespace(id='old-message',content='new edited content',role='assistant')]
        self.db.add(ContextSummary(workspace_id='workspace',session_id='session',version=1,kind='model',
            source_message_ids=['old-message'],source_hash=hashlib.sha256(b'old-message:obsolete content').hexdigest(),
            summary='obsolete-summary-marker',estimated_tokens_before=10,estimated_tokens_after=5))
        self.db.commit()
        chat=ChatService.__new__(ChatService); chat.db=self.db; chat.workspace_id='workspace'
        block,kind=chat._compose_context_summary_text('session',older)
        self.assertEqual(kind,'mechanical'); self.assertNotIn('obsolete-summary-marker',block)

    def outbox_fixture(self):
        from __main__ import make_row
        row=make_row(MemoryProjectionOutbox,id='job',tenant_id='tenant',workspace_id='workspace',
            projection_kind='index',status='queued',available_at=utc_now()-timedelta(seconds=1),lease_generation=0)
        self.db.add(row); self.db.commit(); return row

    def test_26_outbox_cannot_steal_active_lease_even_legacy_flag(self):
        outbox_fixture(self)
        first=MemoryOutboxWorker(self.db,{},worker_id='first',strict_leases=False)
        second=MemoryOutboxWorker(self.db,{},worker_id='second',strict_leases=False)
        claim=first._claim(['job'],utc_now()); self.db.commit()
        self.assertEqual(len(claim),1)
        self.assertEqual(second._claim(['job'],utc_now()),[])
        self.db.rollback()

    def test_27_unconfigured_outbox_fails_once_not_eight_times(self):
        outbox_fixture(self)
        def unsupported(item): raise PermanentProjectionError('not configured')
        worker=MemoryOutboxWorker(self.db,{'index':unsupported},worker_id='worker')
        result=worker.run_once(); self.assertEqual(result.dead_letter,1)
        self.assertEqual(worker.run_once().claimed,0)

    def request_fixture(self, linked):
        from app.services.chat import ProviderChatMessage
        chat=ChatService.__new__(ChatService)
        chat.db=self.db; chat.workspace_id='workspace'
        chat.sessions=SimpleNamespace(require=lambda *a:self.session)
        chat.memory_context_loader=None
        chat._authorized_context=lambda *a,**k:''
        chat._build_v2_memory_context=Mock(return_value=('corrected-request-marker',{}))
        history=[ProviderChatMessage(role='user',content='earlier user turn')]
        if linked:
            history.append(ProviderChatMessage(role='tool',content='tool output',tool_call_id='call-test'))
        chat._structured_history_messages=lambda *a,**k:history
        chat._style_instructions=lambda:'style'
        chat._mode_tool_policy=lambda **k:'policy'
        chat._structured_billing_input=lambda messages:'serialized over budget'
        chat._input_token_budget=lambda:1000
        chat._estimate_tokens=lambda value:10000
        chat._context_compaction_ratio=lambda *a:.8
        chat._session_timeline=Mock(return_value=[])
        chat._compose_context_summary_text=lambda *a:('earlier summary','mechanical')
        chat._record_context_summary=lambda *a,**k:None
        self.stack.enter_context(patch('app.services.chat.learning_package_prompt_context',return_value=''))
        return chat

    def test_28_linked_history_compaction_keeps_final_reference_message(self):
        chat=request_fixture(self,True)
        messages,_=chat._build_structured_messages('session','current question')
        payload=[message.as_payload() for message in messages]
        body=str(payload)
        self.assertEqual(body.count('corrected-request-marker'),1)
        self.assertEqual(payload[-1]['content'],'current question')
        self.assertIn('corrected-request-marker',payload[-2]['content'])
        self.assertEqual(chat._build_v2_memory_context.call_count,1)

    def test_29_plain_history_compaction_reuses_one_retrieval(self):
        chat=request_fixture(self,False)
        messages,_=chat._build_structured_messages('session','current question')
        payload=[message.as_payload() for message in messages]
        self.assertEqual(str(payload).count('corrected-request-marker'),1)
        self.assertIn('current question',str(payload))
        self.assertEqual(chat._build_v2_memory_context.call_count,1)

    def test_30_voice_static_prompt_contains_neither_memory_nor_history(self):
        chat=request_fixture(self,False)
        block,_=chat._build_model_prompt('session','',include_memory=False,include_history=False)
        self.assertNotIn('corrected-request-marker',block)
        chat._build_v2_memory_context.assert_not_called()
        chat._session_timeline.assert_not_called()

    def test_31_explicit_old_target_outside_eighty_is_loaded_with_permissions(self):
        from app.services.memory_profile import MemoryProfileService
        old=self.record('old-explicit'); old.updated_at=utc_now()-timedelta(days=3)
        for i in range(90): self.record('recent-'+str(i))
        self.record('private-explicit',subject='bob'); self.db.commit()
        service=MemoryProfileService.__new__(MemoryProfileService)
        service.db=self.db;service.workspace_id='workspace';service.workspace=self.workspace;service.actor_id='alice'
        self.assertNotIn('old-explicit',{x['id'] for x in service._existing_atom_context()})
        self.assertIn('old-explicit',{x['id'] for x in service._existing_atom_context(['old-explicit'])})
        with self.assertRaises(AppError):service._existing_atom_context(['private-explicit'])

    def test_32_skipped_jobs_do_not_starve_other_sessions(self):
        from __main__ import make_row
        from app.domain.models import ChatSession
        from app.services import memory_enhancement as enhancement
        for n in range(8):
            self.db.add(make_row(ChatSession,id='queued-'+str(n),workspace_id='workspace',memory_enabled=True,
                updated_at=utc_now()-timedelta(hours=1)))
        self.db.commit();self.mock_extraction()
        results=[{'status':'retry_exhausted'}]*3+[{'status':'model_error'}]*3
        with patch.object(enhancement,'extract_session_memories',side_effect=results) as run:
            totals=enhancement.run_workspace_extraction_sweep(self.db,self.workspace,self.settings,idle_seconds=30,sessions_per_sweep=3)
        self.assertEqual(run.call_count,6);self.assertEqual(totals['sessions_attempted'],3)

    def test_33_session_learning_disabled_skips_model(self):
        from app.services import memory_enhancement as enhancement
        self.message(0)
        self.session.memory_learning_enabled = False
        self.db.commit()
        bill = self.mock_extraction()
        with patch.object(enhancement, 'model_provider_for_workspace') as provider:
            result = enhancement.extract_session_memories(
                self.db, self.workspace, 'session', self.settings
            )
        self.assertEqual(result['status'], 'policy_disabled')
        provider.assert_not_called()
        bill.preflight_model_call.assert_not_called()
        self.assertEqual(self.prompts, [])

    def test_34_force_does_not_override_session_learning_disabled(self):
        from app.services import memory_enhancement as enhancement
        self.message(0)
        self.session.memory_learning_enabled = False
        self.db.commit()
        bill = self.mock_extraction()
        with patch.object(enhancement, 'model_provider_for_workspace') as provider:
            with self.assertRaises(AppError) as caught:
                enhancement.extract_session_memories(
                    self.db, self.workspace, 'session', self.settings, force=True
                )
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.code, 'memory_policy_disabled')
        provider.assert_not_called()
        bill.preflight_model_call.assert_not_called()
        self.assertEqual(self.prompts, [])

    for name,method in list(locals().items()):
        if name.startswith('test_'):
            setattr(cls,name,method)
