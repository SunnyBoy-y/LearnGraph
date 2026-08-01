"""Tests for episode source-reference validation.

Plan §5.4 requires every decision / constraint / open question to cite its
source message/event. These tests pin that contract on the schema layer.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.schemas.memory_tasks import EpisodeGenerateRequest


def _req(**overrides) -> EpisodeGenerateRequest:
    payload = {
        "conversation_id": "conv-1",
        "title": "Episode one",
        "idempotency_key": "ep-src-key-1",
        **overrides,
    }
    return EpisodeGenerateRequest(**payload)


def test_decisions_require_source_ref():
    with pytest.raises(ValidationError):
        _req(decisions=[{"decision": "use sqlite", "rationale": "local dev"}])


def test_constraints_require_source_ref():
    with pytest.raises(ValidationError):
        _req(constraints=[{"constraint": "must not use redis"}])


def test_open_questions_require_source_ref():
    with pytest.raises(ValidationError):
        _req(open_questions=[{"question": "why?"}])


def test_decisions_accept_source_string():
    req = _req(decisions=[{"decision": "use sqlite", "source": "msg-42"}])
    assert req.decisions[0]["source"] == "msg-42"


def test_constraints_accept_source_refs_list():
    req = _req(constraints=[{"constraint": "no redis", "source_refs": ["msg-1", "msg-2"]}])
    assert req.constraints[0]["source_refs"] == ["msg-1", "msg-2"]


def test_open_questions_accept_source_message_refs():
    req = _req(open_questions=[{"question": "why?", "source_message_refs": ["msg-7"]}])
    assert req.open_questions[0]["source_message_refs"] == ["msg-7"]


def test_entities_do_not_require_source():
    # Entities are a bag of named things, not claims; plan does not require a
    # per-entity source.
    req = _req(entities=[{"name": "sqlite", "kind": "tech"}])
    assert len(req.entities) == 1


def test_empty_structured_lists_are_fine():
    req = _req(decisions=[], open_questions=[], constraints=[])
    assert req.decisions == []
