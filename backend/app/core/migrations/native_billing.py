"""Upgrade the existing billing ledger without synthesizing exchanged amounts."""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection
from sqlalchemy.schema import CreateColumn

from app.core.money import amount_text, currency_code
from app.domain.models import BudgetAlert, BudgetPolicy, PriceVersion, ResearchJob, UsageEvent


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def _amount(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return amount_text(value)
    except ValueError:
        return None


def _add_columns(connection: Connection, model: Any) -> set[str]:
    table = model.__table__
    if not inspect(connection).has_table(table.name):
        table.create(connection, checkfirst=True)
        return set(table.columns.keys())
    existing = {column["name"] for column in inspect(connection).get_columns(table.name)}
    for column in table.columns:
        if column.name in existing:
            continue
        # SQL defaults must cover persisted rows, including the oldest ledger.
        ddl = str(CreateColumn(column).compile(dialect=connection.dialect))
        if column.server_default is not None:
            pass
        elif not column.nullable and column.default is not None and column.default.is_scalar:
            default = column.default.arg
            if isinstance(default, bool):
                default = int(default)
            literal = str(default) if isinstance(default, (int, float)) else "'" + str(default).replace("'", "''") + "'"
            ddl += " DEFAULT " + literal
        elif not column.nullable and str(column.type) == "JSON":
            ddl += " DEFAULT '{}'"
        elif not column.nullable and column.name in {"created_at", "updated_at"}:
            # SQLite cannot ADD COLUMN with a nonconstant timestamp default.
            ddl = f'"{column.name}" DATETIME'
        connection.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}')
    if "created_at" not in existing or "updated_at" not in existing:
        connection.exec_driver_sql(f'UPDATE "{table.name}" SET created_at=COALESCE(created_at,CURRENT_TIMESTAMP), updated_at=COALESCE(updated_at,created_at,CURRENT_TIMESTAMP)')
    return existing


def _update(connection: Connection, table: str, row_id: str, values: dict[str, Any]) -> None:
    payload = {key: json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, dict) else value for key, value in values.items()}
    payload["row_id"] = row_id
    connection.execute(text(f'UPDATE "{table}" SET ' + ", ".join(f'"{key}"=:{key}' for key in values) + ' WHERE id=:row_id'), payload)


def _currency_unique(connection: Connection, table: str, constraint: str) -> None:
    unique = next((item for item in inspect(connection).get_unique_constraints(table) if item["name"] == constraint), None)
    if unique is None or "currency" in unique["column_names"]:
        return
    columns = list(unique["column_names"])
    if connection.dialect.name != "sqlite":
        quoted = ", ".join(f'"{name}"' for name in [*columns, "currency"])
        operation = "DROP INDEX" if connection.dialect.name == "mysql" else "DROP CONSTRAINT"
        connection.exec_driver_sql(f'ALTER TABLE "{table}" {operation} "{constraint}"')
        connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint}" UNIQUE ({quoted})')
        return
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar():
        raise RuntimeError("Native billing migration must hold the startup migration lock with foreign keys temporarily disabled")
    # Preserve ALL existing columns, indexes and triggers, including extensions
    # unknown to this checkout. Renaming the old parent would rewrite child FKs.
    ddl = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).scalar_one()
    pattern = r'(CONSTRAINT\s+"?' + re.escape(constraint) + r'"?\s+UNIQUE\s*\()([^)]*)(\))'
    ddl, changed = re.subn(pattern, lambda match: match[1] + match[2] + ', currency' + match[3], ddl, flags=re.I)
    if changed != 1:
        raise RuntimeError(f"Cannot safely upgrade {table}.{constraint}")
    replacement = table + "__native_upgrade"
    ddl = re.sub(r'^(CREATE TABLE\s+)(?:"[^"]+"|\S+)', lambda match: match[1] + '"' + replacement + '"', ddl, count=1, flags=re.I)
    objects = connection.exec_driver_sql("SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN ('index','trigger') AND sql IS NOT NULL", (table,)).scalars().all()
    names = [column["name"] for column in inspect(connection).get_columns(table)]
    quoted = ",".join('"' + name + '"' for name in names)
    before = connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar_one()
    connection.exec_driver_sql(ddl)
    connection.exec_driver_sql(f'INSERT INTO "{replacement}" ({quoted}) SELECT {quoted} FROM "{table}"')
    connection.exec_driver_sql(f'DROP TABLE "{table}"')
    connection.exec_driver_sql(f'ALTER TABLE "{replacement}" RENAME TO "{table}"')
    for sql in objects:
        connection.exec_driver_sql(sql)
    after = connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar_one()
    if before != after:
        raise RuntimeError(f"Native billing migration changed row count for {table}")


def native_billing_shape_needs_repair(connection: Connection) -> bool:
    inspector = inspect(connection)
    for model in (PriceVersion, BudgetPolicy, BudgetAlert, UsageEvent, ResearchJob):
        table = model.__table__
        if not inspector.has_table(table.name):
            return True
        columns = {column["name"] for column in inspector.get_columns(table.name)}
        if set(table.columns.keys()) - columns:
            return True
    for table, name in (("price_versions", "uq_price_version_scope"), ("budget_policies", "uq_budget_policy_scope")):
        constraint = next((item for item in inspector.get_unique_constraints(table) if item["name"] == name), None)
        if constraint is not None and "currency" not in constraint["column_names"]:
            return True
    return False


def apply_native_billing_migration(connection: Connection) -> None:
    originals = {model.__tablename__: _add_columns(connection, model) for model in (PriceVersion, BudgetPolicy, BudgetAlert, UsageEvent, ResearchJob)}
    prices: dict[str, dict[str, Any]] = {}
    for row in connection.exec_driver_sql("SELECT * FROM price_versions").mappings().all():
        item = dict(row)
        conditions = _json(item.get("conditions"))
        if _json(item.get("legacy_billing_snapshot")) or ("currency" in originals["price_versions"] and conditions.get("native_schema") == 2):
            prices[item["id"]] = item
            continue
        currency = currency_code(str(conditions.get("pricing_currency") or item.get("currency") or "USD"))
        suffix = "cny" if currency == "CNY" else "usd"
        rates = {}
        for name, old in (("input_per_million", "input"), ("cached_input_per_million", "cached_input"), ("cache_write_per_million", "cache_write"), ("output_per_million", "output"), ("fixed_per_call", "fixed")):
            legacy_key = f"{old}_{suffix}_" + ("per_call" if old == "fixed" else "per_million")
            value = conditions.get(legacy_key) if currency == "CNY" else item.get(legacy_key)
            rates[name] = _amount(value)
        if rates["input_per_million"] is None or rates["output_per_million"] is None:
            conditions["native_unpriced"] = True
        conditions["native_schema"] = 2
        values = {**rates, "currency": currency, "input_per_million": rates["input_per_million"] or "0", "output_per_million": rates["output_per_million"] or "0", "fixed_per_call": rates["fixed_per_call"] or "0", "conditions": conditions, "legacy_billing_snapshot": {key: item[key] for key in originals["price_versions"] if key in item}}
        _update(connection, "price_versions", item["id"], values)
        prices[item["id"]] = {**item, **values}
    for row in connection.exec_driver_sql("SELECT * FROM budget_policies").mappings().all():
        item = dict(row)
        if _json(item.get("legacy_billing_snapshot")) or ("currency" in originals["budget_policies"] and (item.get("soft_limit") is not None or item.get("hard_limit") is not None)):
            continue
        currency = currency_code(str(item.get("limit_currency") or item.get("currency") or "CNY"))
        _update(connection, "budget_policies", item["id"], {"currency": currency, "soft_limit": _amount(item.get("soft_limit_cny")), "hard_limit": _amount(item.get("hard_limit_cny")), "legacy_billing_snapshot": {key: item[key] for key in originals["budget_policies"] if key in item}})
    for row in connection.exec_driver_sql("SELECT * FROM usage_events").mappings().all():
        item = dict(row)
        if (_json(item.get("legacy_billing_snapshot")) or item.get("amount_source") not in (None, "unknown")
            or _json(item.get("pricing_snapshot")).get("schema_version") == 2):
            continue
        price = prices.get(item.get("price_version_id"))
        if price and price.get("workspace_id") != item.get("workspace_id"):
            raise RuntimeError("Usage price belongs to a different workspace")
        status = item.get("cost_status") or "unpriced"
        currency, amount, source = None, None, "legacy_unknown"
        if price and not _json(price.get("conditions")).get("native_unpriced") and status == "priced":
            currency = price["currency"]
            amount = _amount(item.get("cost_cny" if currency == "CNY" else "cost_usd"))
            source = "legacy_native"
        elif price and status == "estimated_usage_missing":
            currency, source = price["currency"], "legacy_estimate"
            item["reserved_amount"] = _amount(item.get("cost_cny" if currency == "CNY" else "cost_usd"))
        elif status == "provider_reported" and item.get("feature") == "deep_research":
            # Older adapters converted some vendor USD reports into this CNY
            # column. Without the original report, neither value proves the
            # original denomination. Preserve both as evidence only.
            currency, amount, source = None, None, "legacy_unknown"
        elif status == "non_billable" or item.get("provider_id") == "local_mock":
            currency, amount, source = "USD", "0", "non_billable"
            status = "non_billable"
        _update(connection, "usage_events", item["id"], {"currency": currency, "cost_amount": amount, "reserved_amount": item.get("reserved_amount"), "amount_source": source, "record_stage": "settled", "cost_status": status if amount is not None else "legacy_unknown", "pricing_snapshot": {"schema_version": 2, "currency": currency, "price_version_id": item.get("price_version_id")}, "legacy_billing_snapshot": {key: item[key] for key in originals["usage_events"] if key in item}})
    for row in connection.exec_driver_sql("SELECT * FROM budget_alerts").mappings().all():
        item = dict(row)
        if not _json(item.get("legacy_billing_snapshot")) and item.get("currency") is None:
            _update(connection, "budget_alerts", item["id"], {"currency": None, "spent_amount": None, "projected_amount": None, "limit_amount": None, "legacy_billing_snapshot": {key: item[key] for key in originals["budget_alerts"] if key in item}})
    for row in connection.exec_driver_sql("SELECT * FROM research_jobs").mappings().all():
        item = dict(row)
        if (_json(item.get("legacy_billing_snapshot")) or _json(item.get("billing_snapshot")).get("schema_version") == 2
            or ("currency" in originals["research_jobs"] and item.get("currency") is not None)):
            continue
        evidence = _json(item.get("evidence_pack"))
        reported = (evidence.get("usage") or {}).get("cost_usd") if isinstance(evidence.get("usage"), dict) else None
        # The old actual_cost_cny column also held converted vendor USD costs.
        # Only an original vendor report proves a native actual amount.
        values = {"currency": "USD" if reported is not None else "CNY", "budget_amount": None if reported is not None else _amount(item.get("budget_cny")), "estimated_cost_amount": None if reported is not None else _amount(item.get("estimated_cost_cny")), "actual_cost_amount": _amount(reported) if reported is not None else None, "legacy_billing_snapshot": {key: item[key] for key in originals["research_jobs"] if key in item}}
        _update(connection, "research_jobs", item["id"], values)
    _currency_unique(connection, "price_versions", "uq_price_version_scope")
    _currency_unique(connection, "budget_policies", "uq_budget_policy_scope")
    connection.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS uq_usage_event_idempotency ON usage_events(idempotency_key)")
    connection.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_usage_native_scope ON usage_events(workspace_id,currency,record_stage,created_at)")
    if connection.dialect.name == "sqlite" and connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("Foreign key validation failed during native billing migration")
