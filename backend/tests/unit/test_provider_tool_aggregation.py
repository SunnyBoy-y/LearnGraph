"""Provider multi-tool-call aggregation robustness tests.

Verifies the deterministic ordering contract and the no-silent-merge guard in
the OpenAI-compatible adapter (``_merge_chat_tool_delta`` /
``_completed_chat_tool_calls``). Anthropic's sort-by-numeric-index behavior is
covered implicitly by the same contract; the adapter-level aggregation is
static and exercised directly here without any HTTP transport.
"""

from __future__ import annotations

import pytest

from app.providers.remote.openai import (
    OpenAICompatibleChatProvider,
    ProviderResponseError,
)


def _merge_all(provider_cls, chunks):
    aggregates: dict[int, dict] = {}
    for index, chunk in enumerate(chunks):
        provider_cls._merge_chat_tool_delta(aggregates, chunk, fallback_index=index)
    return provider_cls._completed_chat_tool_calls(aggregates)


def _chunk(tool_index: int, *, call_id: str = "", name: str = "", arguments: str = "") -> dict:
    chunk: dict = {"index": tool_index, "type": "function"}
    if call_id:
        chunk["id"] = call_id
    function: dict = {}
    if name:
        function["name"] = name
    if arguments:
        function["arguments"] = arguments
    if function:
        chunk["function"] = function
    return chunk


class TestOpenAICompatibleAggregation:
    def test_two_calls_merged_by_index_and_sorted(self):
        tool_calls = _merge_all(
            OpenAICompatibleChatProvider,
            [
                _chunk(0, call_id="a", name="search_web", arguments='{"qu'),
                _chunk(1, call_id="b", name="search_images", arguments='{"q'),
                _chunk(0, arguments='ery": "x"}'),
                _chunk(1, arguments='uery": "y"}'),
            ],
        )
        assert [item["id"] for item in tool_calls] == ["a", "b"]
        assert [item["function"]["name"] for item in tool_calls] == [
            "search_web",
            "search_images",
        ]
        assert tool_calls[0]["function"]["arguments"] == '{"query": "x"}'
        assert tool_calls[1]["function"]["arguments"] == '{"query": "y"}'

    def test_out_of_order_index_still_sorts_by_position(self):
        tool_calls = _merge_all(
            OpenAICompatibleChatProvider,
            [
                _chunk(1, call_id="second", name="search_web", arguments="{}"),
                _chunk(0, call_id="first", name="get_current_time", arguments="{}"),
            ],
        )
        assert [item["id"] for item in tool_calls] == ["first", "second"]

    def test_continuation_chunks_with_blank_identity_do_not_erase(self):
        # DashScope-style relay echoes id/name as blanks on every fragment.
        tool_calls = _merge_all(
            OpenAICompatibleChatProvider,
            [
                _chunk(0, call_id="a", name="search_web", arguments='{"q"'),
                _chunk(0, call_id="", name="", arguments=': "x"}'),
            ],
        )
        assert tool_calls[0]["id"] == "a"
        assert tool_calls[0]["function"]["name"] == "search_web"
        assert tool_calls[0]["function"]["arguments"] == '{"q": "x"}'

    def test_missing_index_correlates_by_stable_call_id(self):
        # A gateway that omits index on every chunk (fallback would be 0 each
        # time) must still keep two distinct calls apart via their call ids.
        tool_calls = _merge_all(
            OpenAICompatibleChatProvider,
            [
                {"id": "a", "type": "function", "function": {"name": "search_web", "arguments": "{}"}},
                {"id": "b", "type": "function", "function": {"name": "search_images", "arguments": "{}"}},
            ],
        )
        assert [item["id"] for item in tool_calls] == ["a", "b"]

    def test_missing_index_with_conflicting_ids_raises_instead_of_merging(self):
        # Without an index and with a fallback slot already occupied by a
        # different call id, the adapter must fail loudly rather than silently
        # merging two distinct tool calls into one.
        aggregates: dict[int, dict] = {}
        OpenAICompatibleChatProvider._merge_chat_tool_delta(
            aggregates,
            {"id": "a", "type": "function", "function": {"name": "search_web", "arguments": "{}"}},
            fallback_index=0,
        )
        with pytest.raises(ProviderResponseError):
            OpenAICompatibleChatProvider._merge_chat_tool_delta(
                aggregates,
                {"id": "b", "type": "function", "function": {"name": "search_images", "arguments": "{}"}},
                fallback_index=0,
            )

    def test_missing_index_with_known_id_merges_into_existing_slot(self):
        aggregates: dict[int, dict] = {}
        OpenAICompatibleChatProvider._merge_chat_tool_delta(
            aggregates,
            {"id": "a", "type": "function", "function": {"name": "search_web", "arguments": '{"q'}},
            fallback_index=0,
        )
        # Continuation without index but with the same id: correlate, do not collide.
        OpenAICompatibleChatProvider._merge_chat_tool_delta(
            aggregates,
            {"id": "a", "function": {"arguments": 'uery": "x"}'}},
            fallback_index=1,
        )
        tool_calls = OpenAICompatibleChatProvider._completed_chat_tool_calls(aggregates)
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "a"
        assert tool_calls[0]["function"]["arguments"] == '{"query": "x"}'
