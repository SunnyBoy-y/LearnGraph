"""Fixtures for unit tests that simulate Docker host access without Docker DNS."""

import socket

import pytest


@pytest.fixture
def docker_host_dns(monkeypatch):
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host == "host.docker.internal":
            return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.65.2", port or 0))]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
