"""Verify the _record_agent_run_event SAVEPOINT fix in-process.

Simulates the failing demo scenario: two tool calls in the same assistant
turn reuse the same run id (both derive from assistant_message_id) but
produce different payloads. The second memory ingest must raise a 409
idempotency conflict inside a SAVEPOINT, and the outer chat stream
transaction (the pending MessagePart) must survive.

Before the fix, the except handler called self.db.rollback(), which
discarded the pending part; the following _append_event then failed with a
FOREIGN KEY constraint violation on message_stream_events.part_id.
"""
import sys
import time
import uuid

from sqlalchemy import delete, func, select

sys.path.insert(0, ".")

from app.core.database import SessionLocal
from app.domain.models import (
    ChatSession,
    Message,
    MessagePartRecord,
    MessageVersion,
    Workspace,
)
from app.services.chat import ChatService

WORKSPACE_ID = "demo-workspace"
RUN_ID = f"verify-run-{uuid.uuid4().hex[:12]}"

db = SessionLocal()
try:
    workspace = db.get(Workspace, WORKSPACE_ID)
    if workspace is None:
        raise SystemExit(f"{WORKSPACE_ID} missing")

    # --- fixture: temporary session/message/version/part -------------------
    session = ChatSession(
        workspace_id=WORKSPACE_ID,
        title="verify-fk-fix",
        session_kind="main",
        status="active",
    )
    db.add(session)
    db.flush()
    message = Message(
        workspace_id=WORKSPACE_ID,
        session_id=session.id,
        role="user",
        content="verify",
        status="completed",
    )
    db.add(message)
    db.flush()
    version = MessageVersion(
        workspace_id=WORKSPACE_ID,
        message_id=message.id,
        version=1,
        status="completed",
    )
    db.add(version)
    db.flush()
    # This mirrors the tool-call part created right before _execute_agent_tool
    # in the agent loop; it must survive the duplicate ingest below.
    part = MessagePartRecord(
        workspace_id=WORKSPACE_ID,
        message_version_id=version.id,
        ordinal=0,
        part_type="tool_call",
        status="streaming",
        content="",
    )
    db.add(part)
    db.flush()

    svc = ChatService(
        db,
        WORKSPACE_ID,
        "verify-actor",
        model_provider=object(),  # _record_agent_run_event never touches it
        tenant_id="local-tenant",
    )

    # --- first tool call: ingest succeeds ----------------------------------
    svc._record_agent_run_event(
        session.id,
        RUN_ID,
        succeeded=True,
        output="list_providers returned 2 providers",
        meta={},
        sources=[],
    )
    assert db.is_active, "session must stay active after the first ingest"

    # --- second tool call: same run id, different payload -> 409 conflict --
    svc._record_agent_run_event(
        session.id,
        RUN_ID,
        succeeded=True,
        output="get_budget_status returned 0 budgets",
        meta={},
        sources=[],
    )
    assert db.is_active, "session must stay active after the duplicate ingest"

    # --- the outer unit of work must still commit the pending part ---------
    db.commit()
    persisted = db.get(MessagePartRecord, part.id)
    assert persisted is not None, "pending MessagePart was rolled back by the ingest!"
    print("PASS: pending MessagePart survived the duplicate memory ingest")

    # --- memory event count: first ingest persisted, second deduplicated ---
    from app.domain.memory_event_models import MemoryEvent, MemoryStream

    stream_id = db.scalar(
        select(MemoryStream.id).where(MemoryStream.aggregate_id == RUN_ID[:64])
    )
    if stream_id is None:
        print("NOTE: no memory stream created (ingest may be disabled)")
    else:
        count = db.scalar(
            select(func.count(MemoryEvent.global_position)).where(
                MemoryEvent.stream_id == stream_id
            )
        )
        print(f"PASS: memory events for run = {count} (expected 1, second deduped)")
        assert count == 1, "duplicate ingest wrote a second event"

    # --- cleanup fixture ---------------------------------------------------
    db.execute(delete(MessagePartRecord).where(MessagePartRecord.id == part.id))
    db.execute(delete(MessageVersion).where(MessageVersion.id == version.id))
    db.execute(delete(Message).where(Message.id == message.id))
    db.execute(delete(ChatSession).where(ChatSession.id == session.id))
    if stream_id is not None:
        db.execute(delete(MemoryEvent).where(MemoryEvent.stream_id == stream_id))
        db.execute(delete(MemoryStream).where(MemoryStream.id == stream_id))
    db.commit()
    print("cleanup OK")
finally:
    db.rollback()
    db.close()
