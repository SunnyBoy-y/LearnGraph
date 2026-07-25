from __future__ import annotations

from collections.abc import Iterable
from typing import Any


class ModelProviderUnavailableError(RuntimeError):
    """No usable model provider is configured for the requested operation."""


class UnavailableModelProvider:
    """Expose an unusable provider configuration instead of returning demo data."""

    available = False
    remote_capability = False
    context_window_tokens = 0
    max_output_tokens = 0
    last_usage: dict[str, int] = {}
    last_request_id: str | None = None

    def __init__(
        self,
        reason: str,
        *,
        provider_id: str = "model_provider_unavailable",
        model_id: str = "unavailable",
    ) -> None:
        self.provider_id = provider_id
        self.model_id = model_id
        self.reason = reason

    def stream_answer(self, prompt: str) -> Iterable[str]:
        del prompt
        raise ModelProviderUnavailableError(self.reason)
        yield  # pragma: no cover - keeps this method an Iterable implementation

    def generate_json(
        self,
        prompt: str,
        schema_name: str,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        del prompt, schema_name, schema
        raise ModelProviderUnavailableError(self.reason)


class LocalDemoModelProvider:
    """Deterministic demo provider; it never contacts a model endpoint."""

    provider_id = "local_mock"
    model_id = "deterministic-demo"
    available = True
    remote_capability = False
    context_window_tokens = 32_000
    max_output_tokens = 2_000
    last_usage: dict[str, int] = {}
    last_request_id: str | None = None

    def stream_answer(self, prompt: str) -> Iterable[str]:
        subject = prompt.rsplit("当前用户消息：", 1)[-1].strip().replace("\n", " ")[:80]
        answer = (
            f"这是本地演示回复，已收到：{subject}。"
            "当前未配置远程模型，因此不会声称执行了联网检索或模型推理。"
            "你可以继续检查图谱节点、资料状态和练习闭环。"
        )
        chunk_size = 18
        for index in range(0, len(answer), chunk_size):
            yield answer[index : index + chunk_size]

    def generate_json(self, prompt: str, schema_name: str, schema: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("The local demo provider does not claim structured model generation")
