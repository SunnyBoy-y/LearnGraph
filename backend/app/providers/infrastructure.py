from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text

from app.providers.ports.database import ProviderProbe


_UNSET = object()


def _unavailable(kind: str, capability: str, reason: str, *, configured: bool = False) -> ProviderProbe:
    return ProviderProbe(
        provider_kind=kind,
        capability=capability,
        status="unavailable",
        configured=configured,
        driver_available=False if reason == "driver_missing" else True,
        connection_verified=False,
        details={"reason": reason},
    )


def probe_database(
    kind: str,
    *,
    sqlite_path: Path | None = None,
    connection_url: Any = _UNSET,
    ssl_mode: str | None = None,
) -> ProviderProbe:
    normalized = kind.casefold()
    if normalized == "postgres":
        normalized = "postgresql"
    if normalized in {"sqlite", "sqlite_compatible", "sqlite-copy"}:
        if sqlite_path is None:
            return _unavailable("sqlite", "database", "target_path_missing")
        sqlite_path = sqlite_path.resolve()
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path = sqlite_path.with_suffix(sqlite_path.suffix + ".preflight")
        try:
            engine = create_engine(f"sqlite:///{probe_path.as_posix()}")
            with engine.begin() as connection:
                connection.exec_driver_sql("CREATE TABLE migration_probe (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
                connection.exec_driver_sql("INSERT INTO migration_probe(value) VALUES ('中文🙂')")
                value = connection.exec_driver_sql("SELECT value FROM migration_probe").scalar_one()
                if value != "中文🙂":
                    raise RuntimeError("round_trip_failed")
            engine.dispose()
            probe_path.unlink(missing_ok=True)
            return ProviderProbe(
                provider_kind="sqlite",
                capability="database",
                status="available",
                configured=True,
                driver_available=True,
                connection_verified=True,
                details={"transaction": "passed", "round_trip": "passed"},
            )
        except Exception as exc:
            probe_path.unlink(missing_ok=True)
            return ProviderProbe(
                provider_kind="sqlite",
                capability="database",
                status="unavailable",
                configured=True,
                driver_available=True,
                connection_verified=False,
                details={"reason": "probe_failed", "error_type": type(exc).__name__},
            )

    env_name = "LEARNGRAPH_POSTGRES_URL" if normalized == "postgresql" else "LEARNGRAPH_MYSQL_URL"
    url = (
        os.getenv(env_name, "").strip()
        if connection_url is _UNSET
        else connection_url
    )
    if not url:
        return _unavailable(normalized, "database", "missing_configuration")
    try:
        connect_args: dict[str, Any] = {}
        if normalized == "postgresql":
            connect_args["connect_timeout"] = 3
        elif normalized == "mysql":
            connect_args["connect_timeout"] = 3
            connect_args["read_timeout"] = 3
            connect_args["write_timeout"] = 3
            if ssl_mode == "disable":
                connect_args["ssl_disabled"] = True
            elif ssl_mode in {"prefer", "require"}:
                connect_args["ssl"] = {"check_hostname": False}
        engine = create_engine(url, pool_pre_ping=True, connect_args=connect_args)
        if engine.dialect.name not in ({"postgresql"} if normalized == "postgresql" else {"mysql", "mariadb"}):
            engine.dispose()
            return _unavailable(normalized, "database", "dialect_mismatch", configured=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            tls_verified = None
            if normalized == "mysql" and ssl_mode in {"prefer", "require"}:
                tls_cipher = connection.execute(
                    text("SHOW STATUS LIKE 'Ssl_cipher'")
                ).first()
                tls_verified = bool(tls_cipher and tls_cipher[1])
                if ssl_mode == "require" and not tls_verified:
                    raise RuntimeError("tls_required")
        dialect = engine.dialect.name
        driver = engine.dialect.driver
        engine.dispose()
        return ProviderProbe(
            provider_kind=normalized,
            capability="database",
            status="available",
            configured=True,
            driver_available=True,
            connection_verified=True,
            details={
                "dialect": dialect,
                "driver": driver,
                "transaction": "passed",
                **({"tls": "passed"} if tls_verified else {}),
            },
        )
    except (ImportError, ModuleNotFoundError):
        return _unavailable(normalized, "database", "driver_missing", configured=True)
    except Exception as exc:
        return ProviderProbe(
            provider_kind=normalized,
            capability="database",
            status="unavailable",
            configured=True,
            driver_available=True,
            connection_verified=False,
            details={"reason": "connection_failed", "error_type": type(exc).__name__},
        )


def probe_redis() -> ProviderProbe:
    url = os.getenv("LEARNGRAPH_REDIS_URL", "").strip()
    if not url:
        return _unavailable("redis", "queue", "missing_configuration")
    try:
        import redis

        client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        return ProviderProbe(
            provider_kind="redis",
            capability="queue",
            status="available",
            configured=True,
            driver_available=True,
            connection_verified=True,
            details={"protocol": "RESP", "ping": "passed"},
        )
    except (ImportError, ModuleNotFoundError):
        return _unavailable("redis", "queue", "driver_missing", configured=True)
    except Exception as exc:
        return ProviderProbe(
            provider_kind="redis",
            capability="queue",
            status="unavailable",
            configured=True,
            driver_available=True,
            connection_verified=False,
            details={"reason": "connection_failed", "error_type": type(exc).__name__},
        )


def probe_minio() -> ProviderProbe:
    endpoint = os.getenv("LEARNGRAPH_MINIO_ENDPOINT", "").strip()
    access_key = os.getenv("LEARNGRAPH_MINIO_ACCESS_KEY", "").strip()
    secret_key = os.getenv("LEARNGRAPH_MINIO_SECRET_KEY", "").strip()
    bucket = os.getenv("LEARNGRAPH_MINIO_BUCKET", "").strip()
    if not all((endpoint, access_key, secret_key, bucket)):
        return _unavailable("minio", "object_storage", "missing_configuration")
    try:
        from minio import Minio

        client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=os.getenv("LEARNGRAPH_MINIO_SECURE", "true").casefold() not in {"0", "false", "no"},
        )
        if not client.bucket_exists(bucket):
            return ProviderProbe(
                provider_kind="minio",
                capability="object_storage",
                status="unavailable",
                configured=True,
                driver_available=True,
                connection_verified=True,
                details={"reason": "bucket_missing", "bucket_verified": False},
            )
        return ProviderProbe(
            provider_kind="minio",
            capability="object_storage",
            status="available",
            configured=True,
            driver_available=True,
            connection_verified=True,
            details={"protocol": "S3", "bucket_verified": True},
        )
    except (ImportError, ModuleNotFoundError):
        return _unavailable("minio", "object_storage", "driver_missing", configured=True)
    except Exception as exc:
        return ProviderProbe(
            provider_kind="minio",
            capability="object_storage",
            status="unavailable",
            configured=True,
            driver_available=True,
            connection_verified=False,
            details={"reason": "connection_failed", "error_type": type(exc).__name__},
        )


def adapter_inventory(
    database_urls: dict[str, Any] | None = None,
    configured_database_kinds: set[str] | None = None,
    database_ssl_modes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    database_urls = database_urls or {}
    configured_database_kinds = configured_database_kinds or set()
    database_ssl_modes = database_ssl_modes or {}
    local_root = Path(os.getenv("LEARNGRAPH_MIGRATION_ROOT", "./data/migrations")) / "probe.sqlite3"
    postgres_probe = (
        probe_database(
            "postgresql",
            connection_url=database_urls.get("postgresql", ""),
            ssl_mode=database_ssl_modes.get("postgresql"),
        )
        if "postgresql" in configured_database_kinds
        else probe_database("postgresql")
    )
    mysql_probe = (
        probe_database(
            "mysql",
            connection_url=database_urls.get("mysql", ""),
            ssl_mode=database_ssl_modes.get("mysql"),
        )
        if "mysql" in configured_database_kinds
        else probe_database("mysql")
    )
    probes = [
        probe_database("sqlite", sqlite_path=local_root),
        postgres_probe,
        mysql_probe,
        ProviderProbe("in_process", "queue", "available", True, True, True, {"durability": "process"}),
        probe_redis(),
        ProviderProbe("local", "object_storage", "available", True, True, True, {"protocol": "filesystem"}),
        probe_minio(),
    ]
    return [asdict(item) for item in probes]
