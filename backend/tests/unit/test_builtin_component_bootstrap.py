import pytest

from app.core.errors import AppError
from app.services.components import _builtin_specs, _schema_guard


def test_checked_in_builtin_schemas_pass_the_bootstrap_guard():
    for component_id, spec in _builtin_specs().items():
        _schema_guard(spec["data_schema"], label=component_id, trusted_builtin=True)


def test_imported_schemas_still_cannot_supply_regex_even_with_builtin_name():
    with pytest.raises(AppError, match="caller-supplied regular expressions"):
        _schema_guard({"type": "object", "additionalProperties": False,
                       "properties": {"value": {"type": "string", "pattern": "(a+)+$"}}}, label="code_trainer")
