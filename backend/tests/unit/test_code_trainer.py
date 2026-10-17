import pytest
from app.core.errors import AppError
from app.services.canvas_cards import CHANNEL_A_TYPES, build_trusted_component_part
from app.services.components import BUILTIN_COMPONENT_IDS, _builtin_specs


def test_code_trainer_is_registered_and_preserves_inert_code():
    assert "code_trainer" in CHANNEL_A_TYPES & BUILTIN_COMPONENT_IDS
    code = 'console.log("</script><script>alert(1)</script>")'
    part = build_trusted_component_part(component_type="code_trainer", props={
        "title": "安全编辑代码", "language": "javascript", "starter_code": code,
        "tests": [{"name": "样例", "stdin": "", "expected_stdout": "<script>"}],
    })
    assert part["data"]["props"]["starter_code"] == code
    assert part["data"]["allowed_events"] == ["submit"]


@pytest.mark.parametrize("props", [
    {"title": "x", "starter_code": "a" * 16001},
    {"title": "x", "tests": [{"name": "x", "stdin": "", "expected_stdout": ""}] * 13},
    {"title": "x", "time_limit_ms": 5001},
    {"title": "x", "runner_url": "https://example.com"},
    {"title": "x", "tests": [{"name": "x", "stdin": ""}]},
])
def test_invalid_trainer_props_rejected(props):
    with pytest.raises(AppError):
        build_trusted_component_part(component_type="code_trainer", props=props)


@pytest.mark.parametrize("language", ["python", "javascript", "typescript", "java", "go", "cpp", "rust"])
def test_contract_accepts_ai_only_languages_without_executing(language):
    part = build_trusted_component_part(component_type="code_trainer", props={"title": "练习", "language": language})
    assert part["type"] == "component"
    assert part["data"]["props"]["language"] == language


def test_registered_example_is_valid():
    build_trusted_component_part(component_type="code_trainer", props=_builtin_specs()["code_trainer"]["example_data"])


def test_other_components_still_reject_executable_markup():
    with pytest.raises(AppError):
        build_trusted_component_part(component_type="fill_blank", props={"title": "<script>alert(1)</script>"})
