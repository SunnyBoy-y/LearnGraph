"""Additive, idempotent schedule expansion; preserves existing cron tasks."""
from sqlalchemy import inspect
from sqlalchemy.schema import CreateColumn


def apply_scheduled_automation(connection):
    from app.domain.extension_models import ScheduledExtensionTask, ScheduledExtensionRun
    for table in (ScheduledExtensionTask.__table__,ScheduledExtensionRun.__table__):
        if not inspect(connection).has_table(table.name):
            table.create(bind=connection,checkfirst=True)
            continue
        existing={column['name'] for column in inspect(connection).get_columns(table.name)}
        for column in table.columns:
            if column.name not in existing:
                ddl=str(CreateColumn(column).compile(dialect=connection.dialect))
                connection.exec_driver_sql(f'ALTER TABLE "{table.name}" ADD COLUMN {ddl}')
