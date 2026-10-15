"""追问提示锚点闸回归测试。

冻结契约：一轮追问提示只能挂在「文本输入框回合」的完成回答上。

* 空会话：GET/POST 都回 204，一行 ``suggested_prompt_batches`` 都不写（新会话开场
  问题来自工作区记忆，属于客户端本地内容）；历史遗留的空会话批次也不得再被读出。
* 语音回合：同上——通话进行中与挂断之后都不出现、也不生成。
* 文本回合：锚点解析与既有 409 语义不变（回归保护）。

锚点解析用服务层直调断言（不起 provider、不花模型调用），HTTP 层只断言状态码与
"零写入"。
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.database import SessionLocal
from app.domain.models import (
    ChatSession,
    Message,
    MessageVersion,
    SuggestedPromptBatch,
    utc_now,
)
from app.services.chat import ChatService


class _StubProvider:
    """只满足构造期读取的桩：本文件的断言不走到任何模型调用。"""

    available = True
    provider_id = "stub-provider"
    model_id = "stub-model"
    remote_capability = True
    thinking_mode = "off"


VOICE_TRACE = {"voice": True, "voice_turn": True, "voice_turn_id": "turn-1"}
TEXT_TRACE = {"request_id": "request-1"}


# --------------------------------------------------------------------- fixtures


@pytest.fixture()
def db_workspace_session(client) -> tuple[str, str]:
    """直接落库的工作区 + 会话：服务层断言不需要 HTTP 与 provider 装配。

    依赖 ``client`` 只是为了 app lifespan 先把临时库的 schema 建起来（本文件的
    服务层断言本身不发请求）。
    """
    workspace_id = f"ws-{uuid.uuid4().hex[:12]}"
    with SessionLocal() as db:
        session = ChatSession(workspace_id=workspace_id, title="anchor gate")
        db.add(session)
        db.commit()
        session_id = session.id
    return workspace_id, session_id


# --------------------------------------------------------------------- helpers


def _workspace_and_headers(register_user, auth_headers, prefix: str):
    token, workspace_id, _username, _password = register_user(prefix)
    return workspace_id, auth_headers(token, workspace_id)


def _make_session(client, headers, title: str = "anchor gate") -> str:
    resp = client.post("/api/v1/sessions", headers=headers, json={"title": title})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _write_turn(
    workspace_id: str,
    session_id: str,
    *,
    trace: dict,
    assistant_status: str = "completed",
) -> tuple[str, str, str]:
    """写一对 user/assistant 消息 + 完成的 MessageVersion，返回 (user, assistant, version)。

    ``created_at`` 显式错开：时间线按 created_at 排序，同刻写入会让"最后一条是谁"
    变成未定义行为，测试就会时红时绿。
    """
    user_id, assistant_id, version_id = (str(uuid.uuid4()) for _ in range(3))
    base = utc_now()
    with SessionLocal() as db:
        user = Message(
            id=user_id,
            workspace_id=workspace_id,
            session_id=session_id,
            role="user",
            status="completed",
            content="问题",
            parts=[],
            provider_trace=dict(trace),
            created_at=base,
        )
        assistant = Message(
            id=assistant_id,
            workspace_id=workspace_id,
            session_id=session_id,
            parent_message_id=user_id,
            role="assistant",
            status=assistant_status,
            content="回答",
            parts=[],
            provider_trace=dict(trace),
            created_at=base + timedelta(seconds=1),
        )
        db.add_all([user, assistant])
        db.flush()
        db.add(
            MessageVersion(
                id=version_id,
                workspace_id=workspace_id,
                message_id=assistant_id,
                version=1,
                status="completed",
            )
        )
        db.commit()
    return user_id, assistant_id, version_id


def _write_legacy_batch(
    workspace_id: str,
    session_id: str,
    *,
    anchor_message_id: str | None = None,
    anchor_message_version_id: str | None = None,
) -> None:
    """造一行"改动之前就存在"的批次，验证闸门在读取之前就短路。"""
    with SessionLocal() as db:
        db.add(
            SuggestedPromptBatch(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                session_id=session_id,
                anchor_message_id=anchor_message_id,
                anchor_message_version_id=anchor_message_version_id,
                context_hash="legacy-context-hash",
                generation_key=str(uuid.uuid4()),
                source_message_ids=[],
                prompt_count=1,
                prompts=[{"id": "legacy", "content": "旧批次问题"}],
                provider_trace={},
            )
        )
        db.commit()


def _batch_count(session_id: str) -> int:
    with SessionLocal() as db:
        return (
            db.query(SuggestedPromptBatch)
            .filter(SuggestedPromptBatch.session_id == session_id)
            .count()
        )


def _anchor(workspace_id: str, session_id: str):
    """返回 (时间线末条角色, 锚点解析三元组)。

    末条角色先自证，免得排序意外把断言变成"恰好通过"。
    """
    with SessionLocal() as db:
        service = ChatService(db, workspace_id, "tester", _StubProvider())
        timeline = service._session_timeline(session_id)
        last_role = timeline[-1].role if timeline else None
        return last_role, service._suggested_prompt_anchor(timeline)


# --------------------------------------------------------------- 服务层：锚点解析


def test_anchor_refuses_an_empty_timeline(db_workspace_session):
    workspace_id, session_id = db_workspace_session
    # 空会话：刻意不写任何消息。
    last_role, (message, version, blocked) = _anchor(workspace_id, session_id)
    assert last_role is None
    assert (message, version) == (None, None)
    assert blocked == "empty"


def test_anchor_refuses_a_voice_answer_as_the_anchor(db_workspace_session):
    workspace_id, session_id = db_workspace_session
    _write_turn(workspace_id, session_id, trace=VOICE_TRACE)
    last_role, (message, version, blocked) = _anchor(workspace_id, session_id)
    assert last_role == "assistant"
    assert (message, version) == (None, None)
    assert blocked == "voice"


def test_anchor_refuses_a_voice_answer_spelled_with_only_the_legacy_key(
    db_workspace_session,
):
    """落库是双拼写；只带 ``voice`` 的旧行同样要被拦住。"""
    workspace_id, session_id = db_workspace_session
    _write_turn(workspace_id, session_id, trace={"voice": True})
    last_role, (message, version, blocked) = _anchor(workspace_id, session_id)
    assert last_role == "assistant"
    assert (message, version) == (None, None)
    assert blocked == "voice"


def test_anchor_still_resolves_a_text_turn(db_workspace_session):
    workspace_id, session_id = db_workspace_session
    _user, assistant_id, version_id = _write_turn(
        workspace_id, session_id, trace=TEXT_TRACE
    )
    last_role, (message, version, blocked) = _anchor(workspace_id, session_id)
    assert last_role == "assistant"
    assert blocked is None
    assert message is not None and message.id == assistant_id
    assert version is not None and version.id == version_id


def test_anchor_keeps_the_unavailable_semantics_for_an_unfinished_answer(
    db_workspace_session,
):
    """失败的/还在流式的回答不是"结构性拒绝"：仍走既有 409 语义。"""
    workspace_id, session_id = db_workspace_session
    _write_turn(workspace_id, session_id, trace=TEXT_TRACE, assistant_status="streaming")
    last_role, (message, version, blocked) = _anchor(workspace_id, session_id)
    assert last_role == "assistant"
    assert (message, version) == (None, None)
    assert blocked is None


# ------------------------------------------------------------------- HTTP 层：零写入


def test_empty_session_answers_204_and_writes_nothing(
    client, register_user, auth_headers, stub_chat_service
):
    _workspace, headers = _workspace_and_headers(register_user, auth_headers, "sp_empty")
    session_id = _make_session(client, headers)

    get_resp = client.get(
        f"/api/v1/sessions/{session_id}/suggested-prompts", headers=headers
    )
    assert get_resp.status_code == 204, get_resp.text
    post_resp = client.post(
        f"/api/v1/sessions/{session_id}/suggested-prompts",
        headers=headers,
        json={"count": 2},
    )
    assert post_resp.status_code == 204, post_resp.text
    assert _batch_count(session_id) == 0


def test_empty_session_does_not_resurface_a_legacy_batch(
    client, register_user, auth_headers, stub_chat_service
):
    workspace_id, headers = _workspace_and_headers(register_user, auth_headers, "sp_legacy")
    session_id = _make_session(client, headers)
    _write_legacy_batch(workspace_id, session_id)

    get_resp = client.get(
        f"/api/v1/sessions/{session_id}/suggested-prompts", headers=headers
    )
    assert get_resp.status_code == 204, get_resp.text


def test_openapi_documents_the_204_no_batch_answer(client):
    """204 是契约的一部分，不是实现细节：文档缺失会让客户端把它当异常处理。"""
    schema = client.get("/openapi.json").json()
    responses = schema["paths"]["/api/v1/sessions/{session_id}/suggested-prompts"]["post"][
        "responses"
    ]
    assert "204" in responses


def test_voice_turn_answers_204_and_writes_nothing(
    client, register_user, auth_headers, stub_chat_service
):
    workspace_id, headers = _workspace_and_headers(register_user, auth_headers, "sp_voice")
    session_id = _make_session(client, headers)
    _user, assistant_id, version_id = _write_turn(
        workspace_id, session_id, trace=VOICE_TRACE
    )
    # 遗留批次精确指向这条语音回答：闸门必须在读取之前短路，否则它会被读出来。
    _write_legacy_batch(
        workspace_id,
        session_id,
        anchor_message_id=assistant_id,
        anchor_message_version_id=version_id,
    )

    get_resp = client.get(
        f"/api/v1/sessions/{session_id}/suggested-prompts", headers=headers
    )
    assert get_resp.status_code == 204, get_resp.text
    post_resp = client.post(
        f"/api/v1/sessions/{session_id}/suggested-prompts",
        headers=headers,
        json={
            "count": 2,
            "anchor_message_id": assistant_id,
            "anchor_message_version_id": version_id,
        },
    )
    assert post_resp.status_code == 204, post_resp.text
    assert _batch_count(session_id) == 1  # 只有那行遗留批次，没有任何新增
