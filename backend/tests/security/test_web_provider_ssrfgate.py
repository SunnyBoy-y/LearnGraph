from __future__ import annotations

import socket

import pytest

from app.providers.remote.fetch import UnsafeFetchURL, require_public_http_url, validate_bridge_url


def _resolved(ip: str):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000",
        "http://10.0.0.1",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "file:///etc/passwd",
        "http://user:secret@example.com",
    ],
)
def test_validate_bridge_url_rejects_unsafe_destinations(url: str) -> None:
    with pytest.raises(UnsafeFetchURL):
        validate_bridge_url(url)


def test_validate_bridge_url_rejects_private_dns_answer(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _resolved("10.0.0.2"))
    with pytest.raises(UnsafeFetchURL):
        validate_bridge_url("https://bridge.example.com")


def test_validate_bridge_url_rejects_mixed_dns_answers(monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: _resolved("93.184.216.34") + _resolved("127.0.0.1"),
    )
    with pytest.raises(UnsafeFetchURL):
        validate_bridge_url("https://bridge.example.com")


def test_validate_bridge_url_accepts_public_dns_answer(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _resolved("93.184.216.34"))
    assert validate_bridge_url("https://Bridge.Example.com/api/") == "https://Bridge.Example.com/api"


def test_validate_bridge_url_allow_private_accepts_private_dns_answer(monkeypatch) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: _resolved("10.0.0.2"))
    assert (
        validate_bridge_url("https://bridge.example.com/", allow_private=True)
        == "https://bridge.example.com"
    )


def test_validate_bridge_url_allow_private_accepts_loopback_literal() -> None:
    assert validate_bridge_url("http://127.0.0.1:8000", allow_private=True) == "http://127.0.0.1:8000"


def test_validate_bridge_url_allow_private_keeps_structural_checks() -> None:
    # The escape hatch only relaxes the private/reserved DNS check; the
    # scheme, userinfo, and port guards must still fire.
    for url in [
        "file:///etc/passwd",
        "http://user:secret@example.com",
        "http://example.com:0",
    ]:
        with pytest.raises(UnsafeFetchURL):
            validate_bridge_url(url, allow_private=True)


def test_source_fetch_rejects_userinfo_and_metadata() -> None:
    with pytest.raises(UnsafeFetchURL):
        require_public_http_url("https://user@example.com", {"example.com"})
    with pytest.raises(UnsafeFetchURL):
        require_public_http_url("http://169.254.169.254", {"169.254.169.254"})


def test_provider_constructors_validate_bridge_url_source() -> None:
    source = __import__("pathlib").Path(__file__).resolve().parents[2]
    fetch_source = (source / "app" / "providers" / "remote" / "fetch.py").read_text(encoding="utf-8")
    search_source = (source / "app" / "providers" / "remote" / "search.py").read_text(encoding="utf-8")
    assert fetch_source.count("validate_bridge_url(base_url") >= 2
    assert search_source.count("validate_bridge_url(base_url") >= 2
