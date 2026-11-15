"""Backend-only exact-message links, using the existing isolated memory fixture."""
import json
from unittest.mock import MagicMock

import pytest

from test_memory_batch_deletion import service  # noqa: F401 -- shared memory fixture
from app.core.errors import AppError
from app.domain import models as m
from app.services.agent_runtime import AgentToolRuntime
from app.services.agent_tool_batch import ToolExecutionClass, tool_execution_class


@pytest.fixture
def target(service):
    session = m.ChatSession(id="prior-session", workspace_id="w", title="Prior [discussion]")
    message = m.Message(id="exact-message", workspace_id="w", session_id=session.id,
                        role="assistant", content="An exact\nanswer " + "x" * 500)
    service.db.add_all([session, message])
    service.db.commit()
    return session, message


@pytest.fixture
def runtime(service):
    extensions = MagicMock(db=service.db)
    extensions.agent_tool_definitions.return_value = []
    extensions._authorized_packages.return_value = []
    return AgentToolRuntime(workspace_id="w", actor_id="u", search_provider=None,
                            extensions=extensions, sandbox=None,
                            sandbox_authorized=False, memory_tools=service)


def invoke(runtime, arguments):
    return runtime.execute(
        {"id": "link-call", "function": {"name": "create_conversation_link",
                                           "arguments": json.dumps(arguments)}},
        allowed_domains=[], chat_session_id="current-session",
    )


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_exact_schema_and_readonly_link(service, target, role):
    session, message = target
    message.role = role
    service.db.commit()
    result = service.create_conversation_link(session_id=session.id, message_id=message.id)
    url = "/w/w/chat/prior-session?message=exact-message"
    assert result == {
        "session_id": session.id, "message_id": message.id,
        "title": session.title, "excerpt": ("An exact answer " + "x" * 500)[:400],
        "url": url, "markdown_link": f"[Prior \\[discussion\\]]({url})",
        "instruction": "Include markdown_link verbatim in your final answer so the user can click "
                       "to open this exact message. Do not navigate automatically.",
    }
    assert not service.db.new and not service.db.dirty and not service.db.deleted


def test_custom_label_and_encoded_identifiers(service):
    session = m.ChatSession(id="session/?#", workspace_id="w", title="Title")
    message = m.Message(id="message&=/#", workspace_id="w", session_id=session.id,
                        role="user", content="hello")
    service.db.add_all([session, message])
    service.db.commit()
    result = service.create_conversation_link(session_id=session.id, message_id=message.id,
                                               label="Open ](https://evil.test)\nnow")
    assert result["url"] == "/w/w/chat/session%2F%3F%23?message=message%26%3D%2F%23"
    assert result["markdown_link"] == (
        "[Open \\]\\(https://evil.test\\) now](" + result["url"] + ")"
    )


@pytest.mark.parametrize("field,value", [
    ("session_id", ""), ("session_id", "  "), ("session_id", None),
    ("message_id", ""), ("message_id", "\n"), ("message_id", 1),
    ("label", ""), ("label", "  "), ("label", []),
])
def test_invalid_service_arguments(service, target, field, value):
    arguments = {"session_id": target[0].id, "message_id": target[1].id, field: value}
    with pytest.raises(AppError) as error:
        service.create_conversation_link(**arguments)
    assert error.value.status_code == 422


@pytest.mark.parametrize("case", [
    "missing_session", "missing_message", "other_workspace_session", "other_workspace_message",
    "mismatched_session", "deleted_session", "deleted_message", "removed_session", "removed_message",
])
def test_invalid_or_deleted_target_is_not_linked(service, target, case):
    session, message = target
    arguments = {"session_id": session.id, "message_id": message.id}
    if case == "missing_session":
        arguments["session_id"] = "missing"
    elif case == "missing_message":
        arguments["message_id"] = "missing"
    elif case == "other_workspace_session":
        session.workspace_id = "other"
    elif case == "other_workspace_message":
        message.workspace_id = "other"
    elif case == "mismatched_session":
        other = m.ChatSession(id="other-session", workspace_id="w", title="Other")
        service.db.add(other)
        message.session_id = other.id
    elif case == "deleted_session":
        session.status = "deleted"
    elif case == "deleted_message":
        message.status = "deleted"
    elif case == "removed_session":
        service.db.delete(message)
        service.db.delete(session)
    elif case == "removed_message":
        service.db.delete(message)
    service.db.commit()
    with pytest.raises(AppError) as error:
        service.create_conversation_link(**arguments)
    assert error.value.status_code == 404


@pytest.mark.parametrize("role,status", [
    ("system", "completed"), ("tool", "completed"),
    ("assistant", "pending"), ("assistant", "streaming"),
    ("assistant", "failed"), ("assistant", "cancelled"),
    ("user", "pending"),
])
def test_nonvisible_or_unfinished_message_rejected(service, target, role, status):
    session, message = target
    message.role, message.status = role, status
    service.db.commit()
    with pytest.raises(AppError) as error:
        service.create_conversation_link(session_id=session.id, message_id=message.id)
    assert error.value.status_code == 404


def test_workspace_permission_required(service, target):
    service.actor_id = "outsider"
    with pytest.raises(AppError) as error:
        service.create_conversation_link(session_id=target[0].id, message_id=target[1].id)
    assert error.value.status_code == 404


@pytest.mark.parametrize("resource_type", ["session", "project"])
def test_resource_acl_and_binding_authorization(service, target, resource_type):
    session, message = target
    db = service.db
    service.workspace.organization_id = "org"
    service.workspace.workspace_kind = "organization"
    db.add_all([
        m.Organization(id="org", tenant_id="t", owner_user_id="u", name="Org"),
        m.Role(id="reader", tenant_id="t", organization_id="org", name="Reader"),
        m.Permission(id="read-workspace", key="workspace.read"),
        m.RolePermission(role_id="reader", permission_id="read-workspace"),
        m.Membership(tenant_id="t", organization_id="org", user_id="member", role_id="reader"),
    ])
    resource_id = session.id
    if resource_type == "project":
        project = m.Project(id="project", workspace_id="w", title="Private")
        db.add(project)
        session.project_id = resource_id = project.id
    grant = m.ResourceACL(workspace_id="w", tenant_id="t", resource_type=resource_type,
                          resource_id=resource_id, grantee_type="user", grantee_id="someone-else",
                          permissions=["read"], granted_by="u")
    db.add(grant)
    db.commit()
    service.actor_id = "member"
    with pytest.raises(AppError) as error:
        service.create_conversation_link(session_id=session.id, message_id=message.id)
    assert error.value.status_code == 404
    grant.grantee_id = "member"
    db.commit()
    assert service.create_conversation_link(session_id=session.id, message_id=message.id)["message_id"] == message.id
    grant.permissions = ["write"]
    db.commit()
    with pytest.raises(AppError):
        service.create_conversation_link(session_id=session.id, message_id=message.id)


def test_full_runtime_dispatch(runtime, target):
    content, meta, sources = invoke(runtime, {"session_id": target[0].id,
                                             "message_id": target[1].id, "label": "View answer"})
    result = json.loads(content)
    assert result["markdown_link"] == "[View answer](/w/w/chat/prior-session?message=exact-message)"
    assert meta["status"] == "completed" and not meta["result_truncated"]
    assert meta["session_id"] == target[0].id and meta["message_id"] == target[1].id
    assert sources == []


@pytest.mark.parametrize("arguments", [
    {}, {"session_id": "prior-session"}, {"message_id": "exact-message"},
    {"session_id": " ", "message_id": "exact-message"},
    {"session_id": "prior-session", "message_id": 2},
    {"session_id": "prior-session", "message_id": "exact-message", "label": None},
])
def test_dispatch_rejects_missing_invalid_arguments(runtime, arguments):
    content, meta, _ = invoke(runtime, arguments)
    assert meta["status"] == "failed"
    assert json.loads(content)["error"] == "invalid_tool_arguments"


@pytest.mark.parametrize("memory_enabled,available,agent_mode", [
    (True, True, True), (False, True, True), (True, False, True), (True, True, False),
])
@pytest.mark.parametrize("progressive", [False, True])
def test_registration_and_catalog_compaction(runtime, memory_enabled, available, agent_mode, progressive):
    if not available:
        runtime.memory_tools = None
    definitions = runtime.definitions(
        agent_mode_enabled=agent_mode, web_search_enabled=False, memory_enabled=memory_enabled,
        **({"capability_families": set(), "activated_capabilities": set()} if progressive else {}),
    )
    functions = {item["function"]["name"]: item["function"] for item in definitions}
    assert ("create_conversation_link" in functions) == (memory_enabled and available and agent_mode)
    if "create_conversation_link" in functions:
        schema = functions["create_conversation_link"]["parameters"]
        assert schema["required"] == ["session_id", "message_id"]
        assert set(schema["properties"]) == {"session_id", "message_id", "label"}
        assert schema["additionalProperties"] is False
        for name in ("search_conversation_history", "read_conversation_segment"):
            assert "create_conversation_link" in functions[name]["description"]


def test_batch_classifies_link_as_database_read():
    assert tool_execution_class("create_conversation_link") == ToolExecutionClass.DATABASE_READ
