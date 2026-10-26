"""Native PostgreSQL snapshots and a checked, single-transaction restore.

Credentials travel through a private libpq passfile, never process arguments.
The application role needs no superuser or CREATEDB privilege.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory, TemporaryFile

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.core.database_routing import postgres_engine, startup_url
from app.core.errors import AppError

REQUIRED = {"users", "workspaces", "provider_configs", "provider_secrets", "auth_sessions", "files"}


def effective_url(settings) -> str:
    return startup_url(settings)


def database_bytes(url: str) -> int:
    engine = postgres_engine(url)
    try:
        with engine.connect() as connection:
            return int(connection.exec_driver_sql("SELECT pg_database_size(current_database())").scalar_one())
    finally:
        engine.dispose()


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@contextmanager
def client_environment(url: str):
    parsed = make_url(url)
    with TemporaryDirectory(prefix="lg-libpq-") as temporary:
        password = Path(temporary) / "pgpass"
        def escape(value):
            return str(value or "").replace("\\", "\\\\").replace(":", "\\:")
        password.write_text(":".join(escape(value) for value in (parsed.host or "localhost", parsed.port or 5432, parsed.database, parsed.username, parsed.password)) + "\n", encoding="utf-8")
        password.chmod(0o600)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("PG")}
        environment.update(PGHOST=parsed.host or "localhost", PGPORT=str(parsed.port or 5432), PGDATABASE=parsed.database or "", PGUSER=parsed.username or "", PGPASSFILE=str(password), PGCONNECT_TIMEOUT="10", PGCLIENTENCODING="UTF8")
        for key in ("sslmode", "sslrootcert", "sslcert", "sslkey", "options"):
            if key in parsed.query:
                environment["PG" + key.upper()] = str(parsed.query[key])
        yield environment


def run_tool(tool: str, args: list[str], *, environment=None, timeout=900) -> bytes:
    executable = shutil.which(tool)
    if not executable:
        raise AppError(503, "postgresql_backup_tools_missing", "当前运行环境缺少 PostgreSQL 18 备份恢复客户端，请使用包含客户端的应用镜像")
    try:
        # Only --list has useful stdout. Never accumulate psql output or tool
        # diagnostics (which may include large stored values) in Python RAM.
        with TemporaryFile() as output:
            result = subprocess.run([executable, *args], env=environment, stdout=output if "--list" in args else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=timeout,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            output.seek(0)
            catalogue = output.read(32 * 1024 * 1024 + 1)
            if len(catalogue) > 32 * 1024 * 1024:
                raise AppError(413, "postgresql_backup_catalogue_too_large", "数据库对象清单超过安全上限")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AppError(503, "postgresql_backup_tool_failed", "PostgreSQL 备份恢复工具不可用或超时，数据未被确认为恢复成功") from exc
    if result.returncode:
        # SQL and libpq diagnostics may contain stored data or credentials.
        raise AppError(422, "postgresql_backup_tool_failed", "PostgreSQL 原生备份或恢复校验失败，未提交失败的恢复事务")
    return catalogue


def summary_sql(schema: str, table: str) -> str:
    # Four commutative 64-bit sums preserve duplicate multiplicity with bounded
    # server memory. Together with count they attest all 256 digest bits.
    sums = ",".join(f"COALESCE(sum(('x'||substr(h,{offset},16))::bit(64)::bigint::numeric),0)::text" for offset in (1, 17, 33, 49))
    # Materialize only the 64-byte row digests. Otherwise PostgreSQL inlines the
    # expression into all four sums and decompresses/hashes large rows four
    # times. The tuplestore is bounded by server work_mem and spills to disk.
    return f"WITH lg_row_hashes AS MATERIALIZED (SELECT encode(sha256(convert_to(ROW(t.*)::text,'UTF8')),'hex') h FROM {identifier(schema)}.{identifier(table)} t) SELECT count(*)::bigint,{sums} FROM lg_row_hashes"


def _configure(connection):
    connection.exec_driver_sql("SET LOCAL work_mem='16MB'")
    connection.exec_driver_sql("SET LOCAL timezone='UTC'")
    connection.exec_driver_sql("SET LOCAL datestyle='ISO,YMD'")
    connection.exec_driver_sql("SET LOCAL extra_float_digits=3")
    connection.exec_driver_sql("SET LOCAL bytea_output='hex'")


def snapshot(url: str, destination: Path, *, include_files=True) -> dict:
    engine = postgres_engine(url)
    try:
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection, connection.begin():
            _configure(connection)
            schema = connection.execute(text("SELECT current_schema()")).scalar_one()
            names = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=:schema ORDER BY tablename"), {"schema": schema}).scalars().all()
            if not REQUIRED <= set(names):
                raise AppError(422, "site_backup_database_invalid", "当前 PostgreSQL 不包含完整全站数据库")
            if connection.execute(text("SELECT count(*) FROM pg_namespace WHERE nspname NOT IN ('public','information_schema') AND nspname NOT LIKE 'pg_%' AND nspname<>:schema"), {"schema": schema}).scalar_one():
                raise AppError(409, "site_backup_database_unsupported", "全站主库包含额外自定义 schema，不能生成可能不完整的备份")
            exported = connection.execute(text("SELECT pg_export_snapshot()")).scalar_one()
            with client_environment(url) as environment:
                run_tool("pg_dump", ["--no-password", "--format=custom", "--quote-all-identifiers", "--no-owner", "--no-privileges", "--schema=" + schema, "--snapshot=" + exported, "--file=" + str(destination)], environment=environment)
            tables = {name: list(connection.exec_driver_sql(summary_sql(schema, name)).one()) for name in names}
            files = []
            if include_files:
                if connection.execute(text(f"SELECT count(*) FROM {identifier(schema)}.files")).scalar_one() > 100_000:
                    raise AppError(413, "site_backup_too_many_files", "备份文件清单超过 100,000 项安全上限")
                files = [list(row) for row in connection.execute(text(f"SELECT object_key,size_bytes,sha256 FROM {identifier(schema)}.files"), execution_options={"yield_per": 500})]
            result = {"engine": "postgresql", "schema": schema, "fingerprint": "sha256-multiset-sum64-v1", "tables": tables, "files": files,
                "server_version": connection.execute(text("SHOW server_version_num")).scalar_one()}
        validate_archive(destination, result)
        return result
    finally:
        engine.dispose()


def validate_metadata(metadata: dict) -> tuple[int, int]:
    def require(condition):
        if not condition:
            raise ValueError("invalid PostgreSQL backup metadata")
    try:
        require(metadata["engine"] == "postgresql" and metadata["fingerprint"] == "sha256-multiset-sum64-v1")
        require(isinstance(metadata["schema"], str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", metadata["schema"]))
        tables = metadata["tables"]
        require(isinstance(tables, dict) and REQUIRED <= set(tables))
        for name, values in tables.items():
            require(isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))
            require(isinstance(values, list) and len(values) == 5 and type(values[0]) is int and values[0] >= 0)
            require(all(isinstance(value, str) and re.fullmatch(r"-?\d{1,60}", value) for value in values[1:]))
        require(isinstance(metadata["files"], list) and len(metadata["files"]) <= 100_000)
        for key, size, digest in metadata["files"]:
            require(isinstance(key, str) and type(size) is int and size >= 0 and isinstance(digest, str))
        return len(tables), sum(values[0] for values in tables.values())
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise AppError(422, "site_backup_database_invalid", "PostgreSQL 备份数据清单无效") from exc


def validate_archive(path: Path, metadata: dict) -> bytes:
    validate_metadata(metadata)
    with path.open("rb") as handle:
        if handle.read(5) != b"PGDMP":
            raise AppError(422, "site_backup_database_invalid", "PostgreSQL 原生备份格式无效")
    catalogue = run_tool("pg_restore", ["--list", str(path)])
    # Read/decompress every archive segment without executing any SQL.
    run_tool("pg_restore", ["--no-owner", "--no-privileges", "--file=" + os.devnull, str(path)])
    return catalogue


def _export_restore_sql(archive: Path, listing: Path, sql: Path, max_bytes: int) -> None:
    """Bound actual COPY expansion even when a native dump compresses highly."""
    from app.services.backup_io import BLOCK, require_space
    executable = shutil.which('pg_restore')
    if not executable:
        raise AppError(503, 'postgresql_backup_tools_missing', '缺少 PostgreSQL 恢复客户端')
    process = subprocess.Popen([executable, '--no-owner', '--no-privileges', '--use-list='+str(listing), '--file=-', str(archive)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    timer = threading.Timer(900, process.kill)
    timer.daemon = True
    timer.start()
    try:
        total, checked = 0, -64 * BLOCK
        with sql.open('xb') as output:
            sql.chmod(0o600)
            while block := process.stdout.read(BLOCK):
                total += len(block)
                if total > max_bytes:
                    raise AppError(413, 'site_restore_sql_too_large', '数据库恢复 SQL 展开超过容量上限')
                if total - checked >= 64 * BLOCK:
                    require_space(sql.parent, 64 * BLOCK)
                    checked = total
                output.write(block)
        if process.wait(timeout=10):
            raise AppError(422, 'postgresql_backup_tool_failed', 'PostgreSQL 恢复 SQL 生成失败或超时')
    finally:
        timer.cancel()
        process.stdout.close()
        if process.poll() is None:
            process.kill()
        process.wait()


def restore(url: str, archive: Path, metadata: dict, *, activation_id: str | None, restore_id: str, revoke_sessions=True, activation_commit: int | None = None, max_sql_bytes=64*1024**3) -> None:
    catalogue = validate_archive(archive, metadata)
    schema = metadata["schema"]
    engine = postgres_engine(url)
    try:
        with engine.connect() as connection:
            if connection.execute(text("SELECT current_schema()")).scalar_one() != schema:
                raise AppError(409, "site_restore_schema_mismatch", "备份与目标 PostgreSQL schema 不一致")
            current_tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=:schema"), {"schema": schema}).scalars().all()
        from app.services.backup_io import require_space
        # bytea expands to hex and text COPY escaping can expand fourfold.
        require_space(archive.parent, max(archive.stat().st_size * 8, database_bytes(url) * 5))
        with TemporaryDirectory(prefix="lg-pg-restore-", dir=archive.parent) as temporary:
            root = Path(temporary)
            listing, sql, before, after = (root / name for name in ("restore.list", "restore.sql", "before.sql", "after.sql"))
            # The application uses an existing dedicated schema and trusted
            # pg_trgm extension. Never drop/recreate extensions or their owner.
            kept = []
            for line in catalogue.decode("utf-8").splitlines():
                if not line.startswith(";") and re.match(r"^\d+; \d+ \d+ (?:SCHEMA|EXTENSION|COMMENT - EXTENSION|COMMENT - SCHEMA|ACL) ", line):
                    continue
                kept.append(line)
            listing.write_text("\n".join(kept) + "\n", encoding="utf-8")
            _export_restore_sql(archive, listing, sql, max_sql_bytes)
            drop = "DROP TABLE " + ",".join(f"{identifier(schema)}.{identifier(name)}" for name in current_tables) + " CASCADE;" if current_tables else ""
            before.write_text("SET LOCAL lock_timeout='10s'; SET LOCAL work_mem='16MB'; SET LOCAL timezone='UTC'; SET LOCAL datestyle='ISO,YMD'; SET LOCAL extra_float_digits=3; SET LOCAL bytea_output='hex'; SELECT pg_advisory_xact_lock(684728190123); CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;\n" + drop, encoding="utf-8")
            # Explicit aliases avoid duplicate aggregate output names.
            checks = []
            for name, values in metadata["tables"].items():
                expected = "ARRAY[" + ",".join(literal(str(value)) for value in values) + "]::text[]"
                query = summary_sql(schema, name)
                checks.append(f"IF (SELECT ARRAY[a::text,b,c,d,e] FROM ({query}) q(a,b,c,d,e)) IS DISTINCT FROM {expected} THEN RAISE EXCEPTION 'Restored table checksum mismatch'; END IF;")
            statements = ["DO $lg_checks$ BEGIN " + "\n".join(checks) + " END $lg_checks$;"]
            statements.append("DO $lg_integrity$ BEGIN IF EXISTS(SELECT 1 FROM pg_constraint WHERE connamespace=" + literal(schema) + "::regnamespace AND NOT convalidated) OR EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid WHERE c.relnamespace=" + literal(schema) + "::regnamespace AND NOT i.indisvalid) THEN RAISE EXCEPTION 'Restored constraints or indexes are invalid'; END IF; END $lg_integrity$;")
            if revoke_sessions:
                statements.append(f"UPDATE {identifier(schema)}.auth_sessions SET revoked_at=CURRENT_TIMESTAMP WHERE revoked_at IS NULL;")
            if activation_id:
                if metadata["tables"].get("_lg_database_activation", [0])[0] != 1:
                    raise AppError(409, "site_restore_activation_missing", "PostgreSQL 备份缺少单向激活标记")
                statements.append(f"UPDATE {identifier(schema)}._lg_database_activation SET job_id={literal(activation_id)} WHERE singleton=1;")
                if activation_commit is not None:
                    statements.append(f"INSERT INTO {identifier(schema)}._lg_database_copy(job_id,applied_commit) VALUES({literal(activation_id)},{int(activation_commit)}) ON CONFLICT(job_id) DO UPDATE SET applied_commit=EXCLUDED.applied_commit;")
            statements.append(f"CREATE TABLE IF NOT EXISTS {identifier(schema)}._lg_site_restore(singleton INTEGER PRIMARY KEY CHECK(singleton=1),restore_id TEXT NOT NULL);")
            statements.append(f"INSERT INTO {identifier(schema)}._lg_site_restore VALUES(1,{literal(restore_id)}) ON CONFLICT(singleton) DO UPDATE SET restore_id=EXCLUDED.restore_id;")
            after.write_text("\n".join(statements), encoding="utf-8")
            with client_environment(url) as environment:
                run_tool("psql", ["--no-psqlrc", "--no-password", "--quiet", "--single-transaction", "--set=ON_ERROR_STOP=on", "--file=" + str(before), "--file=" + str(sql), "--file=" + str(after)], environment=environment)
    finally:
        engine.dispose()
