"""Tests for backend persistence of interactive multiple-choice / quiz part ui_state."""
from __future__ import annotations

import uuid

from app.core.database import SessionLocal
from app.domain.models import Message, MessagePartRecord, MessageVersion


def _seed_interactive_message(
    workspace_id: str,
    session_id: str,
    *,
    parts: list[dict],
) -> tuple[str, list[str]]:
    message_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    part_ids: list[str] = []
    snapshot_parts: list[dict] = []

    with SessionLocal() as db:
        for ordinal, spec in enumerate(parts):
            pid = spec.get("id") or str(uuid.uuid4())
            part_ids.append(pid)
            snapshot_parts.append(
                {
                    "id": pid,
                    "type": spec["part_type"],
                    "status": "completed",
                    "content": spec.get("content", ""),
                    "data": spec.get("data", {}),
                    "sequence": ordinal,
                }
            )

        message = Message(
            id=message_id,
            workspace_id=workspace_id,
            session_id=session_id,
            role="assistant",
            version=1,
            status="completed",
            content="练习题如下",
            parts=snapshot_parts,
        )
        version = MessageVersion(
            id=version_id,
            workspace_id=workspace_id,
            message_id=message_id,
            version=1,
            status="completed",
        )
        db.add(message)
        db.add(version)
        db.flush()

        for ordinal, (pid, spec) in enumerate(zip(part_ids, parts)):
            db.add(
                MessagePartRecord(
                    id=pid,
                    workspace_id=workspace_id,
                    message_version_id=version_id,
                    ordinal=ordinal,
                    part_type=spec["part_type"],
                    status="completed",
                    content=spec.get("content", ""),
                    data=spec.get("data", {}),
                )
            )
        db.commit()
    return message_id, part_ids


def test_put_part_ui_state_persists_across_messages_and_snapshot_reads(
    client, register_user, auth_headers
):
    token, ws, _, _ = register_user("ui_state_ok")
    headers = auth_headers(token, ws)
    session_resp = client.post(
        "/api/v1/sessions", headers=headers, json={"title": "choice persistence"}
    )
    assert session_resp.status_code == 201, session_resp.text
    sid = session_resp.json()["id"]

    msg_id, [quiz_id, choice_id, batch_id] = _seed_interactive_message(
        ws,
        sid,
        parts=[
            {
                "part_type": "quiz",
                "data": {
                    "prompt": "TCP 握手第一步？",
                    "options": [{"id": "a", "label": "SYN", "is_correct": True}],
                },
            },
            {
                "part_type": "component",
                "data": {
                    "component_type": "single_choice",
                    "schema_version": "1.0",
                    "props": {
                        "title": "选择语言",
                        "options": [{"id": "py", "label": "Python"}],
                    },
                },
            },
            {
                "part_type": "component",
                "data": {
                    "component_type": "question_batch",
                    "schema_version": "1.0",
                    "props": {
                        "title": "综合测验",
                        "questions": [],
                    },
                },
            },
        ],
    )

    quiz_state = {"answer": "a", "submitted": True}
    put_quiz = client.put(
        f"/api/v1/sessions/{sid}/parts/{quiz_id}/ui-state",
        headers=headers,
        json={"ui_state": quiz_state},
    )
    assert put_quiz.status_code == 200, put_quiz.text
    assert put_quiz.json() == {
        "part_id": quiz_id,
        "session_id": sid,
        "message_id": msg_id,
        "ui_state": quiz_state,
    }

    choice_state = {"values": ["py"], "customValue": "", "submitted": True}
    put_choice = client.put(
        f"/api/v1/sessions/{sid}/parts/{choice_id}/ui-state",
        headers=headers,
        json={"ui_state": choice_state},
    )
    assert put_choice.status_code == 200, put_choice.text

    batch_state = {
        "activeQuestionIndex": 1,
        "batchAnswers": {"q1": {"values": ["py"], "labels": ["Python"]}},
        "batchDrafts": {"q1": ["py"]},
        "batchSubmitted": True,
    }
    put_batch = client.put(
        f"/api/v1/sessions/{sid}/parts/{batch_id}/ui-state",
        headers=headers,
        json={"ui_state": batch_state},
    )
    assert put_batch.status_code == 200, put_batch.text

    # Re-fetch session messages (cache-busted new read) and message snapshot.
    msgs_resp = client.get(f"/api/v1/sessions/{sid}/messages", headers=headers)
    assert msgs_resp.status_code == 200, msgs_resp.text
    fetched_msg = next(m for m in msgs_resp.json()["items"] if m["id"] == msg_id)
    parts_by_id = {p["id"]: p for p in fetched_msg["parts"]}
    assert parts_by_id[quiz_id]["data"]["ui_state"] == quiz_state
    assert parts_by_id[choice_id]["data"]["ui_state"] == choice_state
    assert parts_by_id[batch_id]["data"]["ui_state"] == batch_state

    snap_resp = client.get(
        f"/api/v1/sessions/{sid}/messages/{msg_id}", headers=headers
    )
    assert snap_resp.status_code == 200, snap_resp.text
    snap_by_id = {p["id"]: p for p in snap_resp.json()["parts"]}
    assert snap_by_id[quiz_id]["data"]["ui_state"] == quiz_state
    assert snap_by_id[choice_id]["data"]["ui_state"] == choice_state
    assert snap_by_id[batch_id]["data"]["ui_state"] == batch_state


def test_put_part_ui_state_rejects_unsupported_invalid_and_foreign_parts(
    client, register_user, auth_headers
):
    token_a, ws_a, _, _ = register_user("ui_state_a")
    headers_a = auth_headers(token_a, ws_a)
    sid_a = client.post(
        "/api/v1/sessions", headers=headers_a, json={"title": "session a"}
    ).json()["id"]
    sid_other = client.post(
        "/api/v1/sessions", headers=headers_a, json={"title": "session other"}
    ).json()["id"]

    _, [text_id, choice_id] = _seed_interactive_message(
        ws_a,
        sid_a,
        parts=[
            {"part_type": "text", "content": "plain markdown", "data": {}},
            {
                "part_type": "component",
                "data": {
                    "component_type": "multiple_choice",
                    "schema_version": "1.0",
                    "props": {"title": "多选题", "options": []},
                },
            },
        ],
    )

    # Unsupported part type -> 409
    r_text = client.put(
        f"/api/v1/sessions/{sid_a}/parts/{text_id}/ui-state",
        headers=headers_a,
        json={"ui_state": {"answer": "x"}},
    )
    assert r_text.status_code == 409, r_text.text

    # Part does not belong to sid_other -> 404
    r_wrong_session = client.put(
        f"/api/v1/sessions/{sid_other}/parts/{choice_id}/ui-state",
        headers=headers_a,
        json={"ui_state": {"values": ["x"]}},
    )
    assert r_wrong_session.status_code == 404, r_wrong_session.text

    # Excessive nesting depth -> 422
    deep: dict = {"leaf": 1}
    for _ in range(12):
        deep = {"nested": deep}
    r_deep = client.put(
        f"/api/v1/sessions/{sid_a}/parts/{choice_id}/ui-state",
        headers=headers_a,
        json={"ui_state": deep},
    )
    assert r_deep.status_code == 422, r_deep.text

    # Cross-workspace isolation -> 404
    token_b, ws_b, _, _ = register_user("ui_state_b")
    headers_b = auth_headers(token_b, ws_b)
    r_foreign = client.put(
        f"/api/v1/sessions/{sid_a}/parts/{choice_id}/ui-state",
        headers=headers_b,
        json={"ui_state": {"values": ["x"]}},
    )
    assert r_foreign.status_code == 404, r_foreign.text


def test_put_part_ui_state_allows_branched_session_ancestor_parts(
    client, register_user, auth_headers
):
    token, ws, _, _ = register_user("ui_state_branch")
    headers = auth_headers(token, ws)
    parent_sid = client.post(
        "/api/v1/sessions", headers=headers, json={"title": "parent"}
    ).json()["id"]

    msg_1, [quiz_id] = _seed_interactive_message(
        ws,
        parent_sid,
        parts=[
            {
                "part_type": "quiz",
                "data": {
                    "prompt": "分支前的题目",
                    "options": [{"id": "opt1", "label": "答案1", "is_correct": True}],
                },
            }
        ],
    )
    msg_2, _ = _seed_interactive_message(
        ws,
        parent_sid,
        parts=[{"part_type": "text", "content": "后续消息", "data": {}}],
    )

    branch_resp = client.post(
        f"/api/v1/sessions/{parent_sid}/messages/{msg_2}/branch",
        headers=headers,
        json={},
    )
    assert branch_resp.status_code in (200, 201), branch_resp.text
    child_sid = branch_resp.json()["id"]

    r = client.put(
        f"/api/v1/sessions/{child_sid}/parts/{quiz_id}/ui-state",
        headers=headers,
        json={"ui_state": {"answer": "opt1", "submitted": True}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["message_id"] == msg_1
    assert r.json()["ui_state"] == {"answer": "opt1", "submitted": True}
