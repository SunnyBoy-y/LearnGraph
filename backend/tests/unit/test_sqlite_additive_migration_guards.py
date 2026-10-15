"""SQLite additive migration guards.

背景：`_apply_sqlite_additive_migrations` 的 `additions` 是一个巨型 dict 字面量。
一旦同一张表被写了两次，Python 会静默用后一个覆盖前一个，新增列就永远不会执行，
直到 `_verify_sqlite_metadata_shape` 在启动时抛错（Practice Session 列就这样丢过一次）。
这里用 ast 直接检查字面量里没有重复表名，并覆盖关键列。
"""

from __future__ import annotations

import ast
from pathlib import Path

DATABASE_MODULE = Path(__file__).resolve().parents[2] / "app" / "core" / "database.py"

PRACTICE_ANSWER_COLUMNS = {
    "practice_session_id",
    "attempt_index",
    "duration_ms",
    "hint_count",
    "score_ratio",
    "evaluation_json",
}


def _additions_literal() -> ast.Dict:
    tree = ast.parse(DATABASE_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "additions"
            for target in node.targets
        ):
            assert isinstance(node.value, ast.Dict)
            return node.value
    raise AssertionError("additions dict literal not found")


def _table_keys(literal: ast.Dict) -> list[str]:
    keys: list[str] = []
    for key in literal.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.append(key.value)
    return keys


def _columns_for(literal: ast.Dict, table: str) -> dict[str, str]:
    for key, value in zip(literal.keys, literal.values, strict=True):
        if isinstance(key, ast.Constant) and key.value == table:
            assert isinstance(value, ast.Dict)
            return {
                column.value: column_def
                for column, column_def in zip(
                    value.keys, value.values, strict=True
                )
                if isinstance(column, ast.Constant)
            }
    raise AssertionError(f"table {table!r} is not in the additions literal")


def test_additions_literal_has_no_duplicate_table_keys():
    literal = _additions_literal()
    keys = _table_keys(literal)
    duplicates = {key for key in keys if keys.count(key) > 1}
    assert not duplicates, f"duplicate table keys silently drop columns: {duplicates}"


def test_answer_records_practice_columns_are_registered():
    literal = _additions_literal()
    columns = _columns_for(literal, "answer_records")
    assert PRACTICE_ANSWER_COLUMNS.issubset(set(columns)), (
        "practice columns missing from answer_records additive migration: "
        f"{PRACTICE_ANSWER_COLUMNS - set(columns)}"
    )
    assert "actor_id" in columns, "legacy answer_records columns must be preserved"


def test_sqlite_metadata_shape_matches_after_init():
    """真实启动路径必须通过列校验（启动失败会直接阻断整个应用）。"""

    from app.core.database import init_database

    init_database()
