"""Bounded shared scene projection. UI availability is never evidence of truth."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.domain.schemas.stream_ui import Snapshot


class SceneReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_version: int = Field(ge=1)
    result_version: int = Field(ge=1)
    session_epoch: int = Field(ge=1)
    status: Literal["rendered", "failed"]
    anchors: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(default_factory=list, max_length=100)


def speech_brief(value: Any) -> dict[str, Any]:
    """Keep worker traces, checkpoints and bulk UI data out of the fast model."""
    result = value if isinstance(value, dict) else {}
    brief: dict[str, Any] = {"short_answer": str(result.get("short_answer") or result.get("summary") or "")[:1600]}
    for key in ("findings", "limitations", "unresolved_questions"):
        items = result.get(key)
        brief[key] = [item[:500] for item in items[:6] if isinstance(item, str)] if isinstance(items, list) else []
    for key in ("sources", "artifacts"):
        items = result.get(key)
        fields = ("title", "url") if key == "sources" else ("id", "name", "file_id", "path", "verified")
        brief[key] = [{field: str(item[field])[:500] for field in fields if field in item}
                      for item in items[:6] if isinstance(item, dict)] if isinstance(items, list) else []
    return brief


def valid_visuals(value: Any) -> list[dict[str, Any]]:
    """Share the existing SUIP component registry; never accept executable UI."""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value[:8] if isinstance(value, list) else []:
        try:
            snapshot = Snapshot.model_validate(item)
        except (ValueError, TypeError):
            continue
        if snapshot.block_id in seen or snapshot.state == "removed":
            continue
        if len(json.dumps(item, ensure_ascii=False)) > 100_000:
            continue
        seen.add(snapshot.block_id)
        result.append(snapshot.model_dump(mode="json"))
    return result


def project_scene(payload: dict[str, Any], summary: str = "") -> dict[str, Any]:
    agent = payload.get("agent_result")
    agent = agent if isinstance(agent, dict) else payload
    findings = agent.get("findings")
    findings = [item[:2000] for item in findings[:20] if isinstance(item, str)] if isinstance(findings, list) else []
    visuals = valid_visuals(payload.get("visuals"))
    anchors = ["summary"] if summary or agent.get("short_answer") else []
    anchors += [f"finding-{index}" for index in range(len(findings))]
    anchors += [f"visual-{item['block_id']}" for item in visuals]
    return {"anchors": anchors, "findings": findings, "visuals": visuals}


def scene_grounding(payload: dict[str, Any], *, summary: str, session_epoch: int,
                    requirement_version: int, result_version: int) -> dict[str, Any]:
    scene = project_scene(payload, summary)
    receipt = payload.get("_scene_receipt")
    receipt = receipt if isinstance(receipt, dict) else {}
    try:
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(receipt.get("at")))).total_seconds()
    except (ValueError, TypeError):
        age = float("inf")
    fresh = (
        receipt.get("session_epoch") == session_epoch
        and receipt.get("requirement_version") == requirement_version
        and receipt.get("result_version") == result_version
        and 0 <= age < 30
    )
    available = fresh and receipt.get("status") == "rendered"
    return {
        "status": "rendered" if available else "failed" if fresh and receipt.get("status") == "failed" else "unconfirmed",
        "available_anchors": scene["anchors"],
        "anchors": [anchor for anchor in scene["anchors"] if anchor in receipt.get("anchors", [])] if available else [],
        "findings": scene["findings"],
        "visuals": scene["visuals"],
    }


def current_scene_presentation(db: Any, session: Any) -> dict[str, Any] | None:
    """Replay focus lifetime from the playback journal, including other clients."""
    from sqlalchemy import select
    from app.domain.models import VoiceEventRecord
    if session.status != "active":
        return None
    lease = getattr(session, 'runtime_owner_lease_until', None)
    if lease and lease.replace(tzinfo=timezone.utc) <= datetime.now(timezone.utc):
        return None
    scope = (VoiceEventRecord.voice_session_id == session.id,
             VoiceEventRecord.workspace_id == session.workspace_id,
             VoiceEventRecord.tenant_id == session.tenant_id,
             VoiceEventRecord.session_epoch == session.session_epoch)
    focus = db.scalar(select(VoiceEventRecord).where(*scope, VoiceEventRecord.event_type == "scene.focus")
                      .order_by(VoiceEventRecord.event_seq.desc()).limit(1))
    if focus is None:
        return None
    age = (datetime.now(timezone.utc) - focus.created_at.replace(tzinfo=timezone.utc)).total_seconds()
    if age > 300:
        return None
    ending = db.scalars(select(VoiceEventRecord).where(*scope, VoiceEventRecord.event_seq > focus.event_seq,
        VoiceEventRecord.event_type.in_(("turn.finalized", "turn.interrupted", "user.started", "turn.accepted", "session.closed", "session.reconnecting"))))
    for event in ending:
        if event.event_type in {"user.started", "session.closed", "session.reconnecting"}:
            return None
        if event.event_type == "turn.accepted":
            if event.turn_id != focus.turn_id:
                return None
        elif (not focus.turn_id or event.turn_id == focus.turn_id) and (event.payload or {}).get("role") != "user":
            return None
    return {**dict(focus.payload or {}), "id": focus.event_id, "turn_id": focus.turn_id}
