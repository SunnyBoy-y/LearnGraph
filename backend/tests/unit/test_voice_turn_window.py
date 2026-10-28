"""Exercise the bounded turn query against an isolated SQLite database."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.domain.models import VoiceTurnRecord
from app.voice import turns
from app.voice.embedded_context import VoiceContextAdapter


@pytest.fixture
def turn_database(monkeypatch):
    engine = create_engine("sqlite://")
    VoiceTurnRecord.__table__.create(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(turns, "SessionLocal", sessions)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with sessions() as db:
        for number in range(1, 61):
            when = start + timedelta(seconds=number)
            db.add(VoiceTurnRecord(
                id=f"turn-{number:02}",
                workspace_id="workspace",
                chat_session_id="chat",
                voice_session_id="voice",
                status=("finalized", "interrupted", "failed")[number % 3],
                user_text=f"question-{number:02}",
                assistant_text=f"answer-{number:02}",
                created_at=when,
                updated_at=when,
                finalized_at=when,
            ))
        db.add(VoiceTurnRecord(
            id="open-turn", workspace_id="workspace", chat_session_id="chat",
            voice_session_id="voice", status="accepted", user_text="still running",
            created_at=start + timedelta(seconds=61),
        ))
        db.add(VoiceTurnRecord(
            id="other-session", workspace_id="workspace", chat_session_id="chat",
            voice_session_id="other", status="finalized", user_text="unrelated",
            finalized_at=start + timedelta(seconds=62),
        ))
        db.commit()
    yield sessions
    engine.dispose()


@pytest.mark.parametrize("limit", [40, 50])
def test_recent_window_queries_newest_turns_in_chronological_order(turn_database, limit):
    rows = turns.list_turns("voice", limit=limit)
    assert [row["turn_id"] for row in rows] == [
        f"turn-{number:02}" for number in range(61 - limit, 61)
    ]
    assert {row["status"] for row in rows} == {"finalized", "interrupted", "failed"}


def test_recent_window_can_append_the_open_turn(turn_database):
    rows = turns.list_turns("voice", limit=40, include_open=True)
    assert len(rows) == 41
    assert rows[0]["turn_id"] == "turn-21"
    assert rows[-2]["turn_id"] == "turn-60"
    assert rows[-1]["turn_id"] == "open-turn"


def test_reconnect_uses_latest_database_turns_with_a_bounded_snapshot(turn_database):
    handle = SimpleNamespace(
        workspace_id="workspace",
        chat_session_id="chat",
        context_snapshot={
            "history": [{"role": "user", "content": "obsolete snapshot question"}]
        },
    )
    state = VoiceContextAdapter(
        "voice", "system", session_loader=lambda _: handle,
        turn_loader=turns.list_turns,
    ).load()
    messages = list(state.messages)
    assert len(messages) == 40
    assert messages[0] == {"role": "system", "content": "system"}
    assert messages[-2:] == [
        {"role": "user", "content": "question-60"},
        {"role": "assistant", "content": "answer-60"},
    ]
    assert not any("obsolete" in item["content"] for item in messages)
    assert not any("question-21" == item["content"] for item in messages)
