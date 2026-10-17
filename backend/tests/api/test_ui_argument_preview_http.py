"""Exercise real chat routes, SSE persistence and history with fragmented tool input."""
import json
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.providers.ports.model import ProviderStreamEvent
from app.services.chat import ChatService
from test_stream_ui_http import UIProvider


@pytest.mark.parametrize("runtime", [False, True])
@pytest.mark.parametrize("ending", ["valid", "malformed", "unsafe", "patch", "repaired", "cancel"])
def test_argument_preview_precedes_tool_commit_and_replays(client, register_user, auth_headers, monkeypatch, runtime, ending):
    from app.api.routers import chat as router
    from app.domain.models import MessagePartRecord

    services = []
    checks = []

    class Provider(UIProvider):
        def stream_chat(self, messages, **kwargs):
            if messages[-1].role == "tool":
                yield ProviderStreamEvent("text_delta", content="已接收内容。")
                yield ProviderStreamEvent("completed", finish_reason="stop")
                return
            yield ProviderStreamEvent("text_delta", content="先看已经生成的内容。")
            base = {"op": "mount", "block_id": "progress", "component": "document", "props": {"title": "流式参数"}}
            blocks = [{"id": "root", "type": "section"},
                      {"id": "p", "type": "paragraph", "parent_id": "root", "text":
                       '第一段已经可以阅读，后面的内容仍在生成。参数中的“文字”、转义引号 " 和换行\n会被逐步解码，表情 🐉 也会完整保留。界面先展示已接收部分，完整参数通过校验后再正式提交。'},
                      {"id": "later", "type": "paragraph", "parent_id": "root", "text": "第二段在同一次调用中继续生成。"}]
            command = {**base, "upsert": {"blocks": blocks}}
            if ending == "patch":
                yield ProviderStreamEvent("tool_calls", tool_calls=[{"id": "mount", "type": "function",
                    "function": {"name": "ui_stream", "arguments": json.dumps(base)}}])
                # The normal tool loop will call this provider again.
                self.patch_phase = True
                yield ProviderStreamEvent("completed", finish_reason="tool_calls")
                return
            yield from self.fragments(command, ending)

        def fragments(self, command, mode):
            raw = json.dumps(command, ensure_ascii=True)
            split = raw.index('{"id": "later"')
            partial = raw.index('"text":') + 140
            pieces = [raw[:partial], raw[partial:split], raw[split:]] if partial < split else [raw[:split], raw[split:]]
            if mode == "malformed":
                pieces[-1] = pieces[-1][:-1] + ",}"
            elif mode == "repaired":
                pieces[-1] += "!"
            elif mode == "unsafe":
                pieces[-1] = pieces[-1].replace('"paragraph"', '"script"')
            for index, piece in enumerate(pieces):
                yield ProviderStreamEvent("tool_call_delta", tool_calls=[{"id": "fragmented", "index": 0,
                    "function": {"name": "ui_stream", "arguments": piece}}])
                if index == 0:
                    service = services[-1]
                    # This runs BEFORE the final bytes are yielded. The record
                    # and durable event must already exist in the real DB session.
                    parts = list(service.db.scalars(select(MessagePartRecord).where(
                        MessagePartRecord.part_type == "ui", MessagePartRecord.workspace_id == service.workspace_id)))
                    part = next(p for p in parts if p.data.get("block_id") == "progress")
                    assert part.data["preview"] and len(part.data["blocks"]) == 2
                    assert part.data["revision"] == 0
                    from app.core.database import SessionLocal
                    with SessionLocal() as other:
                        assert other.get(MessagePartRecord, part.id).data["preview"]
                    checks.append(True)
                    if mode == "cancel":
                        from app.domain.models import MessageControl
                        control = service.db.get(MessageControl, part.message_version_id)
                        control.cancel_requested = True
                        service.db.commit()
            yield ProviderStreamEvent("tool_calls", tool_calls=[{"id": "fragmented", "type": "function",
                "function": {"name": "ui_stream", "arguments": raw if mode == "repaired" else "".join(pieces)}}])
            yield ProviderStreamEvent("completed", finish_reason="tool_calls")

    provider = Provider()
    original = provider.stream_chat
    def stream(messages, **kwargs):
        if ending == "patch" and getattr(provider, "patch_phase", False) and messages[-1].role == "tool":
            provider.patch_phase = False
            command = {"op": "patch", "block_id": "progress", "base_revision": 0,
                "upsert": {"blocks": [{"id": "root", "type": "section"},
                    {"id": "p", "parent_id": "root", "type": "paragraph", "text": "第一段"},
                    {"id": "later", "parent_id": "root", "type": "paragraph", "text": "第二段"}]}}
            yield from provider.fragments(command, "valid")
        else:
            yield from original(messages, **kwargs)
    provider.stream_chat = stream

    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id, provider, settings=settings)
        service._build_fast_preface_provider = lambda: None
        if runtime:
            from app.services.chat_service_factory import build_agent_tool_runtime
            service.agent_tool_runtime = build_agent_tool_runtime(db, context, settings)
        services.append(service)
        return service
    monkeypatch.setattr(router, "service", factory)
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "参数预览"}).json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                           json={"content": "逐段生成", "agent_mode": True})
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    assert events[-1]["type"] == ("message.cancelled" if ending == "cancel" else "message.completed"), response.text[-5000:]
    assert checks
    updates = [e for e in events if e.get("payload", {}).get("ui_operation")]
    previews = [e for e in updates if e["payload"]["ui_operation"] == "ui.preview"]
    assert previews and all(e["payload"]["part"]["data"]["revision"] == 0 for e in previews)
    assert len({e["payload"]["part"]["id"] for e in updates}) == 1
    if ending in {"valid", "patch", "repaired"}:
        commit = next(e for e in updates if e["payload"]["ui_operation"] == "ui." + ("patch" if ending == "patch" else "mount"))
        assert commit["sequence"] > previews[0]["sequence"]
        assert commit["payload"]["part"]["data"]["preview"] is None
    history = next(item for item in client.get(f"/api/v1/sessions/{sid}/messages", headers=headers).json()["items"]
                   if item["id"] == events[-1]["message_id"])
    assert any(p["type"] == "ui" for p in history["parts"]), json.dumps(history, ensure_ascii=False)
    part = next(p for p in history["parts"] if p["type"] == "ui")
    if ending in {"valid", "patch", "repaired"}:
        assert part["data"]["state"] == "completed" and len(part["data"]["blocks"]) == 3
        assert part["data"]["revision"] == (2 if ending == "patch" else 1)
    elif ending == "cancel":
        assert part["data"]["preview"] and part["data"]["blocks"]
        assert part["status"] in {"interrupted", "cancelled"}
        assert not any(e["payload"]["ui_operation"] == "ui.mount" for e in updates)
    else:
        assert part["data"]["state"] == "removed" and not part["data"].get("preview")
    replay = client.get(f"/api/v1/sessions/{sid}/messages/{history['id']}/events", headers=headers).json()
    assert any(e.get("payload", {}).get("ui_operation") == "ui.preview" for e in replay)
    if runtime and ending == "valid" and os.getenv("LEARNGRAPH_UI_STREAM_EVIDENCE"):
        destination = Path(os.environ["LEARNGRAPH_UI_STREAM_EVIDENCE"])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "events.json").write_text(json.dumps({"source": "real chat HTTP routes with deterministic provider fragments",
            "events": events, "history": history, "replay": replay}, ensure_ascii=False, indent=2), encoding="utf-8")


def test_svg_is_requested_only_after_search_failure(client, register_user, auth_headers, monkeypatch):
    from app.api.routers import chat as router
    from app.services.chat_service_factory import build_agent_tool_runtime
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="20"/></svg>'
    image = {"id": "pic", "type": "image", "query": "leaf shape", "alt": "叶片"}

    class Provider(UIProvider):
        step = 0
        def stream_chat(self, messages, **kwargs):
            self.step += 1
            commands = [
                {"op": "mount", "block_id": "on_demand", "component": "document", "props": {"title": "形态"},
                 "upsert": {"blocks": [image]}},
                {"op": "complete", "block_id": "on_demand", "base_revision": 0},
                {"op": "patch", "block_id": "on_demand", "base_revision": 0,
                 "upsert": {"blocks": [{**image, "fallback_svg": svg}]}},
                {"op": "complete", "block_id": "on_demand", "base_revision": 1},
            ]
            if self.step == 3:
                result = json.loads(messages[-1].content)
                assert "ui_fallback_needed" in json.dumps(result)
                assert "on_demand" in json.dumps(result)
            if self.step <= 4:
                yield ProviderStreamEvent("tool_calls", tool_calls=[{"id": str(self.step), "type": "function",
                    "function": {"name": "ui_stream", "arguments": json.dumps(commands[self.step - 1])}}])
                yield ProviderStreamEvent("completed", finish_reason="tool_calls")
            else:
                yield ProviderStreamEvent("text_delta", content="检索失败后已按需补充示意图。")
                yield ProviderStreamEvent("completed", finish_reason="stop")

    provider = Provider()
    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id, provider, settings=settings)
        service._build_fast_preface_provider = lambda: None
        service.agent_tool_runtime = build_agent_tool_runtime(db, context, settings)
        return service
    monkeypatch.setattr(router, "service", factory)
    token, workspace, _, _ = register_user()
    headers = auth_headers(token, workspace)
    sid = client.post("/api/v1/sessions", headers=headers, json={"title": "按需示意图"}).json()["id"]
    response = client.post(f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                          json={"content": "用图解释叶片", "agent_mode": True, "web_search": False})
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    assert events[-1]["type"] == "message.completed", response.text[-3000:]
    updates = [e["payload"] for e in events if e.get("payload", {}).get("ui_operation")]
    mount = next(e for e in updates if e["ui_operation"] == "ui.mount")
    assert mount["part"]["data"]["blocks"][0]["fallback_svg"] == ""
    patch = next(e for e in updates if e["ui_operation"] == "ui.patch")
    assert patch["part"]["data"]["blocks"][0]["fallback_svg"] == svg
    assert patch["part"]["data"]["blocks"][0]["resource_state"] == "unavailable"
    assert provider.step == 5
