"""Generic TURN regressions, using disposable SQLite and fictitious credentials."""
from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.migrations import _voice_relay_generic_turn
from app.domain.models import VoiceRelayConfig, VoiceRelaySecret
from app.services import voice_relay as relay

URLS = [
    "turn:relay.example.com:3478?transport=udp",
    "turn:relay.example.com:3478?transport=tcp",
    "turns:relay.example.com:5349?transport=tcp",
]
PASSWORD = "test-only-turn-password"


class VoiceRelayTurnTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        VoiceRelayConfig.__table__.create(self.engine)
        VoiceRelaySecret.__table__.create(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.settings = SimpleNamespace(
            secret_provider="environment", master_key="test-only-master-key", master_key_version=1,
        )
        self.service = relay.VoiceRelayService(self.db, self.settings)
        relay.invalidate_cache()
        self.mint = self.enterContext(patch.object(
            relay, "mint_cloudflare_credentials", side_effect=AssertionError("Unexpected Cloudflare request")
        ))
        self.probe = self.enterContext(patch.object(relay, "probe_ice_urls", new_callable=AsyncMock))
        self.probe.return_value = [{"url": url, "scheme": "turn", "ok": True, "elapsed_ms": 1} for url in URLS]

    def tearDown(self):
        relay.invalidate_cache()
        self.db.close()
        self.engine.dispose()

    def save(self, **extra):
        return self.service.save({
            "mode": "turn", "urls": URLS, "username": "test-user", "secret": PASSWORD, **extra,
        }, actor_id="admin")

    def test_password_is_encrypted_and_admin_config_never_echoes_it(self):
        public = self.save()
        config = self.service.get_config()
        row = self.service.get_secret_row(config.id)
        self.assertNotIn(PASSWORD, row.ciphertext)
        self.assertNotIn(PASSWORD, json.dumps(public))
        self.assertEqual(public["secret_masked"], "••••")
        self.assertEqual(public["secret_mode"], "turn")
        self.assertEqual(public["username"], "test-user")
        resolved = self.service.resolve()
        self.assertEqual(resolved.source, "turn")
        self.assertEqual(resolved.urls, tuple(URLS))
        self.assertEqual(resolved.as_client_config()["credential"], PASSWORD)
        server = resolved.as_aiortc_servers()[0]
        self.assertEqual(server.urls, URLS)
        self.assertEqual(server.username, "test-user")
        self.assertEqual(server.credential, PASSWORD)
        self.mint.assert_not_called()

    def test_empty_password_retains_ciphertext_and_edit_invalidates_cache(self):
        self.save()
        self.service.resolve()
        row = self.service.get_secret_row(self.service.get_config().id)
        original = row.ciphertext
        self.save(secret="", username="changed-user", urls=list(reversed(URLS)))
        self.assertEqual(row.ciphertext, original)
        resolved = self.service.resolve()
        self.assertEqual(resolved.username, "changed-user")
        self.assertEqual(resolved.urls[0], URLS[-1])
        self.assertEqual(resolved.credential, PASSWORD)

    def test_password_rotation_retires_old_runtime_credential(self):
        self.save()
        self.service.resolve()
        self.save(secret="replacement-test-password")
        self.assertEqual(self.service.resolve().credential, "replacement-test-password")
        self.assertIsNone(relay._LAST_GOOD)

    def test_static_password_preserves_spaces(self):
        self.save(secret=" test-password ")
        self.assertEqual(self.service.resolve().credential, " test-password ")

    def test_disabling_and_deleting_retire_cached_credentials(self):
        self.save()
        self.service.resolve()
        self.service.set_enabled(False, actor_id="admin")
        self.assertEqual(self.service.resolve().urls, ())
        self.service.set_enabled(True, actor_id="admin")
        self.assertEqual(self.service.resolve().credential, PASSWORD)
        self.service.clear()
        self.assertEqual(self.service.resolve().urls, ())
        self.assertEqual(self.db.query(VoiceRelaySecret).count(), 0)

    def test_manual_stun_retains_generic_credential_without_decrypting(self):
        self.save()
        with patch.object(self.service, "_open_secret", side_effect=AssertionError("Must not decrypt")):
            public = self.service.save({"mode": "manual"}, actor_id="admin")
            self.assertEqual(public["secret_mode"], "turn")
            self.assertIsNone(self.service.resolve().credential)
        self.service.save({"mode": "turn"}, actor_id="admin")
        self.assertEqual(self.service.resolve().credential, PASSWORD)

    def test_cloudflare_token_cannot_be_reused_as_turn_password(self):
        self.service.save({"mode": "cloudflare", "key_id": "test-key", "secret": "test-api-token"}, actor_id="admin")
        with self.assertRaises(AppError):
            self.save(secret="")
        with self.assertRaises(AppError):
            asyncio.run(self.service.test({"mode": "turn", "urls": URLS, "username": "user"}))

    def test_turn_password_cannot_be_reused_as_cloudflare_token_even_via_manual(self):
        self.save()
        self.service.save({"mode": "manual"}, actor_id="admin")
        with self.assertRaises(AppError):
            self.service.save({"mode": "cloudflare", "key_id": "test-key"}, actor_id="admin")
        with self.assertRaises(AppError):
            asyncio.run(self.service.test({"mode": "cloudflare", "key_id": "test-key"}))
        self.mint.assert_not_called()

    def test_test_draft_never_saves_password_or_changes_active_config(self):
        result = asyncio.run(self.service.test({
            "mode": "turn", "urls": URLS, "username": "draft-user", "secret": PASSWORD,
        }))
        self.assertTrue(result["ok"])
        self.assertNotIn(PASSWORD, json.dumps(result))
        self.assertIsNone(self.service.get_config())
        self.probe.assert_awaited_once_with(tuple(URLS), username="draft-user", credential=PASSWORD)
        self.mint.assert_not_called()

    def test_probe_requires_first_turn_to_work_and_persists_matching_status(self):
        self.save()
        self.probe.return_value[0]["ok"] = False
        result = asyncio.run(self.service.test({"mode": "turn"}))
        self.assertFalse(result["ok"], "Secondary TCP/TLS success must not hide broken server preference")
        self.assertEqual(self.service.get_config().status, "error")
        self.service.resolve()
        self.assertEqual(self.service.get_config().status, "error", "Reading credentials cannot erase a failed probe")

    def test_draft_probe_cannot_overwrite_saved_status(self):
        self.save()
        self.probe.return_value[0]["ok"] = False
        asyncio.run(self.service.test({"mode": "turn", "username": "draft-user"}))
        self.assertEqual(self.service.get_config().status, "configured")

    def test_optional_stun_is_browser_only_and_never_implies_turn_success(self):
        stun = ["stun:stun.example.com:3478"]
        self.save(stun_urls=stun, stun_fallback_enabled=True)
        resolved = self.service.resolve()
        self.assertEqual(resolved.urls, tuple(URLS + stun))
        self.assertEqual(resolved.as_aiortc_servers()[0].urls, URLS)
        self.probe.return_value = [
            {"url": URLS[0], "scheme": "turn", "ok": False, "elapsed_ms": 1},
            {"url": stun[0], "scheme": "stun", "ok": True, "elapsed_ms": 1},
        ]
        self.assertFalse(asyncio.run(self.service.test({}))["ok"])

    def test_unreadable_password_never_revives_cloudflare_cache(self):
        self.save()
        self.settings.master_key = "wrong-test-key"
        result = self.service.resolve()
        self.assertEqual(result.source, "none")
        self.assertIsNone(result.credential)
        self.mint.assert_not_called()

    def test_cloudflare_draft_does_not_send_its_credential_to_saved_generic_urls(self):
        self.save()
        self.mint.side_effect = None
        self.mint.return_value = {"username": "ephemeral-user", "credential": "test-ephemeral-password", "urls": list(relay.DEFAULT_URLS)}
        asyncio.run(self.service.test({"mode": "cloudflare", "key_id": "test-key", "secret": "test-api-token"}))
        self.assertEqual(self.probe.await_args.args[0], relay.DEFAULT_URLS)
        self.assertEqual(self.service.get_config().mode, "turn")

    def test_rotation_during_decryption_cannot_restore_old_cached_password(self):
        self.save()
        original = self.service._open_secret
        def decrypt_then_rotate(config):
            stored = original(config)
            self.save(secret="rotated-test-password")
            self.service._open_secret = original
            return stored
        with patch.object(self.service, "_open_secret", side_effect=decrypt_then_rotate):
            self.assertEqual(self.service.resolve().credential, "rotated-test-password")
        self.assertEqual(self.service.resolve().credential, "rotated-test-password")

    def test_missing_fields_and_invalid_turn_urls_are_rejected(self):
        for extra in ({"secret": ""}, {"username": ""}, {"username": True}, {"urls": None}, {"urls": []},
                      {"urls": ["stun:example.com"]}, {"urls": ["turn:"]}, {"urls": ["turn:host:0"]},
                      {"urls": ["turn:host:65536"]}, {"urls": ["turn:user:pass@host"]},
                      {"urls": ["turn:host/path"]}, {"urls": [123]}, {"secret": True},
                      {"urls": [f"turn:h{i}.example:3478" for i in range(17)]}):
            with self.subTest(extra=extra), self.assertRaises(AppError):
                self.save(**extra)
        self.assertIsNone(self.service.get_config())

    def test_newline_input_deduplicates_without_changing_order(self):
        self.save(urls=f" {URLS[1]}\n{URLS[0]}\n{URLS[1]} ")
        self.assertEqual(self.service.resolve().urls, (URLS[1], URLS[0]))

    def test_generic_migration_is_additive_idempotent_and_labels_legacy_tokens(self):
        engine = create_engine("sqlite:///:memory:")
        try:
            with engine.begin() as conn:
                conn.exec_driver_sql("CREATE TABLE voice_relay_configs (id TEXT PRIMARY KEY, mode TEXT)")
                conn.exec_driver_sql("INSERT INTO voice_relay_configs VALUES ('old', 'cloudflare')")
                _voice_relay_generic_turn(conn)
                _voice_relay_generic_turn(conn)
                row = conn.exec_driver_sql("SELECT mode, username, secret_mode FROM voice_relay_configs").one()
                self.assertEqual(tuple(row), ("cloudflare", None, "cloudflare"))
        finally:
            engine.dispose()
