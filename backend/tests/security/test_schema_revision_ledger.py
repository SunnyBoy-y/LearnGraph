from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_schema_revision_ledger_is_declared_and_checked_at_startup() -> None:
    migration_models = (
        ROOT / "app" / "domain" / "migration_models.py"
    ).read_text(encoding="utf-8")
    database = (ROOT / "app" / "core" / "database.py").read_text(encoding="utf-8")

    assert "class SchemaRevision(Base)" in migration_models
    assert '__tablename__ = "schema_revisions"' in migration_models
    assert "revision: Mapped[str]" in migration_models
    assert "checksum: Mapped[str]" in migration_models
    assert "applied_at: Mapped[datetime]" in migration_models

    assert "CURRENT_SCHEMA_REVISION" in database
    assert "_compute_schema_checksum" in database
    assert "_verify_schema_revisions" in database
    assert "_verify_schema_revisions()" in database
    assert "row.revision != CURRENT_SCHEMA_REVISION" in database


def test_schema_revision_ledger_has_idempotent_initialization_path() -> None:
    database = (ROOT / "app" / "core" / "database.py").read_text(encoding="utf-8")

    # A fresh database records the baseline once; an existing row is only
    # checked, so repeated startup does not create duplicate revisions.
    assert "if row is None:" in database
    assert "session.add(info)" in database
    assert ".order_by(SchemaRevision.applied_at.desc())" in database
    assert "elif row.revision != CURRENT_SCHEMA_REVISION" in database
