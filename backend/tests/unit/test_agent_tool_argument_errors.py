import pytest

from app.core.errors import AppError
from app.services.agent_runtime import AgentToolRuntime


def test_ui_json_error_reports_location_and_safe_retry_revision():
    with pytest.raises(AppError) as caught:
        AgentToolRuntime._parse_arguments('{"op":"patch",}', tool_name="ui_stream")
    error = caught.value
    assert error.code == "invalid_tool_arguments"
    assert error.details["line"] == 1 and error.details["column"] > 0
    assert "last successful result" in error.message
    assert "sandbox" not in error.message


def test_publish_json_error_retains_its_own_contract_guidance():
    with pytest.raises(AppError) as caught:
        AgentToolRuntime._parse_arguments('{', tool_name="sandbox_publish_web_app")
    assert "contract_path" in caught.value.message
    assert "ui_stream" not in caught.value.message


def test_other_tool_json_error_has_no_unrelated_tool_instructions():
    with pytest.raises(AppError) as caught:
        AgentToolRuntime._parse_arguments('{', tool_name="search_web")
    assert "sandbox" not in caught.value.message
    assert "ui_stream" not in caught.value.message


def test_valid_ui_json_is_preserved_and_never_repaired_into_an_operation():
    assert AgentToolRuntime._parse_arguments('{"op":"patch","text":"a\\nb"}', tool_name="ui_stream") == {
        "op": "patch", "text": "a\nb",
    }
    with pytest.raises(AppError):
        AgentToolRuntime._parse_arguments('[]', tool_name="ui_stream")
