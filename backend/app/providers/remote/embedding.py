from __future__ import annotations

import json

import httpx

from app.providers.remote.openai import (
    ProviderHTTPError,
    ProviderTimeoutError,
    merge_provider_request_headers,
    normalize_openai_api_base_url,
)


# DashScope's OpenAI-compatible mode caps text-embedding batches at 10 inputs
# per request. Using that as the universal batch size keeps one adapter valid
# for OpenAI, DashScope (Qwen), SiliconFlow and other compatible gateways.
_MAX_BATCH_SIZE = 10


class OpenAICompatibleEmbeddingProvider:
    """``POST {base}/embeddings`` adapter for OpenAI-compatible endpoints."""

    def __init__(
        self,
        *,
        provider_id: str,
        model_id: str,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        extra_headers: dict[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self.available = True
        # Token usage of the most recent embed() call, in the same shape the
        # chat adapters use so BillingService can record it uniformly.
        self.last_usage: dict[str, int] = {}
        self._base_url = normalize_openai_api_base_url(base_url)
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._extra_headers = dict(extra_headers or {})
        self._transport = transport

    def embed(self, texts: list[str]) -> list[list[float]]:
        cleaned = [(text or " ").strip() or " " for text in texts]
        self.last_usage = {}
        if not cleaned:
            return []
        input_tokens = 0
        vectors: list[list[float]] = []
        with httpx.Client(
            headers=merge_provider_request_headers(
                api_key=self._api_key,
                extra_headers=self._extra_headers,
            ),
            timeout=self._timeout_seconds,
            transport=self._transport,
        ) as client:
            for start in range(0, len(cleaned), _MAX_BATCH_SIZE):
                batch = cleaned[start : start + _MAX_BATCH_SIZE]
                try:
                    response = client.post(
                        f"{self._base_url}/embeddings",
                        json={"model": self.model_id, "input": batch},
                    )
                except httpx.TimeoutException as exc:
                    raise ProviderTimeoutError("Embedding request timed out") from exc
                except httpx.HTTPError as exc:
                    raise ProviderHTTPError(f"Embedding request failed: {exc}") from exc
                if not response.is_success:
                    detail = response.text[:300]
                    raise ProviderHTTPError(
                        f"Embedding provider returned HTTP {response.status_code}: {detail}"
                    )
                try:
                    payload = response.json()
                except json.JSONDecodeError as exc:
                    raise ProviderHTTPError("Embedding provider returned invalid JSON") from exc
                usage = payload.get("usage")
                if isinstance(usage, dict):
                    reported = usage.get("prompt_tokens", usage.get("total_tokens"))
                    if isinstance(reported, (int, float)) and not isinstance(reported, bool):
                        input_tokens += int(reported)
                items = payload.get("data")
                if not isinstance(items, list) or len(items) != len(batch):
                    raise ProviderHTTPError(
                        "Embedding provider returned a mismatched data array"
                    )
                ordered = sorted(
                    items,
                    key=lambda item: item.get("index", 0) if isinstance(item, dict) else 0,
                )
                for item in ordered:
                    vector = item.get("embedding") if isinstance(item, dict) else None
                    if not isinstance(vector, list) or not vector:
                        raise ProviderHTTPError(
                            "Embedding provider returned an empty embedding"
                        )
                    vectors.append([float(value) for value in vector])
        if input_tokens:
            self.last_usage = {"input_tokens": input_tokens, "output_tokens": 0}
        return vectors
