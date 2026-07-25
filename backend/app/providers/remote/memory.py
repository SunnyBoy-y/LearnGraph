from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any
from urllib.parse import quote

import httpx

from app.core.errors import AppError
from app.providers.ports.memory import (
    CanonicalMemory,
    ProviderBindingResult,
    ProviderHealth,
)


def mem0_entity_id(*, tenant_id: str, user_id: str, workspace_id: str, secret: str) -> str:
    material = f"{tenant_id}\0{user_id}\0{workspace_id}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
    return f"lgws_{digest}"


class Mem0PlatformAdapter:
    """Hosted Mem0 Platform REST adapter; OSS paths intentionally differ."""

    available = True
    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str,
        workspace_entity: str,
        timeout_seconds: float = 20.0,
        event_poll_seconds: float = 0.1,
        event_max_polls: int = 50,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider_id = provider_id
        normalized = base_url.rstrip("/")
        for suffix in ("/v1", "/v3"):
            if normalized.endswith(suffix):
                normalized = normalized[: -len(suffix)]
        self.base_url = normalized
        self.api_key = api_key
        self.workspace_entity = workspace_entity
        self.timeout_seconds = timeout_seconds
        self.event_poll_seconds = event_poll_seconds
        self.event_max_polls = event_max_polls
        self.transport = transport

    def _client(self) -> httpx.Client:
        return httpx.Client(
            headers={
                "Authorization": f"Token {self.api_key}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=self.timeout_seconds,
            transport=self.transport,
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.request(
                    method,
                    f"{self.base_url}/{path.lstrip('/')}",
                    json=json_body,
                )
        except httpx.TimeoutException as exc:
            raise AppError(504, "memory_provider_timeout", "Mem0 Platform request timed out") from exc
        except httpx.HTTPError as exc:
            raise AppError(502, "memory_provider_http_error", str(exc)) from exc
        if response.status_code not in expected:
            raise AppError(
                502,
                "memory_provider_http_error",
                "Mem0 Platform returned an unsuccessful response",
                {
                    "provider_id": self.provider_id,
                    "status_code": response.status_code,
                    "body": response.text[:500],
                },
            )
        if response.status_code == 204 or not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise AppError(
                502,
                "memory_provider_invalid_response",
                "Mem0 Platform returned non-JSON data",
            ) from exc
        if not isinstance(payload, dict):
            raise AppError(
                502,
                "memory_provider_invalid_response",
                "Mem0 Platform response must be an object",
            )
        return payload

    def _entity(self, memory: CanonicalMemory) -> tuple[str, str]:
        if memory.namespace == "session":
            if not memory.session_id:
                raise AppError(
                    422,
                    "memory_session_required",
                    "Session memories require a session ID",
                )
            digest = hmac.new(
                self.workspace_entity.encode("utf-8"),
                memory.session_id.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()
            return "run_id", f"lgrun_{digest}"
        return "user_id", self.workspace_entity

    @staticmethod
    def _text(memory: CanonicalMemory) -> str:
        return f"# {memory.title}\n\n{memory.content.rstrip()}\n"

    @staticmethod
    def _metadata(memory: CanonicalMemory) -> dict[str, str]:
        return {
            "lg_memory_id": memory.memory_id,
            "lg_revision": str(memory.revision),
            "lg_content_sha256": memory.content_hash,
            "lg_record_kind": memory.record_kind,
            "lg_zone": memory.zone,
            "lg_state": memory.state,
            "lg_namespace": memory.namespace,
            "lg_origin_created_at": memory.origin_created_at.isoformat(),
            "lg_origin_updated_at": memory.origin_updated_at.isoformat(),
            "lg_policy_version": memory.policy_version,
            "lg_policy_sha256": memory.policy_sha256,
        }

    def health(self) -> ProviderHealth:
        page = self._request(
            "POST",
            "/v3/memories/?page=1&page_size=1",
            json_body={"filters": {"user_id": self.workspace_entity}},
        )
        if not isinstance(page.get("results"), list) or not isinstance(page.get("count"), int):
            raise AppError(
                502,
                "memory_provider_invalid_response",
                "Mem0 Platform health probe returned no paginated results envelope",
            )
        return ProviderHealth(
            provider_id=self.provider_id,
            available=True,
            status="healthy",
            remote_capability=True,
            details={"visible_record_count": page["count"], "api_family": "platform_v3"},
        )

    def _wait_for_event(self, event_id: str) -> None:
        for attempt in range(self.event_max_polls):
            payload = self._request("GET", f"/v1/event/{quote(event_id, safe='')}/")
            status = str(payload.get("status") or "").upper()
            if status == "SUCCEEDED":
                return
            if status == "FAILED":
                raise AppError(
                    502,
                    "memory_provider_event_failed",
                    "Mem0 Platform rejected the memory mutation",
                    {"provider_id": self.provider_id, "event_id": event_id},
                )
            if attempt + 1 < self.event_max_polls:
                time.sleep(self.event_poll_seconds)
        raise AppError(
            504,
            "memory_provider_event_timeout",
            "Mem0 Platform mutation did not reach a terminal state",
            {"provider_id": self.provider_id, "event_id": event_id},
        )

    def _readback(self, memory: CanonicalMemory, entity_kind: str, entity_value: str) -> dict[str, Any]:
        filters = {
            "AND": [
                {entity_kind: entity_value},
                {"lg_memory_id": memory.memory_id},
                {"lg_revision": str(memory.revision)},
            ]
        }
        page = self._request(
            "POST",
            "/v3/memories/?page=1&page_size=200",
            json_body={"filters": filters},
        )
        results = page.get("results")
        if not isinstance(results, list):
            raise AppError(
                502,
                "memory_provider_invalid_response",
                "Mem0 Platform readback returned no results array",
            )
        matches = [
            item
            for item in results
            if isinstance(item, dict)
            and isinstance(item.get("metadata"), dict)
            and item["metadata"].get("lg_memory_id") == memory.memory_id
            and str(item["metadata"].get("lg_revision")) == str(memory.revision)
        ]
        if len(matches) != 1:
            raise AppError(
                409,
                "memory_provider_binding_ambiguous",
                "Mem0 readback must contain exactly one LG memory revision",
                {"memory_id": memory.memory_id, "revision": memory.revision, "matches": len(matches)},
            )
        item = matches[0]
        metadata = item["metadata"]
        if metadata.get("lg_content_sha256") != memory.content_hash:
            raise AppError(
                409,
                "memory_provider_hash_mismatch",
                "Mem0 readback hash does not match the LearnGraph canonical hash",
                {"memory_id": memory.memory_id, "revision": memory.revision},
            )
        if not isinstance(item.get("id"), str) or not item["id"]:
            raise AppError(
                502,
                "memory_provider_invalid_response",
                "Mem0 readback record has no ID",
            )
        return item

    def upsert(
        self,
        memory: CanonicalMemory,
        *,
        provider_record_id: str | None = None,
    ) -> ProviderBindingResult:
        entity_kind, entity_value = self._entity(memory)
        metadata = self._metadata(memory)
        import_event_id: str | None = None
        if provider_record_id:
            self._request(
                "PUT",
                f"/v1/memories/{quote(provider_record_id, safe='')}/",
                json_body={"text": self._text(memory), "metadata": metadata},
            )
        else:
            response = self._request(
                "POST",
                "/v3/memories/add/",
                json_body={
                    "messages": [{"role": "user", "content": self._text(memory)}],
                    entity_kind: entity_value,
                    "metadata": metadata,
                    "infer": False,
                },
            )
            event_id = response.get("event_id")
            if not isinstance(event_id, str) or not event_id:
                raise AppError(
                    502,
                    "memory_provider_invalid_response",
                    "Mem0 Platform add response has no event_id",
                )
            import_event_id = event_id
            self._wait_for_event(event_id)
        readback = self._readback(memory, entity_kind, entity_value)
        return ProviderBindingResult(
            provider_record_id=str(readback["id"]),
            provider_entity_kind=entity_kind,
            provider_entity_value=entity_value,
            target_readback_hash=memory.content_hash,
            import_event_id=import_event_id,
        )

    def delete(self, provider_record_id: str) -> None:
        self._request(
            "DELETE",
            f"/v1/memories/{quote(provider_record_id, safe='')}/",
            expected=(200, 204),
        )


class UnavailableMemoryProvider:
    available = False
    remote_capability = True

    def __init__(self, provider_id: str, reason: str) -> None:
        self.provider_id = provider_id
        self.reason = reason

    def _raise(self) -> None:
        raise AppError(
            503,
            "memory_provider_unavailable",
            self.reason,
            {"provider_id": self.provider_id},
        )

    def health(self) -> ProviderHealth:
        return ProviderHealth(
            provider_id=self.provider_id,
            available=False,
            status="unavailable",
            remote_capability=True,
            details={"reason": self.reason},
        )

    def upsert(
        self,
        memory: CanonicalMemory,
        *,
        provider_record_id: str | None = None,
    ) -> ProviderBindingResult:
        self._raise()
        raise AssertionError("unreachable")

    def delete(self, provider_record_id: str) -> None:
        self._raise()

