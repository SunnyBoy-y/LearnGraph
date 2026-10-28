import asyncio
from contextlib import nullcontext
from unittest.mock import Mock

import pytest

from app.services import model_pricing_sync as sync


@pytest.mark.parametrize("stale, lease, expected", [(False, "owner", 0), (True, None, 0), (True, "owner", 1)])
def test_daily_sync_checks_freshness_and_acquires_one_worker_lease(monkeypatch, stale, lease, expected):
    monkeypatch.setattr(sync, "snapshot_status", lambda: {"stale": stale})
    monkeypatch.setattr(sync, "SessionLocal", lambda: nullcontext("db"))
    acquire = Mock(return_value=lease)
    refresh = Mock(return_value={"model_count": 10})
    release = Mock()
    monkeypatch.setattr(sync, "acquire_advisory_lock", acquire)
    monkeypatch.setattr(sync, "release_advisory_lock", release)
    monkeypatch.setattr(sync, "refresh_snapshot", refresh)
    sync.refresh_if_due()
    assert refresh.call_count == expected
    assert acquire.call_count == int(stale)
    assert release.call_count == expected


def test_failed_refresh_releases_lease(monkeypatch):
    monkeypatch.setattr(sync, "snapshot_status", lambda: {"stale": True})
    monkeypatch.setattr(sync, "SessionLocal", lambda: nullcontext("db"))
    monkeypatch.setattr(sync, "acquire_advisory_lock", lambda *args, **kwargs: "owner")
    monkeypatch.setattr(sync, "refresh_snapshot", Mock(side_effect=RuntimeError("offline")))
    release = Mock()
    monkeypatch.setattr(sync, "release_advisory_lock", release)
    with pytest.raises(RuntimeError, match="offline"):
        sync.refresh_if_due()
    release.assert_called_once_with("db", sync.LOCK_NAME, "owner")


def test_worker_retries_network_failures_and_stops_promptly(monkeypatch):
    async def run():
        stop = asyncio.Event()
        delays = []

        async def wait(awaitable, *, timeout):
            delays.append(timeout)
            awaitable.close()
            stop.set()
            raise asyncio.TimeoutError

        monkeypatch.setattr(sync, "refresh_if_due", Mock(side_effect=RuntimeError("offline")))
        monkeypatch.setattr(sync.asyncio, "wait_for", wait)
        await sync.model_pricing_sync_worker(stop)
        assert delays == [sync.RETRY_SECONDS]

    asyncio.run(run())
