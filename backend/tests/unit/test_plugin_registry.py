from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.domain.models import PluginRecord
from app.services.management import PluginService


def test_plugin_registry_excludes_subapp_owners_without_deleting_them():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for key, kind, workspace in (
            ("parser", "document_processor", "ws"),
            ("component", "trusted_component", "ws"),
            ("agent-app", "agent_subapp", "ws"),
            ("other", "document_processor", "other-ws"),
        ):
            db.add(PluginRecord(workspace_id=workspace, plugin_key=key, name=key,
                                version="1.0.0", plugin_type=kind, status="disabled",
                                enabled=False, permissions=[], capabilities=[]))
        db.commit()

        visible = PluginService(db, "ws", "actor").list()
        assert {item.plugin_key for item in visible} == {"parser", "component"}
        owner = db.scalar(select(PluginRecord).where(PluginRecord.plugin_key == "agent-app"))
        assert owner is not None
        assert owner.plugin_type == "agent_subapp"
    engine.dispose()
