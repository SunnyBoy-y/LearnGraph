"""QwenChatProvider DashScope 原生协议通道（搜索来源 + 角标）回归测试。

官方协议差异表：返回搜索来源（enable_source）与角标标注（enable_citation）
仅 DashScope 原生协议支持，OpenAI 兼容协议不返回 search_info。此测试验证
QwenChatProvider 在 DashScope origin 且开启原生联网搜索时切到原生
generation 流式通道，并正确解析 output.search_info / content / usage。
"""
from __future__ import annotations

import json
from typing import Any, Iterable

import pytest

from app.providers.model_options import ModelCallOptions
from app.providers.ports.model import ProviderChatMessage
from app.providers.remote.openai import QwenChatProvider

DASHSCOPE_COMPAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DASHSCOPE_MAAS_URL = "https://ws-123.cn-beijing.maas.aliyuncs.com/api/v1"
RELAY_URL = "https://relay.example.com/v1"


class FakeResponse:
    is_success = True
    headers = {"x-request-id": "req-native-1"}

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def iter_lines(self) -> Iterable[str]:
        return iter(self._lines)

    def read(self) -> bytes:
        return b""


class _StreamContext:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response

    def __enter__(self) -> FakeResponse:
        return self._response

    def __exit__(self, *args: Any) -> bool:
        return False


class FakeClient:
    def __init__(self, lines: list[str]) -> None:
        self._lines = lines
        self.posted: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def stream(
        self,
        method: str,
        url: str,
        json: dict[str, Any],
        headers: dict[str, Any] | None = None,
    ) -> _StreamContext:
        self.posted.append((url, json, headers or {}))
        return _StreamContext(FakeResponse(self._lines))

    def __enter__(self) -> "FakeClient":
        return self

    def __exit__(self, *args: Any) -> bool:
        return False


def _provider(
    base_url: str, *, native_web_search: bool = True, strategy: str = "turbo"
) -> QwenChatProvider:
    return QwenChatProvider(
        provider_id="p1",
        model_id="qwen3.8-max",
        base_url=base_url,
        api_key="k",
        call_options=ModelCallOptions(
            thinking_mode="off",
            actual_reasoning_effort=None,
            reasoning_parameter="enable_thinking",
            search_route="model_native" if native_web_search else "disabled",
            native_web_search=native_web_search,
            provider_options={"enable_thinking": False},
        ),
        capabilities={"chat_search_strategy": strategy},
    )


def _sse(chunks: list[dict[str, Any]]) -> list[str]:
    return [f"data: {json.dumps(chunk, ensure_ascii=False)}" for chunk in chunks]


def _user(text: str = "杭州明天天气") -> list[ProviderChatMessage]:
    return [ProviderChatMessage(role="user", content=text)]


def _native_chunks() -> list[dict[str, Any]]:
    return [
        {
            "output": {
                "choices": [
                    {
                        "finish_reason": None,
                        "message": {"role": "assistant", "content": "根据"},
                    }
                ],
                "search_info": {
                    "search_results": [
                        {
                            "index": 1,
                            "title": "来源A",
                            "url": "https://a.example.com/news",
                            "site_name": "站点A",
                        },
                        {
                            "index": 2,
                            "title": "来源B",
                            "url": "https://b.example.com/wx",
                            "site_name": "站点B",
                        },
                    ]
                },
            }
        },
        {
            "output": {
                "choices": [
                    {
                        "finish_reason": None,
                        "message": {
                            "role": "assistant",
                            "content": "最新天气见[ref_1]和[ref_2]。",
                        },
                    }
                ]
            }
        },
        {
            "output": {
                "choices": [
                    {"finish_reason": "stop", "message": {"role": "assistant", "content": ""}}
                ]
            },
            "usage": {
                "input_tokens": 120,
                "output_tokens": 60,
                "input_tokens_details": {"cached_tokens": 30},
            },
        },
    ]


def test_native_channel_used_on_dashscope_compat_url(monkeypatch):
    provider = _provider(DASHSCOPE_COMPAT_URL)
    client = FakeClient(_sse(_native_chunks()))
    monkeypatch.setattr(provider, "_client", lambda: client)

    events = list(provider.stream_chat(_user()))
    url, body, headers = client.posted[0]

    assert url.endswith("/api/v1/services/aigc/text-generation/generation")
    assert headers.get("X-DashScope-SSE") == "enable"
    parameters = body["parameters"]
    assert parameters["enable_search"] is True
    assert parameters["search_options"] == {
        "search_strategy": "turbo",
        "enable_source": True,
        "enable_citation": True,
        "citation_format": "[ref_<number>]",
    }
    assert parameters["result_format"] == "message"
    assert parameters["incremental_output"] is True

    # 文本增量拼接完整
    text = "".join(e.content for e in events if e.type == "text_delta")
    assert text == "根据最新天气见[ref_1]和[ref_2]。"
    # search_info 进入 last_sources（含 url / title / index）
    assert [s["url"] for s in provider.last_sources] == [
        "https://a.example.com/news",
        "https://b.example.com/wx",
    ]
    assert [s["index"] for s in provider.last_sources] == [1, 2]
    # usage 解析
    assert provider.last_usage == {
        "input_tokens": 120,
        "output_tokens": 60,
        "cached_input_tokens": 30,
    }
    # 结束事件
    assert events[-1].type == "completed"
    assert events[-1].finish_reason == "stop"


def test_native_channel_used_on_maas_workspace_url(monkeypatch):
    provider = _provider(DASHSCOPE_MAAS_URL)
    client = FakeClient(_sse(_native_chunks()))
    monkeypatch.setattr(provider, "_client", lambda: client)
    list(provider.stream_chat(_user()))
    url, _, _ = client.posted[0]
    assert url == (
        "https://ws-123.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/aigc/text-generation/generation"
    )


def test_native_channel_skipped_for_relay(monkeypatch):
    provider = _provider(RELAY_URL)
    assert provider._use_native_dashscope(_user()) is False
    # relay 走父类兼容路径：请求打到 /chat/completions
    client = FakeClient(_sse([]))
    monkeypatch.setattr(provider, "_client", lambda: client)
    with pytest.raises(Exception):
        list(provider.stream_chat(_user()))
    assert client.posted and client.posted[0][0].endswith("/chat/completions")


def test_native_channel_skipped_without_search_and_with_tools(monkeypatch):
    provider = _provider(DASHSCOPE_COMPAT_URL, native_web_search=False)
    assert provider._use_native_dashscope(_user()) is False
    provider = _provider(DASHSCOPE_COMPAT_URL)
    client = FakeClient(_sse(_native_chunks()))
    monkeypatch.setattr(provider, "_client", lambda: client)
    # Agent 工具回合保持兼容模式
    with pytest.raises(Exception):
        list(provider.stream_chat(_user(), tools=[{"type": "function"}]))
    assert client.posted and client.posted[0][0].endswith("/chat/completions")


def test_native_channel_skipped_for_multimodal_parts():
    provider = _provider(DASHSCOPE_COMPAT_URL)
    messages = [
        ProviderChatMessage(
            role="user",
            content="看图",
            content_parts=[{"type": "input_image", "image_url": "data:image/png;base64,AA=="}],
        )
    ]
    assert provider._use_native_dashscope(messages) is False


def test_native_channel_stream_error(monkeypatch):
    provider = _provider(DASHSCOPE_COMPAT_URL)
    client = FakeClient(
        _sse([{"code": "InvalidParameter", "message": "search_options 参数非法"}])
    )
    monkeypatch.setattr(provider, "_client", lambda: client)
    with pytest.raises(Exception) as excinfo:
        list(provider.stream_chat(_user()))
    assert "search_options 参数非法" in str(excinfo.value)


def test_stream_answer_native_returns_text(monkeypatch):
    provider = _provider(DASHSCOPE_COMPAT_URL)
    client = FakeClient(_sse(_native_chunks()))
    monkeypatch.setattr(provider, "_client", lambda: client)
    text = "".join(provider.stream_answer("杭州明天天气"))
    assert "根据" in text and "[ref_1]" in text
    assert len(provider.last_sources) == 2
