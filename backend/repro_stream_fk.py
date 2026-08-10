"""Reproduce the message_stream_events FK failure in-process (agent mode, multi tool calls)."""
import sys
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import WorkspaceContext
from app.api.routers.chat import service as build_chat_service
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.security import Principal
from app.domain.models import ChatSession, User, Workspace, utc_now
from app.domain.schemas.chat import MessageCreateRequest

settings = get_settings()
db = SessionLocal()
session_id = None
try:
    workspace = db.get(Workspace, "admin-workspace")
    if workspace is None:
        raise SystemExit("admin-workspace missing")
    user = db.scalar(select(User).where(User.username == "admin"))
    if user is None:
        raise SystemExit("admin user missing")
    principal = Principal(
        user_id=user.id,
        username=user.username,
        tenant_id=user.tenant_id,
        session_id="repro-session",
        display_name=user.display_name or user.username,
        is_system_admin=bool(user.is_system_admin),
    )
    ctx = WorkspaceContext(
        principal=principal,
        workspace=workspace,
        permissions=frozenset({"*"}),
    )
    chat = ChatSession(
        workspace_id="admin-workspace",
        title="repro-fk",
        session_kind="main",
        status="active",
    )
    db.add(chat)
    db.commit()
    session_id = chat.id
    print("repro session:", session_id, flush=True)

    svc = build_chat_service(
        db,
        ctx,
        settings,
        model_id="deepseek-v4-flash",
        provider_id="b15c5a0a-0804-47a6-8e24-f239a50fe6a1",
        thinking_mode="medium",
        search_route="model_native",
        agent_mode=True,
    )
    payload = MessageCreateRequest(
        content="我来查一下你账户相关的余额和预算情况",
        agent_mode=True,
        thinking_mode="medium",
        search_route="model_native",
        model_id="deepseek-v4-flash",
        provider_id="b15c5a0a-0804-47a6-8e24-f239a50fe6a1",
    )
    key = f"repro-{int(time.time() * 1000)}"
    started = time.monotonic()
    count = 0
    try:
        for chunk in svc.create_stream(session_id, payload, idempotency_key=key):
            count += 1
            if count % 100 == 0:
                print(f"...{count} chunks in {time.monotonic()-started:.1f}s", flush=True)
        print(f"STREAM COMPLETED OK after {time.monotonic()-started:.1f}s, {count} chunks", flush=True)
    except IntegrityError as exc:
        print("\n=== INTEGRITY ERROR (FULL) ===", flush=True)
        print(str(exc)[:4000], flush=True)
        print("=== END ===", flush=True)
    except Exception as exc:
        print(f"\n=== {type(exc).__name__} ===", flush=True)
        print(str(exc)[:2000], flush=True)
finally:
    db.rollback()
    db.close()
