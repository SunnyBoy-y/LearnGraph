"""Offline regression checks: python -m unittest discover -s tests -p test_voice_relay_stun.py."""
from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.core.migrations import _voice_relay_stun_pool
from app.domain.models import VoiceRelayConfig, VoiceRelaySecret
from app.services import voice_relay as relay


class VoiceRelayStunTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        VoiceRelayConfig.__table__.create(self.engine)
        VoiceRelaySecret.__table__.create(self.engine)
        self.db = Session(self.engine, expire_on_commit=False)
        self.service = relay.VoiceRelayService(self.db, SimpleNamespace())
        relay.invalidate_cache()
        self.mint = self.enterContext(patch.object(
            relay, "mint_cloudflare_credentials", side_effect=AssertionError("Unexpected network mint")
        ))
        self.probe = self.enterContext(patch.object(relay, "probe_ice_urls", new_callable=AsyncMock))
        self.probe.return_value = [{
            "url": relay.DEFAULT_STUN_URLS[0], "scheme": "stun", "ok": True, "elapsed_ms": 1
        }]

    def tearDown(self):
        relay.invalidate_cache()
        self.db.close()
        self.engine.dispose()

    def cloudflare(self, *, fallback=False):
        config = VoiceRelayConfig(
            scope="deployment", enabled=True, mode="cloudflare",
            urls=list(relay.DEFAULT_URLS), stun_urls=list(relay.DEFAULT_STUN_URLS),
            stun_fallback_enabled=fallback, key_id="test-key-id",
            secret_fingerprint="test-fingerprint", secret_masked="test-mask",
            credential_ttl_seconds=3600, status="configured",
        )
        self.db.add(config)
        self.db.commit()
        self.db.add(VoiceRelaySecret(config_id=config.id, ciphertext="test-only-sealed-value"))
        self.db.commit()
        self.open_secret = self.enterContext(patch.object(
            self.service, "_open_secret", return_value={"api_token": "test-only-token"}
        ))
        return config

    def mint_ok(self):
        self.mint.side_effect = None
        self.mint.return_value = {
            "urls": list(relay.DEFAULT_URLS), "username": "ephemeral-user", "credential": "ephemeral-secret"
        }

    def test_unconfigured_does_not_enable_public_stun(self):
        result = self.service.resolve()
        self.assertEqual(result.urls, ())
        self.mint.assert_not_called()
        public = self.service.public_config()
        self.assertFalse(public["configured"])
        self.assertFalse(public["stun_fallback_enabled"])
        self.assertEqual(public["defaults"]["stun_urls"], list(relay.DEFAULT_STUN_URLS))

    def test_manual_save_resolve_and_probe_without_cloudflare(self):
        with patch.object(self.service, "_open_secret", side_effect=AssertionError("Secret must not open")):
            public = self.service.save({"mode": "manual", "stun_urls": list(relay.DEFAULT_STUN_URLS)}, actor_id="admin")
            self.assertTrue(public["configured"])
            self.assertFalse(public["secret_configured"])
            result = self.service.resolve()
            self.assertEqual(result.source, "manual")
            self.assertEqual(result.urls, relay.DEFAULT_STUN_URLS)
            self.assertIsNone(result.credential)
            self.assertEqual(result.as_aiortc_servers(), [])
            self.assertNotIn("credential", result.as_client_config())
            tested = asyncio.run(self.service.test({"mode": "manual"}))
            self.assertTrue(tested["ok"])
            self.assertEqual(tested["cloudflare_urls"], [])
            self.assertIn("不提供 TURN", tested["detail"])
        self.mint.assert_not_called()

    def test_new_manual_mode_can_use_explicit_default_pool(self):
        saved = self.service.save({"mode": "manual"}, actor_id="admin")
        self.assertEqual(saved["stun_urls"], list(relay.DEFAULT_STUN_URLS))
        self.mint.assert_not_called()

    def test_validation_deduplicates_preserving_order(self):
        a, b, _ = relay.DEFAULT_STUN_URLS
        self.assertEqual(self.service._validate_stun_urls(f" {a} \n{b},{a}\r\n"), (a, b))

    def test_invalid_stun_urls_rejected(self):
        for raw in ([], ["turn:example.com:3478"], ["stuns:example.com:5349"], ["https://example.com"],
                    ["stun:"], ["stun:example.com:65536"], ["stun:example.com:0"],
                    ["stun:user@example.com:3478"], ["stun:bad host:3478"], [123], True,
                    [f"stun:s{i}.example.com:3478" for i in range(17)]):
            with self.subTest(raw=raw), self.assertRaises(AppError):
                self.service._validate_stun_urls(raw)

    def test_false_string_is_not_an_opt_in(self):
        with self.assertRaises(AppError):
            self.service.save({"mode": "manual", "stun_fallback_enabled": "false"}, actor_id="admin")

    def test_cloudflare_still_requires_key_and_secret(self):
        with self.assertRaises(AppError):
            self.service.save({"mode": "cloudflare"}, actor_id="admin")
        with self.assertRaises(AppError):
            self.service.save({"mode": "cloudflare", "key_id": "key"}, actor_id="admin")

    def test_failed_mint_without_opt_in_keeps_legacy_behavior(self):
        self.cloudflare()
        self.mint.side_effect = TimeoutError("test timeout")
        result = self.service.resolve()
        self.assertEqual(result.source, "none")
        self.assertEqual(result.urls, ())

    def test_failed_mint_with_opt_in_returns_only_stun(self):
        self.cloudflare(fallback=True)
        self.mint.side_effect = TimeoutError("test timeout")
        result = self.service.resolve()
        self.assertEqual(result.source, "fallback")
        self.assertEqual(result.urls, relay.DEFAULT_STUN_URLS)
        self.assertIsNone(result.credential)
        self.assertEqual(result.as_aiortc_servers(), [])
        self.assertEqual(self.service.resolve(), result)
        self.mint.assert_called_once()

    def test_missing_or_unreadable_secret_does_not_remove_stun(self):
        config = self.cloudflare(fallback=True)
        self.open_secret.side_effect = AppError(404, "test_missing", "test secret missing")
        self.assertEqual(self.service.resolve().source, "fallback")
        self.mint.assert_not_called()
        relay.invalidate_cache()
        config.secret_fingerprint = None
        self.db.commit()
        self.assertEqual(self.service.resolve().source, "fallback")
        self.mint.assert_not_called()

    def test_success_includes_pool_and_preserves_turn_order(self):
        self.cloudflare(fallback=True)
        self.mint_ok()
        result = self.service.resolve()
        self.assertEqual(result.source, "deployment")
        self.assertEqual(result.urls[:len(relay.DEFAULT_URLS)], relay.DEFAULT_URLS)
        self.assertEqual(result.urls[-3:], relay.DEFAULT_STUN_URLS)
        servers = result.as_aiortc_servers()
        self.assertEqual(len(servers), 1)
        self.assertTrue(all(url.startswith(("turn:", "turns:")) for url in servers[0].urls))
        self.assertEqual(servers[0].credential, "ephemeral-secret")

    def test_failed_refresh_keeps_valid_turn_and_stun(self):
        self.cloudflare(fallback=True)
        self.mint_ok()
        healthy = self.service.resolve()
        self.mint.side_effect = TimeoutError("test refresh timeout")
        stale = self.service.resolve(force=True)
        self.assertEqual(stale.source, "stale")
        self.assertEqual(stale.credential, healthy.credential)
        self.assertEqual(stale.urls, healthy.urls)

    def test_expired_turn_not_served_even_while_cache_is_fresh(self):
        self.cloudflare(fallback=True)
        self.mint_ok()
        self.service.resolve()
        relay._LAST_GOOD.credential_expires_at = time.monotonic() - 1
        self.mint.side_effect = TimeoutError("test refresh timeout")
        result = self.service.resolve()
        self.assertEqual(result.source, "fallback")
        self.assertIsNone(result.credential)
        calls = self.mint.call_count
        self.assertEqual(self.service.resolve(), result)
        self.assertEqual(self.mint.call_count, calls, "Fallback cache must not inherit expired TURN TTL")

    def test_disabled_never_revives_public_pool_or_old_turn(self):
        self.cloudflare(fallback=True)
        self.mint_ok()
        self.service.resolve()
        self.service.set_enabled(False, actor_id="admin")
        self.assertEqual(self.service.resolve().urls, ())
        self.assertIsNone(relay._LAST_GOOD)

    def test_disable_during_mint_discards_successful_old_response(self):
        self.cloudflare(fallback=True)
        def mint_then_disable(**kwargs):
            self.service.set_enabled(False, actor_id="admin")
            return {"urls": list(relay.DEFAULT_URLS), "username": "old", "credential": "old"}
        self.mint.side_effect = mint_then_disable
        result = self.service.resolve()
        self.assertEqual(result.urls, ())
        self.assertIsNone(relay._LAST_GOOD)

    def test_disable_during_failed_mint_does_not_revive_stun(self):
        self.cloudflare(fallback=True)
        def fail_then_disable(**kwargs):
            self.service.set_enabled(False, actor_id="admin")
            raise TimeoutError("test timeout")
        self.mint.side_effect = fail_then_disable
        self.assertEqual(self.service.resolve().urls, ())

    def test_clearing_manual_configuration_drops_cached_pool(self):
        self.service.save({"mode": "manual"}, actor_id="admin")
        self.assertTrue(self.service.resolve().urls)
        self.service.clear()
        self.assertEqual(self.service.resolve().urls, ())
        self.mint.assert_not_called()

    def test_mode_switch_preserves_stored_cloudflare_but_never_uses_it(self):
        config = self.cloudflare(fallback=True)
        self.mint_ok()
        self.service.resolve()
        saved = self.service.save({"mode": "manual", "stun_urls": list(relay.DEFAULT_STUN_URLS)}, actor_id="admin")
        self.assertEqual(saved["key_id"], config.key_id)
        self.assertEqual(saved["secret_fingerprint"], "test-fingerprint")
        self.mint.reset_mock()
        self.open_secret.reset_mock()
        result = self.service.resolve()
        self.assertEqual(result.source, "manual")
        self.assertIsNone(result.credential)
        self.mint.assert_not_called()
        self.open_secret.assert_not_called()

    def test_manual_probe_does_not_overwrite_saved_cloudflare_status(self):
        config = self.cloudflare()
        config.status = "error"
        config.status_detail = "existing error"
        self.db.commit()
        asyncio.run(self.service.test({"mode": "manual", "stun_urls": list(relay.DEFAULT_STUN_URLS)}))
        self.db.refresh(config)
        self.assertEqual(config.status_detail, "existing error")
        self.mint.assert_not_called()

    def test_cloudflare_probe_reports_stun_only_as_degraded(self):
        self.cloudflare(fallback=True)
        self.mint.side_effect = TimeoutError("test timeout")
        tested = asyncio.run(self.service.test({"mode": "cloudflare"}))
        self.assertFalse(tested["ok"])
        self.assertTrue(tested["fallback_available"])
        self.assertIn("不代表 TURN", tested["detail"])
        self.probe.assert_awaited_once_with(relay.DEFAULT_STUN_URLS)

    def test_separate_nonexpiring_session_disable_during_mint(self):
        self.cloudflare(fallback=True)
        def mint(**kwargs):
            with Session(self.engine, expire_on_commit=False) as admin_db:
                relay.VoiceRelayService(admin_db, SimpleNamespace()).set_enabled(False, actor_id="admin")
            return {"urls": list(relay.DEFAULT_URLS), "username": "old", "credential": "old"}
        self.mint.side_effect = mint
        self.assertEqual(self.service.resolve().urls, ())
        self.mint.assert_called_once()

    def test_disable_during_secret_failure_cannot_revive_pool(self):
        self.cloudflare(fallback=True)
        def open_secret(config):
            with Session(self.engine, expire_on_commit=False) as admin_db:
                relay.VoiceRelayService(admin_db, SimpleNamespace()).set_enabled(False, actor_id="admin")
            raise AppError(404, "test_missing", "test secret failure")
        self.open_secret.side_effect = open_secret
        self.assertEqual(self.service.resolve().urls, ())
        self.mint.assert_not_called()

    def test_separate_nonexpiring_session_switch_to_manual_during_mint(self):
        self.cloudflare(fallback=True)
        def mint(**kwargs):
            with Session(self.engine, expire_on_commit=False) as admin_db:
                relay.VoiceRelayService(admin_db, SimpleNamespace()).save(
                    {"mode": "manual", "stun_urls": [relay.DEFAULT_STUN_URLS[1]]}, actor_id="admin"
                )
            return {"urls": list(relay.DEFAULT_URLS), "username": "old", "credential": "old"}
        self.mint.side_effect = mint
        resolved = self.service.resolve()
        self.assertEqual(resolved.source, "manual")
        self.assertEqual(resolved.urls, (relay.DEFAULT_STUN_URLS[1],))
        self.assertIsNone(resolved.credential)
        self.mint.assert_called_once()

    def test_enabling_and_editing_stun_pool_keeps_valid_turn(self):
        self.cloudflare()
        self.mint_ok()
        healthy = self.service.resolve()
        expiry = relay._LAST_GOOD.credential_expires_at
        self.service.save({"mode": "cloudflare", "stun_fallback_enabled": True}, actor_id="admin")
        self.assertIsNotNone(relay._LAST_GOOD)
        self.assertEqual(relay._LAST_GOOD.credential_expires_at, expiry)
        self.mint.side_effect = TimeoutError("test refresh failure")
        resolved = self.service.resolve()
        self.assertEqual(resolved.source, "stale")
        self.assertEqual(resolved.credential, healthy.credential)
        self.assertIn(relay.DEFAULT_STUN_URLS[0], resolved.urls)
        self.service.save({"mode": "cloudflare", "stun_urls": [relay.DEFAULT_STUN_URLS[1]]}, actor_id="admin")
        resolved = self.service.resolve()
        self.assertEqual(resolved.credential, healthy.credential)
        self.assertNotIn(relay.DEFAULT_STUN_URLS[0], resolved.urls)
        self.assertIn(relay.DEFAULT_STUN_URLS[1], resolved.urls)
        self.service.save({"mode": "cloudflare", "stun_fallback_enabled": False}, actor_id="admin")
        resolved = self.service.resolve()
        self.assertEqual(resolved.credential, healthy.credential)
        self.assertNotIn(relay.DEFAULT_STUN_URLS[1], resolved.urls)

    def test_changing_turn_identity_retires_old_credentials(self):
        self.cloudflare(fallback=True)
        self.mint_ok()
        self.service.resolve()
        self.service.save({"mode": "cloudflare", "key_id": "different-key"}, actor_id="admin")
        self.assertIsNone(relay._LAST_GOOD)

    def test_cloudflare_probe_does_not_overwrite_saved_manual_status(self):
        self.cloudflare()
        self.service.save({"mode": "manual"}, actor_id="admin")
        config = self.service.get_config()
        config.status = "error"
        config.status_detail = "manual status marker"
        self.db.commit()
        self.mint_ok()
        self.probe.return_value = [{"url": relay.DEFAULT_URLS[1], "scheme": "turn", "ok": True, "elapsed_ms": 1}]
        asyncio.run(self.service.test({"mode": "cloudflare"}))
        self.db.refresh(config)
        self.assertEqual(config.status_detail, "manual status marker")

    def test_matching_failed_mint_records_degradation_and_check_time(self):
        config = self.cloudflare(fallback=True)
        self.mint.side_effect = TimeoutError("test timeout")
        asyncio.run(self.service.test({"mode": "cloudflare"}))
        self.db.refresh(config)
        self.assertEqual(config.status, "error")
        self.assertIsNotNone(config.last_checked_at)
        self.assertIn("STUN", config.status_detail)

    def test_configuration_change_during_probe_blocks_old_status_write(self):
        self.cloudflare(fallback=True)
        self.mint_ok()
        async def probing(*args, **kwargs):
            with Session(self.engine, expire_on_commit=False) as admin_db:
                admin = relay.VoiceRelayService(admin_db, SimpleNamespace())
                admin.save({"mode": "manual", "stun_urls": [relay.DEFAULT_STUN_URLS[0]]}, actor_id="admin")
            return [{"url": relay.DEFAULT_URLS[1], "scheme": "turn", "ok": False, "elapsed_ms": 1}]
        self.probe.side_effect = probing
        asyncio.run(self.service.test({"mode": "cloudflare"}))
        config = self.service.get_config()
        self.assertEqual(config.mode, "manual")
        self.assertEqual(config.status, "configured")
        self.assertIsNone(config.status_detail)

    def test_migration_is_additive_idempotent_and_default_off(self):
        old = create_engine("sqlite:///:memory:")
        try:
            with old.begin() as conn:
                conn.exec_driver_sql("CREATE TABLE voice_relay_configs (id TEXT PRIMARY KEY, mode TEXT)")
                conn.exec_driver_sql("INSERT INTO voice_relay_configs VALUES ('old', 'cloudflare')")
                _voice_relay_stun_pool(conn)
                _voice_relay_stun_pool(conn)
                row = conn.exec_driver_sql("SELECT mode, stun_urls, stun_fallback_enabled FROM voice_relay_configs").one()
                self.assertEqual(tuple(row), ("cloudflare", "[]", 0))
                self.assertEqual(len(inspect(conn).get_columns("voice_relay_configs")), 4)
        finally:
            old.dispose()


if __name__ == "__main__":
    unittest.main(verbosity=2)
