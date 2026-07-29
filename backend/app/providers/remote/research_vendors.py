"""Vendor Deep Research adapters behind LearnGraph's research port.

Each adapter maps one vendor's agentic research API onto the same contract:
``create_task`` returns immediately with a provider task id, ``get_task`` is
polled by the background worker, and a completed task is normalized into the
canonical ``evidence_pack``.

Only vendors with a native asynchronous job API belong here.  The research
worker pool is small, so an adapter must never hold a worker for the whole
multi-minute run.
"""

from __future__ import annotations

import json
import threading
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.providers.remote.research import (
    DeepResearchProviderError,
    DeepResearchProviderTimeout,
)
from app.providers.remote.research_normalize import (
    dedupe_sources,
    evidence_pack,
    source_entry,
)
from app.providers.remote.research_streaming import (
    StreamingCancelled,
    iter_sse_json,
    streaming_research_runner,
)

# Rough per-task list prices, used only when the workspace has not declared
# its own estimate. They bound the pre-approval quote, never the actual bill.
_DEFAULT_ESTIMATE_CNY = {
    "gemini_deep_research": 15.0,
    "openai_deep_research": 20.0,
    "perplexity_deep_research": 12.0,
    "tavily_deep_research": 8.0,
    "exa_deep_research": 8.0,
    "qwen_deep_research": 6.0,
    "jina_deep_research": 6.0,
}


class _HTTPResearchAdapter:
    """Shared HTTP plumbing and error mapping for vendor research APIs."""

    vendor = "generic"
    remote_capability = True

    def __init__(
        self,
        *,
        provider_id: str,
        base_url: str,
        api_key: str,
        model: str,
        declared_capabilities: dict[str, Any] | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.provider_id = provider_id
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.declared_capabilities = dict(declared_capabilities or {})
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        # allowed_domains arrives with create_task but is needed again when the
        # completed report is normalized, so the approved scope is retained.
        self._allowed_domains: list[str] = []

    def capabilities(self) -> dict[str, Any]:
        return {
            "background": True,
            "citations": True,
            "cancel": True,
            "structured_output": False,
            "vendor": self.vendor,
            "model": self.model,
            **self.declared_capabilities,
        }

    def estimate(self, *, question: str, budget_cny: float) -> float:
        del question
        configured = self.declared_capabilities.get("estimated_cost_cny")
        if isinstance(configured, (int, float)) and configured >= 0:
            return min(float(configured), budget_cny)
        return min(_DEFAULT_ESTIMATE_CNY.get(self.vendor, 20.0), budget_cny)

    def _auth_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        expect_json: bool = True,
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                headers=self._auth_headers(),
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.request(method, url, json=json_body)
        except httpx.TimeoutException as exc:
            raise DeepResearchProviderTimeout("Research provider timed out") from exc
        except httpx.HTTPError as exc:
            raise DeepResearchProviderError("Research provider request failed") from exc
        if not response.is_success:
            # Upstream bodies can echo the prompt or key material; only the
            # status code crosses this boundary.
            raise DeepResearchProviderError(
                f"Research provider returned HTTP {response.status_code}"
            )
        if not expect_json:
            return {}
        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise DeepResearchProviderError(
                "Research provider returned non-JSON data"
            ) from exc
        if not isinstance(data, dict):
            raise DeepResearchProviderError("Research provider response must be an object")
        return data

    @staticmethod
    def _require_task_id(value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DeepResearchProviderError("Research provider did not return a task id")
        return value.strip()

    def _finished(
        self,
        *,
        report: str,
        sources: list[dict[str, Any]],
        artifact_ref: str | None,
        usage: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not report.strip():
            raise DeepResearchProviderError("Research provider completed without a report")
        return {
            "status": "completed",
            "evidence_pack": evidence_pack(
                report=report,
                sources=sources,
                model_or_agent_version=self.model,
                artifact_ref=artifact_ref,
                allowed_domains=self._allowed_domains,
            ),
            "model_or_agent_version": self.model,
            "artifact_ref": artifact_ref,
            "usage": usage or {},
        }


class GeminiDeepResearchProvider(_HTTPResearchAdapter):
    """Google Gemini Deep Research through the Interactions API.

    The agent runs as a stored background interaction, so LearnGraph only
    submits the question and later reads the finished report — no long-lived
    connection is held for the multi-minute run.
    """

    vendor = "gemini_deep_research"
    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
    DEFAULT_AGENT = "deep-research-preview-04-2026"

    # Gemini's interaction lifecycle mapped onto the states the research
    # service already understands.
    _STATUS_MAP = {
        "queued": "queued",
        "in_progress": "running",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "incomplete": "failed",
        "budget_exceeded": "failed",
    }

    def _auth_headers(self) -> dict[str, str]:
        # Gemini authenticates with its own key header, not a bearer token.
        return {"x-goog-api-key": self.api_key, "Accept": "application/json"}

    def create_task(
        self,
        *,
        question: str,
        budget_cny: float,
        source_scope: list[str],
        allowed_domains: list[str],
    ) -> str:
        del budget_cny, source_scope
        self._allowed_domains = list(allowed_domains or [])
        payload: dict[str, Any] = {
            "agent": self.model,
            "input": question,
            # Background is required for the deep-research agent and is what
            # makes the poll-based contract possible.
            "background": True,
            "agent_config": {
                "type": "deep-research",
                # A server-side task has nobody to answer a clarifying prompt,
                # so planning must not block on collaboration.
                "collaborative_planning": False,
            },
        }
        data = self._request("POST", f"{self.base_url}/interactions", json_body=payload)
        raw_id = data.get("id") or data.get("name")
        if isinstance(raw_id, str) and raw_id.startswith("interactions/"):
            raw_id = raw_id.split("/", 1)[1]
        return self._require_task_id(raw_id)

    def get_task(self, task_id: str) -> dict[str, Any]:
        data = self._request("GET", f"{self.base_url}/interactions/{task_id}")
        raw_status = str(data.get("status") or "").strip().casefold()
        if raw_status == "requires_action":
            # Collaborative planning is disabled, so this means the agent is
            # blocked on input that a background job can never supply.
            return {
                "status": "failed",
                "error": "Gemini 深度研究请求了交互确认，后台任务无法应答",
            }
        mapped = self._STATUS_MAP.get(raw_status)
        if mapped is None:
            return {"status": raw_status or "unknown"}
        if mapped != "completed":
            result: dict[str, Any] = {"status": mapped}
            if mapped == "failed":
                error = data.get("error")
                if isinstance(error, dict):
                    result["error"] = str(error.get("message") or "")[:2_000]
                elif raw_status in {"incomplete", "budget_exceeded"}:
                    result["error"] = f"Gemini 深度研究未完成（{raw_status}）"
            return result
        report, sources = self._read_output(data)
        return self._finished(
            report=report,
            sources=sources,
            artifact_ref=task_id,
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else {},
        )

    @staticmethod
    def _read_output(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        """Collect the final report text and every URL citation it carries."""

        texts: list[str] = []
        citations: list[dict[str, Any] | None] = []
        steps = data.get("steps")
        for step in steps if isinstance(steps, list) else []:
            if not isinstance(step, dict):
                continue
            contents = step.get("content")
            for content in contents if isinstance(contents, list) else []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
                annotations = content.get("annotations")
                for annotation in annotations if isinstance(annotations, list) else []:
                    if not isinstance(annotation, dict):
                        continue
                    citations.append(
                        source_entry(
                            url=annotation.get("url"),
                            title=annotation.get("title"),
                        )
                    )
        # The last text block is the report; earlier ones are thought summaries.
        report = texts[-1] if texts else ""
        fallback = data.get("output_text")
        if not report.strip() and isinstance(fallback, str):
            report = fallback
        return report, dedupe_sources(citations)

    def cancel_task(self, task_id: str) -> None:
        self._request(
            "POST",
            f"{self.base_url}/interactions/{task_id}/cancel",
            json_body={},
            expect_json=False,
        )

    def probe(self) -> dict[str, Any]:
        # Listing models validates the key without starting a billable agent run.
        root = self.base_url.rstrip("/")
        self._request("GET", f"{root}/models")
        return {
            "capability": "deep_research",
            "status": "ok",
            "vendor": self.vendor,
            "agent": self.model,
        }


class OpenAIDeepResearchProvider(_HTTPResearchAdapter):
    """OpenAI background Responses run with the hosted web-search tool.

    The dedicated ``*-deep-research`` models were shut down on 2026-07-23, so
    research runs on a general reasoning model driving ``web_search`` in
    background mode instead.
    """

    vendor = "openai_deep_research"
    DEFAULT_BASE_URL = "https://api.openai.com/v1"
    DEFAULT_MODEL = "gpt-5.6-sol"

    _STATUS_MAP = {
        "queued": "queued",
        "in_progress": "running",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
        "incomplete": "failed",
    }

    def create_task(
        self,
        *,
        question: str,
        budget_cny: float,
        source_scope: list[str],
        allowed_domains: list[str],
    ) -> str:
        del budget_cny, source_scope
        self._allowed_domains = list(allowed_domains or [])
        max_tool_calls = self.declared_capabilities.get("max_tool_calls")
        payload: dict[str, Any] = {
            "model": self.model,
            "input": question,
            "background": True,
            "store": True,
            "tools": [{"type": "web_search"}],
        }
        if isinstance(max_tool_calls, int) and max_tool_calls > 0:
            payload["max_tool_calls"] = max_tool_calls
        data = self._request("POST", f"{self.base_url}/responses", json_body=payload)
        return self._require_task_id(data.get("id"))

    def get_task(self, task_id: str) -> dict[str, Any]:
        data = self._request("GET", f"{self.base_url}/responses/{task_id}")
        raw_status = str(data.get("status") or "").strip().casefold()
        mapped = self._STATUS_MAP.get(raw_status)
        if mapped is None:
            return {"status": raw_status or "unknown"}
        if mapped != "completed":
            result: dict[str, Any] = {"status": mapped}
            if mapped == "failed":
                error = data.get("error")
                if isinstance(error, dict):
                    result["error"] = str(error.get("message") or "")[:2_000]
            return result
        report, sources = self._read_output(data)
        return self._finished(
            report=report,
            sources=sources,
            artifact_ref=task_id,
            usage=data.get("usage") if isinstance(data.get("usage"), dict) else {},
        )

    @staticmethod
    def _read_output(data: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        texts: list[str] = []
        citations: list[dict[str, Any] | None] = []
        output = data.get("output")
        for item in output if isinstance(output, list) else []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            contents = item.get("content")
            for content in contents if isinstance(contents, list) else []:
                if not isinstance(content, dict):
                    continue
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    texts.append(text)
                annotations = content.get("annotations")
                for annotation in annotations if isinstance(annotations, list) else []:
                    if (
                        isinstance(annotation, dict)
                        and annotation.get("type") == "url_citation"
                    ):
                        citations.append(
                            source_entry(
                                url=annotation.get("url"),
                                title=annotation.get("title"),
                            )
                        )
        report = "\n\n".join(texts)
        fallback = data.get("output_text")
        if not report.strip() and isinstance(fallback, str):
            report = fallback
        return report, dedupe_sources(citations)

    def cancel_task(self, task_id: str) -> None:
        self._request(
            "POST",
            f"{self.base_url}/responses/{task_id}/cancel",
            json_body={},
            expect_json=False,
        )

    def probe(self) -> dict[str, Any]:
        self._request("GET", f"{self.base_url}/models")
        return {
            "capability": "deep_research",
            "status": "ok",
            "vendor": self.vendor,
            "model": self.model,
        }


class PerplexityDeepResearchProvider(_HTTPResearchAdapter):
    """Perplexity async Sonar deep research.

    Perplexity's asynchronous endpoint wraps an otherwise synchronous chat
    request in a job envelope, which is exactly the shape this port needs.
    """

    vendor = "perplexity_deep_research"
    DEFAULT_BASE_URL = "https://api.perplexity.ai"
    DEFAULT_MODEL = "sonar-deep-research"

    _STATUS_MAP = {
        "created": "queued",
        "in_progress": "running",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }

    def create_task(
        self,
        *,
        question: str,
        budget_cny: float,
        source_scope: list[str],
        allowed_domains: list[str],
    ) -> str:
        del budget_cny, source_scope
        self._allowed_domains = list(allowed_domains or [])
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": question}],
        }
        effort = self.declared_capabilities.get("reasoning_effort")
        if effort in {"minimal", "low", "medium", "high"}:
            request["reasoning_effort"] = effort
        if allowed_domains:
            # Perplexity applies its own domain filter upstream; the pack is
            # filtered again after completion so the two cannot disagree.
            request["search_domain_filter"] = list(allowed_domains)[:20]
        data = self._request(
            "POST",
            f"{self.base_url}/v1/async/sonar",
            json_body={"request": request},
        )
        return self._require_task_id(data.get("id"))

    def get_task(self, task_id: str) -> dict[str, Any]:
        data = self._request("GET", f"{self.base_url}/v1/async/sonar/{task_id}")
        raw_status = str(data.get("status") or "").strip().casefold()
        mapped = self._STATUS_MAP.get(raw_status)
        if mapped is None:
            return {"status": raw_status or "unknown"}
        if mapped != "completed":
            result: dict[str, Any] = {"status": mapped}
            if mapped == "failed":
                result["error"] = str(data.get("error_message") or "")[:2_000]
            return result
        response = data.get("response")
        if not isinstance(response, dict):
            raise DeepResearchProviderError(
                "Research provider completed without a response object"
            )
        report, sources = self._read_output(response)
        return self._finished(
            report=report,
            sources=sources,
            artifact_ref=task_id,
            usage=response.get("usage") if isinstance(response.get("usage"), dict) else {},
        )

    @staticmethod
    def _read_output(response: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        report = ""
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                report = message["content"]
        citations: list[dict[str, Any] | None] = []
        # search_results carries titles; citations is a bare URL list. Read the
        # richer one first so dedupe keeps the better metadata.
        results = response.get("search_results")
        for item in results if isinstance(results, list) else []:
            if isinstance(item, dict):
                citations.append(
                    source_entry(
                        url=item.get("url"),
                        title=item.get("title"),
                        snippet=item.get("snippet"),
                    )
                )
        raw_citations = response.get("citations")
        for item in raw_citations if isinstance(raw_citations, list) else []:
            citations.append(source_entry(url=item))
        return report, dedupe_sources(citations)

    def cancel_task(self, task_id: str) -> None:
        # Perplexity exposes no cancel route for async Sonar jobs; the job runs
        # to completion upstream and LearnGraph stops consuming it.
        del task_id

    def probe(self) -> dict[str, Any]:
        self._request("GET", f"{self.base_url}/v1/async/sonar")
        return {
            "capability": "deep_research",
            "status": "ok",
            "vendor": self.vendor,
            "model": self.model,
        }


class TavilyDeepResearchProvider(_HTTPResearchAdapter):
    """Tavily Deep Research: POST /research then poll GET /research/{id}."""

    vendor = "tavily_deep_research"
    DEFAULT_BASE_URL = "https://api.tavily.com"
    DEFAULT_MODEL = "auto"

    _STATUS_MAP = {
        "pending": "queued",
        "in_progress": "running",
        "completed": "completed",
        "failed": "failed",
    }

    def create_task(
        self,
        *,
        question: str,
        budget_cny: float,
        source_scope: list[str],
        allowed_domains: list[str],
    ) -> str:
        del budget_cny, source_scope
        self._allowed_domains = list(allowed_domains or [])
        payload: dict[str, Any] = {
            "input": question,
            "model": self.model if self.model in {"mini", "pro", "auto"} else "auto",
            "stream": False,
        }
        output_length = self.declared_capabilities.get("output_length")
        if output_length in {"short", "standard", "long"}:
            payload["output_length"] = output_length
        if allowed_domains:
            payload["include_domains"] = list(allowed_domains)[:20]
        data = self._request("POST", f"{self.base_url}/research", json_body=payload)
        return self._require_task_id(data.get("request_id") or data.get("id"))

    def get_task(self, task_id: str) -> dict[str, Any]:
        data = self._request("GET", f"{self.base_url}/research/{task_id}")
        raw_status = str(data.get("status") or "").strip().casefold()
        mapped = self._STATUS_MAP.get(raw_status)
        if mapped is None:
            return {"status": raw_status or "unknown"}
        if mapped != "completed":
            result: dict[str, Any] = {"status": mapped}
            if mapped == "failed":
                result["error"] = str(data.get("error") or "")[:2_000]
            return result
        content = data.get("content")
        # A declared output_schema makes content an object; keep it readable.
        report = content if isinstance(content, str) else json.dumps(
            content, ensure_ascii=False, indent=2
        )
        raw_sources = data.get("sources")
        sources = dedupe_sources(
            [
                source_entry(url=item.get("url"), title=item.get("title"))
                for item in (raw_sources if isinstance(raw_sources, list) else [])
                if isinstance(item, dict)
            ]
        )
        return self._finished(report=report, sources=sources, artifact_ref=task_id)

    def cancel_task(self, task_id: str) -> None:
        # Tavily documents no cancel route for a research request.
        del task_id

    def probe(self) -> dict[str, Any]:
        self._request("GET", f"{self.base_url}/usage")
        return {
            "capability": "deep_research",
            "status": "ok",
            "vendor": self.vendor,
            "model": self.model,
        }


class ExaDeepResearchProvider(_HTTPResearchAdapter):
    """Exa Agent API: asynchronous multi-step research runs with grounding."""

    vendor = "exa_deep_research"
    DEFAULT_BASE_URL = "https://api.exa.ai"
    # Exa selects the research model itself; effort is the real control.
    DEFAULT_MODEL = "auto"
    _EFFORTS = {"minimal", "low", "medium", "high", "xhigh", "auto"}

    _STATUS_MAP = {
        "queued": "queued",
        "running": "running",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }

    def create_task(
        self,
        *,
        question: str,
        budget_cny: float,
        source_scope: list[str],
        allowed_domains: list[str],
    ) -> str:
        del budget_cny, source_scope
        self._allowed_domains = list(allowed_domains or [])
        effort = self.declared_capabilities.get("effort")
        payload: dict[str, Any] = {
            "query": question,
            "effort": effort if effort in self._EFFORTS else self.model,
        }
        if payload["effort"] not in self._EFFORTS:
            payload["effort"] = "auto"
        data = self._request("POST", f"{self.base_url}/agent/runs", json_body=payload)
        return self._require_task_id(data.get("id"))

    def get_task(self, task_id: str) -> dict[str, Any]:
        data = self._request("GET", f"{self.base_url}/agent/runs/{task_id}")
        raw_status = str(data.get("status") or "").strip().casefold()
        mapped = self._STATUS_MAP.get(raw_status)
        if mapped is None:
            return {"status": raw_status or "unknown"}
        if mapped != "completed":
            result: dict[str, Any] = {"status": mapped}
            if mapped == "failed":
                result["error"] = str(data.get("stopReason") or data.get("error") or "")[
                    :2_000
                ]
            return result
        output = data.get("output")
        output = output if isinstance(output, dict) else {}
        text = output.get("text")
        report = text if isinstance(text, str) else json.dumps(
            output.get("structured"), ensure_ascii=False, indent=2
        )
        cost = data.get("costDollars")
        result = self._finished(
            report=report,
            sources=self._read_grounding(output.get("grounding")),
            artifact_ref=task_id,
        )
        if isinstance(cost, (int, float)):
            result["usage"] = {"cost_usd": float(cost)}
        return result

    @staticmethod
    def _read_grounding(grounding: Any) -> list[dict[str, Any]]:
        """Read citations from Exa's grounding, which nests them per field."""

        entries: list[dict[str, Any] | None] = []

        def collect(node: Any) -> None:
            if isinstance(node, list):
                for item in node:
                    collect(item)
                return
            if not isinstance(node, dict):
                return
            url = node.get("url")
            if isinstance(url, str):
                entries.append(source_entry(url=url, title=node.get("title")))
            for key in ("citations", "sources", "results"):
                if key in node:
                    collect(node[key])

        collect(grounding)
        return dedupe_sources(entries)

    def cancel_task(self, task_id: str) -> None:
        self._request(
            "POST",
            f"{self.base_url}/agent/runs/{task_id}/cancel",
            json_body={},
            expect_json=False,
        )

    def probe(self) -> dict[str, Any]:
        self._request("GET", f"{self.base_url}/agent/runs")
        return {
            "capability": "deep_research",
            "status": "ok",
            "vendor": self.vendor,
            "effort": self.model,
        }


class _StreamingResearchAdapter(_HTTPResearchAdapter):
    """Base for vendors whose research only exists as one streamed response.

    The run is handed to the dedicated streaming pool so the shared research
    workers stay free for polling.  Because that registry lives in this
    process, a poll after a restart reports a terminal failure instead of
    leaving the job waiting on a run that is gone.
    """

    # Streaming runs legitimately last many minutes.
    stream_timeout_seconds = 1_800.0

    def _stream_headers(self) -> dict[str, str]:
        return {**self._auth_headers(), "Accept": "text/event-stream"}

    def _run_stream(
        self,
        *,
        url: str,
        payload: dict[str, Any],
        cancelled: threading.Event,
    ) -> dict[str, Any]:
        try:
            with httpx.Client(
                headers=self._stream_headers(),
                timeout=httpx.Timeout(self.stream_timeout_seconds, connect=30.0),
                transport=self.transport,
            ) as client:
                with client.stream("POST", url, json=payload) as response:
                    if not response.is_success:
                        response.read()
                        raise DeepResearchProviderError(
                            f"Research provider returned HTTP {response.status_code}"
                        )
                    return self._consume(response, cancelled)
        except StreamingCancelled:
            return {"status": "cancelled"}
        except httpx.TimeoutException as exc:
            raise DeepResearchProviderTimeout("Research provider timed out") from exc
        except httpx.HTTPError as exc:
            raise DeepResearchProviderError("Research provider request failed") from exc

    def _consume(
        self,
        response: httpx.Response,
        cancelled: threading.Event,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def _submit_stream(self, url: str, payload: dict[str, Any]) -> str:
        try:
            return streaming_research_runner.submit(
                lambda cancelled: self._run_stream(
                    url=url, payload=payload, cancelled=cancelled
                )
            )
        except RuntimeError as exc:
            raise DeepResearchProviderError(
                "Streaming research capacity reached; retry when a run finishes"
            ) from exc

    def get_task(self, task_id: str) -> dict[str, Any]:
        state = streaming_research_runner.poll(task_id)
        if state is None:
            # Only reachable when the process restarted mid-run.
            return {
                "status": "failed",
                "error": "流式研究任务已随服务重启丢失，请重新发起研究。",
            }
        return state

    def cancel_task(self, task_id: str) -> None:
        streaming_research_runner.cancel(task_id)


class QwenDeepResearchProvider(_StreamingResearchAdapter):
    """Alibaba DashScope ``qwen-deep-research``.

    DashScope streams the whole run and offers no polling route, so the
    request is driven on the streaming pool.  ``enable_feedback`` is disabled
    because the clarification round-trip expects a human reply that a
    background job cannot give.
    """

    vendor = "qwen_deep_research"
    DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"
    DEFAULT_MODEL = "qwen-deep-research"
    # Official mainline + dated snapshot (MCP tools on the snapshot only).
    KNOWN_MODELS: tuple[str, ...] = (
        "qwen-deep-research",
        "qwen-deep-research-2025-12-15",
    )
    _PATH = "/api/v1/services/aigc/text-generation/generation"

    def _stream_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            # DashScope only streams when this header is present.
            "X-DashScope-SSE": "enable",
            "Accept": "text/event-stream",
        }

    def create_task(
        self,
        *,
        question: str,
        budget_cny: float,
        source_scope: list[str],
        allowed_domains: list[str],
    ) -> str:
        del budget_cny, source_scope
        self._allowed_domains = list(allowed_domains or [])
        output_format = self.declared_capabilities.get("output_format")
        payload: dict[str, Any] = {
            "model": self.model,
            "input": {"messages": [{"role": "user", "content": question}]},
            "output_format": (
                output_format
                if output_format in {"model_detailed_report", "model_summary_report"}
                else "model_detailed_report"
            ),
            "parameters": {"enable_feedback": False},
        }
        return self._submit_stream(f"{self.base_url}{self._PATH}", payload)

    def _consume(
        self,
        response: httpx.Response,
        cancelled: threading.Event,
    ) -> dict[str, Any]:
        report_parts: list[str] = []
        citations: list[dict[str, Any] | None] = []
        usage: dict[str, Any] = {}
        for chunk in iter_sse_json(response, cancelled=cancelled):
            output = chunk.get("output")
            if not isinstance(output, dict):
                continue
            message = output.get("message")
            message = message if isinstance(message, dict) else {}
            # Documentation disagrees on where phase sits; accept either.
            phase = str(message.get("phase") or output.get("phase") or "").strip()
            if phase == "KeepAlive":
                continue
            content = message.get("content")
            if phase == "answer" and isinstance(content, str):
                report_parts.append(content)
            extra = message.get("extra")
            deep = (extra or {}).get("deep_research") if isinstance(extra, dict) else None
            references = (deep or {}).get("references") if isinstance(deep, dict) else None
            for item in references if isinstance(references, list) else []:
                if isinstance(item, dict):
                    citations.append(
                        source_entry(
                            url=item.get("url"),
                            title=item.get("title"),
                            snippet=item.get("description"),
                        )
                    )
            chunk_usage = chunk.get("usage")
            if isinstance(chunk_usage, dict):
                usage = chunk_usage
        report = "".join(report_parts)
        return self._finished(
            report=report,
            sources=dedupe_sources(citations),
            artifact_ref=None,
            usage=usage,
        )

    def probe(self) -> dict[str, Any]:
        # DashScope's compatible-mode model list validates the key without
        # starting a billable research run.
        self._request(
            "GET",
            f"{self.base_url}/compatible-mode/v1/models",
        )
        return {
            "capability": "deep_research",
            "status": "ok",
            "vendor": self.vendor,
            "model": self.model,
        }


class JinaDeepSearchProvider(_StreamingResearchAdapter):
    """Jina DeepSearch, an OpenAI-compatible streaming research endpoint."""

    vendor = "jina_deep_research"
    DEFAULT_BASE_URL = "https://deepsearch.jina.ai"
    DEFAULT_MODEL = "jina-deepsearch-v1"

    def create_task(
        self,
        *,
        question: str,
        budget_cny: float,
        source_scope: list[str],
        allowed_domains: list[str],
    ) -> str:
        del budget_cny, source_scope
        self._allowed_domains = list(allowed_domains or [])
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": question}],
            # Runs routinely exceed a minute, so streaming is the supported
            # way to keep the connection productive.
            "stream": True,
        }
        effort = self.declared_capabilities.get("reasoning_effort")
        if effort in {"low", "medium", "high"}:
            payload["reasoning_effort"] = effort
        if allowed_domains:
            payload["only_hostnames"] = list(allowed_domains)
        return self._submit_stream(
            f"{self.base_url}/v1/chat/completions",
            payload,
        )

    def _consume(
        self,
        response: httpx.Response,
        cancelled: threading.Event,
    ) -> dict[str, Any]:
        answer_parts: list[str] = []
        citations: list[dict[str, Any] | None] = []
        usage: dict[str, Any] = {}
        for chunk in iter_sse_json(response, cancelled=cancelled):
            choices = chunk.get("choices")
            for choice in choices if isinstance(choices, list) else []:
                if not isinstance(choice, dict):
                    continue
                node = choice.get("delta")
                if not isinstance(node, dict):
                    node = choice.get("message")
                if not isinstance(node, dict):
                    continue
                # Only the answer counts; reasoning_content is the visible
                # thinking trace and must not enter the evidence pack.
                content = node.get("content")
                if isinstance(content, str) and choice.get("finish_reason") != "thinking_end":
                    answer_parts.append(content)
                annotations = node.get("annotations")
                for annotation in annotations if isinstance(annotations, list) else []:
                    if not isinstance(annotation, dict):
                        continue
                    if annotation.get("type") != "url_citation":
                        continue
                    citation = annotation.get("url_citation")
                    citation = citation if isinstance(citation, dict) else annotation
                    citations.append(
                        source_entry(
                            url=citation.get("url"),
                            title=citation.get("title"),
                            snippet=citation.get("exactQuote"),
                        )
                    )
            chunk_usage = chunk.get("usage")
            if isinstance(chunk_usage, dict):
                usage = chunk_usage
        return self._finished(
            report="".join(answer_parts),
            sources=dedupe_sources(citations),
            artifact_ref=None,
            usage=usage,
        )

    def probe(self) -> dict[str, Any]:
        # DeepSearch has no metadata route; the shared Jina token endpoint
        # validates the key without spending a research run.
        try:
            with httpx.Client(
                headers=self._auth_headers(),
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.get("https://api.jina.ai/v1/models")
        except httpx.TimeoutException as exc:
            raise DeepResearchProviderTimeout("Research provider timed out") from exc
        except httpx.HTTPError as exc:
            raise DeepResearchProviderError("Research provider request failed") from exc
        if not response.is_success:
            raise DeepResearchProviderError(
                f"Research provider returned HTTP {response.status_code}"
            )
        return {
            "capability": "deep_research",
            "status": "ok",
            "vendor": self.vendor,
            "model": self.model,
        }


def is_official_host(base_url: str | None, hosts: set[str]) -> bool:
    if not base_url:
        return False
    try:
        parsed = urlsplit(base_url.strip())
    except ValueError:
        return False
    return (
        parsed.scheme.casefold() == "https"
        and (parsed.hostname or "").casefold() in hosts
        and not parsed.username
        and not parsed.password
    )
