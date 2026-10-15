"""Execute the three real historical billing schemas, including missing columns."""
import json
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import mapped_column, sessionmaker

from app.core import database
from app.core.migrations.native_billing import apply_native_billing_migration
from app.domain.models import utc_now, new_id

GROUPS = json.loads((Path(__file__).parent / "fixtures/native_billing_legacy_schemas.json").read_text(encoding="utf-8"))["groups"]
NAMES = {"ResearchJob": "research_jobs", "PriceVersion": "price_versions", "ExchangeRateVersion": "exchange_rate_versions", "BudgetPolicy": "budget_policies", "BudgetAlert": "budget_alerts", "UsageEvent": "usage_events"}
UNIQUE = {"PriceVersion": "uq_price_version_scope", "ExchangeRateVersion": "uq_exchange_rate_version_scope", "BudgetPolicy": "uq_budget_policy_scope", "BudgetAlert": "uq_budget_alert_period"}


def historic_tables(group, engine):
    metadata = sa.MetaData()
    sa.Table("workspaces", metadata, sa.Column("id", sa.String(64), primary_key=True))
    namespace = {**vars(sa), "mapped_column": mapped_column, "utc_now": utc_now, "new_id": new_id, "__builtins__": {"dict": dict, "list": list}}
    for model, info in group["schema"].items():
        columns = [sa.Column("workspace_id", sa.String(64), sa.ForeignKey("workspaces.id"), nullable=False),
                   sa.Column("created_at", sa.DateTime(timezone=True), default=utc_now), sa.Column("updated_at", sa.DateTime(timezone=True), default=utc_now)]
        for name, expression in info["columns"].items():
            # These checked-in expressions are the actual audited Git model declarations.
            column = eval(expression, namespace).column
            column.name = column.key = name
            columns.append(column)
        constraints = [sa.UniqueConstraint(*names, name=UNIQUE.get(model)) for names in info["unique_constraints"]]
        sa.Table(NAMES[model], metadata, *columns, *constraints)
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            if table.name != "workspaces":
                table.create(connection)
    return metadata


@pytest.mark.parametrize("group", GROUPS, ids=lambda group: group["signature"])
@pytest.mark.parametrize("revision_ledger", [False, True, "misrecorded_current"])
def test_all_historical_families_upgrade_automatically(tmp_path, monkeypatch, group, revision_ledger):
    engine = sa.create_engine("sqlite:///" + (tmp_path / "historical.db").as_posix())
    sa.event.listen(engine, "connect", database._configure_sqlite_connection)
    # The nonbilling tables represent preserved users, roles and ownership.
    database.Base.metadata.create_all(engine)
    with engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF"); connection.commit()
        for table in ("research_jobs", "usage_events", "budget_alerts", "budget_policies", "price_versions", "exchange_rate_versions"):
            connection.exec_driver_sql(f'DROP TABLE "{table}"')
        if not revision_ledger:
            connection.exec_driver_sql("DROP TABLE schema_revisions")
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON"); connection.commit()
    historic = historic_tables(group, engine)
    if revision_ledger == "misrecorded_current":
        from app.core.migrations import MIGRATIONS
        from app.domain.migration_models import SchemaRevision
        with engine.begin() as connection:
            connection.execute(SchemaRevision.__table__.insert(), [dict(revision=migration.revision,
                checksum=migration.checksum, description=migration.description, applied_at=utc_now()) for migration in MIGRATIONS])
    from app.domain.models import Membership, Organization, Role, User, Workspace
    with sessionmaker(engine)() as db:
        user = User(id="historic-user", tenant_id="historic-tenant", username="historic", username_normalized="historic", password_hash="hash", is_system_admin=False)
        db.add(user); db.flush()
        db.add(User(id="historic-admin", tenant_id=user.tenant_id, username="administrator", username_normalized="administrator", password_hash="hash", is_system_admin=True))
        db.add(Organization(id="historic-org", tenant_id=user.tenant_id, owner_user_id=user.id, name="org")); db.flush()
        db.add(Role(id="historic-viewer-role", tenant_id=user.tenant_id, organization_id="historic-org", name="Viewer")); db.flush()
        db.add(Membership(id="historic-membership", tenant_id=user.tenant_id, organization_id="historic-org", user_id=user.id, role_id="historic-viewer-role"))
        db.add(Workspace(id="historic-workspace", tenant_id=user.tenant_id, owner_user_id=user.id, organization_id="historic-org", name="history")); db.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE price_versions ADD COLUMN extension_note TEXT")
        price = historic.tables["price_versions"]
        connection.execute(price.insert().values(id="native-cny-price", workspace_id="historic-workspace", provider_id="provider", model_id="native", feature="chat", input_usd_per_million=7, output_usd_per_million=14, conditions={"pricing_currency": "CNY", "input_cny_per_million": 1, "cached_input_cny_per_million": 0, "output_cny_per_million": 2}))
        policy = dict(id="historic-policy", workspace_id="historic-workspace", name="native limit", provider_id="*", model_id="*", feature="*", soft_limit_cny=1, hard_limit_cny=2)
        if "limit_currency" in historic.tables["budget_policies"].c:
            policy["limit_currency"] = "USD"
        connection.execute(historic.tables["budget_policies"].insert().values(**policy))
        connection.execute(historic.tables["usage_events"].insert().values(id="historic-usage", workspace_id="historic-workspace", provider_id="provider", model_id="native", feature="chat", price_version_id="native-cny-price", cost_usd=7, cost_cny=1, cost_status="priced"))
        connection.execute(historic.tables["usage_events"].insert().values(id="unknown-usage", workspace_id="historic-workspace", provider_id="provider", model_id="unknown", feature="chat", cost_usd=7, cost_cny=0, cost_status="unpriced"))
        connection.exec_driver_sql("UPDATE price_versions SET extension_note='preserved' WHERE id='native-cny-price'")
        connection.execute(historic.tables["budget_alerts"].insert().values(id="historic-alert", workspace_id="historic-workspace", policy_id="historic-policy", provider_id="provider", model_id="native", feature="chat", level="hard", period_start=utc_now(), period_end=utc_now(), spent_cny=1, projected_cost_cny=2, limit_cny=2))
        research_row = dict(id="historic-research", workspace_id="historic-workspace", question="history", provider_id="provider", status="running", budget_cny=3)
        if "billing_snapshot" in historic.tables["research_jobs"].c:
            research_row["billing_snapshot"] = {"schema_version": 1}
        connection.execute(historic.tables["research_jobs"].insert().values(**research_row))
        connection.exec_driver_sql("INSERT INTO research_job_events (id,workspace_id,research_job_id,sequence,event_type,payload,created_at) VALUES ('historic-event','historic-workspace','historic-research',1,'running','{}',CURRENT_TIMESTAMP)")
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(engine))
    database.init_database()
    database.init_database()
    with engine.connect() as connection:
        price = connection.exec_driver_sql("SELECT currency,input_per_million,cached_input_per_million FROM price_versions WHERE id='native-cny-price'").one()
        assert tuple(price) == ("CNY", "1", "0")
        policy = connection.exec_driver_sql("SELECT currency,soft_limit,hard_limit FROM budget_policies").one()
        assert tuple(policy) == ("USD" if "limit_currency" in group["schema"]["BudgetPolicy"]["columns"] else "CNY", "1", "2")
        assert connection.exec_driver_sql("SELECT currency,cost_amount FROM usage_events WHERE id='historic-usage'").one() == ("CNY", "1")
        unknown = connection.exec_driver_sql("SELECT currency,cost_amount,legacy_billing_snapshot,cost_cny FROM usage_events WHERE id='unknown-usage'").one()
        assert unknown[0] is None and unknown[1] is None and json.loads(unknown[2])["cost_usd"] == 7 and unknown[3] == 0
        assert connection.exec_driver_sql("SELECT owner_user_id FROM workspaces WHERE id='historic-workspace'").scalar_one() == "historic-user"
        assert connection.exec_driver_sql("SELECT is_system_admin FROM users WHERE id='historic-user'").scalar_one() == 0
        assert connection.exec_driver_sql("SELECT is_system_admin FROM users WHERE id='historic-admin'").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT role_id FROM memberships WHERE id='historic-membership'").scalar_one() == "historic-viewer-role"
        assert connection.exec_driver_sql("SELECT extension_note FROM price_versions WHERE id='native-cny-price'").scalar_one() == "preserved"
        assert connection.exec_driver_sql("SELECT policy_id,currency FROM budget_alerts WHERE id='historic-alert'").one() == ("historic-policy", None)
        assert connection.exec_driver_sql("SELECT research_job_id FROM research_job_events WHERE id='historic-event'").scalar_one() == "historic-research"
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
        assert connection.exec_driver_sql("SELECT count(*) FROM schema_revisions WHERE revision='v1.15.0'").scalar_one() == 1
    engine.dispose()


def test_startup_migration_rolls_back_on_failure_and_recovers(tmp_path, monkeypatch):
    from app.core import migrations
    engine = sa.create_engine("sqlite:///" + (tmp_path / "interrupted.db").as_posix())
    sa.event.listen(engine, "connect", database._configure_sqlite_connection)
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(engine))
    def fail_after_apply(connection):
        apply_native_billing_migration(connection)
        connection.exec_driver_sql("CREATE TABLE incomplete_native_migration (id INTEGER)")
        raise RuntimeError("injected interrupted migration")
    original = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", (*original[:-1], migrations.SchemaMigration(original[-1].revision, original[-1].description, fail_after_apply)))
    with pytest.raises(RuntimeError, match="injected interrupted"):
        database.init_database()
    with engine.connect() as connection:
        assert "incomplete_native_migration" not in sa.inspect(connection).get_table_names()
        assert connection.exec_driver_sql("SELECT count(*) FROM schema_revisions").scalar_one() == 0
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    monkeypatch.setattr(migrations, "MIGRATIONS", original)
    database.init_database()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM schema_revisions WHERE revision='v1.15.0'").scalar_one() == 1
    engine.dispose()


def test_concurrent_startups_apply_native_revision_once(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    engine = sa.create_engine("sqlite:///" + (tmp_path / "concurrent.db").as_posix(), connect_args={"timeout": 30})
    sa.event.listen(engine, "connect", database._configure_sqlite_connection)
    database.Base.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    monkeypatch.setattr(database, "SessionLocal", sessionmaker(engine))
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: database.init_database(), range(2)))
    with engine.connect() as connection:
        assert connection.exec_driver_sql("SELECT count(*) FROM schema_revisions WHERE revision='v1.15.0'").scalar_one() == 1
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    engine.dispose()


def test_partial_native_migration_is_idempotent(tmp_path):
    engine = sa.create_engine("sqlite:///" + (tmp_path / "partial.db").as_posix())
    database.Base.metadata.create_all(engine)
    with engine.begin() as connection:
        apply_native_billing_migration(connection)
        apply_native_billing_migration(connection)
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    engine.dispose()
