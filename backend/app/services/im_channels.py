from __future__ import annotations

import hashlib
import os
import secrets
from datetime import timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.im_models import IMBinding, IMBindingCode, IMInbound, IMOutbound
from app.domain.migration_models import MaintenanceLock
from app.domain.models import ChatSession, DurableJob, Message, MessageSubmission, User, Workspace, utc_now
from app.domain.schemas.chat import MessageCreateRequest, SessionCreateRequest
from app.services.authorization import AuthorizationService
from app.services.durable_queue import DurableQueue


def feishu_connection() -> str | None:
    app_id = os.environ.get("LEARNGRAPH_FEISHU_APP_ID", "").strip()
    return f"feishu:{app_id}" if app_id and len(app_id) <= 56 and os.environ.get("LEARNGRAPH_FEISHU_APP_SECRET") else None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def issue_code(db: Session, ctx: WorkspaceContext, connection: str) -> str:
    code = secrets.token_urlsafe(18)
    db.execute(delete(IMBindingCode).where(IMBindingCode.expires_at <= utc_now()))
    db.execute(delete(IMBindingCode).where(IMBindingCode.connection == connection, IMBindingCode.user_id == ctx.principal.user_id))
    db.add(IMBindingCode(digest=digest(code), connection=connection, workspace_id=ctx.workspace_id,
                         user_id=ctx.principal.user_id, expires_at=utc_now() + timedelta(minutes=10)))
    db.commit()
    return code


def actor(db: Session, workspace_id: str, user_id: str) -> WorkspaceContext | None:
    workspace, user = db.get(Workspace, workspace_id), db.get(User, user_id)
    if (not workspace or not user or user.status != "active" or user.must_change_password
            or workspace.tenant_id != user.tenant_id or workspace.workspace_kind != "personal"
            or workspace.owner_user_id != user.id):
        return None
    principal = Principal(user.id, user.username, user.tenant_id, "im", user.display_name or user.username, user.is_system_admin)
    perms = AuthorizationService(db, principal).workspace_permissions(workspace)
    return WorkspaceContext(principal, workspace, perms) if {"workspace.read", "workspace.write"} <= perms else None


def intake(db: Session, connection: str, sender: str, message_id: str, text: str) -> None:
    if db.scalar(select(IMInbound.id).where(IMInbound.connection == connection, IMInbound.message_id == message_id)):
        return
    binding = db.scalar(select(IMBinding).where(IMBinding.connection == connection, IMBinding.sender == sender))
    if not text.strip():
        return
    command = text.split(maxsplit=1)
    is_bind = command[0] == "/bind"
    if is_bind:
        if len(command) != 2 or len(command[1]) != 24:
            return
        code = db.get(IMBindingCode, digest(command[1]))
        if not code or code.connection != connection:
            return
        expires = code.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= utc_now() or not actor(db, code.workspace_id, code.user_id):
            return
        if binding and (binding.user_id != code.user_id or binding.workspace_id != code.workspace_id):
            return
        if not binding and db.scalar(select(IMBinding.id).where(IMBinding.connection == connection, IMBinding.user_id == code.user_id)):
            return
        consumed = db.execute(delete(IMBindingCode).where(IMBindingCode.digest == code.digest)).rowcount
        if not consumed:
            db.rollback()
            return
        if not binding:
            binding = IMBinding(connection=connection, sender=sender, workspace_id=code.workspace_id, user_id=code.user_id)
            db.add(binding)
    if not binding:
        return
    try:
        db.flush()
        row = IMInbound(connection=connection, workspace_id=binding.workspace_id, message_id=message_id,
                       binding_id=binding.id, text="" if is_bind else text)
        db.add(row)
        db.flush()
        DurableQueue(db).enqueue(workspace_id=row.workspace_id, kind="im.process", payload={"id": row.id},
                                 dedupe_key=f"im.process:{row.id}", commit=False)
        db.commit()
    except IntegrityError:
        db.rollback()
        if not db.scalar(select(IMInbound.id).where(IMInbound.connection == connection, IMInbound.message_id == message_id)):
            raise


def _target(db: Session, row: IMInbound) -> tuple[IMBinding, WorkspaceContext] | None:
    binding = db.get(IMBinding, row.binding_id, populate_existing=True)
    if not binding or binding.connection != row.connection or binding.workspace_id != row.workspace_id or row.connection != feishu_connection():
        return None
    ctx = actor(db, binding.workspace_id, binding.user_id)
    if not ctx:
        return None
    if db.scalar(select(MaintenanceLock.job_id).where(MaintenanceLock.workspace_id == row.workspace_id, MaintenanceLock.status == "active")):
        raise AppError(503, "workspace_maintenance", "Workspace writes are paused")
    if binding.session_id:
        chat = db.get(ChatSession, binding.session_id)
        auth = AuthorizationService(db, ctx.principal)
        if (not chat or chat.status == "closed" or chat.memory_enabled
                or any((chat.project_id, chat.goal_id, chat.graph_id, chat.parent_session_id))
                or not auth.can_access_resource(ctx.workspace, "session", chat.id, "write")):
            return None
    return binding, ctx


def _answer(db: Session, settings: Settings, row: IMInbound, binding: IMBinding, ctx: WorkspaceContext) -> str | None:
    from app.services.chat_service_factory import build_chat_service

    key = f"im:{row.id}"
    service = build_chat_service(db, workspace_context=ctx, settings=settings, agent_mode=False, search_route="disabled")
    if not binding.session_id:
        chat = service.create_session(SessionCreateRequest(title="飞书 · 学习助手", session_kind="standalone", memory_enabled=False),
                                      idempotency_key=f"im-session:{binding.id}")
        binding.session_id = chat.id
        db.commit()
    session_id = binding.session_id
    query = select(MessageSubmission).where(MessageSubmission.workspace_id == row.workspace_id,
                                            MessageSubmission.session_id == session_id, MessageSubmission.idempotency_key_hash == digest(key))
    submission = db.scalar(query)
    if not submission:
        for _ in service.create_stream(session_id, MessageCreateRequest(content=row.text), idempotency_key=key):
            pass
        submission = db.scalar(query.execution_options(populate_existing=True))
    if submission and submission.status == "completed":
        answer = db.get(Message, submission.assistant_message_id)
        if answer and answer.content.strip():
            return answer.content
    if submission and submission.status in {"interrupted", "streaming"}:
        # Let the existing chat recovery job finish before we publish a result.
        return None
    return "这次没能生成答案。请在 LearnGraph 查看会话，或重新发送问题。"


def process(db: Session, settings: Settings, row_id: str) -> bool:
    row = db.get(IMInbound, row_id)
    if not row or row.status != "queued":
        return True
    target = _target(db, row)
    if not target:
        row.status, row.text = "cancelled", ""
        db.commit()
        return True
    pending_ids = select(DurableJob.dedupe_key).where(DurableJob.workspace_id == row.workspace_id,
                                                     DurableJob.kind == "im.process", DurableJob.status.in_(("queued", "leased")))
    older = db.scalar(select(IMInbound.id).where(IMInbound.binding_id == row.binding_id, IMInbound.status == "queued",
                       ("im.process:" + IMInbound.id).in_(pending_ids),
                       (IMInbound.created_at < row.created_at) | ((IMInbound.created_at == row.created_at) & (IMInbound.id < row.id))).limit(1))
    if older:
        return False
    if not row.text:
        text = "已绑定 LearnGraph。现在可以直接发送学习问题。"
    else:
        try:
            text = _answer(db, settings, row, *target)
        except AppError as exc:
            if exc.status_code in {408, 429, 500, 502, 503, 504}:
                raise
            db.rollback()
            text = "当前无法处理这条消息，请在 LearnGraph 检查会话或模型配置。"
    if text is None:
        if (utc_now() - row.created_at.replace(tzinfo=timezone.utc)) < timedelta(minutes=20):
            return False
        text = "这次生成中断，请在 LearnGraph 查看会话。"
    encoded = text.encode("utf-8")
    if len(encoded) > 24000:
        text = encoded[:24000].decode("utf-8", errors="ignore") + "\n\n（其余内容请在 LearnGraph 查看。）"
    row.status, row.text = "processed", ""
    db.add(IMOutbound(id=row.id, workspace_id=row.workspace_id, text=text))
    DurableQueue(db).enqueue(workspace_id=row.workspace_id, kind="im.send", payload={"id": row.id},
                             dedupe_key=f"im.send:{row.id}", commit=False)
    db.commit()
    return True


def deliver(db: Session, row_id: str) -> None:
    from app.services.im_feishu import send_text

    outbound, row = db.get(IMOutbound, row_id), db.get(IMInbound, row_id)
    if not row or not outbound or outbound.status not in {"queued", "sending"}:
        return
    target = _target(db, row)
    if not target:
        outbound.status, outbound.text = "cancelled", ""
    elif outbound.first_attempt_at and utc_now() - outbound.first_attempt_at.replace(tzinfo=timezone.utc) > timedelta(minutes=45):
        # Do not retry beyond the platform deduplication window.
        outbound.status, outbound.text = "uncertain", ""
    else:
        outbound.status = "sending"
        outbound.first_attempt_at = outbound.first_attempt_at or utc_now()
        db.commit()
        try:
            outbound.receipt = send_text(target[0].sender, outbound.text, row.id)
        except AppError as exc:
            if exc.status_code in {429, 503}:
                raise
            outbound.status, outbound.text = "failed", ""
        else:
            outbound.status, outbound.text = "sent", ""
    db.commit()
