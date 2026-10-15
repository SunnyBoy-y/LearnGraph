"""Read-only readiness report for the real voice Provider lanes.

This checks the same factory paths used by the embedded runtime without
printing secrets or opening a network connection.  It is useful before running
the live DashScope/Volcengine probes or an Android call.

Usage from ``backend/``::

    .venv/Scripts/python.exe scripts/verify_voice_provider_config.py

Exit codes: 0 when at least one workspace has ASR, TTS and chat model ready;
2 when no workspace is fully ready or the local database has no workspace.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.domain.models import Workspace
from app.providers.factory import (
    model_provider_for_workspace,
    realtime_asr_provider_for_workspace,
    tts_provider_for_workspace,
)
from app.voice.runtime import runtime_info


def _safe_url(value: object) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = urlsplit(raw)
        return f"{parsed.scheme}://{parsed.netloc}" if parsed.netloc else parsed.scheme
    except Exception:
        return None


def _provider_view(provider: object | None) -> dict[str, object]:
    if provider is None:
        return {"available": False, "reason": "not_resolved"}
    return {
        "available": bool(getattr(provider, "available", False)),
        "provider_id": getattr(provider, "provider_id", None),
        "model_id": getattr(provider, "model_id", None),
        "base_url": _safe_url(getattr(provider, "base_url", None)),
        "remote_capability": bool(getattr(provider, "remote_capability", True)),
        "reason": str(getattr(provider, "reason", "") or "")[:300] or None,
    }


def main() -> int:
    settings = get_settings()
    runtime = runtime_info(settings)
    module_state = {
        name: importlib.util.find_spec(name) is not None
        for name in (
            "pipecat",
            "pipecat.transports.smallwebrtc.request_handler",
            "pipecat.workers.llm",
            "websockets",
        )
    }
    report: dict[str, object] = {
        "runtime": {
            "ready": runtime.ready,
            "signaling_url": runtime.signaling_url,
            "reason": runtime.reason,
            "modules": module_state,
        },
        "workspaces": [],
    }
    fully_ready = False
    try:
        db_context = SessionLocal()
        workspaces = list(db_context.scalars(select(Workspace).order_by(Workspace.name)).all())
    except SQLAlchemyError as exc:
        report["database"] = {
            "ready": False,
            "reason": f"database_schema_unavailable: {type(exc).__name__}: {exc}"[:500],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("VOICE_PROVIDER_RESULT database_unavailable", file=sys.stderr)
        return 2
    with db_context as db:
        for workspace in workspaces:
            try:
                asr = realtime_asr_provider_for_workspace(db, workspace.id, settings)
            except Exception as exc:  # noqa: BLE001
                asr = None
                asr_error = f"{type(exc).__name__}: {exc}"[:300]
            else:
                asr_error = None
            try:
                tts = tts_provider_for_workspace(db, workspace.id, settings)
            except Exception as exc:  # noqa: BLE001
                tts = None
                tts_error = f"{type(exc).__name__}: {exc}"[:300]
            else:
                tts_error = None
            try:
                model = model_provider_for_workspace(db, workspace.id, settings)
            except Exception as exc:  # noqa: BLE001
                model = None
                model_error = f"{type(exc).__name__}: {exc}"[:300]
            else:
                model_error = None
            item = {
                "workspace_id": workspace.id,
                "workspace_name": workspace.name,
                "asr": _provider_view(asr),
                "tts": _provider_view(tts),
                "chat_model": _provider_view(model),
                "errors": {
                    key: value
                    for key, value in {
                        "asr": asr_error,
                        "tts": tts_error,
                        "chat_model": model_error,
                    }.items()
                    if value
                },
            }
            report["workspaces"].append(item)  # type: ignore[union-attr]
            fully_ready = fully_ready or all(
                bool(item[channel]["available"])  # type: ignore[index]
                for channel in ("asr", "tts", "chat_model")
            )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["workspaces"]:
        print("VOICE_PROVIDER_RESULT no_workspace", file=sys.stderr)
        return 2
    print(
        "VOICE_PROVIDER_RESULT " + ("ready" if fully_ready else "not_ready"),
        file=sys.stderr,
    )
    return 0 if fully_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
