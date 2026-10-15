"""Independent acceptance contract for the voice tutor / background-agent upgrade.

These tests deliberately exercise public behavior instead of A-D implementation
details.  They are expected to fail while the loop is incomplete; they are not
allowed to modify product code to make themselves pass.
"""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.providers.model_options import resolve_model_call_options
from app.services.sandbox_agent_executor import execute_subagent_job


def _headers(auth_headers, token: str, workspace_id: str | None = None) -> dict[str, str]:
    return auth_headers(token, workspace_id)


def _create_chat_session(client, headers: dict[str, str]) -> str:
    response = client.post(
        "/api/v1/sessions",
        headers=headers,
        json={"title": f"voice-contract-{uuid4().hex[:8]}"},
    )
    assert response.status_code == 201, response.text
    return str(response.json()["id"])


def _create_voice_session(
    client,
    headers: dict[str, str],
    chat_session_id: str,
) -> str:
    response = client.post(
        "/api/v1/voice/sessions",
        headers=headers,
        json={"chat_session_id": chat_session_id, "max_thinking_mode": "high"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def _start_voice_session(client, register_user, auth_headers):
    token, workspace_id, _, _ = register_user("voice")
    headers = _headers(auth_headers, token, workspace_id)
    chat_session_id = _create_chat_session(client, headers)
    voice_session_id = _create_voice_session(client, headers, chat_session_id)
    return token, workspace_id, headers, chat_session_id, voice_session_id


def _start_task(
    client,
    headers: dict[str, str],
    voice_session_id: str,
    *,
    prompt: str = "Research Python and Java hash table collision strategies.",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    request_headers = dict(headers)
    if idempotency_key:
        request_headers["Idempotency-Key"] = idempotency_key
    response = client.post(
        f"/api/v1/voice/sessions/{voice_session_id}/tasks",
        headers=request_headers,
        json={
            "prompt": prompt,
            "title": "Hash table research",
            "role_key": "research",
            "thinking_mode": "high",
            "tools": ["web_search", "fetch_web_page"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_direct_voice_turn_is_persisted_and_replays_idempotently(
    client, register_user, auth_headers
) -> None:
    _token, _workspace_id, headers, _chat_id, voice_session_id = _start_voice_session(
        client, register_user, auth_headers
    )
    turn_id = f"turn_{uuid4().hex[:20]}"
    accepted = client.post(
        f"/api/v1/voice/sessions/{voice_session_id}/turns/accept",
        headers=headers,
        json={
            "turn_id": turn_id,
            "text": "Explain open addressing first.",
            "client_message_id": f"client_{uuid4().hex[:12]}",
        },
    )
    assert accepted.status_code == 200, accepted.text
    replay = client.post(
        f"/api/v1/voice/sessions/{voice_session_id}/turns/accept",
        headers=headers,
        json={"turn_id": turn_id, "text": "Explain open addressing first."},
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["turn_id"] == turn_id

    finalized = client.post(
        f"/api/v1/voice/sessions/{voice_session_id}/turns/{turn_id}/finalize",
        headers=headers,
        json={"assistant_text": "Open addressing places collisions in the next empty slot."},
    )
    assert finalized.status_code == 200, finalized.text

    transcript = client.get(
        f"/api/v1/voice/sessions/{voice_session_id}/transcript",
        headers=headers,
    )
    assert transcript.status_code == 200, transcript.text
    turns = transcript.json()["turns"]
    assert len(turns) == 1
    assert turns[0]["user_text"] == "Explain open addressing first."
    assert "Open addressing" in turns[0]["assistant_text"]

    replay_events = client.get(
        f"/api/v1/voice/sessions/{voice_session_id}/events?after_event_seq=0",
        headers=headers,
    )
    assert replay_events.status_code == 200, replay_events.text
    event_types = [event["type"] for event in replay_events.json()["events"]]
    assert "turn.accepted" in event_types
    assert "turn.finalized" in event_types


def test_delegate_receipt_returns_before_foreground_continues(
    client, register_user, auth_headers
) -> None:
    _token, _workspace_id, headers, _chat_id, voice_session_id = _start_voice_session(
        client, register_user, auth_headers
    )
    started = time.perf_counter()
    task = _start_task(client, headers, voice_session_id)
    receipt_latency = time.perf_counter() - started
    assert receipt_latency < 2.5
    assert task["status"] in {"queued", "accepted", "running"}
    assert task["subagent_id"]

    turn_id = f"turn_{uuid4().hex[:20]}"
    foreground = client.post(
        f"/api/v1/voice/sessions/{voice_session_id}/turns/accept",
        headers=headers,
        json={"turn_id": turn_id, "text": "Explain open addressing first."},
    )
    assert foreground.status_code == 200, foreground.text
    finalized = client.post(
        f"/api/v1/voice/sessions/{voice_session_id}/turns/{turn_id}/finalize",
        headers=headers,
        json={"assistant_text": "It is one form of linear probing."},
    )
    assert finalized.status_code == 200, finalized.text

    snapshot = client.get(
        f"/api/v1/voice/sessions/{voice_session_id}/tasks/{task['subagent_id']}",
        headers=headers,
    )
    assert snapshot.status_code == 200, snapshot.text
    assert snapshot.json()["subagent_id"] == task["subagent_id"]
    assert snapshot.json()["status"] in {
        "queued",
        "running",
        "completed",
        "failed",
        "partial",
        "timed_out",
    }


def test_repeated_delegate_request_is_idempotent(
    client, register_user, auth_headers
) -> None:
    _token, _workspace_id, headers, _chat_id, voice_session_id = _start_voice_session(
        client, register_user, auth_headers
    )
    idempotency_key = f"voice-research-{uuid4().hex}"
    first = _start_task(
        client, headers, voice_session_id, idempotency_key=idempotency_key
    )
    second = _start_task(
        client, headers, voice_session_id, idempotency_key=idempotency_key
    )
    assert second["subagent_id"] == first["subagent_id"]

    session = client.get(
        f"/api/v1/voice/sessions/{voice_session_id}", headers=headers
    )
    assert session.status_code == 200, session.text
    tasks = session.json()["tasks"]
    assert len(tasks) == 1


def test_voice_task_link_exposes_requirement_and_delivery_state(
    client, register_user, auth_headers
) -> None:
    _token, _workspace_id, headers, _chat_id, voice_session_id = _start_voice_session(
        client, register_user, auth_headers
    )
    task = _start_task(client, headers, voice_session_id)

    session_response = client.get(
        f"/api/v1/voice/sessions/{voice_session_id}", headers=headers
    )
    assert session_response.status_code == 200, session_response.text
    links = session_response.json()["tasks"]
    link = next(item for item in links if item["subagent_id"] == task["subagent_id"])
    assert link.get("requirement_version", 0) >= 1
    assert link.get("delivery_status") in {
        "pending",
        "ready",
        "delivered",
        "dismissed",
        "stale",
        "failed",
    }


def test_interrupt_does_not_cancel_task_but_cancel_does(
    client, register_user, auth_headers
) -> None:
    _token, _workspace_id, headers, _chat_id, voice_session_id = _start_voice_session(
        client, register_user, auth_headers
    )
    task = _start_task(client, headers, voice_session_id)
    turn_id = f"turn_{uuid4().hex[:20]}"
    accepted = client.post(
        f"/api/v1/voice/sessions/{voice_session_id}/turns/accept",
        headers=headers,
        json={"turn_id": turn_id, "text": "Pause the speech but keep the task."},
    )
    assert accepted.status_code == 200, accepted.text
    interrupted = client.post(
        f"/api/v1/voice/sessions/{voice_session_id}/interrupt",
        headers=headers,
        json={},
    )
    assert interrupted.status_code == 200, interrupted.text

    after_interrupt = client.get(
        f"/api/v1/voice/sessions/{voice_session_id}/tasks/{task['subagent_id']}",
        headers=headers,
    )
    assert after_interrupt.status_code == 200, after_interrupt.text
    assert after_interrupt.json()["status"] not in {"cancelled", "cancel_requested"}

    cancelled = client.post(
        f"/api/v1/voice/sessions/{voice_session_id}/tasks/{task['subagent_id']}/cancel",
        headers=headers,
        json={},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] in {
        "cancelled",
        "cancel_requested",
        "canceling",
    }


class _RecordingProvider:
    provider_id = "test-provider"
    model_id = "plain-model"

    def __init__(self) -> None:
        self.seen_tools: list[dict[str, Any]] | None = None
        self.last_usage: dict[str, int] = {"input_tokens": 1, "output_tokens": 1}

    def stream_chat(self, messages, tools=None):
        self.seen_tools = list(tools or [])
        yield SimpleNamespace(
            type="text_delta",
            content=json.dumps(
                {
                    "summary": "pure reasoning result",
                    "artifacts": [],
                    "evidence": [{"check": "self", "result": "passed"}],
                    "acceptance": [{"criterion": "no tools", "status": "passed"}],
                    "risks": [],
                    "unresolved": [],
                    "recommended_next_action": "merge",
                    "confidence": 0.8,
                }
            ),
        )
        yield SimpleNamespace(type="completed")


def test_empty_tools_means_no_tools_not_default_sandbox_tools() -> None:
    provider = _RecordingProvider()
    settings = get_settings()
    job = SimpleNamespace(
        id=f"job_{uuid4().hex[:16]}",
        attempt=1,
        status="RUNNING",
        cancel_requested=False,
        workspace_id="workspace-test",
        owner_user_id="owner-test",
        chat_session_id="chat-test",
        payload_json={
            "task_id": "task-test",
            "prompt": "Do a pure reasoning pass without tools.",
            "tools": [],
            "write_set": [],
            "budget": {"max_rounds": 1, "max_seconds": 30},
        },
    )
    task = SimpleNamespace(id="task-test")

    outcome = execute_subagent_job(
        settings,
        job,
        task,
        provider=provider,
        sandbox_service=object(),
    )

    assert outcome.status in {"PARTIAL", "SUCCEEDED"}
    assert provider.seen_tools == []


def test_reasoning_request_falls_back_when_provider_has_no_reasoning() -> None:
    capabilities = {
        "provider_family": "openai_compatible",
        "models": {
            "plain-model": {
                "models_dev_known": True,
                "reasoning_efforts": [],
                "default_thinking_mode": "off",
                "reasoning_parameter": "enable_thinking",
                "thinking_mapping": {"off": False},
            }
        },
    }
    options = resolve_model_call_options(
        capabilities,
        "plain-model",
        thinking_mode="high",
    )
    assert options.thinking_mode == "off"
    assert options.actual_reasoning_effort is False
    assert options.provider_options.get("enable_thinking") is False


def test_tutor_has_a_registered_delegate_research_tool() -> None:
    source_path = (
        Path(__file__).resolve().parents[2] / "app" / "voice" / "embedded_bot.py"
    )
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    tutor = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "TutorAgent"
        ),
        None,
    )
    assert tutor is not None
    delegate_methods = [
        node
        for node in tutor.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("delegate_")
    ]
    assert delegate_methods, "TutorAgent has no delegate_* method"
    assert any(
        any(
            (isinstance(decorator, ast.Name) and decorator.id == "tool")
            or (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Name)
                and decorator.func.id == "tool"
            )
            for decorator in method.decorator_list
        )
        for method in delegate_methods
    ), "delegate_* methods must be registered with Pipecat @tool"


def test_voice_tasks_and_events_are_workspace_isolated(
    client, register_user, auth_headers
) -> None:
    _token_a, _workspace_a, headers_a, _chat_id, voice_session_id = (
        _start_voice_session(client, register_user, auth_headers)
    )
    task = _start_task(client, headers_a, voice_session_id)

    token_b, workspace_b, _, _ = register_user("isolated")
    headers_b = _headers(auth_headers, token_b, workspace_b)

    session_response = client.get(
        f"/api/v1/voice/sessions/{voice_session_id}", headers=headers_b
    )
    assert session_response.status_code == 404
    task_response = client.get(
        f"/api/v1/voice/sessions/{voice_session_id}/tasks/{task['subagent_id']}",
        headers=headers_b,
    )
    assert task_response.status_code in {403, 404}
    event_response = client.get(
        f"/api/v1/voice/sessions/{voice_session_id}/events?after_event_seq=0",
        headers=headers_b,
    )
    assert event_response.status_code in {403, 404}
