"""Transactional row journal for the one-way main-database migration.

Triggers capture Core SQL and cascades as well as ORM flushes. Committed
transaction boundaries live in the same SQLite transaction as the row images.
The source fence lives in SQLite, so an old process cannot resume writes after
activation. No JSON conversion is applied to TEXT values (including legacy JSON).
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Engine


PREFIX = "_lg_"


def quoted(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


class SQLiteCapture:
    def __init__(self, engine: Engine):
        self.engine = engine
        self.enabled = False
        if engine.url.database and engine.url.database != ":memory:":
            path = Path(engine.url.database).resolve()
            if path.is_file():
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                    self.enabled = bool(connection.execute("SELECT 1 FROM sqlite_master WHERE name='_lg_capture_commits'").fetchone())
        event.listen(engine, "before_cursor_execute", self._before)
        event.listen(engine, "commit", self._commit)
        event.listen(engine, "rollback", self._rollback)

    def _before(self, connection, _cursor, _statement, _parameters, _context, _many):
        if "_lg_capture_holder" not in connection.info:
            holder: dict[str, str | None] = {"tx": None}
            def transaction_id():
                if holder["tx"] is None:
                    holder["tx"] = uuid.uuid4().hex
                return holder["tx"]
            connection.connection.driver_connection.create_function("lg_migration_txid", 0, transaction_id)
            connection.connection.driver_connection.execute("PRAGMA recursive_triggers=ON")
            connection.info["_lg_capture_holder"] = holder

    def _commit(self, connection):
        holder = connection.info.get("_lg_capture_holder")
        if holder and holder["tx"]:
            cursor = connection.connection.driver_connection.cursor()
            try:
                cursor.execute("INSERT INTO _lg_capture_commits(txid) VALUES (?)", (holder["tx"],))
            finally:
                cursor.close()
                holder["tx"] = None

    def _rollback(self, connection):
        holder = connection.info.get("_lg_capture_holder")
        if holder:
            holder["tx"] = None

    @staticmethod
    def row_image(columns: list[str], alias: str) -> str:
        objects = []
        for offset in range(0, len(columns), 40):
            items = []
            for name in columns[offset:offset + 40]:
                value = f"{alias}.{quoted(name)}"
                items.extend(["'" + name.replace("'", "''") + "'", f"CASE WHEN typeof({value})='blob' THEN json_object('$lg_blob',hex({value})) ELSE {value} END"])
            objects.append("json_object(" + ",".join(items) + ")")
        if len(objects) == 1:
            return objects[0]
        # JSON Merge Patch removes keys whose values are NULL. Concatenating
        # complete object interiors preserves SQL NULL and stays under SQLite's
        # per-function argument limit for wide tables.
        interiors = " || ',' || ".join(f"substr({item},2,length({item})-2)" for item in objects)
        return f"json('{{' || {interiors} || '}}')"

    def install(self, source_path: Path, tables: dict[str, list[str]], *, job_id: str | None = None) -> None:
        self.enabled = True
        with closing(sqlite3.connect(source_path, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("CREATE TABLE IF NOT EXISTS _lg_database_fence(singleton INTEGER PRIMARY KEY CHECK(singleton=1), sealed INTEGER NOT NULL DEFAULT 0, job_id TEXT)")
            connection.execute("INSERT OR IGNORE INTO _lg_database_fence(singleton,sealed) VALUES(1,0)")
            if connection.execute("SELECT sealed FROM _lg_database_fence WHERE singleton=1").fetchone()[0]:
                raise RuntimeError("SQLite was already migrated; writes remain fenced")
            connection.execute("UPDATE _lg_database_fence SET job_id=? WHERE singleton=1", (job_id,))
            connection.execute("CREATE TABLE IF NOT EXISTS _lg_capture_commits(id INTEGER PRIMARY KEY AUTOINCREMENT, txid TEXT NOT NULL UNIQUE)")
            connection.execute("CREATE TABLE IF NOT EXISTS _lg_capture_rows(id INTEGER PRIMARY KEY AUTOINCREMENT, txid TEXT NOT NULL, table_name TEXT NOT NULL, operation TEXT NOT NULL, old_row TEXT, new_row TEXT)")
            connection.execute("CREATE INDEX IF NOT EXISTS _lg_capture_tx ON _lg_capture_rows(txid,id)")
            for table, columns in tables.items():
                for operation, old, new in [("INSERT", "NULL", self.row_image(columns, "NEW")), ("UPDATE", self.row_image(columns, "OLD"), self.row_image(columns, "NEW")), ("DELETE", self.row_image(columns, "OLD"), "NULL")]:
                    name = quoted(f"_lg_capture_{table}_{operation.lower()}")
                    connection.execute(f"DROP TRIGGER IF EXISTS {name}")
                    connection.execute(f"CREATE TRIGGER {name} AFTER {operation} ON {quoted(table)} BEGIN SELECT CASE WHEN (SELECT sealed FROM _lg_database_fence WHERE singleton=1)=1 THEN RAISE(ABORT,'main_database_migrated_to_postgresql') END; INSERT INTO _lg_capture_rows(txid,table_name,operation,old_row,new_row) VALUES(lg_migration_txid(),'{table.replace(chr(39), chr(39)*2)}','{operation}',{old},{new}); END")

    @staticmethod
    def decode(value: str | None) -> dict[str, Any] | None:
        if value is None:
            return None
        result = json.loads(value)
        return {key: bytes.fromhex(item["$lg_blob"]) if isinstance(item, dict) and set(item) == {"$lg_blob"} else item for key, item in result.items()}

    def cancel(self, source_path: Path, tables: dict[str, list[str]]) -> None:
        with closing(sqlite3.connect(source_path, timeout=5)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT sealed FROM _lg_database_fence WHERE singleton=1").fetchone()
            if row and row[0]:
                raise RuntimeError("PostgreSQL activation is irreversible")
            for table in tables:
                for operation in ("insert", "update", "delete"):
                    connection.execute(f"DROP TRIGGER IF EXISTS {quoted(f'_lg_capture_{table}_{operation}')}")
        self.enabled = False
