"""Connect-time DNS validation and IP pinning for MCP HTTP egress.

Validation before httpx alone has a check/use DNS-rebinding race. Resolve in
httpcore's network backend and connect to that numeric address, leaving the
HTTP origin and TLS SNI unchanged. Private access is limited to explicit local
endpoints and configured Host Bridge/gateway hosts.
"""
from __future__ import annotations
import ipaddress
import socket
import time
import httpcore
import httpx


class PinnedMCPBackend(httpcore.SyncBackend):
    def __init__(self, endpoint_host: str, allow_private_hosts=frozenset()):
        self.host=endpoint_host.casefold().rstrip('.')
        self.allow_private_hosts=frozenset(h.casefold().rstrip('.') for h in allow_private_hosts)

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        hostname=host.decode('ascii') if isinstance(host,bytes) else host
        if hostname.casefold().rstrip('.') != self.host:
            raise httpcore.ConnectError("MCP transport attempted a different origin")
        try:
            addresses=list(dict.fromkeys(record[4][0] for record in socket.getaddrinfo(hostname,port,type=socket.SOCK_STREAM)))
        except OSError as exc:
            raise httpcore.ConnectError("MCP endpoint DNS resolution failed") from exc
        explicit_loopback=self.host=='localhost'
        try: explicit_loopback=explicit_loopback or ipaddress.ip_address(self.host).is_loopback
        except ValueError: pass
        private_allowed=self.host in self.allow_private_hosts
        if not addresses or any(not ipaddress.ip_address(address).is_global and not private_allowed and
                                not (explicit_loopback and ipaddress.ip_address(address).is_loopback) for address in addresses):
            raise httpcore.ConnectError("MCP endpoint resolved to an unauthorized private address")
        deadline=time.monotonic()+timeout if timeout is not None else None
        failure=None
        for address in addresses:
            remaining=max(0,deadline-time.monotonic()) if deadline is not None else None
            if remaining==0: raise httpcore.ConnectTimeout("MCP connect deadline exceeded")
            try:
                return super().connect_tcp(address,port,remaining,local_address,socket_options)
            except (httpcore.ConnectError,httpcore.ConnectTimeout) as exc:
                failure=exc
        raise failure or httpcore.ConnectError("MCP endpoint connection failed")


class PinnedMCPTransport(httpx.HTTPTransport):
    def __init__(self,endpoint_host,allow_private_hosts=frozenset()):
        super().__init__(trust_env=False,retries=0)
        # HTTPTransport owns its httpcore pool; keep its supported exception
        # mapping and TLS configuration, replacing only the dial operation.
        self._pool._network_backend=PinnedMCPBackend(endpoint_host,allow_private_hosts)
