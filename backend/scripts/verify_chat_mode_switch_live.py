"""Real HTTP/provider verification in a disposable copy of the configured DB.

Run in the normal server environment. --serve keeps the isolated result open for
browser inspection; no source database, messages or settings are written.
Reports contain only new educational examples and credential-free wire metadata.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import secrets
import sqlite3
import sys
import tempfile
import time
from uuid import uuid4


def main():
    from app.core.config import get_settings
    from sqlalchemy.engine import make_url

    settings = get_settings()
    if settings.secret_provider == "environment" and not settings.master_key:
        key_file = Path(os.environ.get("LEARNGRAPH_DATA_ROOT", "/data")) / ".master-key"
        if key_file.is_file():
            os.environ["LEARNGRAPH_MASTER_KEY"] = key_file.read_text().strip()
            get_settings.cache_clear()
    source = make_url(get_settings().database_url)
    if source.get_backend_name() != "sqlite" or not source.database or source.database == ":memory:":
        raise RuntimeError("Live verification requires an isolated SQLite backup")

    with tempfile.TemporaryDirectory(prefix="lg-mode-switch-") as scratch:
        database = Path(scratch) / "validation.db"
        with sqlite3.connect(Path(source.database).resolve().as_uri() + "?mode=ro", uri=True) as src, sqlite3.connect(database) as dst:
            src.backup(dst)
        fixture_name = "mode-switch-" + uuid4().hex
        os.environ.update({
            "LEARNGRAPH_DATABASE_URL": f"sqlite:///{database.as_posix()}",
            "LEARNGRAPH_STORAGE_ROOT": str(Path(scratch) / "storage"),
            "LEARNGRAPH_ENV": "development",
            "LEARNGRAPH_ENABLE_DEMO_LOGIN": "true",
            "LEARNGRAPH_ENABLE_DEMO_SEED": "false",
            "LEARNGRAPH_DEMO_USERNAME": fixture_name,
            "LEARNGRAPH_DEMO_PASSWORD": secrets.token_urlsafe(24),
            "LEARNGRAPH_DURABLE_QUEUE_ENABLED": "false",
            "LEARNGRAPH_MEMORY_OUTBOX_WORKER_ENABLED": "false",
        })
        get_settings.cache_clear()
        from app.core.database import SessionLocal
        from app.core.security import hash_password, normalize_identity
        from app.domain.models import ProviderConfig, User, Workspace
        from sqlalchemy import select

        with SessionLocal() as db:
            provider = db.scalar(select(ProviderConfig).where(
                ProviderConfig.enabled.is_(True),
                ProviderConfig.display_name == os.environ.get("MODE_SWITCH_PROVIDER", "DeepSeek1"),
            ))
            if provider is None:
                raise RuntimeError("The requested configured remote provider is unavailable")
            provider_id, workspace_id, provider_name = provider.id, provider.workspace_id, provider.display_name
            owner = db.get(User, db.get(Workspace, workspace_id).owner_user_id)
            owner.username = fixture_name
            owner.username_normalized = normalize_identity(fixture_name)
            owner.password_hash = hash_password(get_settings().demo_password)
            owner.failed_login_count, owner.locked_until = 0, None
            owner.status, owner.must_change_password = "active", False
            db.commit()

        report = {"database": "disposable SQLite backup; source mounted read-only",
                  "provider": provider_name, "model": os.environ.get("MODE_SWITCH_MODEL", "deepseek-flash"),
                  "wire": [], "cases": []}
        label = "setup"
        from app.services import chat_service_factory as factory
        original_factory = factory.model_provider_for_workspace

        def observed_factory(*args, **kwargs):
            instance = original_factory(*args, **kwargs)
            original_options = instance._apply_call_options

            def options(payload, *, responses):
                result = original_options(payload, responses=responses)
                messages = result.get("messages", [])
                report["wire"].append({
                    "case": label, "thinking_mode": instance.thinking_mode,
                    "thinking": result.get("thinking"), "enable_thinking": result.get("enable_thinking"),
                    "reasoning_effort": result.get("reasoning_effort"),
                    "tools": [item.get("function", {}).get("name") for item in result.get("tools", [])],
                    "roles": [item.get("role") for item in messages],
                    "history_tool_calls": sum(bool(item.get("tool_calls")) for item in messages),
                    "history_reasoning": sum(bool(item.get("reasoning_content")) for item in messages),
                    "history_continuations": sum(bool(item.response_items) for item in getattr(instance, "_mode_switch_messages", [])),
                })
                save()
                return result

            instance._apply_call_options = options
            original_chat = instance.stream_chat

            def chat(messages, **options):
                instance._mode_switch_messages = messages
                yield from original_chat(messages, **options)

            instance.stream_chat = chat
            return instance

        factory.model_provider_for_workspace = observed_factory
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        login = client.post("/api/v1/auth/demo-login", json={})
        login.raise_for_status()
        headers = {"Authorization": "Bearer " + login.json()["access_token"], "X-Workspace-Id": workspace_id}

        def api(path, method="get", payload=None):
            response = getattr(client, method)("/api/v1" + path, headers=headers, **({"json": payload} if payload is not None else {}))
            response.raise_for_status()
            return response

        sid = api("/sessions", "post", {"title": "极速切换真实链路验证", "memory_enabled": False}).json()["id"]
        report.update(session_id=sid, workspace_id=workspace_id)
        output = Path(os.environ.get("MODE_SWITCH_REPORT", "/tmp/mode-switch-report.json"))

        def save():
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        def turn(name, content=None, *, high=False, retry=None):
            nonlocal label
            label = name
            payload = {"agent_mode": high, "thinking_mode": "high" if high else "off", "web_search": False,
                       "provider_id": provider_id, "model_id": report["model"]}
            if content is not None:
                payload["content"] = content
            path = f"/sessions/{sid}/messages/{retry}/retry" if retry else f"/sessions/{sid}/messages/stream"
            started = time.monotonic()
            events = [json.loads(line[5:]) for line in api(path, "post", payload).text.splitlines() if line.startswith("data:")]
            message = next(item for item in api(f"/sessions/{sid}/messages").json()["items"] if item["id"] == events[-1]["message_id"])
            ui = [part for part in message["parts"] if part["type"] == "ui" and part["data"].get("state") != "removed"]
            replay = api(f"/sessions/{sid}/messages/{message['id']}/events")
            # Credentials are not part of these new message/event payloads.
            case = {"name": name, "request": payload, "seconds": round(time.monotonic() - started, 3),
                    "message": message, "events": events, "replay_status": replay.status_code,
                    "ui_count": len(ui), "ui_states": [part["data"]["state"] for part in ui]}
            report["cases"].append(case)
            save()
            print(json.dumps({"name": name, "seconds": case["seconds"], "status": message["status"],
                              "ui_count": len(ui), "message_id": message["id"]}, ensure_ascii=False), flush=True)
            assert message["status"] == "completed", message["status"]
            assert all(part["data"]["state"] == "completed" for part in ui)
            assert "DSML" not in json.dumps(events, ensure_ascii=False) and "DSML" not in replay.text
            if not high:
                wires = [wire for wire in report["wire"] if wire["case"] == name]
                assert wires and all(not wire["tools"] and "tool" not in wire["roles"] and not wire["history_tool_calls"]
                                     and not wire["history_reasoning"] and not wire["history_continuations"] for wire in wires)
                assert all(wire["thinking"] == {"type": "disabled"} for wire in wires)
            return message

        turn("high", "用原生图文解释西瓜的外皮、果肉和瓜籽，画一个部位关系图。请用 ui_stream 实际完成文档，不要网页截图。把本次辨认编号记为西瓜731。", high=True)
        fast = turn("fast", "继续刚才的西瓜，编号保持不变。给一个简短的原生图文：一段说明，加一个外皮、果肉、瓜籽的部位关系图。使用本轮提供的原生声明通道。")
        assert any(part["type"] == "ui" and part["data"].get("state") == "completed" for part in fast["parts"])
        assert "731" in json.dumps(fast, ensure_ascii=False)
        retried = turn("fast-retry", retry=fast["id"])
        assert any(part["type"] == "ui" and part["data"].get("state") == "completed" for part in retried["parts"])
        turn("high-again", "继续使用刚才的编号，用原生图文补充西瓜籽的作用，实际完成文档。", high=True)
        report["passed"] = True
        save()
        print(json.dumps({"passed": True, "session_id": sid, "workspace_id": workspace_id, "report": str(output)}, ensure_ascii=False), flush=True)
        if "--serve" in sys.argv:
            import uvicorn
            uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")


if __name__ == "__main__":
    main()
