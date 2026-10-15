"""The tutor LLM's outgoing dialect: no ``developer`` role may leave the box.

pipecat answers a tool call with a ``role="developer"`` message whenever the
tool is registered with ``cancel_on_interruption=False`` -- which is every one
of the tutor's ten tools, delegation included.  The voice provider is a
DeepSeek-compatible gateway that only accepts system/user/assistant/tool, so it
answers ``422 unknown variant `developer``` and pipecat then declares the LLM
processor unusable: one tool call bricks the rest of the call, and every later
turn fails the same way (真机日志 2026-09-18 14:52:20 `VoiceMemoryLLMService#1`
`can no longer do its job`).

The dialect is asserted on the request that actually goes out, not only on the
class attribute, and the shared ``LLMContext`` is asserted to stay untouched --
the turn detector (``answers_the_user_turn``) reads the *context*, so if the
rewrite leaked into it, a tool-result round would be mistaken for the user
speaking and would eat the recall prefetched for the real question.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.voice.embedded_memory import voice_memory_llm_service_class

TOOL_CALL_ID = "call_00_b97T2bW7mHTsqSQWRX1D1927"

STARTED = (
    '{"type": "async_tool", "status": "running", '
    f'"tool_call_id": "{TOOL_CALL_ID}", '
    '"description": "This tool is still running. You will be given its result later."}'
)
FINISHED = (
    '{"type": "async_tool", "status": "finished", '
    f'"tool_call_id": "{TOOL_CALL_ID}", '
    '"description": "This is the final result for the asynchronous task.", '
    '"result": "{\\"count\\": 20}"}'
)


def _tool_round_context():
    """The context pipecat builds after ``list_artifacts`` returns.

    Reproduced from the real log: the started message carries ``role="tool"``
    and the final one ``role="developer"``.
    """

    from pipecat.processors.aggregators.llm_context import LLMContext

    return LLMContext(
        messages=[
            {"role": "user", "content": "看下有哪些图谱"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": TOOL_CALL_ID,
                        "type": "function",
                        "function": {
                            "name": "list_artifacts",
                            "arguments": '{"limit": 20}',
                        },
                    }
                ],
            },
            {"role": "tool", "content": STARTED, "tool_call_id": TOOL_CALL_ID},
            {"role": "developer", "content": FINISHED},
        ]
    )


def _service():
    """The tutor LLM service with its HTTP client stubbed out."""

    pytest.importorskip("pipecat")
    service_class = voice_memory_llm_service_class()
    service = service_class(
        api_key="test",
        base_url="http://127.0.0.1:9",
        settings=service_class.Settings(model="tutor", system_instruction="你是导师"),
    )
    calls: list[dict] = []

    class _Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return object()

    service._client = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    return service, calls


def test_the_tutor_declares_itself_without_the_developer_role():
    pytest.importorskip("pipecat")
    assert voice_memory_llm_service_class().supports_developer_role is False


def test_a_tool_result_leaves_as_a_user_message_not_a_developer_one():
    context = _tool_round_context()
    service, calls = _service()

    asyncio.run(service.get_chat_completions(context))

    messages = calls[0]["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "tool",
        "user",
    ]
    # 换了角色，内容一字不改：模型仍看得出这是哪个 tool_call 的结果。
    assert [message["content"] for message in messages if message["role"] == "user"] == [
        "看下有哪些图谱",
        FINISHED,
    ]
    assert any(
        message.get("tool_call_id") == TOOL_CALL_ID for message in messages
    )


def test_the_rewrite_stays_out_of_the_shared_context():
    """The context keeps ``developer``: the turn detector reads it, not the copy."""

    context = _tool_round_context()
    service, calls = _service()

    asyncio.run(service.get_chat_completions(context))

    assert calls  # 请求确实发出去了
    assert [message["role"] for message in context.get_messages()] == [
        "user",
        "assistant",
        "tool",
        "developer",
    ]
