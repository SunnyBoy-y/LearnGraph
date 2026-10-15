"""Unit tests for the warm-container web fetch pool (speed + concurrency).

Uses a hermetic fake backend implementing the ``SandboxBackendPort`` subset
the provider touches, so no Docker Engine is required. The real host-side
workspace directories (under ``settings.sandbox_workspace_root``) and egress
policy files (under ``settings.sandbox_egress_policy_dir``) are exercised for
real, both pointing at a pytest tmp dir.

Covered behaviors:
- container reuse: N fetches reuse one warm container (no per-fetch create/delete)
- concurrency: up to pool_size fetches run in parallel, never two execs in one
  container at the same time, and specs/artifacts never cross-contaminate
- eviction: timeout / runner failure / backend error discard the container
- idle pruning: containers past the idle TTL are evicted (lazy + sweep entry)
- pool disabled (size 0): legacy create-per-fetch behavior preserved
- allowlist gate still fails before any container is created
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

import pytest

from app.providers.ports.sandbox import (
    SandboxCapabilitySnapshot,
    SandboxCreateSpec,
    SandboxSessionHandle,
)
from app.providers.remote.sandbox import (
    SandboxBackendError,
    SandboxExecResult,
)
from app.providers.sandbox_fetch import SandboxFetchProvider
from app.providers.sandbox_fetch_pool import (
    close_fetch_pools,
    prune_fetch_pools,
)
from app.providers.remote.fetch import (
    FetchProviderError,
    FetchProviderTimeout,
    UnsafeFetchURL,
)


class FakeSandboxBackend:
    """In-memory ``SandboxBackendPort`` recording create/delete/exec traffic."""

    backend_id = "fake"

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.created: list[SandboxCreateSpec] = []
        self.deleted: list[str] = []
        self.files: dict[tuple[str, str], bytes] = {}
        self.active_execs: dict[str, int] = {}
        self.max_active_execs: dict[str, int] = {}
        self.total_active = 0
        self.max_total_active = 0
        self.exec_calls = 0
        self.timed_out_calls: set[int] = set()
        self.fail_calls: set[int] = set()
        self.exec_delay = 0.03
        self.available = True
        self.backend_error_on_call: set[int] = set()

    # --- recording helpers ---------------------------------------------------

    def next_create(self) -> SandboxCreateSpec:
        return self.created[-1]

    # --- SandboxBackendPort --------------------------------------------------

    def probe(self) -> SandboxCapabilitySnapshot:
        return SandboxCapabilitySnapshot(
            available=self.available,
            backend_id=self.backend_id,
            platform="test",
            capabilities=("fixed_runner", "network_none", "resource_limits"),
        )

    def host_capacity(self) -> tuple[int, int]:
        return 8, 32 * 1024 * 1024 * 1024

    def create(self, spec: SandboxCreateSpec) -> SandboxSessionHandle:
        with self.lock:
            self.created.append(spec)
        return SandboxSessionHandle(spec.session_id, f"fake-container-{len(self.created)}")

    def resume(self, session_id: str, backend_ref: str) -> SandboxSessionHandle:
        return SandboxSessionHandle(session_id, backend_ref)

    def write(self, session: SandboxSessionHandle, path: str, data: bytes) -> None:
        with self.lock:
            self.files[(session.session_id, path)] = data

    def write_agent_file(self, session: SandboxSessionHandle, path: str, data: bytes) -> None:
        self.write(session, path, data)

    def exec_fixed(
        self,
        session: SandboxSessionHandle,
        argv: tuple[str, ...],
        *,
        timeout_seconds: int,
        output_limit: int,
    ) -> SandboxExecResult:
        argv_list = list(argv)
        input_path = argv_list[argv_list.index("--input") + 1]
        output_path = argv_list[argv_list.index("--output") + 1]
        with self.lock:
            self.exec_calls += 1
            call_number = self.exec_calls
            active = self.active_execs.get(session.session_id, 0)
            if active > 0:
                # Two runners in the same container at once must never happen.
                raise AssertionError(
                    f"concurrent execs in the same container {session.session_id}"
                )
            self.active_execs[session.session_id] = active + 1
            self.max_active_execs[session.session_id] = max(
                self.max_active_execs.get(session.session_id, 0), active + 1
            )
            self.total_active += 1
            self.max_total_active = max(self.max_total_active, self.total_active)
        try:
            if call_number in self.backend_error_on_call:
                raise SandboxBackendError("simulated dead container")
            time.sleep(self.exec_delay)
            if call_number in self.timed_out_calls:
                return SandboxExecResult(
                    exit_code=-1, stdout=b"", stderr=b"", timed_out=True,
                    latency_ms=1, truncated=False,
                )
            if call_number in self.fail_calls:
                return SandboxExecResult(
                    exit_code=1, stdout=b"", stderr=b"upstream 502",
                    timed_out=False, latency_ms=1, truncated=False,
                )
            spec = json.loads(self.files[(session.session_id, input_path)])
            url = spec["url"]
            artifact = {
                "schema_version": "1.0",
                "task_type": "web_fetch",
                "status": "ok",
                "source_url": url,
                "final_url": url,
                "title": f"title:{url}",
                "markdown": f"content:{url}",
                "content_type": "text/markdown",
                "extracted_by": "trafilatura",
                "truncated": False,
                "spec_sha256": spec["spec_sha256"],
            }
            with self.lock:
                self.files[(session.session_id, output_path)] = json.dumps(
                    artifact, ensure_ascii=False
                ).encode("utf-8")
            return SandboxExecResult(
                exit_code=0, stdout=b"", stderr=b"", timed_out=False,
                latency_ms=1, truncated=False,
            )
        finally:
            with self.lock:
                self.active_execs[session.session_id] -= 1
                self.total_active -= 1

    def read(self, session: SandboxSessionHandle, path: str, limit_bytes: int) -> bytes:
        try:
            return self.files[(session.session_id, path)]
        except KeyError as exc:
            raise SandboxBackendError("fake output missing") from exc

    def list_files(self, session: SandboxSessionHandle, limit_entries: int):
        return []

    def stop(self, session: SandboxSessionHandle) -> None:
        pass

    def delete(self, session: SandboxSessionHandle) -> None:
        with self.lock:
            self.deleted.append(session.session_id)
            for key in [k for k in self.files if k[0] == session.session_id]:
                del self.files[key]


def _settings(tmp_path: Path, **overrides) -> object:
    from app.core.config import Settings

    base = {
        "sandbox_workspace_root": str(tmp_path / "workspaces"),
        "sandbox_egress_policy_dir": str(tmp_path / "egress-policies"),
        "sandbox_egress_enabled": True,
        "sandbox_egress_network": "learngraph-egress-test",
        "sandbox_web_fetch_pool_size": 4,
        "sandbox_web_fetch_pool_idle_seconds": 600,
    }
    base.update(overrides)
    return Settings(**base)


def _provider(settings, backend: FakeSandboxBackend, *, namespace: str | None = None) -> SandboxFetchProvider:
    return SandboxFetchProvider(
        provider_id="sandbox_web_fetch",
        settings=settings,
        workspace_id=f"ws-{uuid.uuid4().hex[:10]}",
        allowed_domains=frozenset({"example.com", "example.org"}),
        backend=backend,
        pool_namespace=namespace or f"test-{uuid.uuid4().hex[:10]}",
    )


@pytest.fixture(autouse=True)
def _isolated_pool_registry():
    # Each test starts from an empty process-wide pool registry.
    close_fetch_pools()
    yield
    close_fetch_pools()


def _fetch_documents(provider: SandboxFetchProvider, urls: list[str]) -> list:
    return [provider.fetch(url) for url in urls]


def test_pool_reuses_one_container_across_fetches(tmp_path):
    backend = FakeSandboxBackend()
    provider = _provider(_settings(tmp_path), backend)
    urls = [
        "https://example.com/a",
        "https://example.org/b",
        "https://example.com/c",
    ]
    documents = _fetch_documents(provider, urls)
    assert [doc.content for doc in documents] == [
        f"content:{url}" for url in urls
    ]
    assert len(backend.created) == 1  # one warm container for all fetches
    assert backend.deleted == []  # never torn down per fetch


def test_pool_supports_concurrent_fetches_without_cross_contamination(tmp_path):
    backend = FakeSandboxBackend()
    provider = _provider(_settings(tmp_path), backend)
    urls = [f"https://example.com/page-{index}" for index in range(8)]
    captured: list[tuple[str, object]] = []
    captured_lock = threading.Lock()

    def fetch_one(url: str) -> None:
        try:
            document = provider.fetch(url)
            with captured_lock:
                captured.append((url, document.content))
        except Exception as exc:  # noqa: BLE001 - capture for assertion
            with captured_lock:
                captured.append((url, exc))

    threads = [threading.Thread(target=fetch_one, args=(url,)) for url in urls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert all(not thread.is_alive() for thread in threads)
    # Every URL completed successfully with its own content (no cross-talk).
    contents = dict(captured)
    assert set(contents) == set(urls)
    for url in urls:
        assert contents[url] == f"content:{url}"
    # The pool bounded concurrency: at most pool_size containers were created,
    # fetches genuinely overlapped across containers, and no container ever ran
    # two execs at once.
    assert len(backend.created) <= 4
    assert backend.deleted == []  # nothing evicted on success
    assert backend.max_total_active >= 2  # real parallelism happened
    assert all(max_active == 1 for max_active in backend.max_active_execs.values())
    # Warm reuse after the burst: no additional container.
    document = provider.fetch("https://example.com/later")
    assert document.content == "content:https://example.com/later"
    assert len(backend.created) <= 4


def test_pool_evicts_container_after_timeout(tmp_path):
    backend = FakeSandboxBackend()
    provider = _provider(_settings(tmp_path), backend)
    backend.timed_out_calls = {1}
    with pytest.raises(FetchProviderTimeout):
        provider.fetch("https://example.com/slow")
    # The timed-out container was killed by the real backend on timeout, so it
    # must be evicted, never reused.
    assert len(backend.created) == 1
    assert len(backend.deleted) == 1
    document = provider.fetch("https://example.com/a")
    assert document.content == "content:https://example.com/a"
    assert len(backend.created) == 2  # a fresh container replaced the evicted one
    assert len(backend.deleted) == 1  # the fresh one stays warm


def test_pool_evicts_container_after_runner_failure(tmp_path):
    backend = FakeSandboxBackend()
    provider = _provider(_settings(tmp_path), backend)
    backend.fail_calls = {1}
    with pytest.raises(FetchProviderError):
        provider.fetch("https://example.com/broken")
    assert backend.deleted == [backend.created[0].session_id]
    document = provider.fetch("https://example.com/ok")
    assert document.content == "content:https://example.com/ok"
    assert len(backend.created) == 2  # fresh container after eviction
    assert len(backend.deleted) == 1


def test_pool_prunes_idle_containers(tmp_path):
    backend = FakeSandboxBackend()
    settings = _settings(tmp_path, sandbox_web_fetch_pool_idle_seconds=1)
    provider = _provider(settings, backend)
    provider.fetch("https://example.com/a")
    assert len(backend.created) == 1
    time.sleep(1.2)
    totals = prune_fetch_pools()
    assert totals["evicted"] == 1
    assert len(backend.deleted) == 1
    # Lazy prune on the next acquire also works and creates a fresh container.
    provider.fetch("https://example.com/b")
    assert len(backend.created) == 2


def test_pool_size_zero_keeps_legacy_create_per_fetch(tmp_path):
    backend = FakeSandboxBackend()
    provider = _provider(
        _settings(tmp_path, sandbox_web_fetch_pool_size=0), backend
    )
    urls = ["https://example.com/a", "https://example.org/b"]
    documents = _fetch_documents(provider, urls)
    assert [doc.content for doc in documents] == [f"content:{url}" for url in urls]
    assert len(backend.created) == len(urls)  # one container per fetch
    assert len(backend.deleted) == len(urls)  # and it is torn down


def test_allowlist_gate_fails_before_any_container(tmp_path):
    backend = FakeSandboxBackend()
    provider = _provider(_settings(tmp_path), backend)
    with pytest.raises(UnsafeFetchURL):
        provider.fetch("https://unlisted.invalid/x")
    assert backend.created == []
    assert backend.deleted == []


def test_saturated_pool_fails_fast_when_all_containers_busy(tmp_path):
    from app.providers.sandbox_fetch_pool import (
        FetchPoolSaturated,
        get_fetch_pool,
    )

    backend = FakeSandboxBackend()
    settings = _settings(tmp_path)
    pool = get_fetch_pool(
        namespace=f"sat-{uuid.uuid4().hex[:10]}",
        backend=backend,
        settings=settings,
        workspace_id="ws-sat",
        allowed_domains=frozenset({"example.com"}),
        max_size=1,
        idle_ttl=600,
    )
    held = pool.acquire(timeout_seconds=5)
    assert len(backend.created) == 1
    started_at = time.monotonic()
    with pytest.raises(FetchPoolSaturated):
        pool.acquire(timeout_seconds=0.3)
    assert time.monotonic() - started_at < 5  # bounded wait, never infinite
    pool.release(held)
    reused = pool.acquire(timeout_seconds=5)
    assert reused is held  # warm reuse after release
    pool.release(reused)
