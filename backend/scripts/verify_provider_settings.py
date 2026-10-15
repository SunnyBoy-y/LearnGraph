# Offline regression suite; no real credentials or vendor requests.
from __future__ import annotations
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from pydantic import SecretStr
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.core.database import Base
from app.core.errors import AppError
from app.core.security import SecretCipher
from app.domain.models import AuditEvent, ProviderConfig, ProviderSecret, WorkspaceSecretReference
from app.domain.provider_balance_models import ProviderBalanceLease, ProviderBalanceManualState, ProviderBalanceState
from app.domain.provider_import_models import ProviderImportIdentity, ProviderImportPreview
from app.domain.schemas.management import DiscoveredProviderImportRequest, ProviderCreateRequest
from app.services.management import ProviderService
from app.services.provider_secrets import EncryptedProviderSecret
from app.services.provider_balance_lifecycle import request_discovery, run_balance, status_for
from app.services.provider_config_import import commit_preview, list_sources, preview_sources
from app.services.provider_config_sources import parse_document, read_source

class ProviderSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='learngraph-provider-regression-')
        self.root = Path(self.tmp.name)
        self.engine = create_engine('sqlite:///' + (self.root / 'test.sqlite').as_posix())
        models = [ProviderConfig, ProviderSecret, WorkspaceSecretReference, AuditEvent, ProviderBalanceState, ProviderBalanceLease, ProviderBalanceManualState, ProviderImportPreview, ProviderImportIdentity]
        Base.metadata.create_all(self.engine, tables=[model.__table__ for model in models])
        self.db = Session(self.engine)
        self.settings = Settings(_env_file=None)
        self.service = ProviderService(self.db, 'test-workspace', 'test-actor', self.settings)
        self.cipher = SecretCipher('fixture-master-key-not-a-real-secret')
        self.crypto = patch('app.services.management.encrypt_provider_secret', side_effect=lambda settings, value: EncryptedProviderSecret(self.cipher.encrypt(value), 'fernet_sha256_v1', 'environment', 1))
        self.crypto.start()
        self.network = patch('httpx.HTTPTransport.handle_request', side_effect=AssertionError('Real HTTP forbidden'))
        self.network.start()
        self.env = patch.dict(os.environ, {'LEARNGRAPH_PROVIDER_IMPORT_ROOT': str(self.root), 'LEARNGRAPH_PROVIDER_IMPORT_SOURCES': '[]'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.network.stop()
        self.crypto.stop()
        self.db.close()
        self.engine.dispose()
        self.tmp.cleanup()

    def create(self, url='https://relay.example/v1', key='fixture-api-key', name='Fixture', commit=True):
        value = self.service.create(ProviderCreateRequest(display_name=name, provider_type='openai_compatible_chat', base_url=url, api_key=SecretStr(key)), auto_probe=False, commit=False)
        value.enabled = True
        value.remote_capability = True
        if commit:
            self.db.commit()
        return value

    def execute(self, provider, handler):
        with patch.object(self.service, '_decrypt_secret', return_value='fixture-api-key'):
            return run_balance(self.service, provider.id, automatic=True, transport=httpx.MockTransport(handler))

    def configure_source(self, data, kind='ccswitch', filename='config.json'):
        path = self.root / filename
        path.write_text(json.dumps(data), encoding='utf-8')
        source = dict(id='fixture-source', label='Fixture source', path=filename, kind=kind, workspace_ids=['test-workspace'])
        os.environ['LEARNGRAPH_PROVIDER_IMPORT_SOURCES'] = json.dumps([source])
        return path, source

    @staticmethod
    def document(keys=('fixture-key-a',)):
        return {'providers': [{'id': str(index), 'name': f'Fixture {index}', 'settingsConfig': {'env': {'OPENAI_BASE_URL': 'https://relay.example/v1', 'OPENAI_API_KEY': key}}} for index, key in enumerate(keys)]}

    def test_initial_state_rolls_back_with_provider(self):
        provider = self.create(commit=False)
        pid = provider.id
        self.assertIsNotNone(self.db.get(ProviderBalanceState, pid))
        self.db.rollback()
        self.assertIsNone(self.db.get(ProviderConfig, pid))
        self.assertIsNone(self.db.get(ProviderBalanceState, pid))

    def test_zero_balance_is_verified_with_two_requests_and_reused(self):
        provider, calls = self.create(), []
        def handler(request):
            calls.append(str(request.url))
            return httpx.Response(200, json={'hard_limit_usd': 0} if 'subscription' in request.url.path else {'total_usage': 0})
        result = self.execute(provider, handler)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['balance_infos'][0]['total_balance'], '0.00')
        self.assertEqual(status_for(self.service, provider.id)['state'], 'ready')
        self.assertIsNone(self.execute(provider, handler))
        self.assertEqual(len(calls), 2)

    def test_auth_failure_stops_across_new_session(self):
        provider, calls = self.create(), []
        self.execute(provider, lambda request: (calls.append(1), httpx.Response(401))[1])
        self.assertEqual(status_for(self.service, provider.id)['state'], 'auth_error')
        for _ in range(3):
            self.execute(provider, lambda request: (calls.append(1), httpx.Response(200))[1])
        with Session(self.engine) as db:
            service = ProviderService(db, 'test-workspace', 'test-actor', self.settings)
            self.assertIsNone(run_balance(service, provider.id, automatic=True, transport=httpx.MockTransport(lambda req: self.fail('Restart retried'))))
        self.assertEqual(len(calls), 1)

    def test_timeout_does_not_retry(self):
        provider, calls = self.create(), []
        def handler(request):
            calls.append(1)
            raise httpx.ReadTimeout('fixture timeout', request=request)
        self.execute(provider, handler)
        self.execute(provider, handler)
        self.assertEqual(len(calls), 1)
        self.assertEqual(status_for(self.service, provider.id)['state'], 'transient_error')

    def test_known_unsupported_uses_zero_requests(self):
        provider = self.create(url='https://api.openai.com/v1')
        self.execute(provider, lambda request: self.fail('Unexpected request'))
        self.assertEqual(status_for(self.service, provider.id)['state'], 'unsupported')
        self.assertEqual(status_for(self.service, provider.id)['request_count'], 0)

    def test_redirect_does_not_forward_credentials(self):
        provider, calls = self.create(), []
        self.execute(provider, lambda request: (calls.append(1), httpx.Response(302, headers={'location': 'https://other.example/'}))[1])
        self.assertEqual(calls, [1])
        self.assertEqual(status_for(self.service, provider.id)['state'], 'unsupported')

    def test_global_lease_prevents_another_provider_request(self):
        first, second = self.create(name='First'), self.create(name='Second')
        def handler(request):
            with Session(self.engine) as db:
                service = ProviderService(db, 'test-workspace', 'test-actor', self.settings)
                self.assertIsNone(run_balance(service, second.id, automatic=True, transport=httpx.MockTransport(lambda req: self.fail('Global single-flight failed'))))
            return httpx.Response(200, json={'hard_limit_usd': 1} if 'subscription' in request.url.path else {'total_usage': 0})
        self.execute(first, handler)
        self.assertEqual(status_for(self.service, second.id)['state'], 'pending')

    def test_expired_claim_is_interrupted_not_requeued(self):
        provider = self.create()
        state = self.db.get(ProviderBalanceState, provider.id)
        state.state, state.consumed, state.run_token, state.deadline_at = 'running', True, 'dead-worker', time.time() - 1
        self.db.commit()
        self.assertEqual(status_for(self.service, provider.id)['state'], 'interrupted')
        self.execute(provider, lambda request: self.fail('Interrupted attempt retried'))

    def test_budget_is_persisted_and_caps_actual_sends(self):
        provider, calls = self.create(), []
        def oversized_round(provider_id, *, transport):
            with httpx.Client(transport=transport) as client:
                for _ in range(5):
                    client.get('https://relay.example/fixture')
            self.fail('Budget not enforced')
        with patch.object(self.service, '_balance_unmanaged', side_effect=oversized_round):
            self.execute(provider, lambda request: (calls.append(1), httpx.Response(200, json={}))[1])
        self.assertEqual(len(calls), 3)
        self.assertEqual(status_for(self.service, provider.id)['request_count'], 3)
        self.assertNotEqual(status_for(self.service, provider.id)['state'], 'ready')

    def test_rotation_between_requests_stops_old_binding(self):
        provider, calls = self.create(), []
        def handler(request):
            calls.append(1)
            with Session(self.engine) as db:
                secret = db.get(ProviderSecret, provider.id)
                secret.secret_version += 1
                db.commit()
            return httpx.Response(200, json={'hard_limit_usd': 1})
        self.execute(provider, handler)
        self.assertEqual(len(calls), 1)
        status = status_for(self.service, provider.id)
        self.assertEqual(status['state'], 'invalidated')
        self.assertIsNone(status['last_result'])

    def test_manual_discovery_is_deduplicated(self):
        provider = self.create(url='https://api.openai.com/v1')
        self.execute(provider, lambda request: self.fail('Unexpected request'))
        state = self.db.get(ProviderBalanceState, provider.id)
        state.last_attempt_at = time.time() - 31
        self.db.commit()
        request_discovery(self.service, provider.id)
        generation = self.db.get(ProviderBalanceState, provider.id).generation
        request_discovery(self.service, provider.id)
        self.assertEqual(self.db.get(ProviderBalanceState, provider.id).generation, generation)

    def test_preview_redacts_commit_encrypts_and_is_idempotent(self):
        self.configure_source(self.document())
        preview = preview_sources(self.service)
        self.assertNotIn('fixture-key-a', json.dumps(preview))
        stored = self.db.get(ProviderImportPreview, preview['preview_id'])
        self.assertNotIn('fixture-key-a', json.dumps(stored.candidates))
        ids = [item['id'] for item in preview['candidates']]
        result = commit_preview(self.service, preview['preview_id'], ids)
        self.assertEqual(len(result['created']), 1)
        self.assertEqual(commit_preview(self.service, preview['preview_id'], ids), result)
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ProviderConfig)), 1)
        secret = self.db.get(ProviderSecret, result['created'][0]['provider_id'])
        self.assertNotEqual(secret.ciphertext, 'fixture-key-a')
        self.assertEqual(self.cipher.decrypt(secret.ciphertext), 'fixture-key-a')
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ProviderBalanceState)), 1)

    def test_same_url_different_keys_are_separate_accounts(self):
        self.configure_source(self.document(('fixture-key-a', 'fixture-key-b')))
        preview = preview_sources(self.service)
        result = commit_preview(self.service, preview['preview_id'], [item['id'] for item in preview['candidates']])
        self.assertEqual(len(result['created']), 2)
        repeated = preview_sources(self.service)
        self.assertTrue(all(item['duplicate'] for item in repeated['candidates']))

    def test_changed_source_requires_new_confirmation(self):
        path, _ = self.configure_source(self.document())
        preview = preview_sources(self.service)
        path.write_text(json.dumps(self.document(('fixture-key-changed',))), encoding='utf-8')
        with self.assertRaises(AppError) as caught:
            commit_preview(self.service, preview['preview_id'], [preview['candidates'][0]['id']])
        self.assertEqual(caught.exception.code, 'import_source_changed')
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ProviderConfig)), 0)

    def test_unconfigured_is_not_an_empty_scan(self):
        self.assertEqual(list_sources(self.service), {'sources': []})
        with self.assertRaises(AppError) as caught:
            preview_sources(self.service)
        self.assertEqual(caught.exception.code, 'import_sources_not_configured')

    def test_workspace_authorization_and_traversal(self):
        _, source = self.configure_source(self.document())
        source['workspace_ids'] = ['another-workspace']
        os.environ['LEARNGRAPH_PROVIDER_IMPORT_SOURCES'] = json.dumps([source])
        self.assertEqual(list_sources(self.service), {'sources': []})
        with self.assertRaises(AppError):
            preview_sources(self.service, ['fixture-source'])
        source['workspace_ids'], source['path'] = ['test-workspace'], '../outside.json'
        os.environ['LEARNGRAPH_PROVIDER_IMPORT_SOURCES'] = json.dumps([source])
        self.assertEqual(list_sources(self.service)['sources'][0]['status'], 'unavailable')

    def test_sqlite_and_nested_codexplusplus_config(self):
        with sqlite3.connect(self.root / 'cc-switch.db') as db:
            db.execute('CREATE TABLE providers (id TEXT, app_type TEXT, name TEXT, settings_config TEXT)')
            db.execute('INSERT INTO providers VALUES (?,?,?,?)', ('p', 'claude', 'Fixture', json.dumps({'env': {'ANTHROPIC_AUTH_TOKEN': 'fixture-token', 'ANTHROPIC_BASE_URL': 'https://anthropic-relay.example', 'ANTHROPIC_MODEL': 'fixture-model'}})))
        db.close()
        items, warnings, _ = read_source(dict(path='cc-switch.db', kind='ccswitch'))
        self.assertEqual(items[0]['provider_type'], 'anthropic_messages')
        self.assertEqual(items[0]['model_ids'], ['fixture-model'])
        self.assertFalse(warnings)
        config = '\n'.join(['model_provider = ' + json.dumps('relay'), 'model = ' + json.dumps('fixture-model'), '[model_providers.relay]', 'base_url = ' + json.dumps('https://relay.example/v1'), 'wire_api = ' + json.dumps('responses'), 'experimental_bearer_token = ' + json.dumps('fixture-nested-key')])
        items, warnings = parse_document({'relayProfiles': [{'name': 'Fixture', 'configContents': config, 'authContents': '{}'}]}, 'codexplusplus')
        self.assertEqual(items[0]['api_key'], 'fixture-nested-key')
        self.assertEqual(items[0]['provider_type'], 'openai_responses')
        self.assertFalse(warnings)

    def test_environment_cannot_choose_arbitrary_variable_or_url(self):
        for source_id, url in [('env-PRIVATE_TEST_TOKEN', 'https://api.openai.com/v1'), ('env-OPENAI_API_KEY', 'https://evil.example')]:
            with self.assertRaises(AppError):
                self.service.import_from_discovered(DiscoveredProviderImportRequest(source_id=source_id, target_provider_type='openai_responses', base_url=url))
        self.assertEqual(self.db.scalar(select(func.count()).select_from(ProviderConfig)), 0)

    def custom_payload(self):
        from app.domain.schemas.management import ProviderBalanceQueryExecuteRequest
        return ProviderBalanceQueryExecuteRequest(request={'url': 'https://relay.example/balance'})

    def test_duplicate_native_ids_do_not_swap_selected_connection(self):
        document = self.document(('fixture-key-a', 'fixture-key-b'))
        for item in document['providers']:
            item['id'] = 'same-id'
        self.configure_source(document)
        preview = preview_sources(self.service)
        result = commit_preview(self.service, preview['preview_id'], [preview['candidates'][0]['id']])
        secret = self.db.get(ProviderSecret, result['created'][0]['provider_id'])
        self.assertEqual(self.cipher.decrypt(secret.ciphertext), 'fixture-key-a')

    def test_historical_import_identity_does_not_shadow_changed_provider(self):
        self.configure_source(self.document())
        first = preview_sources(self.service)
        imported = commit_preview(self.service, first['preview_id'], [first['candidates'][0]['id']])
        old = self.db.get(ProviderConfig, imported['created'][0]['provider_id'])
        old.base_url = 'https://changed.example/v1'
        self.db.commit()
        second = preview_sources(self.service)
        self.assertFalse(second['candidates'][0]['duplicate'])
        imported_again = commit_preview(self.service, second['preview_id'], [second['candidates'][0]['id']])
        self.assertEqual(len(imported_again['created']), 1)
        self.assertNotEqual(imported_again['created'][0]['provider_id'], old.id)
        self.assertEqual(old.base_url, 'https://changed.example/v1')

    def test_invalid_wire_type_is_a_warning(self):
        data = {'config': {'model_provider': 'relay', 'model_providers': {'relay': {'base_url': 'https://relay.example/v1', 'wire_api': []}}}, 'auth': {'OPENAI_API_KEY': 'fixture'}}
        candidates, warnings = parse_document(data, 'codex')
        self.assertEqual(candidates, [])
        self.assertEqual(len(warnings), 1)

    def test_native_codex_pair_and_auth_change(self):
        config = chr(10).join(['model_provider = ' + json.dumps('relay'), 'model = ' + json.dumps('fixture-model'), '[model_providers.relay]', 'base_url = ' + json.dumps('https://relay.example/v1'), 'wire_api = ' + json.dumps('responses')])
        (self.root / 'config.toml').write_text(config, encoding='utf-8')
        auth = self.root / 'auth.json'
        auth.write_text(json.dumps({'OPENAI_API_KEY': 'fixture-native-key'}), encoding='utf-8')
        source = dict(id='native', label='Native', kind='codex', path='config.toml', auth_path='auth.json', workspace_ids=['test-workspace'])
        os.environ['LEARNGRAPH_PROVIDER_IMPORT_SOURCES'] = json.dumps([source])
        candidates, warnings, _ = read_source(source)
        self.assertEqual(candidates[0]['provider_type'], 'openai_responses')
        self.assertEqual(candidates[0]['api_key'], 'fixture-native-key')
        self.assertFalse(warnings)
        preview = preview_sources(self.service)
        self.assertNotIn('fixture-native-key', json.dumps(preview))
        auth.write_text(json.dumps({'OPENAI_API_KEY': 'fixture-new-key'}), encoding='utf-8')
        with self.assertRaises(AppError) as caught:
            commit_preview(self.service, preview['preview_id'], [preview['candidates'][0]['id']])
        self.assertEqual(caught.exception.code, 'import_source_changed')
        with self.assertRaises((ValueError, OSError)):
            read_source({**source, 'auth_path': '../outside.json'})

    def test_ready_refresh_reuses_method_then_pauses_on_failure(self):
        provider, calls = self.create(), []
        def good(request):
            calls.append(1)
            return httpx.Response(200, json={'hard_limit_usd': 5} if 'subscription' in request.url.path else {'total_usage': 0})
        self.execute(provider, good)
        state = self.db.get(ProviderBalanceState, provider.id)
        method = state.method_id
        self.assertEqual(run_balance(self.service, provider.id, transport=httpx.MockTransport(lambda req: self.fail('Cooldown missed'))), state.last_result)
        state.last_attempt_at, state.next_query_at = time.time() - 31, time.time() - 1
        self.db.commit()
        self.execute(provider, good)
        self.assertEqual(len(calls), 4)
        self.assertEqual(status_for(self.service, provider.id)['method_id'], method)
        state = self.db.get(ProviderBalanceState, provider.id)
        state.last_attempt_at, state.next_query_at = time.time() - 31, time.time() - 1
        self.db.commit()
        self.execute(provider, lambda req: httpx.Response(503))
        stopped = status_for(self.service, provider.id)
        self.assertEqual(stopped['state'], 'paused')
        self.assertIsNone(stopped['next_query_at'])
        self.execute(provider, lambda req: self.fail('Paused refresh retried'))

    def test_stale_ready_snapshot_cannot_claim_after_another_completion(self):
        from app.services import provider_balance_lifecycle as lifecycle
        provider, calls = self.create(), []
        def handler(request):
            calls.append(1)
            return httpx.Response(200, json={'hard_limit_usd': 2} if 'subscription' in request.url.path else {'total_usage': 0})
        self.execute(provider, handler)
        state = self.db.get(ProviderBalanceState, provider.id)
        state.last_attempt_at, state.next_query_at = time.time() - 31, time.time() - 1
        self.db.commit()
        original, interleave = lifecycle._acquire, True
        def acquire(db, token, now):
            nonlocal interleave
            if interleave:
                interleave = False
                with Session(self.engine) as other_db:
                    other = ProviderService(other_db, 'test-workspace', 'test-actor', self.settings)
                    with patch.object(other, '_decrypt_secret', return_value='fixture-api-key'):
                        run_balance(other, provider.id, automatic=True, transport=httpx.MockTransport(handler))
            return original(db, token, now)
        with patch.object(lifecycle, '_acquire', side_effect=acquire):
            self.assertIsNone(self.execute(provider, handler))
        self.assertEqual(len(calls), 4)
        self.assertEqual(status_for(self.service, provider.id)['state'], 'ready')

    def test_custom_query_cooldown_shared_across_sessions_and_background(self):
        from app.services.provider_balance_manual import run_custom_balance
        provider, calls = self.create(), []
        response = run_custom_balance(self.service, provider.id, self.custom_payload(), transport=httpx.MockTransport(lambda req: (calls.append(1), httpx.Response(200, json={'remaining': 7}))[1]))
        self.assertTrue(response['query_token'])
        with Session(self.engine) as db:
            service = ProviderService(db, 'test-workspace', 'test-actor', self.settings)
            with self.assertRaises(AppError) as caught:
                run_custom_balance(service, provider.id, self.custom_payload(), transport=httpx.MockTransport(lambda req: self.fail('Repeated manual send')))
            self.assertEqual(caught.exception.code, 'balance_cooldown')
        self.assertIsNone(self.execute(provider, lambda req: self.fail('Background ignored manual cooldown')))
        self.assertEqual(calls, [1])

    def test_custom_result_is_single_use_and_rejects_changed_binding(self):
        from app.services.provider_balance_manual import run_custom_balance
        from app.domain.schemas.management import ProviderBalanceQueryResultRequest
        provider = self.create()
        response = run_custom_balance(self.service, provider.id, self.custom_payload(), transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'remaining': 7})))
        result = ProviderBalanceQueryResultRequest(query_token=response['query_token'], remaining=7)
        self.service.save_balance_query_result(provider.id, result)
        with self.assertRaises(AppError) as caught:
            self.service.save_balance_query_result(provider.id, result)
        self.assertEqual(caught.exception.code, 'balance_result_stale')
        manual = self.db.get(ProviderBalanceManualState, provider.id)
        manual.last_attempt_at = time.time() - 31
        self.db.commit()
        response = run_custom_balance(self.service, provider.id, self.custom_payload(), transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'remaining': 8})))
        provider.base_url = 'https://new.example/v1'
        self.db.commit()
        with self.assertRaises(AppError) as caught:
            self.service.save_balance_query_result(provider.id, ProviderBalanceQueryResultRequest(query_token=response['query_token'], remaining=8))
        self.assertEqual(caught.exception.code, 'balance_result_stale')

    def test_custom_request_has_one_send_budget_and_global_singleflight(self):
        from app.services.provider_balance_manual import run_custom_balance
        first, second, calls = self.create(name='First'), self.create(name='Second'), []
        def handler(request):
            calls.append(1)
            with Session(self.engine) as db:
                other = ProviderService(db, 'test-workspace', 'test-actor', self.settings)
                self.assertIsNone(run_balance(other, second.id, automatic=True, transport=httpx.MockTransport(lambda req: self.fail('Parallel request'))))
            return httpx.Response(200, json={})
        def too_many(provider_id, payload, *, transport):
            with httpx.Client(transport=transport) as client:
                client.get('https://relay.example/one')
                client.get('https://relay.example/two')
            self.fail('Manual budget was not enforced')
        with patch.object(self.service, '_execute_balance_query_unmanaged', side_effect=too_many):
            with self.assertRaises(AppError):
                run_custom_balance(self.service, first.id, self.custom_payload(), transport=httpx.MockTransport(handler))
        self.assertEqual(calls, [1])
        self.assertEqual(self.db.get(ProviderBalanceManualState, first.id).state, 'failed')
        self.assertEqual(self.db.get(ProviderBalanceManualState, first.id).request_count, 1)
        self.assertEqual(self.db.get(ProviderBalanceLease, 'global').expires_at, 0)

    def test_legacy_custom_interval_is_disabled_on_server(self):
        from app.domain.schemas.management import ProviderBalanceQueryConfig
        provider = self.create()
        config = ProviderBalanceQueryConfig(script='fixture', enabled=True, auto_query_interval_minutes=1)
        saved = self.service.update_balance_query_config(provider.id, config)
        self.assertEqual(saved['config'].auto_query_interval_minutes, 0)
        self.assertEqual(status_for(self.service, provider.id)['state'], 'invalidated')

    def test_binding_change_clears_cached_custom_balance_and_receipt(self):
        from app.services.provider_balance_manual import run_custom_balance
        from app.domain.schemas.management import ProviderBalanceQueryResultRequest
        provider = self.create()
        response = run_custom_balance(self.service, provider.id, self.custom_payload(), transport=httpx.MockTransport(lambda req: httpx.Response(200, json={'remaining': 4})))
        self.service.save_balance_query_result(provider.id, ProviderBalanceQueryResultRequest(query_token=response['query_token'], remaining=4))
        self.assertIn('last_result', provider.capabilities['balance_query'])
        provider.base_url = 'https://changed.example/v1'
        self.db.commit()
        self.assertEqual(status_for(self.service, provider.id)['state'], 'invalidated')
        self.db.refresh(provider)
        self.assertNotIn('last_result', provider.capabilities['balance_query'])
        self.assertEqual(self.db.get(ProviderBalanceManualState, provider.id).state, 'invalidated')

if __name__ == '__main__':
    unittest.main(verbosity=2)
