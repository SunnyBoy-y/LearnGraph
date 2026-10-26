"""One cheap scheduling rule shared by event workers and idle recovery sweeps."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re

from app.domain.models import utc_now


@dataclass(frozen=True)
class ExtractionReadiness:
    ready: bool
    reason: str
    retry_after_seconds: float = 0.0


def extraction_readiness(messages, session, settings, *, now: datetime | None = None) -> ExtractionReadiness:
    if not messages:
        return ExtractionReadiness(False, "no_new_messages")
    if session is None:
        return ExtractionReadiness(False, "session_deleted")
    texts = [message.content or "" for message in messages]
    if any(re.search(r"请(?:你)?记住|帮我记住|记住我|忘记(?:我|这|之前)|纠正.*记忆|\b(?:remember that|remember me|forget my)\b", text, re.I) for text in texts):
        return ExtractionReadiness(True, "explicit_memory_intent")
    if session.status in {"closed", "archived", "completed"} or session.closed_at is not None:
        return ExtractionReadiness(True, "task_closed")
    if any(re.search(r"换个(?:任务|主题|话题)|另一个任务|这个任务完成|任务已完成|\b(?:new task|task complete)\b", text, re.I) for text in texts):
        return ExtractionReadiness(True, "task_boundary")
    if len(messages) >= max(2, int(getattr(settings, "memory_extraction_min_user_turns", 10))):
        return ExtractionReadiness(True, "batch_threshold")
    current = now or utc_now()
    latest = max(message.created_at for message in messages)
    # Assistant completion is activity too. Never extract in the middle of an
    # otherwise active exchange merely because the user's message is old.
    if session.updated_at and session.updated_at.replace(tzinfo=None) > latest.replace(tzinfo=None):
        latest = session.updated_at
    age = (current.replace(tzinfo=timezone.utc) - latest.replace(tzinfo=timezone.utc)).total_seconds()
    delay = max(30, int(getattr(settings, "memory_extraction_idle_seconds", 900)))
    if age >= delay:
        return ExtractionReadiness(True, "idle")
    return ExtractionReadiness(False, "batch_deferred", max(1.0, delay - age))
