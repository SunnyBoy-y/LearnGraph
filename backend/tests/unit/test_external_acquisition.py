from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.errors import AppError
from app.domain import models as domain_models
from app.services import external_acquisition as acquisition_module
from app.services.egress_approvals import EgressApprovalService
from app.services.external_acquisition import (
    AcquisitionApprovalRequired,
    DownloadedResponse,
    ExternalAcquisitionService,
)


def _settings(**overrides):
    values = {
        "external_download_timeout_seconds": 2.0,
        "external_download_max_redirects": 2,
        "external_image_download_max_bytes": 1024 * 1024,
        "external_image_download_max_pixels": 1_000_000,
        "external_file_download_max_bytes": 1024 * 1024,
        "external_github_metadata_max_bytes": 1024 * 1024,
        "external_github_file_max_bytes": 1024 * 1024,
        "external_github_total_max_bytes": 4 * 1024 * 1024,
        "external_github_max_files": 20,
        "workspace_storage_quota_bytes": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _service(**settings_overrides):
    service = object.__new__(ExternalAcquisitionService)
    service.settings = _settings(**settings_overrides)
    service.workspace_id = "ws-test"
    service.actor_id = "user-test"
    return service


def _response(url: str, data: bytes, mime: str = "application/octet-stream"):
    return DownloadedResponse(
        requested_url=url,
        final_url=url,
        redirect_chain=[],
        resolved_addresses={"api.github.com": ["93.184.216.34"]},
        status=200,
        declared_mime=mime,
        data=data,
    )


class _FakePinnedConnection:
    def __init__(
        self,
        host: str,
        ip: str,
        *,
        port: int,
        timeout: float,
        body: bytes = b"ok",
        status: int = 200,
        will_close: bool = False,
        stale: bool = False,
    ):
        self.host = host
        self.ip = ip
        self.port = port
        self.timeout = timeout
        self.closed = False
        self.requests = 0
        self._body = body
        self._status = status
        self._will_close = will_close
        self._stale = stale

    def request(self, method, path, headers):
        self.requests += 1
        self.last_request = (method, path, headers)
        if self._stale and self.requests == 2:
            # The server closed this keep-alive connection while it was idle.
            raise ConnectionResetError("stale pooled connection")

    def getresponse(self):
        return _FakeResponse(
            self._status,
            {
                "Content-Type": "application/octet-stream",
                "Content-Length": str(len(self._body)),
            },
            self._body,
            self._will_close,
        )

    def close(self):
        self.closed = True


class _FakeResponse:
    def __init__(self, status: int, headers: dict, body: bytes, will_close: bool = False):
        self.status = status
        self.headers = headers
        self.body = body
        self.will_close = will_close

    def getheaders(self):
        return list(self.headers.items())

    def read(self, n: int = -1):
        if n is None or n < 0:
            return self.body
        return self.body[:n]


def test_download_images_parallel_batch_isolates_failures_and_commits_once(monkeypatch):
    service = _service(external_image_download_max_parallel=2)
    writes: list[dict] = []
    commits = {"count": 0}
    service.workspace = SimpleNamespace(
        put_bytes=lambda **kwargs: writes.append(kwargs) or {
            "blob_sha256": "b" * 64,
            "file_id": f"file-{len(writes)}",
            "size_bytes": kwargs["data"] and len(kwargs["data"]),
            "mime_type": kwargs.get("mime_type", "image/jpeg"),
        },
        get_entry=lambda _chat, _path: (_ for _ in ()).throw(
            AppError(404, "session_workspace_entry_not_found", "nope")
        ),
    )
    service._check_workspace_quota = lambda _incoming: None
    service.db = SimpleNamespace(commit=lambda: commits.__setitem__("count", commits["count"] + 1))
    service.audit = SimpleNamespace(record=lambda **kwargs: None)
    service._receipt = lambda **values: SimpleNamespace(
        id=f"receipt-{len(writes)}",
        kind="image",
        request_spec_sha256="f" * 64,
        stored_bytes=1,
        destination_path="inputs/images/x.jpg",
    )

    def prepare(url, allowed_hosts):
        if "broken" in url:
            raise AppError(422, "external_image_invalid", "not an image")
        return {
            "response": _response(url, b"\x89PNG\r\n\x1a\n"),
            "safe": b"safe-pixels",
            "mime": "image/png",
            "width": 1,
            "height": 1,
            "format": "PNG",
            "wire_sha": "a" * 64,
        }

    service._prepare_image = prepare
    result = service.download_images(
        chat_session_id="chat",
        urls=["https://cdn.example.com/a.png", "https://cdn.example.com/broken.png"],
        destination_dir="inputs/images",
        allowed_hosts={"cdn.example.com"},
        request_spec_sha256="f" * 64,
    )
    assert len(result["downloaded"]) == 1
    assert len(result["failed"]) == 1
    assert result["failed"][0]["error_code"] == "external_image_invalid"
    assert len(writes) == 1  # only the healthy image is persisted
    assert writes[0]["commit"] is False  # caller commits once
    assert commits["count"] == 1


def test_download_images_requires_approval_before_any_network():
    service = _service()
    called = {"count": 0}
    service._prepare_image = lambda *args, **kwargs: called.__setitem__(
        "count", called["count"] + 1
    )
    with pytest.raises(AcquisitionApprovalRequired) as exc:
        service.download_images(
            chat_session_id="chat",
            urls=["https://one.example.com/a.png", "https://two.example.com/b.png"],
            destination_dir="inputs/images",
            allowed_hosts={"one.example.com"},
            request_spec_sha256="f" * 64,
        )
    assert exc.value.hostname == "two.example.com"
    assert called["count"] == 0


def test_initial_hostname_accepts_only_credential_free_https_domains():
    assert ExternalAcquisitionService.initial_hostname("https://CDN.Example.com/a.png") == "cdn.example.com"
    for url in (
        "http://cdn.example.com/a.png",
        "https://127.0.0.1/a.png",
        "https://user:secret@cdn.example.com/a.png",
        "https://cdn.example.com:8443/a.png",
    ):
        with pytest.raises(AppError):
            ExternalAcquisitionService.initial_hostname(url)


def test_safe_download_filename_neutralizes_content_disposition_traversal():
    assert ExternalAcquisitionService._safe_download_filename(
        "https://cdn.example.com/fallback.txt",
        'attachment; filename="../../etc/passwd"',
        "text/plain",
    ) == "passwd"
    assert ExternalAcquisitionService._safe_download_filename(
        "https://cdn.example.com/fallback.txt",
        'attachment; filename="..\\..\\evil.txt"',
        "text/plain",
    ) == "evil.txt"


def test_download_file_writes_safe_path_and_reports_no_scanner():
    service = _service()
    writes: list[dict] = []
    service.workspace = SimpleNamespace(
        put_bytes=lambda **kwargs: writes.append(kwargs) or {
            "blob_sha256": "b" * 64,
            "file_id": "file-1",
            "size_bytes": len(kwargs["data"]),
            "mime_type": kwargs.get("mime_type", "application/octet-stream"),
        },
        get_entry=lambda _chat, _path: (_ for _ in ()).throw(
            AppError(404, "session_workspace_entry_not_found", "missing")
        ),
    )
    service._check_workspace_quota = lambda _incoming: None
    service._download = lambda *args, **kwargs: _response(
        "https://cdn.example.com/../../etc/passwd", b"file-bytes", "text/plain"
    )
    service.db = SimpleNamespace(commit=lambda: None)
    service.db.refresh = lambda _receipt: None
    service._receipt = lambda **values: SimpleNamespace(
        id="receipt-1",
        kind="file",
        request_spec_sha256="f" * 64,
        stored_bytes=10,
        destination_path=values.get("destination_path"),
    )

    result = service.download_file(
        chat_session_id="chat",
        url="https://cdn.example.com/../../etc/passwd",
        destination_path=None,
        allowed_hosts={"cdn.example.com"},
        request_spec_sha256="f" * 64,
    )
    assert writes[0]["path"] == "inputs/downloads/passwd"
    assert writes[0]["data"] == b"file-bytes"
    assert result["scanner"] == {"status": "not_configured", "scanner": None}


def test_download_file_fails_closed_when_scanner_does_not_report_clean():
    service = _service()
    service._download = lambda *args, **kwargs: _response(
        "https://cdn.example.com/a.bin", b"file-bytes", "application/octet-stream"
    )

    class _Scanner:
        name = "test-scanner"

        def scan(self, data, *, filename, mime_type):
            return {"status": "infected"}

    with pytest.raises(AppError) as exc:
        service.download_file(
            chat_session_id="chat",
            url="https://cdn.example.com/a.bin",
            destination_path=None,
            allowed_hosts={"cdn.example.com"},
            request_spec_sha256="f" * 64,
            scanner=_Scanner(),
        )
    assert exc.value.code == "external_download_scan_failed"


def test_download_file_rejects_expected_hash_mismatch():
    service = _service()
    service._download = lambda *args, **kwargs: _response(
        "https://cdn.example.com/a.bin", b"file-bytes"
    )
    with pytest.raises(AppError) as exc:
        service.download_file(
            chat_session_id="chat",
            url="https://cdn.example.com/a.bin",
            destination_path="inputs/a.bin",
            allowed_hosts={"cdn.example.com"},
            request_spec_sha256="f" * 64,
            expected_sha256="0" * 64,
        )
    assert exc.value.code == "external_download_hash_mismatch"


def test_image_sanitization_reencodes_and_removes_metadata():
    service = _service()
    source = BytesIO()
    image = Image.new("RGB", (12, 8), (20, 40, 60))
    image.save(source, format="JPEG", exif=b"Exif\x00\x00untrusted-metadata")

    safe, mime, width, height, image_format = service._sanitize_image(source.getvalue())

    assert (mime, width, height, image_format) == ("image/jpeg", 12, 8, "JPEG")
    with Image.open(BytesIO(safe)) as decoded:
        assert decoded.size == (12, 8)
        assert not decoded.getexif()


def test_image_sanitization_rejects_pixel_bomb_before_decode():
    service = _service(external_image_download_max_pixels=10)
    source = BytesIO()
    Image.new("RGB", (4, 4)).save(source, format="PNG")
    with pytest.raises(AppError) as exc:
        service._sanitize_image(source.getvalue())
    assert exc.value.code == "external_image_pixels_exceeded"


def test_download_requires_exact_host_approval_before_dns(monkeypatch):
    service = _service()
    called = False

    def resolver(_host: str):
        nonlocal called
        called = True
        return ["93.184.216.34"]

    monkeypatch.setattr(acquisition_module, "system_resolver", resolver)
    with pytest.raises(AcquisitionApprovalRequired) as exc:
        service._download("https://cdn.example.com/a.png", allowed_hosts=set(), max_bytes=100)
    assert exc.value.hostname == "cdn.example.com"
    assert called is False


def test_download_rejects_any_private_dns_answer(monkeypatch):
    service = _service()
    monkeypatch.setattr(
        acquisition_module,
        "system_resolver",
        lambda _host: ["93.184.216.34", "127.0.0.1"],
    )
    with pytest.raises(AppError) as exc:
        service._download(
            "https://cdn.example.com/a.png",
            allowed_hosts={"cdn.example.com"},
            max_bytes=100,
        )
    assert exc.value.code == "external_download_host_blocked"


def test_download_redirect_to_metadata_is_rejected(monkeypatch):
    service = _service()
    monkeypatch.setattr(
        acquisition_module, "system_resolver", lambda _host: ["93.184.216.34"]
    )
    service._request_hop = lambda **_kwargs: (
        302,
        {"location": "https://169.254.169.254/latest/meta-data/"},
        b"",
    )
    with pytest.raises(AppError) as exc:
        service._download(
            "https://cdn.example.com/start",
            allowed_hosts={"cdn.example.com"},
            max_bytes=100,
        )
    assert exc.value.code == "external_download_host_invalid"


def test_classified_host_addresses_caches_resolution_per_instance(monkeypatch):
    service = _service()
    calls = {"count": 0}

    def resolver(_host: str):
        calls["count"] += 1
        return ["93.184.216.34"]

    monkeypatch.setattr(acquisition_module, "system_resolver", resolver)
    first = service._classified_host_addresses("cdn.example.com")
    second = service._classified_host_addresses("cdn.example.com")
    assert first == ("93.184.216.34",)
    assert second == first
    assert calls["count"] == 1  # one resolution serves every file of the batch


def test_classified_host_addresses_never_caches_denied_answers(monkeypatch):
    service = _service()
    monkeypatch.setattr(
        acquisition_module,
        "system_resolver",
        lambda _host: ["93.184.216.34", "127.0.0.1"],
    )
    with pytest.raises(AppError) as exc:
        service._classified_host_addresses("cdn.example.com")
    assert exc.value.code == "external_download_host_blocked"
    # A denied answer must not be cached: a later healthy answer resolves.
    monkeypatch.setattr(
        acquisition_module,
        "system_resolver",
        lambda _host: ["93.184.216.34"],
    )
    assert service._classified_host_addresses("cdn.example.com") == ("93.184.216.34",)


def test_classified_host_cache_ttl_expiry(monkeypatch):
    fake_time = {"now": 1_000.0}
    monkeypatch.setattr(acquisition_module.time, "monotonic", lambda: fake_time["now"])
    cache = acquisition_module._ClassifiedHostCache(ttl_seconds=30.0, max_entries=8)
    cache.put("cdn.example.com", ("93.184.216.34",))
    assert cache.get("cdn.example.com") == ("93.184.216.34",)
    fake_time["now"] += 31.0
    assert cache.get("cdn.example.com") is None


def test_classified_host_cache_lru_eviction():
    cache = acquisition_module._ClassifiedHostCache(ttl_seconds=300.0, max_entries=2)
    cache.put("a.example.com", ("1.1.1.1",))
    cache.put("b.example.com", ("2.2.2.2",))
    assert cache.get("a.example.com") == ("1.1.1.1",)  # touch a: b becomes LRU
    cache.put("c.example.com", ("3.3.3.3",))
    assert cache.get("b.example.com") is None  # b evicted
    assert cache.get("a.example.com") == ("1.1.1.1",)
    assert cache.get("c.example.com") == ("3.3.3.3",)


def test_pinned_connection_pool_reuses_and_expires_idle(monkeypatch):
    fake_time = {"now": 1_000.0}
    monkeypatch.setattr(acquisition_module.time, "monotonic", lambda: fake_time["now"])
    pool = acquisition_module._PinnedConnectionPool(max_idle_connections=2, idle_timeout_seconds=60.0)
    a = _FakePinnedConnection("cdn.example.com", "93.184.216.34", port=443, timeout=2.0)
    pool.release(a, host="cdn.example.com", ip="93.184.216.34")
    assert pool.acquire("cdn.example.com", "93.184.216.34") is a
    pool.release(a, host="cdn.example.com", ip="93.184.216.34")
    fake_time["now"] += 61.0
    assert pool.acquire("cdn.example.com", "93.184.216.34") is None
    assert a.closed is True  # expired idle connection is closed, not reused


def test_pinned_connection_pool_caps_idle_and_close_all():
    pool = acquisition_module._PinnedConnectionPool(max_idle_connections=1, idle_timeout_seconds=60.0)
    a = _FakePinnedConnection("cdn.example.com", "93.184.216.34", port=443, timeout=2.0)
    b = _FakePinnedConnection("cdn.example.com", "93.184.216.34", port=443, timeout=2.0)
    pool.release(a, host="cdn.example.com", ip="93.184.216.34")
    pool.release(b, host="cdn.example.com", ip="93.184.216.34")  # cap: b is closed
    assert b.closed is True
    assert pool.acquire("cdn.example.com", "93.184.216.34") is a
    pool.release(a, host="cdn.example.com", ip="93.184.216.34")
    pool.close_all()
    assert a.closed is True
    assert pool.acquire("cdn.example.com", "93.184.216.34") is None

    disabled = acquisition_module._PinnedConnectionPool(max_idle_connections=0, idle_timeout_seconds=60.0)
    c = _FakePinnedConnection("cdn.example.com", "93.184.216.34", port=443, timeout=2.0)
    disabled.release(c, host="cdn.example.com", ip="93.184.216.34")
    assert c.closed is True


def _hop_kwargs():
    return dict(
        host="cdn.example.com",
        ip="93.184.216.34",
        path="/a.png",
        accept="*/*",
        max_bytes=100,
        timeout=2.0,
    )


def test_request_hop_reuses_pooled_connection(monkeypatch):
    service = _service()
    created: list[_FakePinnedConnection] = []

    def factory(host, ip, *, port, timeout):
        connection = _FakePinnedConnection(host, ip, port=port, timeout=timeout)
        created.append(connection)
        return connection

    monkeypatch.setattr(acquisition_module, "_PinnedHTTPSConnection", factory)
    first = service._request_hop(**_hop_kwargs())
    second = service._request_hop(**_hop_kwargs())
    assert first[0] == second[0] == 200
    assert len(created) == 1  # the second hop reused the pooled connection
    assert created[0].requests == 2


def test_request_hop_retries_stale_pooled_connection_once(monkeypatch):
    service = _service()
    created: list[_FakePinnedConnection] = []

    def factory(host, ip, *, port, timeout):
        connection = _FakePinnedConnection(
            host, ip, port=port, timeout=timeout, stale=len(created) == 0
        )
        created.append(connection)
        return connection

    monkeypatch.setattr(acquisition_module, "_PinnedHTTPSConnection", factory)
    first = service._request_hop(**_hop_kwargs())  # fresh connection, pooled after use
    second = service._request_hop(**_hop_kwargs())  # pooled conn stale -> retried fresh
    assert first[0] == second[0] == 200
    assert len(created) == 2  # stale pooled connection was dropped, not reused


def test_request_hop_never_reuses_will_close_or_over_limit_connections(monkeypatch):
    service = _service()
    created: list[_FakePinnedConnection] = []

    def factory(host, ip, *, port, timeout):
        connection = _FakePinnedConnection(host, ip, port=port, timeout=timeout, will_close=True)
        created.append(connection)
        return connection

    monkeypatch.setattr(acquisition_module, "_PinnedHTTPSConnection", factory)
    service._request_hop(**_hop_kwargs())
    service._request_hop(**_hop_kwargs())
    assert len(created) == 2  # will_close responses never return to the pool

    # Over-limit body: 413, connection closed and nothing pooled.
    over_limit = _FakePinnedConnection(
        "cdn.example.com", "93.184.216.34", port=443, timeout=2.0, body=b"x" * 200
    )
    monkeypatch.setattr(
        acquisition_module,
        "_PinnedHTTPSConnection",
        lambda host, ip, *, port, timeout: over_limit,
    )
    with pytest.raises(AppError) as exc:
        service._request_hop(**_hop_kwargs())
    assert exc.value.code == "external_download_too_large"
    assert over_limit.closed is True
    assert service._connection_pool.acquire("cdn.example.com", "93.184.216.34") is None




def test_destination_rejects_overwrite_with_different_content():
    service = _service()
    service.workspace = SimpleNamespace(
        get_entry=lambda _chat, _path: SimpleNamespace(blob_sha256="a" * 64)
    )
    with pytest.raises(AppError) as exc:
        service._ensure_destination_available("chat", "inputs/a.png", "b" * 64)
    assert exc.value.code == "external_download_destination_exists"


def test_claim_once_is_atomic_single_use_with_release_and_consume():
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    try:
        with SessionLocal() as db:
            request = domain_models.EgressAuthorizationRequest(
                workspace_id="ws-claim",
                hostname="cdn.example.com",
                capability=domain_models.EXTERNAL_ACQUISITION_CAPABILITY,
                requested_by="user-claim",
                status="approved",
                decision="allow_once",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                ttl_seconds=600,
                dedupe_key="dedupe-claim",
            )
            db.add(request)
            db.commit()
            db.refresh(request)
            service = EgressApprovalService(
                db,
                "ws-claim",
                None,
                capability=domain_models.EXTERNAL_ACQUISITION_CAPABILITY,
            )
            first = service.claim_once(request_id=request.id, actor_id="user-claim")
            assert first is not None and first.status == "claimed"
            # A second concurrent claim must lose.
            assert service.claim_once(request_id=request.id, actor_id="user-claim") is None
            # Failed executions release the lease for retry.
            service.release_once(request_id=request.id)
            again = service.claim_once(request_id=request.id, actor_id="user-claim")
            assert again is not None and again.status == "claimed"
            # Successful executions consume it.
            service.consume_once(request_id=request.id)
            assert service._load(request.id).status == "consumed"
    finally:
        engine.dispose()


def test_github_network_failure_leaves_no_partial_workspace(monkeypatch):
    service = _service()
    writes: list[str] = []
    service.workspace = SimpleNamespace(
        put_bytes=lambda **kwargs: writes.append(kwargs["path"])
    )
    service._check_workspace_quota = lambda _incoming: None
    commit = "a" * 40
    tree = {
        "truncated": False,
        "tree": [
            {"type": "blob", "mode": "100644", "path": "src/a.py", "size": 1},
            {"type": "blob", "mode": "100644", "path": "src/b.py", "size": 1},
        ],
    }
    metadata_calls = iter([
        ({"sha": commit}, _response("https://api.github.com/commit", b"{}")),
        (tree, _response("https://api.github.com/tree", b"{}")),
    ])
    service._download_json = lambda *args, **kwargs: next(metadata_calls)
    calls = 0

    def download(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AppError(502, "external_download_failed", "failed")
        return _response(args[0], b"a")

    service._download = download
    with pytest.raises(AppError):
        service.download_github_source(
            chat_session_id="chat",
            owner="owner",
            repo="repo",
            ref="main",
            path="src",
            destination_root="inputs/github/repo",
            allowed_hosts={"api.github.com", "raw.githubusercontent.com"},
            request_spec_sha256="f" * 64,
        )
    assert writes == []


def test_github_rejects_symlink_and_submodule_modes():
    for mode in ("120000", "160000"):
        service = _service()
        commit = "a" * 40
        metadata_calls = iter([
            ({"sha": commit}, _response("https://api.github.com/commit", b"{}")),
            (
                {
                    "truncated": False,
                    "tree": [{"type": "blob", "mode": mode, "path": "unsafe", "size": 10}],
                },
                _response("https://api.github.com/tree", b"{}"),
            ),
        ])
        service._download_json = lambda *args, **kwargs: next(metadata_calls)
        with pytest.raises(AppError) as exc:
            service.download_github_source(
                chat_session_id="chat",
                owner="owner",
                repo="repo",
                ref="main",
                path="",
                destination_root="inputs/github/repo",
                allowed_hosts={"api.github.com", "raw.githubusercontent.com"},
                request_spec_sha256="f" * 64,
            )
        assert exc.value.code == "github_entry_unsupported"
