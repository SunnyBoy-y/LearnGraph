from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import os
import secrets
import threading
import time

import httpx

from app.core.errors import AppError

logger = logging.getLogger(__name__)


BASE = "https://open.feishu.cn/open-apis"
LARK_BASE = "https://open.larksuite.com/open-apis"
FEISHU_ACCOUNTS_URL = "https://accounts.feishu.cn"
LARK_ACCOUNTS_URL = "https://accounts.larksuite.com"
REGISTRATION_PATH = "/oauth/v1/app/registration"


def open_api_base(domain: str = "feishu") -> str:
    return LARK_BASE if domain == "lark" else BASE


def accounts_base(domain: str = "feishu") -> str:
    return LARK_ACCOUNTS_URL if domain == "lark" else FEISHU_ACCOUNTS_URL


def _tenant_token(
    client: httpx.Client,
    api_base: str,
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
) -> str:
    resolved_app_id = (app_id if app_id is not None else os.environ["LEARNGRAPH_FEISHU_APP_ID"]).strip()
    resolved_app_secret = app_secret if app_secret is not None else os.environ["LEARNGRAPH_FEISHU_APP_SECRET"]
    token = client.post(f"{api_base}/auth/v3/tenant_access_token/internal", json={
        "app_id": resolved_app_id,
        "app_secret": resolved_app_secret,
    })
    credentials = _result(token)
    tenant_access_token = credentials.get("tenant_access_token")
    if not tenant_access_token:
        raise AppError(422, "feishu_auth_failed", "Feishu bot credentials were rejected")
    return str(tenant_access_token)


def send_text(
    sender: str,
    text: str,
    nonce: str,
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    domain: str = "feishu",
) -> str:
    api_base = open_api_base(domain)
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            tenant_token = _tenant_token(client, api_base, app_id=app_id, app_secret=app_secret)
            reply = client.post(
                f"{api_base}/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {tenant_token}"},
                json={
                    "receive_id": sender,
                    "msg_type": "text",
                    "uuid": nonce,
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
            receipt = _result(reply).get("data", {}).get("message_id")
            if not receipt:
                raise AppError(503, "feishu_receipt_unknown", "Feishu did not return a message receipt")
            return receipt
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "feishu_unavailable", "Feishu is unavailable") from exc


def send_image(
    sender: str,
    image_bytes: bytes,
    filename: str,
    nonce: str,
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    domain: str = "feishu",
) -> str:
    api_base = open_api_base(domain)
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            tenant_token = _tenant_token(client, api_base, app_id=app_id, app_secret=app_secret)
            upload_resp = client.post(
                f"{api_base}/im/v1/images",
                headers={"Authorization": f"Bearer {tenant_token}"},
                data={"image_type": "message"},
                files={"image": (filename or "image.png", image_bytes, "image/png")},
            )
            image_key = _result(upload_resp).get("data", {}).get("image_key")
            if not image_key:
                raise AppError(503, "feishu_upload_failed", "Feishu did not return an image_key")
            reply = client.post(
                f"{api_base}/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {tenant_token}"},
                json={
                    "receive_id": sender,
                    "msg_type": "image",
                    "uuid": nonce,
                    "content": json.dumps({"image_key": image_key}, ensure_ascii=False),
                },
            )
            receipt = _result(reply).get("data", {}).get("message_id")
            if not receipt:
                raise AppError(503, "feishu_receipt_unknown", "Feishu did not return a message receipt")
            return receipt
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "feishu_unavailable", "Feishu is unavailable") from exc


def _feishu_file_type(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].casefold()
    mapping = {
        ".opus": "opus",
        ".ogg": "opus",
        ".mp4": "mp4",
        ".pdf": "pdf",
        ".doc": "doc",
        ".docx": "doc",
        ".xls": "xls",
        ".xlsx": "xls",
        ".ppt": "ppt",
        ".pptx": "ppt",
    }
    return mapping.get(ext, "stream")


def send_file(
    sender: str,
    file_bytes: bytes,
    filename: str,
    nonce: str,
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    domain: str = "feishu",
    mime_type: str = "application/octet-stream",
) -> str:
    api_base = open_api_base(domain)
    resolved_name = filename or "attachment.bin"
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            tenant_token = _tenant_token(client, api_base, app_id=app_id, app_secret=app_secret)
            upload_resp = client.post(
                f"{api_base}/im/v1/files",
                headers={"Authorization": f"Bearer {tenant_token}"},
                data={"file_type": _feishu_file_type(resolved_name), "file_name": resolved_name},
                files={"file": (resolved_name, file_bytes, mime_type)},
            )
            file_key = _result(upload_resp).get("data", {}).get("file_key")
            if not file_key:
                raise AppError(503, "feishu_upload_failed", "Feishu did not return a file_key")
            reply = client.post(
                f"{api_base}/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {tenant_token}"},
                json={
                    "receive_id": sender,
                    "msg_type": "file",
                    "uuid": nonce,
                    "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
                },
            )
            receipt = _result(reply).get("data", {}).get("message_id")
            if not receipt:
                raise AppError(503, "feishu_receipt_unknown", "Feishu did not return a message receipt")
            return receipt
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "feishu_unavailable", "Feishu is unavailable") from exc


def send_audio(
    sender: str,
    audio_bytes: bytes,
    filename: str,
    nonce: str,
    *,
    duration_ms: int = 1000,
    app_id: str | None = None,
    app_secret: str | None = None,
    domain: str = "feishu",
) -> str:
    api_base = open_api_base(domain)
    resolved_name = filename or "reply.ogg"
    ext = os.path.splitext(resolved_name)[1].casefold()
    is_opus = ext in {".opus", ".ogg"}
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            tenant_token = _tenant_token(client, api_base, app_id=app_id, app_secret=app_secret)
            upload_data = {
                "file_type": "opus" if is_opus else "stream",
                "file_name": resolved_name,
            }
            if is_opus:
                upload_data["duration"] = str(max(100, int(duration_ms)))
            upload_resp = client.post(
                f"{api_base}/im/v1/files",
                headers={"Authorization": f"Bearer {tenant_token}"},
                data=upload_data,
                files={"file": (resolved_name, audio_bytes, "audio/ogg" if is_opus else "audio/wav")},
            )
            file_key = _result(upload_resp).get("data", {}).get("file_key")
            if not file_key:
                raise AppError(503, "feishu_upload_failed", "Feishu did not return a file_key")
            msg_type = "audio" if is_opus else "file"
            reply = client.post(
                f"{api_base}/im/v1/messages",
                params={"receive_id_type": "open_id"},
                headers={"Authorization": f"Bearer {tenant_token}"},
                json={
                    "receive_id": sender,
                    "msg_type": msg_type,
                    "uuid": nonce,
                    "content": json.dumps({"file_key": file_key}, ensure_ascii=False),
                },
            )
            receipt = _result(reply).get("data", {}).get("message_id")
            if not receipt:
                raise AppError(503, "feishu_receipt_unknown", "Feishu did not return a message receipt")
            return receipt
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "feishu_unavailable", "Feishu is unavailable") from exc


def download_message_resource(
    message_id: str,
    file_key: str,
    resource_type: str,
    *,
    app_id: str | None = None,
    app_secret: str | None = None,
    domain: str = "feishu",
    max_bytes: int = 32 * 1024 * 1024,
) -> bytes:
    api_base = open_api_base(domain)
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            tenant_token = _tenant_token(client, api_base, app_id=app_id, app_secret=app_secret)
            resp = client.get(
                f"{api_base}/im/v1/messages/{message_id}/resources/{file_key}",
                params={"type": resource_type},
                headers={"Authorization": f"Bearer {tenant_token}"},
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise AppError(503, "feishu_unavailable", "Feishu resource download temporarily unavailable")
            if not resp.is_success:
                raise AppError(422, "feishu_resource_failed", "Feishu rejected resource download")
            if len(resp.content) > max_bytes:
                raise AppError(413, "im_attachment_too_large", "IM attachment exceeds size limit")
            return resp.content
    except AppError:
        raise
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "feishu_unavailable", "Feishu resource download failed") from exc


def _result(response: httpx.Response) -> dict:
    if response.status_code == 429 or response.status_code >= 500:
        raise AppError(503, "feishu_unavailable", "Feishu is temporarily unavailable")
    data = response.json()
    if data.get("code") in (99991400, 230020):
        raise AppError(429, "feishu_throttled", "Feishu rate limit")
    if not response.is_success or data.get("code") != 0:
        raise AppError(422, "feishu_rejected", "Feishu rejected the request")
    return data


def parse_event(data) -> tuple[str, str, str] | None:
    event = data.event
    message, sender = event.message, event.sender
    if message.chat_type != "p2p" or message.message_type != "text" or sender.sender_type != "user" or not sender.sender_id:
        return None
    user, mid = sender.sender_id.open_id, message.message_id
    try:
        payload = json.loads(message.content)
    except (TypeError, ValueError):
        return None
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str) or not text.strip() or len(text) > 8000 or not user or len(user) > 192 or not mid or len(mid) > 128:
        return None
    return user, mid, text.strip()


def parse_event_rich(data) -> dict | None:
    """Parse p2p Feishu message events including text, image, file, audio, media, and post."""
    event = data.event
    message, sender = event.message, event.sender
    if message.chat_type != "p2p" or sender.sender_type != "user" or not sender.sender_id:
        return None
    user, mid = sender.sender_id.open_id, message.message_id
    if not user or len(user) > 192 or not mid or len(mid) > 128:
        return None
    try:
        payload = json.loads(message.content)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None

    mtype = message.message_type
    if mtype == "text":
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            return None
        return {"sender": user, "message_id": mid, "text": text.strip(), "media": []}

    if mtype == "image":
        image_key = str(payload.get("image_key") or "").strip()
        if not image_key:
            return None
        return {
            "sender": user,
            "message_id": mid,
            "text": "",
            "media": [{"kind": "image", "source": "feishu", "message_id": mid, "file_key": image_key, "resource_type": "image", "filename": f"{image_key}.png", "mime_type": "image/png"}],
        }

    if mtype == "file":
        file_key = str(payload.get("file_key") or "").strip()
        if not file_key:
            return None
        filename = str(payload.get("file_name") or f"{file_key}.bin").strip()
        return {
            "sender": user,
            "message_id": mid,
            "text": "",
            "media": [{"kind": "file", "source": "feishu", "message_id": mid, "file_key": file_key, "resource_type": "file", "filename": filename}],
        }

    if mtype == "audio":
        file_key = str(payload.get("file_key") or "").strip()
        if not file_key:
            return None
        duration = payload.get("duration")
        return {
            "sender": user,
            "message_id": mid,
            "text": "",
            "media": [{"kind": "audio", "source": "feishu", "message_id": mid, "file_key": file_key, "resource_type": "file", "filename": f"{file_key}.opus", "mime_type": "audio/ogg", "duration_ms": duration}],
        }

    if mtype == "media":
        file_key = str(payload.get("file_key") or "").strip()
        if not file_key:
            return None
        filename = str(payload.get("file_name") or f"{file_key}.mp4").strip()
        return {
            "sender": user,
            "message_id": mid,
            "text": "",
            "media": [{"kind": "file", "source": "feishu", "message_id": mid, "file_key": file_key, "resource_type": "file", "filename": filename}],
        }

    if mtype == "post":
        texts: list[str] = []
        media: list[dict] = []
        title = payload.get("title")
        if isinstance(title, str) and title.strip():
            texts.append(title.strip())
        content = payload.get("content")
        if isinstance(content, list):
            for line in content:
                if not isinstance(line, list):
                    continue
                for elem in line:
                    if not isinstance(elem, dict):
                        continue
                    tag = elem.get("tag")
                    if tag == "text" and isinstance(elem.get("text"), str):
                        texts.append(elem["text"])
                    elif tag == "a" and isinstance(elem.get("text"), str):
                        href = elem.get("href") or ""
                        texts.append(f"{elem['text']} ({href})" if href else elem["text"])
                    elif tag == "img" and elem.get("image_key"):
                        ikey = str(elem["image_key"]).strip()
                        media.append({"kind": "image", "source": "feishu", "message_id": mid, "file_key": ikey, "resource_type": "image", "filename": f"{ikey}.png", "mime_type": "image/png"})
                    elif tag == "emotion" and elem.get("emoji_type"):
                        texts.append(f"[表情: {elem['emoji_type']}]")
        joined = "\n".join(t for t in texts if t.strip()).strip()
        if not joined and not media:
            return None
        return {"sender": user, "message_id": mid, "text": joined[:8000], "media": media}

    if mtype == "sticker":
        file_key = str(payload.get("file_key") or "").strip()
        if not file_key:
            return None
        return {
            "sender": user,
            "message_id": mid,
            "text": "[表情]",
            "media": [{"kind": "image", "source": "feishu", "message_id": mid, "file_key": file_key, "resource_type": "image", "filename": f"{file_key}.png", "mime_type": "image/png"}],
        }

    return None


def _post_registration(base_url: str, form_data: dict[str, str]) -> dict:
    action = form_data.get("action", "unknown")
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            resp = client.post(
                f"{base_url}{REGISTRATION_PATH}",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data=form_data,
            )
            if resp.status_code >= 500:
                logger.warning("Feishu registration %s failed with HTTP %s", action, resp.status_code)
                raise AppError(503, "feishu_unavailable", "Feishu registration service is unavailable")
            data = resp.json()
            if not isinstance(data, dict):
                logger.warning("Feishu registration %s returned non-dict JSON (HTTP %s)", action, resp.status_code)
                raise AppError(503, "feishu_unavailable", "Feishu returned invalid registration payload")
            if resp.status_code >= 400 and "error" not in data:
                logger.warning("Feishu registration %s failed with HTTP %s: %s", action, resp.status_code, data)
                raise AppError(503, "feishu_unavailable", "Feishu registration service rejected the request")
            return data
    except AppError:
        raise
    except (httpx.TransportError, ValueError) as exc:
        logger.warning("Feishu registration %s transport/parse error: %s", action, exc)
        raise AppError(503, "feishu_unavailable", "Unable to reach Feishu registration service") from exc


def get_app_owner_open_id(app_id: str, app_secret: str, *, domain: str = "feishu") -> str | None:
    api_base = open_api_base(domain)
    try:
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            token_resp = client.post(
                f"{api_base}/auth/v3/tenant_access_token/internal",
                json={"app_id": app_id, "app_secret": app_secret},
            )
            token_data = token_resp.json()
            tenant_token = token_data.get("tenant_access_token") if isinstance(token_data, dict) else None
            if not tenant_token:
                return None
            app_resp = client.get(
                f"{api_base}/application/v6/applications/{app_id}",
                params={"user_id_type": "open_id"},
                headers={"Authorization": f"Bearer {tenant_token}"},
            )
            app_data = app_resp.json()
            if not isinstance(app_data, dict) or app_data.get("code") != 0:
                return None
            app_info = (app_data.get("data") or {}).get("app") or {}
            owner = app_info.get("owner") or {}
            owner_type = owner.get("owner_type") if owner.get("owner_type") is not None else owner.get("type")
            if owner_type == 2 and owner.get("owner_id"):
                return str(owner["owner_id"])
            creator = app_info.get("creator_id") or owner.get("owner_id")
            return str(creator) if creator else None
    except Exception:
        return None


@dataclass
class _FeishuQrSession:
    session_id: str
    user_id: str
    workspace_id: str
    device_code: str
    user_code: str
    qr_url: str
    domain: str
    domain_switched: bool
    interval: int
    expires_at: float


class FeishuQrRegistrationManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _FeishuQrSession] = {}

    def _prune_locked(self, now: float) -> None:
        expired = [sid for sid, sess in self._sessions.items() if sess.expires_at <= now]
        for sid in expired:
            del self._sessions[sid]

    def start(self, *, user_id: str, workspace_id: str, domain: str = "feishu") -> dict:
        resolved_domain = "lark" if domain == "lark" else "feishu"
        base_url = accounts_base(resolved_domain)
        init_res = _post_registration(base_url, {"action": "init"})
        if init_res.get("error"):
            desc = str(init_res.get("error_description") or init_res.get("error")).strip()
            logger.warning("Feishu QR init returned error: %s", desc)
            raise AppError(503, "feishu_qr_failed", desc or "Feishu QR initialization failed")
        methods = init_res.get("supported_auth_methods")
        if not isinstance(methods, list) or "client_secret" not in methods:
            logger.warning("Feishu QR init missing client_secret support: %s", methods)
            raise AppError(503, "feishu_qr_unsupported", "Feishu QR registration does not support client_secret")

        begin_res = _post_registration(
            base_url,
            {
                "action": "begin",
                "archetype": "PersonalAgent",
                "auth_method": "client_secret",
                "request_user_info": "open_id",
            },
        )
        if begin_res.get("error"):
            desc = str(begin_res.get("error_description") or begin_res.get("error")).strip()
            logger.warning("Feishu QR begin returned error: %s", desc)
            raise AppError(503, "feishu_qr_failed", desc or "Feishu QR registration begin failed")
        device_code = str(begin_res.get("device_code") or "").strip()
        qr_url = str(begin_res.get("verification_uri_complete") or "").strip()
        user_code = str(begin_res.get("user_code") or "").strip()
        if not device_code or not qr_url:
            logger.warning("Feishu QR begin returned incomplete payload: keys=%s", list(begin_res.keys()))
            raise AppError(503, "feishu_qr_failed", "Feishu did not return a valid registration QR code")

        raw_expire = begin_res.get("expires_in", begin_res.get("expire_in", 600))
        try:
            expires_in = max(60, min(3600, int(raw_expire)))
        except (TypeError, ValueError):
            expires_in = 600
        raw_interval = begin_res.get("interval", 5)
        try:
            interval = max(2, min(30, int(raw_interval)))
        except (TypeError, ValueError):
            interval = 5

        session_id = secrets.token_urlsafe(18)
        now = time.monotonic()
        session = _FeishuQrSession(
            session_id=session_id,
            user_id=user_id,
            workspace_id=workspace_id,
            device_code=device_code,
            user_code=user_code,
            qr_url=qr_url,
            domain=resolved_domain,
            domain_switched=False,
            interval=interval,
            expires_at=now + expires_in,
        )
        with self._lock:
            self._prune_locked(now)
            # Keep at most one active Feishu QR session per user/workspace.
            for existing_id in [
                sid
                for sid, sess in self._sessions.items()
                if sess.user_id == user_id and sess.workspace_id == workspace_id
            ]:
                del self._sessions[existing_id]
            self._sessions[session_id] = session

        return {
            "session_id": session_id,
            "qr_url": qr_url,
            "user_code": user_code,
            "expires_in": expires_in,
            "interval": interval,
        }

    def poll(self, session_id: str, *, user_id: str, workspace_id: str) -> dict:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            session = self._sessions.get(session_id)
            if not session or session.user_id != user_id or session.workspace_id != workspace_id:
                return {"status": "expired"}
            device_code = session.device_code
            domain = session.domain
            domain_switched = session.domain_switched
            interval = session.interval

        poll_res = _post_registration(
            accounts_base(domain),
            {"action": "poll", "device_code": device_code},
        )
        user_info = poll_res.get("user_info") if isinstance(poll_res.get("user_info"), dict) else {}
        if not domain_switched and user_info.get("tenant_brand") == "lark":
            domain = "lark"
            with self._lock:
                if session_id in self._sessions:
                    self._sessions[session_id].domain = "lark"
                    self._sessions[session_id].domain_switched = True
            poll_res = _post_registration(
                accounts_base(domain),
                {"action": "poll", "device_code": device_code},
            )
            user_info = poll_res.get("user_info") if isinstance(poll_res.get("user_info"), dict) else {}

        client_id = str(poll_res.get("client_id") or "").strip()
        client_secret = str(poll_res.get("client_secret") or "").strip()
        if client_id and client_secret:
            open_id = str(user_info.get("open_id") or "").strip() or None
            if not open_id:
                open_id = get_app_owner_open_id(client_id, client_secret, domain=domain)
            return {
                "status": "confirmed",
                "app_id": client_id,
                "app_secret": client_secret,
                "domain": domain,
                "open_id": open_id,
            }

        error = str(poll_res.get("error") or "").strip()
        if not error or error == "authorization_pending":
            return {"status": "pending", "interval": interval}
        if error == "slow_down":
            new_interval = min(30, interval + 5)
            with self._lock:
                if session_id in self._sessions:
                    self._sessions[session_id].interval = new_interval
            return {"status": "pending", "interval": new_interval}
        if error == "access_denied":
            self.cancel(session_id, user_id=user_id, workspace_id=workspace_id)
            return {"status": "denied"}
        if error == "expired_token":
            self.cancel(session_id, user_id=user_id, workspace_id=workspace_id)
            return {"status": "expired"}

        self.cancel(session_id, user_id=user_id, workspace_id=workspace_id)
        desc = str(poll_res.get("error_description") or error).strip()
        return {"status": "error", "message": desc}

    def consume(self, session_id: str, *, user_id: str, workspace_id: str) -> None:
        self.cancel(session_id, user_id=user_id, workspace_id=workspace_id)

    def cancel(self, session_id: str, *, user_id: str, workspace_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.user_id == user_id and session.workspace_id == workspace_id:
                del self._sessions[session_id]


feishu_qr_manager = FeishuQrRegistrationManager()


def run_listener(app_id: str, app_secret: str, *, domain: str = "feishu") -> None:
    from app.core.database import SessionLocal, retry_sqlite_locked
    from app.services.im_channels import intake
    import lark_oapi as lark

    connection = f"feishu:{app_id}"

    def on_message(data):
        parsed = parse_event_rich(data)
        if parsed:
            with SessionLocal() as db:
                retry_sqlite_locked(
                    db,
                    lambda: intake(
                        db,
                        connection,
                        parsed["sender"],
                        parsed["message_id"],
                        parsed["text"],
                        media=parsed.get("media") or None,
                    ),
                    attempts=6,
                    base_delay_seconds=0.2,
                )

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
    client_kwargs: dict = {"event_handler": handler, "log_level": lark.LogLevel.WARNING}
    if domain == "lark":
        client_kwargs["domain"] = lark.LARK_DOMAIN
    lark.ws.Client(app_id, app_secret, **client_kwargs).start()


def main(argv: list[str] | None = None) -> None:
    import argparse

    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.domain.im_models import IMChannelConfig
    from app.services.im_channels import decrypt_feishu_credentials, feishu_connection

    parser = argparse.ArgumentParser(description="LearnGraph Feishu WebSocket listener")
    parser.add_argument("--config-id", default=None, help="IMChannelConfig ID to listen for")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.durable_queue_enabled:
        raise SystemExit("Enable the durable queue before running the listener.")

    if args.config_id:
        with SessionLocal() as db:
            cfg = db.get(IMChannelConfig, args.config_id)
            if not cfg or cfg.provider != "feishu" or not cfg.enabled:
                raise SystemExit(f"Feishu config {args.config_id} is not active.")
            app_id = cfg.app_id
            app_secret, domain = decrypt_feishu_credentials(settings, cfg)
        run_listener(app_id, app_secret, domain=domain)
        return

    connection = feishu_connection()
    if not connection:
        raise SystemExit("Configure the Feishu bot and enable the durable queue before running the listener.")
    run_listener(connection.removeprefix("feishu:"), os.environ["LEARNGRAPH_FEISHU_APP_SECRET"])


if __name__ == "__main__":
    main()
