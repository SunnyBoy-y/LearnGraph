"""Real-provider smoke check against an ephemeral copy of the configured SQLite DB.

Run with the normal server environment/dependencies (e.g. inside the validation
container). No live messages/settings are changed. Reports include only newly
generated verification conversations; credentials are never exported.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from uuid import uuid4


def main():
    from app.core.config import get_settings
    from sqlalchemy.engine import make_url
    configured = get_settings()
    if configured.secret_provider == "environment" and not configured.master_key:
        # docker exec does not inherit exports performed by the entrypoint.
        # Resolve the same existing key file without printing or copying it.
        key_file = Path(os.environ.get("LEARNGRAPH_DATA_ROOT", "/data")) / ".master-key"
        if key_file.is_file():
            os.environ["LEARNGRAPH_MASTER_KEY"] = key_file.read_text(encoding="utf-8").strip()
            get_settings.cache_clear()
    source = make_url(get_settings().database_url)
    if source.get_backend_name() != "sqlite" or not source.database or source.database == ":memory:":
        raise RuntimeError("This verification requires SQLite backup isolation")
    with tempfile.TemporaryDirectory(prefix="rich-response-") as scratch:
        database = Path(scratch) / "validation.db"
        source_uri = Path(source.database).resolve().as_uri() + "?mode=ro"
        with sqlite3.connect(source_uri, uri=True) as src, sqlite3.connect(database) as dst:
            src.backup(dst)
        os.environ["LEARNGRAPH_DATABASE_URL"] = f"sqlite:///{database.as_posix()}"
        os.environ["LEARNGRAPH_STORAGE_ROOT"] = str(Path(scratch) / "storage")
        get_settings.cache_clear()
        # Prepare a disposable login fixture in the provider owner's copied
        # workspace. Authentication and workspace authorization remain real.
        from app.core.database import SessionLocal
        from app.core.security import hash_password, normalize_identity
        from app.domain.models import ProviderConfig, User, Workspace
        from sqlalchemy import select
        with SessionLocal() as db:
            selected = db.scalar(select(ProviderConfig).where(ProviderConfig.enabled.is_(True), ProviderConfig.display_name == "DeepSeek1"))
            if selected is None:
                raise RuntimeError("No configured DeepSeek1 provider exists in the validation backup")
            target_workspace = db.get(Workspace, selected.workspace_id)
            selected_provider_id = selected.id
            selected_workspace_id = selected.workspace_id
            demo = db.get(User, target_workspace.owner_user_id)
            if demo is None:
                raise RuntimeError("Validation backup has no configured demo fixture")
            demo.password_hash = hash_password(get_settings().demo_password)
            # Tenant-scoped copies can share the demo name; give this isolated
            # fixture a unique login identity without changing its memberships.
            fixture_name = "rich-verify-" + uuid4().hex
            demo.username = fixture_name
            demo.username_normalized = normalize_identity(fixture_name)
            demo.failed_login_count = 0
            demo.locked_until = None
            demo.status = "active"
            demo.must_change_password = False
            db.commit()
        os.environ["LEARNGRAPH_DEMO_USERNAME"] = fixture_name
        get_settings.cache_clear()
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)  # no background scheduler lifespan
        from app.core.security import verify_password
        with SessionLocal() as db:
            fixture = db.scalar(select(User).where(User.username_normalized == normalize_identity(get_settings().demo_username)))
            assert fixture is not None and verify_password(get_settings().demo_password, fixture.password_hash), "Disposable login fixture mismatch"
        response = client.post("/api/v1/auth/demo-login", json={})
        if response.status_code == 404:
            # Production installations may disable the demo endpoint. This
            # disposable backup fixture can use the normal login route instead.
            response = client.post("/api/v1/auth/login", json={
                "username": fixture_name, "password": get_settings().demo_password})
        response.raise_for_status()
        login = response.json()
        headers = {"Authorization": "Bearer " + login["access_token"], "X-Workspace-Id": selected_workspace_id}
        def api(path, method="get", payload=None):
            result = getattr(client, method)("/api/v1" + path, headers=headers, **({"json": payload} if payload is not None else {}))
            result.raise_for_status()
            return result
        providers = api("/providers").json()
        provider = next((item for item in providers if item["id"] == selected_provider_id), None)
        if not provider:
            raise RuntimeError("No existing DeepSeek1 provider available for the real smoke check")
        report = {"provider": provider["display_name"], "model": "deepseek-flash", "database": "ephemeral SQLite backup", "cases": []}
        cases = [
            ("plain", False, "什么是 HTTP？请用三句话解释。"),
            ("fast-rich", False, "解释芝诺的阿基里斯悖论。请在解释中穿插原生可调速度的追赶演示和公式，并使用双栏组织图文；最后继续用一段文字总结。请用系统支持的原生声明式UI，不要HTML。"),
            ("agent-rich", True, "比较这组实际给定的阶段用时：第1步8、第2步4、第3步2、第4步1、第5步0.5。先简短说明，再用ui_stream document把一段解释和可交互折线图左右排列，在后面给出结论。图中数值只用我给的数据。"),
        ]
        input_preview = os.environ.get("RICH_RESPONSE_CASE") == "ui-input"
        if input_preview:
            cases = [("ui-input", True,
                "直接调用 ui_stream，用一次有实际内容的 mount 创建 document，先写一段不少于120字的中文说明，"
                "解释数据随生成逐段显示的过程，再加入一个三行数据表格：第一步8、第二步4、第三步2。"
                "数据只用给定值。不要空mount，不要图片，不要SVG，无需预先规划整页。最后complete并简短总结。")]
        for name, agent, prompt in cases:
            sid = api("/sessions", "post", {"title": "Rich response verification " + name, "memory_enabled": False}).json()["id"]
            started = time.monotonic()
            events = []
            with client.stream("POST", f"/api/v1/sessions/{sid}/messages/stream", headers=headers,
                               json={"content": prompt, "agent_mode": agent, "web_search": False,
                                     "provider_id": provider["id"], "model_id": report["model"]}) as result:
                result.raise_for_status()
                for line in result.iter_lines():
                    if line.startswith("data:"):
                        events.append(json.loads(line[5:]))
            messages = api(f"/sessions/{sid}/messages").json()["items"]
            message = messages[-1]
            ui = [part for part in message["parts"] if part["type"] == "ui" and part["data"].get("state") != "removed"]
            case = {"name": name, "seconds": round(time.monotonic()-started, 3), "status": message["status"],
                    "prompt": prompt, "message": message, "events": events,
                    "ui_count": len(ui), "components": [part["data"]["component"] for part in ui]}
            report["cases"].append(case)
            if input_preview:
                previews = [event for event in events if event.get("payload", {}).get("ui_operation") == "ui.preview"]
                commits = [event for event in events if event.get("payload", {}).get("ui_operation") in {"ui.mount", "ui.patch"}]
                from datetime import datetime
                case["preview_count"] = len(previews)
                case["commit_count"] = len(commits)
                case["previews_before_first_commit"] = sum(event["sequence"] < commits[0]["sequence"] for event in previews) if commits else 0
                case["first_preview_lead_seconds"] = round((datetime.fromisoformat(commits[0]["created_at"]) -
                    datetime.fromisoformat(previews[0]["created_at"])).total_seconds(), 3) if previews and commits else None
                readable = next((event for event in previews if any(node.get("text") for node in
                                event["payload"]["part"]["data"].get("blocks", []))), None)
                case["first_readable_preview_lead_seconds"] = round((datetime.fromisoformat(commits[0]["created_at"]) -
                    datetime.fromisoformat(readable["created_at"])).total_seconds(), 3) if readable and commits else None
                case["failed_tool_calls"] = sum(part["status"] == "failed" for part in message["parts"] if part["type"] == "tool_call")
                print(json.dumps({key: case[key] for key in ("preview_count", "commit_count", "previews_before_first_commit", "first_preview_lead_seconds")}), flush=True)
            print(json.dumps({key: case[key] for key in ["name", "seconds", "status", "ui_count", "components"]}, ensure_ascii=False), flush=True)
        output = Path(os.environ.get("RICH_RESPONSE_REPORT", "/tmp/rich-response-verification.json"))
        # Current verification outputs contain only new public educational examples.
        for case in report["cases"]:
            case["message"].pop("provider_trace", None)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert all(case["status"] == "completed" for case in report["cases"])
        if input_preview:
            assert report["cases"][0]["ui_count"] > 0
            assert report["cases"][0]["previews_before_first_commit"] > 0
        else:
            assert report["cases"][0]["ui_count"] == 0
            assert all(case["ui_count"] for case in report["cases"][1:])


if __name__ == "__main__":
    main()
