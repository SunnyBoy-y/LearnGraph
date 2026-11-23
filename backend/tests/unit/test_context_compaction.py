from __future__ import annotations

import json
from types import SimpleNamespace

from app.domain.schemas.management import ProviderModelCapabilityUpdateRequest
from app.providers.model_options import catalog_capability_snapshot, validate_model_capability_update
from app.providers.ports.model import ProviderChatMessage
from app.providers.remote.openai import ProviderHTTPError, ProviderTransportError
from app.services.context_compaction import (
    DEFAULT_AGENT_COMPACTION_RATIO,
    IN_LOOP_SUMMARY_PREFIX,
    MICROCOMPACT_MARKER,
    REHYDRATION_PREFIX,
    build_rehydration_note,
    build_structured_handoff_prompt,
    build_structured_mechanical_summary,
    compact_in_loop_provider_messages,
    estimate_provider_messages_tokens,
    estimate_tool_definitions_tokens,
    is_context_overflow_error,
    microcompact_provider_messages,
)
from app.services.tool_results import ToolResultStore


def test_default_agent_compaction_ratio_is_078_and_preserves_explicit():
    assert DEFAULT_AGENT_COMPACTION_RATIO == 0.78
    snapshot = catalog_capability_snapshot("unknown-test-model")
    assert snapshot["agent_compaction_ratio"] == 0.78
    assert snapshot["chat_compaction_ratio"] == 0.8

    validated_default = validate_model_capability_update({})
    assert validated_default["agent_compaction_ratio"] == 0.78
    assert validated_default["chat_compaction_ratio"] == 0.8

    validated_custom = validate_model_capability_update({"agent_compaction_ratio": 0.42})
    assert validated_custom["agent_compaction_ratio"] == 0.42

    schema_default = ProviderModelCapabilityUpdateRequest()
    assert schema_default.agent_compaction_ratio == 0.78


def test_tool_definitions_accounted_in_provider_token_estimate():
    messages = [
        ProviderChatMessage(role="system", content="You are an agent."),
        ProviderChatMessage(role="user", content="Read app.py and inspect errors."),
    ]
    tool_defs = [
        {
            "type": "function",
            "function": {
                "name": "sandbox_read_file",
                "description": "Read a workspace file with line offsets and hash tracking.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]
    without_tools = estimate_provider_messages_tokens(messages)
    with_tools = estimate_provider_messages_tokens(messages, tool_defs)
    tool_tokens = estimate_tool_definitions_tokens(tool_defs)
    assert tool_tokens > 0
    assert with_tools == without_tools + tool_tokens


def test_microcompact_offloads_old_bulky_tool_outputs_with_recoverable_refs(tmp_path):
    store = ToolResultStore(tmp_path, "ws", "actor", "sess-1")
    long_a = "A" * 900 + "\nTAIL_A"
    long_b = json.dumps({"status": "completed", "result_ref": "result_" + "b" * 32, "stdout": "B" * 800})
    long_recent = "RECENT_" + ("C" * 700)

    messages = [
        ProviderChatMessage(role="user", content="Inspect files"),
        ProviderChatMessage(
            role="assistant",
            tool_calls=[
                {"id": "c1", "type": "function", "function": {"name": "sandbox_read_file", "arguments": '{"path": "a.py"}'}},
                {"id": "c2", "type": "function", "function": {"name": "sandbox_bash", "arguments": '{"command": "pytest"}'}},
                {"id": "c3", "type": "function", "function": {"name": "sandbox_read_file", "arguments": '{"path": "recent.py"}'}},
            ],
        ),
        ProviderChatMessage(role="tool", tool_call_id="c1", content=long_a),
        ProviderChatMessage(role="tool", tool_call_id="c2", content=long_b),
        ProviderChatMessage(role="tool", tool_call_id="c3", content=long_recent),
    ]

    compacted, stats = microcompact_provider_messages(
        messages,
        keep_recent_tool_messages=1,
        min_clear_chars=320,
        store=store,
    )

    assert stats.cleared_count == 2
    assert stats.tokens_after < stats.tokens_before
    assert stats.chars_saved > 0

    stub_1 = compacted[2].content
    stub_2 = compacted[3].content
    assert stub_1.startswith(MICROCOMPACT_MARKER)
    assert "sandbox_read_file(path=a.py)" in stub_1
    assert "result_ref=result_" in stub_1
    assert "sandbox_read_result" in stub_1

    # Extract persisted ref for c1 and verify full content is recoverable
    ref_1 = stub_1.split('result_ref="', 1)[1].split('"', 1)[0]
    restored = store.read(ref_1, field="content", max_chars=5000)
    assert restored["content"] == long_a

    # Existing result_ref in JSON payload is reused directly
    assert f"result_ref=result_{'b' * 32}" in stub_2

    # Recent tool output remains intact
    assert compacted[4].content == long_recent

    # Idempotent on second run
    compacted_2, stats_2 = microcompact_provider_messages(
        compacted,
        keep_recent_tool_messages=1,
        min_clear_chars=320,
        store=store,
    )
    assert stats_2.cleared_count == 0
    assert compacted_2 == compacted


def test_split_turn_in_loop_compaction_keeps_atomic_units_and_folds_prior_summary():
    prior_summary_msg = ProviderChatMessage(
        role="system",
        content=f"{IN_LOOP_SUMMARY_PREFIX} and conversation.\nEarlier goal: build RAG pipeline.",
    )
    stale_rehydration = ProviderChatMessage(
        role="system",
        content=f"[{REHYDRATION_PREFIX}]\nstale state",
    )
    messages = [
        ProviderChatMessage(role="system", content="Base system prompt."),
        prior_summary_msg,
        stale_rehydration,
        ProviderChatMessage(role="user", content="Step 1 request " + ("x" * 1600)),
        ProviderChatMessage(
            role="assistant",
            content="Running step 1",
            tool_calls=[{"id": "call_1", "type": "function", "function": {"name": "sandbox_read_file", "arguments": '{"path":"one.py"}'}}],
            response_items=[{"type": "function_call", "call_id": "call_1", "name": "sandbox_read_file"}],
        ),
        ProviderChatMessage(role="tool", tool_call_id="call_1", content="output one " + ("y" * 2400)),
        ProviderChatMessage(
            role="user",
            content="",
            content_parts=[{"type": "image_url", "image_url": {"url": "https://example.com/img.png"}}],
        ),
        ProviderChatMessage(
            role="assistant",
            content="Running step 2",
            tool_calls=[{"id": "call_2", "type": "function", "function": {"name": "sandbox_bash", "arguments": '{"command":"pytest"}'}}],
        ),
        ProviderChatMessage(role="tool", tool_call_id="call_2", content="recent tool output ok"),
    ]

    captured: list[tuple[str, str]] = []

    def fake_summary(prior: str, transcript: str) -> str:
        captured.append((prior, transcript))
        return "1. Primary Goal: RAG pipeline\n2. Pending Work: verify call_2"

    fresh_rehydration = build_rehydration_note(
        goal=SimpleNamespace(status="active", objective="Build RAG", summary="Step 1 done", rounds_used=2, max_rounds=6),
        observed_paths=["one.py"],
        activated_capability_families=["sandbox"],
    )

    compacted, result = compact_in_loop_provider_messages(
        messages,
        keep_tail_tokens=600,
        min_keep_units=1,
        summary_fn=fake_summary,
        rehydration_note=fresh_rehydration,
    )

    assert result.compacted is True
    assert result.summary_kind == "model_in_loop"
    assert result.tokens_after < result.tokens_before
    assert len(captured) == 1
    assert "Earlier goal: build RAG pipeline." in captured[0][0]

    # Ensure stale summary/rehydration were folded/replaced rather than stacked
    in_loop_summaries = [m for m in compacted if (m.content or "").startswith(IN_LOOP_SUMMARY_PREFIX)]
    rehydration_msgs = [m for m in compacted if (m.content or "").startswith(f"[{REHYDRATION_PREFIX}]")]
    assert len(in_loop_summaries) == 1
    assert len(rehydration_msgs) == 1
    assert "Build RAG" in rehydration_msgs[0].content
    assert "stale state" not in rehydration_msgs[0].content

    # Protocol integrity: the surviving tool result has its matching assistant tool_call
    kept_tool_ids = {m.tool_call_id for m in compacted if m.role == "tool"}
    declared_call_ids = {
        tc["id"]
        for m in compacted
        if m.role == "assistant" and m.tool_calls
        for tc in m.tool_calls
    }
    assert kept_tool_ids == {"call_2"}
    assert kept_tool_ids.issubset(declared_call_ids)


def test_rehydration_note_empty_when_no_state_and_populated_with_active_state():
    assert build_rehydration_note() == ""
    assert build_rehydration_note(goal=SimpleNamespace(status="cleared")) == ""

    note = build_rehydration_note(
        goal=SimpleNamespace(
            status="active",
            objective="Implement layered compaction",
            summary="Microcompact ready",
            rounds_used=3,
            max_rounds=8,
        ),
        session_files=[{"filename": "notes.md", "file_id": "file-123", "origin": "workspace", "size_bytes": 512}],
        observed_paths=["backend/app/services/chat.py"],
        activated_capability_ids=["cap.sandbox"],
        activated_capability_families=["sandbox"],
    )
    assert REHYDRATION_PREFIX in note
    assert "round=3/8" in note
    assert "notes.md (file_id=file-123, origin=workspace, 512B)" in note
    assert "backend/app/services/chat.py" in note
    assert "cap.sandbox" in note


def test_is_context_overflow_error_classification():
    assert is_context_overflow_error(ProviderHTTPError("context_length_exceeded: too many tokens", status_code=400))
    assert is_context_overflow_error(ProviderHTTPError("This model's maximum context length is 128000 tokens", status_code=400))
    assert is_context_overflow_error(ProviderHTTPError("Request entity too large", status_code=413))
    assert is_context_overflow_error(ProviderHTTPError("Please reduce the length of the messages.", status_code=422))
    assert not is_context_overflow_error(ProviderHTTPError("Invalid API key", status_code=401))
    assert not is_context_overflow_error(ProviderHTTPError("Invalid JSON schema", status_code=400))
    assert not is_context_overflow_error(ProviderHTTPError("Bad gateway", status_code=502))
    assert not is_context_overflow_error(ProviderTransportError("RemoteProtocolError"))
    assert not is_context_overflow_error(RuntimeError("context_length_exceeded"))


def test_structured_handoff_prompt_and_mechanical_summary_preserve_execution_and_stickers():
    prompt = build_structured_handoff_prompt(
        "[user] Fix SQLite lock\nExecution signals: tool:sandbox_bash(command=pytest) [failed]",
        base_summary="Previous attempt checked WAL mode.",
        focus="Keep SQLite traceback and exact file_ids",
        has_tools=True,
    )
    assert "PRIORITY FOCUS FROM USER (must preserve in detail): Keep SQLite traceback and exact file_ids" in prompt
    assert "Previous attempt checked WAL mode." in prompt
    assert "1. Primary Goal & Constraints" in prompt
    assert "5. Errors, Dead Ends & Fixes" in prompt

    msg = SimpleNamespace(
        id="m1",
        role="assistant",
        content="Checked logs and uploaded report.",
        parts=[
            {
                "type": "agent_step",
                "data": {
                    "provider_assistant": {
                        "tool_calls": [
                            {"id": "tc1", "function": {"name": "sandbox_bash", "arguments": '{"command":"pytest"}'}}
                        ]
                    },
                    "tool_results": [
                        {"tool_call_id": "tc1", "content": json.dumps({"status": "failed", "result_ref": "result_abc"})}
                    ],
                },
            },
            {
                "type": "attachment",
                "data": {"file_id": "f-99", "filename": "trace.log"},
            },
        ],
    )
    summary = build_structured_mechanical_summary(
        [msg],
        sticker_stub_fn=lambda parts: " [sticker:thumbs_up]",
        focus="Keep SQLite traceback",
    )
    assert "[Compaction Focus] Keep SQLite traceback" in summary
    assert "tool:sandbox_bash(command=pytest) [failed | ref=result_abc]" in summary
    assert "file:trace.log(id=f-99)" in summary
    assert "[sticker:thumbs_up]" in summary
