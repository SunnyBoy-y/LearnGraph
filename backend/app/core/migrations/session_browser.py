"""Additive, idempotent indexes for bounded session browsing."""
from sqlalchemy import Index
from sqlalchemy.engine import Connection

from app.domain.models import ChatSession, ResourceACL

_SESSION = ChatSession.__table__.c
_ACL = ResourceACL.__table__.c
INDEXES = (
    Index('ix_session_browse_recent', _SESSION.workspace_id, _SESSION.pinned.desc(), _SESSION.updated_at.desc(), _SESSION.id.desc()),
    Index('ix_session_browse_project', _SESSION.workspace_id, _SESSION.project_id, _SESSION.pinned.desc(), _SESSION.updated_at.desc(), _SESSION.id.desc()),
    Index('ix_session_browse_children', _SESSION.workspace_id, _SESSION.parent_session_id, _SESSION.pinned.desc(), _SESSION.updated_at.desc(), _SESSION.id.desc()),
    Index('ix_session_browse_acl', _ACL.workspace_id, _ACL.tenant_id, _ACL.resource_type, _ACL.resource_id, _ACL.revoked_at),
)


def apply_session_browser(connection: Connection) -> None:
    for index in INDEXES:
        index.create(connection, checkfirst=True)
