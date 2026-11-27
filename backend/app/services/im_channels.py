from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.core.security import Principal
from app.domain.im_models import (
    IMBinding,
    IMBindingCode,
    IMChannelConfig,
    IMChannelState,
    IMInbound,
    IMMessageContext,
    IMOutbound,
)
from app.domain.migration_models import MaintenanceLock
from app.domain.models import ChatSession, DurableJob, Message, MessageSubmission, User, Workspace, utc_now
from app.domain.schemas.chat import MessageCreateRequest, SessionCreateRequest
from app.services.authorization import AuthorizationService
from app.services.durable_queue import DurableQueue


def feishu_connection() -> str | None:
    app_id = os.environ.get("LEARNGRAPH_FEISHU_APP_ID", "").strip()
    return f"feishu:{app_id}" if app_id and len(app_id) <= 56 and os.environ.get("LEARNGRAPH_FEISHU_APP_SECRET") else None


def channel_config_for_user(
    db: Session,
    *,
    provider: str,
    user_id: str,
    workspace_id: str,
) -> IMChannelConfig | None:
    return db.scalar(select(IMChannelConfig).where(
        IMChannelConfig.provider == provider,
        IMChannelConfig.user_id == user_id,
        IMChannelConfig.workspace_id == workspace_id,
        IMChannelConfig.enabled.is_(True),
    ))


def feishu_config_for_user(db: Session, *, user_id: str, workspace_id: str) -> IMChannelConfig | None:
    return channel_config_for_user(db, provider="feishu", user_id=user_id, workspace_id=workspace_id)


def feishu_connection_for_user(db: Session, *, user_id: str, workspace_id: str) -> tuple[str | None, IMChannelConfig | None]:
    cfg = feishu_config_for_user(db, user_id=user_id, workspace_id=workspace_id)
    if cfg is not None:
        return f"feishu:{cfg.app_id}", cfg
    return feishu_connection(), None


def wechat_config_for_user(db: Session, *, user_id: str, workspace_id: str) -> IMChannelConfig | None:
    return channel_config_for_user(db, provider="wechat", user_id=user_id, workspace_id=workspace_id)


def wechat_connection_for_user(db: Session, *, user_id: str, workspace_id: str) -> tuple[str | None, IMChannelConfig | None]:
    cfg = wechat_config_for_user(db, user_id=user_id, workspace_id=workspace_id)
    if cfg is not None:
        return f"wechat:{cfg.app_id}", cfg
    return None, None


def decrypt_feishu_credentials(settings: Settings, cfg: IMChannelConfig) -> tuple[str, str]:
    from app.services.provider_secrets import decrypt_secret_fields

    raw = decrypt_secret_fields(
        settings,
        ciphertext=cfg.secret_ciphertext,
        algorithm=cfg.secret_algorithm,
        key_provider=cfg.secret_key_provider,
        key_version=cfg.secret_key_version,
    )
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict) and isinstance(payload.get("app_secret"), str):
                domain = "lark" if payload.get("domain") == "lark" else "feishu"
                return payload["app_secret"], domain
        except ValueError:
            pass
    return raw, "feishu"


def decrypt_feishu_secret(settings: Settings, cfg: IMChannelConfig) -> str:
    app_secret, _domain = decrypt_feishu_credentials(settings, cfg)
    return app_secret


def encode_feishu_secret_payload(app_secret: str, domain: str = "feishu") -> str:
    if domain == "lark":
        return json.dumps({"app_secret": app_secret, "domain": "lark"}, ensure_ascii=False)
    return app_secret


def encode_wechat_secret_payload(*, bot_token: str, base_url: str, ilink_user_id: str) -> str:
    return json.dumps(
        {
            "bot_token": bot_token,
            "base_url": base_url,
            "ilink_user_id": ilink_user_id,
        },
        ensure_ascii=False,
    )


def decrypt_wechat_credentials(settings: Settings, cfg: IMChannelConfig) -> tuple[str, str, str]:
    from app.services.im_wechat import DEFAULT_BASE_URL, normalize_base_url
    from app.services.provider_secrets import decrypt_secret_fields

    raw = decrypt_secret_fields(
        settings,
        ciphertext=cfg.secret_ciphertext,
        algorithm=cfg.secret_algorithm,
        key_provider=cfg.secret_key_provider,
        key_version=cfg.secret_key_version,
    )
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise AppError(500, "wechat_config_corrupted", "WeChat credentials could not be decoded") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("bot_token"), str):
        raise AppError(500, "wechat_config_corrupted", "WeChat credentials are incomplete")
    bot_token = payload["bot_token"].strip()
    base_url = normalize_base_url(payload.get("base_url") or DEFAULT_BASE_URL)
    ilink_user_id = str(payload.get("ilink_user_id") or "").strip()
    return bot_token, base_url, ilink_user_id


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


def intake(
    db: Session,
    connection: str,
    sender: str,
    message_id: str,
    text: str,
    *,
    context_token: str | None = None,
    media: list[dict] | None = None,
) -> None:
    from app.services.im_media import detect_tts_request

    if db.scalar(select(IMInbound.id).where(IMInbound.connection == connection, IMInbound.message_id == message_id)):
        return
    binding = db.scalar(select(IMBinding).where(IMBinding.connection == connection, IMBinding.sender == sender))
    has_media = bool(media)
    if not text.strip() and not has_media:
        return
    command = text.split(maxsplit=1) if text.strip() else []
    is_bind = bool(command and command[0] == "/bind")
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
    tts_requested = False
    clean_text = "" if is_bind else text
    if not is_bind and text.strip():
        tts_requested, clean_text = detect_tts_request(text)
    media_json = json.dumps(media, ensure_ascii=False) if (not is_bind and media) else ""
    try:
        db.flush()
        row = IMInbound(
            connection=connection,
            workspace_id=binding.workspace_id,
            message_id=message_id,
            binding_id=binding.id,
            text=clean_text,
            media_json=media_json,
            tts_requested=tts_requested,
        )
        db.add(row)
        db.flush()
        if context_token:
            db.add(IMMessageContext(inbound_id=row.id, context_token=context_token))
        DurableQueue(db).enqueue(workspace_id=row.workspace_id, kind="im.process", payload={"id": row.id},
                                 dedupe_key=f"im.process:{row.id}", commit=False)
        db.commit()
    except IntegrityError:
        db.rollback()
        if not db.scalar(select(IMInbound.id).where(IMInbound.connection == connection, IMInbound.message_id == message_id)):
            raise


def _target(db: Session, row: IMInbound) -> tuple[IMBinding, WorkspaceContext] | None:
    binding = db.get(IMBinding, row.binding_id, populate_existing=True)
    if not binding or binding.connection != row.connection or binding.workspace_id != row.workspace_id:
        return None
    if row.connection.startswith("wechat:"):
        expected_conn, cfg = wechat_connection_for_user(db, user_id=binding.user_id, workspace_id=binding.workspace_id)
        if not expected_conn or row.connection != expected_conn or cfg is None:
            return None
        state = db.get(IMChannelState, cfg.id)
        if state and state.session_expired:
            return None
    else:
        expected_conn, _ = feishu_connection_for_user(db, user_id=binding.user_id, workspace_id=binding.workspace_id)
        if not expected_conn or row.connection != expected_conn:
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


def _prepare_inbound_media(
    db: Session,
    settings: Settings,
    row: IMInbound,
    binding: IMBinding,
    ctx: WorkspaceContext,
) -> tuple[str, list[str]]:
    from app.services.im_feishu import download_message_resource
    from app.services.im_media import store_im_bytes, transcribe_im_audio
    from app.services.im_wechat import download_media as download_wechat_media

    raw_json = (row.media_json or "").strip()
    if not raw_json:
        return row.text, []
    try:
        items = json.loads(raw_json)
    except ValueError:
        return row.text, []
    if not isinstance(items, list) or not items:
        return row.text, []

    is_wechat = row.connection.startswith("wechat:")
    feishu_kwargs: dict[str, str] = {}
    wechat_kwargs: dict[str, str] = {}
    if is_wechat:
        cfg = wechat_config_for_user(db, user_id=binding.user_id, workspace_id=binding.workspace_id)
        if cfg is not None:
            bot_token, base_url, _ = decrypt_wechat_credentials(settings, cfg)
            wechat_kwargs = {"bot_token": bot_token, "base_url": base_url}
    else:
        cfg = feishu_config_for_user(db, user_id=binding.user_id, workspace_id=binding.workspace_id)
        if cfg is not None:
            app_secret, domain = decrypt_feishu_credentials(settings, cfg)
            feishu_kwargs = {"app_id": cfg.app_id, "app_secret": app_secret, "domain": domain}

    file_ids: list[str] = []
    asr_transcripts: list[str] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "file")
        filename = str(item.get("filename") or f"im_attachment_{idx}.bin")
        mime_type = str(item.get("mime_type") or "") or None
        raw_bytes = b""
        try:
            if item.get("source") == "wechat" or is_wechat:
                if wechat_kwargs:
                    raw_bytes = download_wechat_media(
                        item,
                        max_bytes=settings.max_upload_bytes,
                        **wechat_kwargs,
                    )
            else:
                msg_id = str(item.get("message_id") or row.message_id)
                file_key = str(item.get("file_key") or "")
                res_type = str(item.get("resource_type") or ("image" if kind == "image" else "file"))
                if file_key:
                    raw_bytes = download_message_resource(
                        msg_id,
                        file_key,
                        res_type,
                        max_bytes=settings.max_upload_bytes,
                        **feishu_kwargs,
                    )
        except Exception:
            if kind == "audio" and item.get("wechat_asr_text"):
                asr_transcripts.append(str(item["wechat_asr_text"]).strip())
            continue

        if not raw_bytes:
            if kind == "audio" and item.get("wechat_asr_text"):
                asr_transcripts.append(str(item["wechat_asr_text"]).strip())
            continue

        file_record = store_im_bytes(
            db,
            ctx,
            settings,
            filename=filename,
            content=raw_bytes,
            mime_type=mime_type,
            source=f"im:{'wechat' if is_wechat else 'feishu'}",
        )
        if kind == "audio":
            transcript = transcribe_im_audio(
                db,
                ctx,
                settings,
                file_record=file_record,
                raw_bytes=raw_bytes,
                filename=file_record.original_name,
                mime_type=file_record.mime_type,
                idempotency_key=f"{row.id}:{idx}",
                fallback_text=str(item.get("wechat_asr_text") or ""),
            )
            if transcript:
                asr_transcripts.append(transcript)
            else:
                file_ids.append(file_record.id)
        else:
            file_ids.append(file_record.id)

    prompt_parts = [p for p in (row.text.strip(), *asr_transcripts) if p]
    prompt_text = "\n".join(prompt_parts).strip()
    if not prompt_text and file_ids:
        prompt_text = "请解析并结合我发送的文件内容进行解答。"
    return prompt_text, file_ids


def _collect_assistant_part_media(db: Session, submission: MessageSubmission | None) -> list[dict]:
    from app.domain.models import MessagePartRecord

    if not submission or not submission.assistant_message_version_id:
        return []
    parts = list(
        db.scalars(
            select(MessagePartRecord).where(
                MessagePartRecord.message_version_id == submission.assistant_message_version_id,
                MessagePartRecord.status == "completed",
            )
        )
    )
    media: list[dict] = []
    for part in parts:
        data = part.data if isinstance(part.data, dict) else {}
        file_id = data.get("file_id")
        if not isinstance(file_id, str) or not file_id:
            continue
        if part.part_type == "image":
            media.append({
                "kind": "image",
                "file_id": file_id,
                "filename": str(data.get("filename") or f"{file_id}.png"),
                "mime_type": str(data.get("mime_type") or "image/png"),
            })
        elif part.part_type in {"sandbox_artifact", "subapp_artifact"}:
            media.append({
                "kind": "file",
                "file_id": file_id,
                "filename": str(data.get("filename") or data.get("title") or f"{file_id}.bin"),
                "mime_type": str(data.get("mime_type") or "application/octet-stream"),
            })
    return media


def _answer(
    db: Session,
    settings: Settings,
    row: IMInbound,
    binding: IMBinding,
    ctx: WorkspaceContext,
    *,
    prompt_text: str | None = None,
    file_ids: list[str] | None = None,
) -> str | None:
    from app.services.chat_service_factory import build_chat_service

    key = f"im:{row.id}"
    service = build_chat_service(db, workspace_context=ctx, settings=settings, agent_mode=False, search_route="disabled")
    if not binding.session_id:
        title = "微信 · 学习助手" if row.connection.startswith("wechat:") else "飞书 · 学习助手"
        chat = service.create_session(SessionCreateRequest(title=title, session_kind="standalone", memory_enabled=False),
                                      idempotency_key=f"im-session:{binding.id}")
        binding.session_id = chat.id
        db.commit()
    session_id = binding.session_id
    query = select(MessageSubmission).where(MessageSubmission.workspace_id == row.workspace_id,
                                            MessageSubmission.session_id == session_id, MessageSubmission.idempotency_key_hash == digest(key))
    submission = db.scalar(query)
    if not submission:
        content = (prompt_text if prompt_text is not None else row.text) or "请解析并解答我发送的内容。"
        req_kwargs: dict = {"content": content, "file_ids": file_ids or []}
        if binding.preferred_model_id:
            req_kwargs["model_id"] = binding.preferred_model_id
        if binding.preferred_provider_id:
            req_kwargs["provider_id"] = binding.preferred_provider_id
        for _ in service.create_stream(
            session_id,
            MessageCreateRequest(**req_kwargs),
            idempotency_key=key,
        ):
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


def _build_outbound_media(
    db: Session,
    settings: Settings,
    row: IMInbound,
    binding: IMBinding,
    ctx: WorkspaceContext,
    text: str,
) -> tuple[str, list[dict]]:
    from app.services.im_media import extract_html_blocks, render_html_to_png, store_im_bytes, synthesize_tts_audio

    outbound_media: list[dict] = []
    if binding.session_id:
        query = select(MessageSubmission).where(
            MessageSubmission.workspace_id == row.workspace_id,
            MessageSubmission.session_id == binding.session_id,
            MessageSubmission.idempotency_key_hash == digest(f"im:{row.id}"),
        )
        submission = db.scalar(query)
        outbound_media.extend(_collect_assistant_part_media(db, submission))

    cleaned_text, html_blocks = extract_html_blocks(text)
    for idx, html_block in enumerate(html_blocks):
        try:
            png_bytes = render_html_to_png(html_block)
            rec = store_im_bytes(
                db,
                ctx,
                settings,
                filename=f"html_preview_{idx + 1}.png",
                content=png_bytes,
                mime_type="image/png",
                source="im:html_preview",
            )
            outbound_media.append({
                "kind": "image",
                "file_id": rec.id,
                "filename": rec.original_name,
                "mime_type": "image/png",
            })
        except Exception:
            pass

    if row.tts_requested and cleaned_text:
        tts_result = synthesize_tts_audio(
            db,
            ctx,
            settings,
            text=cleaned_text,
            prefer_opus=not row.connection.startswith("wechat:"),
        )
        if tts_result is not None:
            audio_bytes, audio_name, audio_mime, duration_ms = tts_result
            audio_rec = store_im_bytes(
                db,
                ctx,
                settings,
                filename=audio_name,
                content=audio_bytes,
                mime_type=audio_mime,
                source="im:tts",
            )
            outbound_media.append({
                "kind": "audio",
                "file_id": audio_rec.id,
                "filename": audio_rec.original_name,
                "mime_type": audio_rec.mime_type,
                "duration_ms": duration_ms,
            })

    return cleaned_text or text, outbound_media


_MODEL_LIST_RE = re.compile(
    r"^\s*(?:/models?|查看模型|有哪些模型|当前模型|模型列表)\s*$",
    re.IGNORECASE,
)
_MODEL_SWITCH_RE = re.compile(
    r"^\s*(?:/(?:model|switch)\s+|(?:切换(?:模型|到|为)?|换(?:成|用)?(?:模型)?|使用模型)\s*[:：]?\s*)(.+?)\s*$",
    re.IGNORECASE,
)


def _list_workspace_chat_models(db: Session, workspace_id: str) -> list[tuple[str, str, str]]:
    from app.domain.models import ProviderConfig
    from app.providers.catalog import MODEL_PROVIDER_TYPES
    from app.providers.factory import _pinned_model_rejection, _provider_priority_order
    from app.providers.model_catalog import unified_model_defaults

    providers = list(
        db.scalars(
            select(ProviderConfig)
            .where(
                ProviderConfig.workspace_id == workspace_id,
                ProviderConfig.enabled.is_(True),
                ProviderConfig.provider_type.in_(MODEL_PROVIDER_TYPES),
            )
            .order_by(*_provider_priority_order())
        )
    )
    candidates: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for provider in providers:
        caps = dict(provider.capabilities or {})
        raw_ids: list[str] = []
        default_m = str(caps.get("default_model") or "").strip()
        if default_m:
            raw_ids.append(default_m)
        for m in caps.get("discovered_model_ids") or []:
            ms = str(m).strip()
            if ms:
                raw_ids.append(ms)
        models_map = caps.get("models")
        if isinstance(models_map, dict):
            for k in models_map:
                ks = str(k).strip()
                if ks:
                    raw_ids.append(ks)
        label = str(provider.display_name or provider.provider_type).strip()
        for mid in raw_ids:
            if (provider.id, mid) in seen:
                continue
            if _pinned_model_rejection(caps, mid) is not None:
                continue
            defaults = unified_model_defaults(mid, provider_type=provider.provider_type)
            if defaults.get("supports_text_output", True) is False:
                continue
            seen.add((provider.id, mid))
            candidates.append((provider.id, label, mid))
    return candidates


def _handle_model_command(db: Session, binding: IMBinding, text: str) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    if _MODEL_LIST_RE.match(stripped):
        candidates = _list_workspace_chat_models(db, binding.workspace_id)
        current = binding.preferred_model_id or "工作区默认模型"
        if not candidates:
            return f"当前模型：{current}\n工作区暂未配置可用的对话模型。"
        lines = [f"当前模型：{current}", "可用模型列表："]
        for _pid, pname, mid in candidates[:20]:
            mark = "（当前）" if binding.preferred_model_id == mid else ""
            lines.append(f"- {mid}（{pname}）{mark}")
        lines.append("发送「/model 模型名」或「切换模型 模型名」即可切换，发送「/model default」恢复默认。")
        return "\n".join(lines)

    match = _MODEL_SWITCH_RE.match(stripped)
    if not match:
        if stripped in {"恢复默认模型", "使用默认模型"}:
            binding.preferred_provider_id = None
            binding.preferred_model_id = None
            db.commit()
            return "已恢复使用工作区默认模型。"
        return None

    target = match.group(1).strip()
    target = re.sub(r"\s*模型\s*$", "", target).strip()
    if not target:
        return None
    if target.casefold() in {"default", "reset", "默认", "默认模型"}:
        binding.preferred_provider_id = None
        binding.preferred_model_id = None
        db.commit()
        return "已恢复使用工作区默认模型。"

    candidates = _list_workspace_chat_models(db, binding.workspace_id)
    if not candidates:
        return "工作区暂未配置可用的对话模型，请先在 LearnGraph 网页端启用模型 Provider。"

    needle = target.casefold()
    chosen = next((c for c in candidates if c[2].casefold() == needle), None)
    if chosen is None:
        chosen = next((c for c in candidates if needle in c[2].casefold() or needle in c[1].casefold()), None)
    if chosen is None:
        preview = "、".join(c[2] for c in candidates[:10])
        return f"未找到匹配的模型「{target}」。\n当前可用模型：{preview}"

    pid, pname, mid = chosen
    binding.preferred_provider_id = pid
    binding.preferred_model_id = mid
    db.commit()
    return f"已切换模型为：{mid}（{pname}）。后续消息将使用该模型回答。"


def process(db: Session, settings: Settings, row_id: str) -> bool:
    row = db.get(IMInbound, row_id)
    if not row or row.status != "queued":
        return True
    target = _target(db, row)
    if not target:
        row.status, row.text, row.media_json = "cancelled", "", ""
        db.commit()
        return True
    pending_ids = select(DurableJob.dedupe_key).where(DurableJob.workspace_id == row.workspace_id,
                                                     DurableJob.kind == "im.process", DurableJob.status.in_(("queued", "leased")))
    older = db.scalar(select(IMInbound.id).where(IMInbound.binding_id == row.binding_id, IMInbound.status == "queued",
                       ("im.process:" + IMInbound.id).in_(pending_ids),
                       (IMInbound.created_at < row.created_at) | ((IMInbound.created_at == row.created_at) & (IMInbound.id < row.id))).limit(1))
    if older:
        return False
    binding, ctx = target
    has_media = bool((row.media_json or "").strip())
    if not row.text and not has_media:
        text = "已绑定 LearnGraph。现在可以直接发送学习问题。"
        outbound_media: list[dict] = []
    elif not has_media and (model_reply := _handle_model_command(db, binding, row.text)) is not None:
        text = model_reply
        outbound_media = []
    else:
        try:
            if has_media:
                prompt_text, file_ids = _prepare_inbound_media(db, settings, row, binding, ctx)
                text = _answer(db, settings, row, binding, ctx, prompt_text=prompt_text, file_ids=file_ids)
            else:
                text = _answer(db, settings, row, binding, ctx)
        except AppError as exc:
            if exc.status_code in {408, 429, 500, 502, 503, 504}:
                raise
            db.rollback()
            text = "当前无法处理这条消息，请在 LearnGraph 检查会话或模型配置。"
        outbound_media = []
    if text is None:
        if (utc_now() - row.created_at.replace(tzinfo=timezone.utc)) < timedelta(minutes=20):
            return False
        text = "这次生成中断，请在 LearnGraph 查看会话。"
    if text and (row.tts_requested or "```html" in text.casefold() or "<html" in text.casefold() or binding.session_id):
        if settings is not None:
            text, outbound_media = _build_outbound_media(db, settings, row, binding, ctx, text)
    encoded = text.encode("utf-8")
    if len(encoded) > 24000:
        text = encoded[:24000].decode("utf-8", errors="ignore") + "\n\n（其余内容请在 LearnGraph 查看。）"
    row.status, row.text, row.media_json = "processed", "", ""
    db.add(IMOutbound(
        id=row.id,
        workspace_id=row.workspace_id,
        text=text,
        media_json=json.dumps(outbound_media, ensure_ascii=False) if outbound_media else "",
    ))
    DurableQueue(db).enqueue(workspace_id=row.workspace_id, kind="im.send", payload={"id": row.id},
                             dedupe_key=f"im.send:{row.id}", commit=False)
    db.commit()
    return True


def _deliver_media_items(
    db: Session,
    settings: Settings,
    ctx: WorkspaceContext,
    *,
    is_wechat: bool,
    sender: str,
    row_id: str,
    media_items: list[dict],
    wechat_kwargs: dict[str, str],
    feishu_kwargs: dict[str, str],
) -> str | None:
    from app.services.files import FileService
    from app.services.im_feishu import send_audio as send_feishu_audio, send_file as send_feishu_file, send_image as send_feishu_image
    from app.services.im_wechat import send_audio as send_wechat_audio, send_file as send_wechat_file, send_image as send_wechat_image

    file_service = FileService(db, ctx.workspace_id, ctx.principal.user_id, settings)
    last_receipt: str | None = None
    for idx, item in enumerate(media_items):
        if not isinstance(item, dict):
            continue
        file_id = str(item.get("file_id") or "").strip()
        if not file_id:
            continue
        record, content = file_service.content(file_id)
        kind = str(item.get("kind") or "file")
        filename = str(item.get("filename") or record.original_name)
        nonce = f"{row_id}-m{idx}"
        if is_wechat:
            if kind == "image":
                last_receipt = send_wechat_image(sender, content, filename, nonce, **wechat_kwargs)
            elif kind == "audio":
                last_receipt = send_wechat_audio(
                    sender,
                    content,
                    filename,
                    nonce,
                    duration_ms=int(item.get("duration_ms") or 1000),
                    **wechat_kwargs,
                )
            else:
                last_receipt = send_wechat_file(sender, content, filename, nonce, **wechat_kwargs)
        else:
            if kind == "image":
                last_receipt = send_feishu_image(sender, content, filename, nonce, **feishu_kwargs)
            elif kind == "audio":
                last_receipt = send_feishu_audio(
                    sender,
                    content,
                    filename,
                    nonce,
                    duration_ms=int(item.get("duration_ms") or 1000),
                    **feishu_kwargs,
                )
            else:
                last_receipt = send_feishu_file(
                    sender,
                    content,
                    filename,
                    nonce,
                    mime_type=record.mime_type,
                    **feishu_kwargs,
                )
    return last_receipt


def deliver(db: Session, row_id: str, settings: Settings | None = None) -> None:
    from app.core.config import get_settings
    from app.services.im_feishu import send_text as send_feishu_text
    from app.services.im_wechat import send_text as send_wechat_text

    outbound, row = db.get(IMOutbound, row_id), db.get(IMInbound, row_id)
    if not row or not outbound or outbound.status not in {"queued", "sending"}:
        return
    resolved_settings = settings or get_settings()
    target = _target(db, row)
    media_items: list[dict] = []
    if outbound.media_json:
        try:
            parsed_media = json.loads(outbound.media_json)
            if isinstance(parsed_media, list):
                media_items = parsed_media
        except ValueError:
            media_items = []
    if not target:
        outbound.status, outbound.text, outbound.media_json = "cancelled", "", ""
    elif outbound.first_attempt_at and utc_now() - outbound.first_attempt_at.replace(tzinfo=timezone.utc) > timedelta(minutes=45):
        # Do not retry beyond the platform deduplication window.
        outbound.status, outbound.text, outbound.media_json = "uncertain", "", ""
    elif row.connection.startswith("wechat:"):
        cfg = wechat_config_for_user(db, user_id=target[0].user_id, workspace_id=target[0].workspace_id)
        if cfg is None or f"wechat:{cfg.app_id}" != row.connection:
            outbound.status, outbound.text, outbound.media_json = "cancelled", "", ""
        else:
            bot_token, base_url, _ilink_user_id = decrypt_wechat_credentials(resolved_settings, cfg)
            msg_ctx = db.get(IMMessageContext, row.id)
            state = db.get(IMChannelState, cfg.id)
            context_token = (msg_ctx.context_token if msg_ctx else "") or (state.context_token if state else "")
            outbound.status = "sending"
            outbound.first_attempt_at = outbound.first_attempt_at or utc_now()
            db.commit()
            try:
                wechat_kwargs = {
                    "bot_token": bot_token,
                    "base_url": base_url,
                    "context_token": context_token,
                }
                if outbound.text.strip():
                    outbound.receipt = send_wechat_text(
                        target[0].sender,
                        outbound.text,
                        row.id,
                        **wechat_kwargs,
                    )
                if media_items:
                    media_receipt = _deliver_media_items(
                        db,
                        resolved_settings,
                        target[1],
                        is_wechat=True,
                        sender=target[0].sender,
                        row_id=row.id,
                        media_items=media_items,
                        wechat_kwargs=wechat_kwargs,
                        feishu_kwargs={},
                    )
                    if media_receipt:
                        outbound.receipt = media_receipt
            except AppError as exc:
                if exc.status_code in {429, 503}:
                    raise
                if exc.code == "wechat_session_expired":
                    state_row = db.get(IMChannelState, cfg.id)
                    if state_row is None:
                        state_row = IMChannelState(config_id=cfg.id)
                        db.add(state_row)
                    state_row.session_expired = True
                    state_row.last_error = "微信登录态已失效，请重新扫码登录。"
                outbound.status, outbound.text, outbound.media_json = "failed", "", ""
            else:
                outbound.status, outbound.text, outbound.media_json = "sent", "", ""
    else:
        cfg = feishu_config_for_user(db, user_id=target[0].user_id, workspace_id=target[0].workspace_id)
        send_kwargs: dict[str, str] = {}
        if cfg is not None and f"feishu:{cfg.app_id}" == row.connection:
            app_secret, domain = decrypt_feishu_credentials(resolved_settings, cfg)
            send_kwargs = {
                "app_id": cfg.app_id,
                "app_secret": app_secret,
            }
            if domain == "lark":
                send_kwargs["domain"] = "lark"
        outbound.status = "sending"
        outbound.first_attempt_at = outbound.first_attempt_at or utc_now()
        db.commit()
        try:
            if outbound.text.strip():
                outbound.receipt = send_feishu_text(target[0].sender, outbound.text, row.id, **send_kwargs)
            if media_items:
                media_receipt = _deliver_media_items(
                    db,
                    resolved_settings,
                    target[1],
                    is_wechat=False,
                    sender=target[0].sender,
                    row_id=row.id,
                    media_items=media_items,
                    wechat_kwargs={},
                    feishu_kwargs=send_kwargs,
                )
                if media_receipt:
                    outbound.receipt = media_receipt
        except AppError as exc:
            if exc.status_code in {429, 503}:
                raise
            outbound.status, outbound.text, outbound.media_json = "failed", "", ""
        else:
            outbound.status, outbound.text, outbound.media_json = "sent", "", ""
    db.commit()
