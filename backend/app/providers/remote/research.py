from __future__ import annotations

from typing import Any

import httpx


class DeepResearchProviderError(RuntimeError):
    """A configured deep-research provider failed without a safe fallback."""


class DeepResearchProviderTimeout(DeepResearchProviderError):
    pass


class HTTPDeepResearchProvider:
    """Small adapter for a provider-neutral asynchronous research task API.

    The configured endpoint owns provider-specific task semantics.  LearnGraph
    only sends an approved, bounded plan and normalizes task state and evidence
    back into its own persisted ResearchJob.
    """

    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str,
        declared_capabilities: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.declared_capabilities = dict(declared_capabilities or {})
        self.transport = transport
        self.timeout_seconds = timeout_seconds

    def capabilities(self) -> dict[str, Any]:
        return {
            "background": True,
            "citations": True,
            "cancel": True,
            "structured_output": False,
            **self.declared_capabilities,
        }

    def estimate(self, *, question: str, budget_cny: float) -> float:
        del question
        configured = self.declared_capabilities.get("estimated_cost_cny")
        if isinstance(configured, (int, float)) and configured >= 0:
            return min(float(configured), budget_cny)
        return budget_cny

    def create_task(
        self,
        *,
        question: str,
        budget_cny: float,
        source_scope: list[str],
        allowed_domains: list[str],
    ) -> str:
        payload = {
            "question": question,
            "budget_cny": budget_cny,
            "source_scope": source_scope,
            "allowed_domains": allowed_domains,
        }
        data = self._request("POST", "/tasks", json=payload)
        task_id = data.get("task_id") or data.get("id")
        if not isinstance(task_id, str) or not task_id.strip():
            raise DeepResearchProviderError("Research provider did not return a task id")
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any]:
        data = self._request("GET", f"/tasks/{task_id}")
        status = data.get("status")
        if not isinstance(status, str) or not status.strip():
            raise DeepResearchProviderError("Research provider returned a task without status")
        return data

    def cancel_task(self, task_id: str) -> None:
        self._request("POST", f"/tasks/{task_id}/cancel", json={})

    def probe(self) -> dict[str, Any]:
        payload = self._request("GET", "/health")
        status = payload.get("status")
        if not isinstance(status, str) or status.casefold() not in {
            "ok",
            "healthy",
            "available",
        }:
            raise DeepResearchProviderError(
                "Research provider health response has no healthy status"
            )
        return {
            "capability": "deep_research",
            "status": status,
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            with httpx.Client(
                headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.request(method, f"{self.base_url}{path}", **kwargs)
        except httpx.TimeoutException as exc:
            raise DeepResearchProviderTimeout("Research provider timed out") from exc
        except httpx.HTTPError as exc:
            raise DeepResearchProviderError("Research provider request failed") from exc
        if not response.is_success:
            raise DeepResearchProviderError(f"Research provider returned HTTP {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise DeepResearchProviderError("Research provider returned non-JSON data") from exc
        if not isinstance(data, dict):
            raise DeepResearchProviderError("Research provider response must be an object")
        return data


class UnavailableDeepResearchProvider:
    remote_capability = True

    def __init__(self, provider_id: str, reason: str) -> None:
        self.provider_id = provider_id
        self.reason = reason

    def capabilities(self) -> dict[str, Any]:
        return {"background": True, "available": False}

    def estimate(self, *, question: str, budget_cny: float) -> float:
        del question
        return budget_cny

    def create_task(self, **_: Any) -> str:
        raise DeepResearchProviderError(self.reason)

    def get_task(self, task_id: str) -> dict[str, Any]:
        del task_id
        raise DeepResearchProviderError(self.reason)

    def cancel_task(self, task_id: str) -> None:
        del task_id
        raise DeepResearchProviderError(self.reason)

    def probe(self) -> dict[str, Any]:
        raise DeepResearchProviderError(self.reason)
