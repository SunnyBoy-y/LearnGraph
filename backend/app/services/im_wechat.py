"""WeChat ClawBot (Tencent iLink Bot API) QR login and messaging client."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import logging
import os
import secrets
import threading
import time
from urllib.parse import urlparse

from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
import httpx

from app.core.errors import AppError

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
BOT_TYPE = "3"
CHANNEL_VERSION = "1.0.0"
QR_SESSION_TTL_SECONDS = 300
MAX_TEXT_CHUNK = 1800
SESSION_EXPIRED_ERRCODE = -14


def normalize_base_url(raw: str | None) -> str:
    candidate = (raw or "").strip().rstrip("/")
    if not candidate:
        return DEFAULT_BASE_URL
    parsed = urlparse(candidate)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not (host == "weixin.qq.com" or host.endswith(".weixin.qq.com")):
        return DEFAULT_BASE_URL
    return f"https://{parsed.netloc}{parsed.path}".rstrip("/")


def build_headers(bot_token: str) -> dict[str, str]:
    uin = base64.b64encode(str(secrets.randbelow(2**32 - 1) + 1).encode("ascii")).decode("ascii")
    return {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {bot_token}",
        "X-WECHAT-UIN": uin,
    }


def _base_info() -> dict[str, str]:
    return {"channel_version": CHANNEL_VERSION}


def chunk_text(text: str, limit: int = MAX_TEXT_CHUNK) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []
    if len(stripped) <= limit:
        return [stripped]
    chunks: list[str] = []
    remaining = stripped
    while len(remaining) > limit:
        cut = remaining.rfind("\n\n", 0, limit)
        if cut < limit // 3:
            cut = remaining.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = remaining.rfind(" ", 0, limit)
        if cut < limit // 3:
            cut = limit
        piece = remaining[:cut].strip()
        if piece:
            chunks.append(piece)
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def parse_message(msg: dict) -> tuple[str, str, str, str] | None:
    """Return (sender_id, message_id, text, context_token) for a user text message."""
    if not isinstance(msg, dict):
        return None
    if msg.get("message_type") != 1:
        return None
    sender = str(msg.get("from_user_id") or "").strip()
    raw_mid = msg.get("message_id")
    mid = str(raw_mid if raw_mid is not None else msg.get("client_id") or "").strip()
    context_token = str(msg.get("context_token") or "").strip()
    items = msg.get("item_list")
    if not isinstance(items, list):
        return None
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("type") != 1:
            continue
        text_item = item.get("text_item")
        if isinstance(text_item, dict) and isinstance(text_item.get("text"), str):
            segment = text_item["text"].strip()
            if segment:
                parts.append(segment)
    text = "\n".join(parts).strip()
    if (
        not text
        or len(text) > 8000
        or not sender
        or len(sender) > 192
        or not mid
        or len(mid) > 128
    ):
        return None
    return sender, mid, text, context_token


def _extract_media_ref(container: dict, media_obj: dict, *, kind: str, filename: str, mime_type: str | None = None, extra: dict | None = None) -> dict | None:
    url = str(container.get("url") or container.get("cdnurl") or media_obj.get("full_url") or media_obj.get("url") or "").strip()
    enc = str(media_obj.get("encrypt_query_param") or container.get("encrypt_query_param") or "").strip()
    aes_key = str(media_obj.get("aes_key") or container.get("aeskey") or container.get("aes_key") or "").strip()
    if not url and not enc:
        return None
    ref: dict = {
        "kind": kind,
        "source": "wechat",
        "filename": filename,
        "url": url,
        "encrypt_query_param": enc,
        "aes_key": aes_key,
        "encrypt_type": media_obj.get("encrypt_type", 1),
    }
    if mime_type:
        ref["mime_type"] = mime_type
    if extra:
        ref.update(extra)
    return ref


def _parse_ilink_item(item: dict, mid: str, idx: int, *, is_quote: bool = False) -> tuple[list[str], list[dict]]:
    parts: list[str] = []
    media: list[dict] = []
    if not isinstance(item, dict):
        return parts, media
    itype = item.get("type")

    # Handle nested ref_msg (quoted/replied message) on item or text_item
    ref_candidates: list[dict] = []
    if isinstance(item.get("ref_msg"), dict):
        ref_candidates.append(item["ref_msg"])
    text_item = item.get("text_item") if isinstance(item.get("text_item"), dict) else None
    if text_item and isinstance(text_item.get("ref_msg"), dict):
        ref_candidates.append(text_item["ref_msg"])
    for ref_msg in ref_candidates:
        ref_parts: list[str] = []
        title = str(ref_msg.get("title") or "").strip()
        if title:
            ref_parts.append(title)
        nested = ref_msg.get("message_item")
        if isinstance(nested, dict):
            n_parts, n_media = _parse_ilink_item(nested, mid, idx + 100, is_quote=True)
            ref_parts.extend(n_parts)
            media.extend(n_media)
        if ref_parts:
            quoted = " ".join(p for p in ref_parts if p).strip()
            parts.append(f"[引用消息: {quoted}]")

    if itype == 1 and text_item and isinstance(text_item.get("text"), str):
        segment = text_item["text"].strip()
        if segment:
            parts.append(segment)
    elif itype == 2:
        img = item.get("image_item")
        if isinstance(img, dict):
            media_obj = img.get("media") if isinstance(img.get("media"), dict) else {}
            ref = _extract_media_ref(
                img,
                media_obj,
                kind="image",
                filename=f"{mid}_{'q_' if is_quote else ''}{idx}.png",
                mime_type="image/png",
            )
            if ref:
                media.append(ref)
    elif itype == 3:
        voice = item.get("voice_item")
        if isinstance(voice, dict):
            asr_text = str(voice.get("text") or "").strip()
            media_obj = voice.get("media") if isinstance(voice.get("media"), dict) else {}
            encode_type = voice.get("encode_type")
            ext = ".silk" if encode_type == 6 else ".amr" if encode_type in (1, 2) else ".ogg"
            ref = _extract_media_ref(
                voice,
                media_obj,
                kind="audio",
                filename=f"{mid}_{'q_' if is_quote else ''}{idx}{ext}",
                mime_type="audio/ogg" if ext == ".ogg" else f"audio/{ext.lstrip('.')}",
                extra={"wechat_asr_text": asr_text, "playtime": voice.get("playtime")},
            )
            if ref:
                media.append(ref)
            elif asr_text:
                parts.append(asr_text)
    elif itype == 4:
        fitem = item.get("file_item")
        if isinstance(fitem, dict):
            media_obj = fitem.get("media") if isinstance(fitem.get("media"), dict) else {}
            fname = str(fitem.get("file_name") or f"{mid}_{idx}.bin").strip()
            ref = _extract_media_ref(fitem, media_obj, kind="file", filename=fname)
            if ref:
                media.append(ref)
    elif itype == 5:
        vitem = item.get("video_item")
        if isinstance(vitem, dict):
            media_obj = vitem.get("media") if isinstance(vitem.get("media"), dict) else {}
            ref = _extract_media_ref(
                vitem,
                media_obj,
                kind="file",
                filename=f"{mid}_{'q_' if is_quote else ''}{idx}.mp4",
                mime_type="video/mp4",
            )
            if ref:
                media.append(ref)
    else:
        # Emoji / sticker items (type 6 / 47 or emoji_item / emotion_item / sticker_item)
        emoji_obj = None
        for key in ("emoji_item", "emotion_item", "sticker_item"):
            if isinstance(item.get(key), dict):
                emoji_obj = item[key]
                break
        if emoji_obj is not None or itype in (6, 47):
            e_dict = emoji_obj or {}
            desc = str(e_dict.get("desc") or e_dict.get("text") or e_dict.get("name") or e_dict.get("emoji") or "").strip()
            media_obj = e_dict.get("media") if isinstance(e_dict.get("media"), dict) else {}
            ref = _extract_media_ref(
                e_dict,
                media_obj,
                kind="image",
                filename=f"{mid}_emoji_{idx}.png",
                mime_type="image/png",
            )
            if ref:
                media.append(ref)
            parts.append(f"[表情: {desc}]" if desc else "[表情]")
        else:
            # Shared link / appmsg / card items
            link_obj = None
            for key in ("link_item", "appmsg_item", "url_item", "card_item"):
                if isinstance(item.get(key), dict):
                    link_obj = item[key]
                    break
            if link_obj is not None:
                l_title = str(link_obj.get("title") or "").strip()
                l_desc = str(link_obj.get("desc") or link_obj.get("description") or "").strip()
                l_url = str(link_obj.get("url") or "").strip()
                summary = " - ".join(x for x in (l_title, l_desc, l_url) if x)
                if summary:
                    parts.append(f"[链接卡片: {summary}]")

    return parts, media


def parse_message_rich(msg: dict) -> dict | None:
    """Return structured message payload including text, quotes, emojis, media items, and context_token."""
    if not isinstance(msg, dict):
        return None
    if msg.get("message_type") != 1:
        return None
    sender = str(msg.get("from_user_id") or "").strip()
    raw_mid = msg.get("message_id")
    mid = str(raw_mid if raw_mid is not None else msg.get("client_id") or "").strip()
    if not sender or len(sender) > 192 or not mid or len(mid) > 128:
        return None
    context_token = str(msg.get("context_token") or "").strip()
    items = msg.get("item_list")
    if not isinstance(items, list):
        return None

    parts: list[str] = []
    media: list[dict] = []

    top_ref = msg.get("ref_msg")
    if isinstance(top_ref, dict):
        r_title = str(top_ref.get("title") or "").strip()
        r_parts: list[str] = [r_title] if r_title else []
        if isinstance(top_ref.get("message_item"), dict):
            n_parts, n_media = _parse_ilink_item(top_ref["message_item"], mid, 900, is_quote=True)
            r_parts.extend(n_parts)
            media.extend(n_media)
        if r_parts:
            parts.append(f"[引用消息: {' '.join(p for p in r_parts if p).strip()}]")

    for idx, item in enumerate(items):
        i_parts, i_media = _parse_ilink_item(item, mid, idx)
        parts.extend(i_parts)
        media.extend(i_media)

    text = "\n".join(parts).strip()
    if len(text) > 8000:
        text = text[:8000]
    if not text and not media:
        return None
    return {
        "sender": sender,
        "message_id": mid,
        "text": text,
        "context_token": context_token,
        "media": media,
    }


def _decode_aes_key(raw_key: str) -> bytes:
    if not raw_key:
        return b""
    try:
        decoded = base64.b64decode(raw_key)
        if len(decoded) == 16:
            return decoded
        if len(decoded) == 32:
            return bytes.fromhex(decoded.decode("ascii"))
    except Exception:
        pass
    if len(raw_key) == 32:
        try:
            return bytes.fromhex(raw_key)
        except ValueError:
            pass
    raw_bytes = raw_key.encode("utf-8")
    return raw_bytes[:16] if len(raw_bytes) >= 16 else b""


def _aes_ecb_decrypt(ciphertext: bytes, key: bytes) -> bytes:
    if not key or not ciphertext:
        return ciphertext
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    decryptor = cipher.decryptor()
    padded = decryptor.update(ciphertext) + decryptor.finalize()
    unpadder = sym_padding.PKCS7(128).unpadder()
    try:
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError:
        return padded


def _aes_ecb_encrypt(plaintext: bytes, key: bytes) -> bytes:
    padder = sym_padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def download_media(
    media_ref: dict,
    *,
    bot_token: str,
    base_url: str = DEFAULT_BASE_URL,
    max_bytes: int = 32 * 1024 * 1024,
) -> bytes:
    resolved_base = normalize_base_url(base_url)
    url = str(media_ref.get("url") or "").strip()
    enc = str(media_ref.get("encrypt_query_param") or "").strip()
    aes_key_raw = str(media_ref.get("aes_key") or "").strip()
    if not url and enc:
        url = f"{resolved_base}/c2c/download?encrypted_query_param={enc}"
    if not url:
        raise AppError(422, "wechat_media_missing", "WeChat media URL is missing")
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url, headers={"AuthorizationType": "ilink_bot_token", "Authorization": f"Bearer {bot_token}"})
            if resp.status_code == 429 or resp.status_code >= 500:
                raise AppError(503, "wechat_unavailable", "WeChat media download unavailable")
            if not resp.is_success:
                raise AppError(422, "wechat_media_failed", "WeChat rejected media download")
            raw = resp.content
            if len(raw) > max_bytes:
                raise AppError(413, "im_attachment_too_large", "IM attachment exceeds size limit")
            key = _decode_aes_key(aes_key_raw)
            if key and media_ref.get("encrypt_type", 1) == 1:
                return _aes_ecb_decrypt(raw, key)
            return raw
    except AppError:
        raise
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "wechat_unavailable", "WeChat media download failed") from exc


def send_text(
    sender: str,
    text: str,
    nonce: str,
    *,
    bot_token: str,
    base_url: str = DEFAULT_BASE_URL,
    context_token: str = "",
) -> str:
    resolved_base = normalize_base_url(base_url)
    chunks = chunk_text(text)
    if not chunks:
        return nonce
    if not context_token:
        raise AppError(422, "wechat_context_missing", "WeChat message context token is missing")
    last_client_id = nonce
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            for idx, chunk in enumerate(chunks):
                client_id = f"{nonce}-{idx}" if len(chunks) > 1 else nonce
                last_client_id = client_id
                payload = {
                    "msg": {
                        "from_user_id": "",
                        "to_user_id": sender,
                        "client_id": client_id,
                        "message_type": 2,
                        "message_state": 2,
                        "context_token": context_token,
                        "item_list": [{"type": 1, "text_item": {"text": chunk}}],
                    },
                    "base_info": _base_info(),
                }
                resp = client.post(
                    f"{resolved_base}/ilink/bot/sendmessage",
                    headers=build_headers(bot_token),
                    json=payload,
                )
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise AppError(503, "wechat_unavailable", "WeChat iLink service is temporarily unavailable")
                data = resp.json() if resp.content else {}
                if not isinstance(data, dict):
                    raise AppError(503, "wechat_unavailable", "WeChat iLink returned invalid response")
                errcode = data.get("errcode", 0)
                ret = data.get("ret", 0)
                if errcode == SESSION_EXPIRED_ERRCODE or ret == SESSION_EXPIRED_ERRCODE:
                    raise AppError(422, "wechat_session_expired", "WeChat bot session expired")
                if not resp.is_success or (ret not in (0, None)) or (errcode not in (0, None)):
                    raise AppError(422, "wechat_rejected", str(data.get("errmsg") or "WeChat rejected the message"))
        return last_client_id
    except AppError:
        raise
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "wechat_unavailable", "WeChat iLink service is unavailable") from exc


def _send_ilink_message(
    sender: str,
    item_list: list[dict],
    nonce: str,
    *,
    bot_token: str,
    base_url: str = DEFAULT_BASE_URL,
    context_token: str = "",
) -> str:
    resolved_base = normalize_base_url(base_url)
    if not context_token:
        raise AppError(422, "wechat_context_missing", "WeChat message context token is missing")
    payload = {
        "msg": {
            "from_user_id": "",
            "to_user_id": sender,
            "client_id": nonce,
            "message_type": 2,
            "message_state": 2,
            "context_token": context_token,
            "item_list": item_list,
        },
        "base_info": _base_info(),
    }
    try:
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            resp = client.post(
                f"{resolved_base}/ilink/bot/sendmessage",
                headers=build_headers(bot_token),
                json=payload,
            )
            if resp.status_code == 429 or resp.status_code >= 500:
                raise AppError(503, "wechat_unavailable", "WeChat iLink service is temporarily unavailable")
            data = resp.json() if resp.content else {}
            if not isinstance(data, dict):
                raise AppError(503, "wechat_unavailable", "WeChat iLink returned invalid response")
            errcode = data.get("errcode", 0)
            ret = data.get("ret", 0)
            if errcode == SESSION_EXPIRED_ERRCODE or ret == SESSION_EXPIRED_ERRCODE:
                raise AppError(422, "wechat_session_expired", "WeChat bot session expired")
            if not resp.is_success or (ret not in (0, None)) or (errcode not in (0, None)):
                raise AppError(422, "wechat_rejected", str(data.get("errmsg") or "WeChat rejected the message"))
            return nonce
    except AppError:
        raise
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "wechat_unavailable", "WeChat iLink service is unavailable") from exc


def _upload_media(
    sender: str,
    raw_bytes: bytes,
    *,
    media_type: int,
    bot_token: str,
    base_url: str = DEFAULT_BASE_URL,
) -> dict:
    resolved_base = normalize_base_url(base_url)
    aes_key = secrets.token_bytes(16)
    encrypted = _aes_ecb_encrypt(raw_bytes, aes_key)
    filekey = secrets.token_hex(16)
    raw_md5 = hashlib.md5(raw_bytes).hexdigest()
    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            url_resp = client.post(
                f"{resolved_base}/ilink/bot/getuploadurl",
                headers=build_headers(bot_token),
                json={
                    "filekey": filekey,
                    "media_type": media_type,
                    "to_user_id": sender,
                    "rawsize": len(raw_bytes),
                    "rawfilemd5": raw_md5,
                    "filesize": len(encrypted),
                    "no_need_thumb": True,
                    "aeskey": aes_key.hex(),
                    "base_info": _base_info(),
                },
            )
            if url_resp.status_code == 429 or url_resp.status_code >= 500:
                raise AppError(503, "wechat_unavailable", "WeChat upload URL unavailable")
            url_data = url_resp.json() if url_resp.content else {}
            errcode = url_data.get("errcode", 0)
            ret = url_data.get("ret", 0)
            if errcode == SESSION_EXPIRED_ERRCODE or ret == SESSION_EXPIRED_ERRCODE:
                raise AppError(422, "wechat_session_expired", "WeChat bot session expired")
            upload_full_url = str(url_data.get("upload_full_url") or "").strip()
            upload_param = str(url_data.get("upload_param") or "").strip()
            if not upload_full_url and upload_param:
                upload_full_url = f"{resolved_base}/c2c/upload?encrypted_query_param={upload_param}&filekey={filekey}"
            if not upload_full_url:
                raise AppError(422, "wechat_upload_failed", "WeChat did not return an upload URL")
            put_resp = client.post(
                upload_full_url,
                content=encrypted,
                headers={"Content-Type": "application/octet-stream"},
            )
            if put_resp.status_code == 429 or put_resp.status_code >= 500:
                raise AppError(503, "wechat_unavailable", "WeChat media upload unavailable")
            if not put_resp.is_success:
                raise AppError(422, "wechat_upload_failed", "WeChat media upload failed")
            download_param = str(put_resp.headers.get("x-encrypted-param") or upload_param).strip()
            return {
                "encrypt_query_param": download_param,
                "aes_key": base64.b64encode(aes_key).decode("ascii"),
                "encrypt_type": 1,
                "ciphertext_size": len(encrypted),
            }
    except AppError:
        raise
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "wechat_unavailable", "WeChat media upload failed") from exc


def send_image(
    sender: str,
    image_bytes: bytes,
    filename: str,
    nonce: str,
    *,
    bot_token: str,
    base_url: str = DEFAULT_BASE_URL,
    context_token: str = "",
) -> str:
    uploaded = _upload_media(sender, image_bytes, media_type=1, bot_token=bot_token, base_url=base_url)
    return _send_ilink_message(
        sender,
        [
            {
                "type": 2,
                "image_item": {
                    "media": {
                        "encrypt_query_param": uploaded["encrypt_query_param"],
                        "aes_key": uploaded["aes_key"],
                        "encrypt_type": uploaded["encrypt_type"],
                    },
                    "mid_size": uploaded["ciphertext_size"],
                },
            }
        ],
        nonce,
        bot_token=bot_token,
        base_url=base_url,
        context_token=context_token,
    )


def send_file(
    sender: str,
    file_bytes: bytes,
    filename: str,
    nonce: str,
    *,
    bot_token: str,
    base_url: str = DEFAULT_BASE_URL,
    context_token: str = "",
) -> str:
    uploaded = _upload_media(sender, file_bytes, media_type=4, bot_token=bot_token, base_url=base_url)
    return _send_ilink_message(
        sender,
        [
            {
                "type": 4,
                "file_item": {
                    "media": {
                        "encrypt_query_param": uploaded["encrypt_query_param"],
                        "aes_key": uploaded["aes_key"],
                        "encrypt_type": uploaded["encrypt_type"],
                    },
                    "file_name": filename or "attachment.bin",
                    "len": str(len(file_bytes)),
                },
            }
        ],
        nonce,
        bot_token=bot_token,
        base_url=base_url,
        context_token=context_token,
    )


def send_audio(
    sender: str,
    audio_bytes: bytes,
    filename: str,
    nonce: str,
    *,
    duration_ms: int = 1000,
    bot_token: str,
    base_url: str = DEFAULT_BASE_URL,
    context_token: str = "",
) -> str:
    ext = os.path.splitext(filename or "")[1].casefold()
    if ext == ".silk":
        uploaded = _upload_media(sender, audio_bytes, media_type=3, bot_token=bot_token, base_url=base_url)
        return _send_ilink_message(
            sender,
            [
                {
                    "type": 3,
                    "voice_item": {
                        "media": {
                            "encrypt_query_param": uploaded["encrypt_query_param"],
                            "aes_key": uploaded["aes_key"],
                            "encrypt_type": uploaded["encrypt_type"],
                        },
                        "encode_type": 6,
                        "bits_per_sample": 16,
                        "sample_rate": 24000,
                        "playtime": max(100, int(duration_ms)),
                    },
                }
            ],
            nonce,
            bot_token=bot_token,
            base_url=base_url,
            context_token=context_token,
        )
    # Fallback: send standard audio file (wav/ogg/mp3) as file attachment so it is always playable on WeChat.
    return send_file(
        sender,
        audio_bytes,
        filename or "reply.wav",
        nonce,
        bot_token=bot_token,
        base_url=base_url,
        context_token=context_token,
    )


@dataclass
class _WeChatQrSession:
    session_id: str
    user_id: str
    workspace_id: str
    qrcode_token: str
    qr_url: str
    expires_at: float
    status: str = "wait"
    result: dict | None = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)


class WeChatQrLoginManager:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, _WeChatQrSession] = {}

    def _prune_locked(self, now: float) -> None:
        expired = [sid for sid, sess in self._sessions.items() if sess.expires_at <= now]
        for sid in expired:
            self._sessions[sid].cancel_event.set()
            del self._sessions[sid]

    def _poll_worker(self, session: _WeChatQrSession) -> None:
        try:
            with httpx.Client(timeout=40, follow_redirects=False) as client:
                while not session.cancel_event.is_set() and time.monotonic() < session.expires_at:
                    try:
                        resp = client.get(
                            f"{DEFAULT_BASE_URL}/ilink/bot/get_qrcode_status",
                            params={"qrcode": session.qrcode_token},
                            headers={"iLink-App-ClientVersion": "1"},
                        )
                    except httpx.TimeoutException:
                        continue
                    except httpx.TransportError:
                        if session.cancel_event.wait(2.0):
                            return
                        continue

                    if resp.status_code >= 500:
                        if session.cancel_event.wait(2.0):
                            return
                        continue

                    try:
                        data = resp.json()
                    except ValueError:
                        if session.cancel_event.wait(2.0):
                            return
                        continue

                    if not isinstance(data, dict):
                        continue

                    raw_status = str(data.get("status") or "").strip().lower()
                    if raw_status in {"scaned", "scanned"}:
                        with self._lock:
                            if session.status == "wait":
                                session.status = "scanned"
                        continue
                    if raw_status == "confirmed":
                        bot_token = str(data.get("bot_token") or "").strip()
                        bot_id = str(data.get("ilink_bot_id") or "").strip()
                        ilink_user_id = str(data.get("ilink_user_id") or "").strip()
                        base_url = normalize_base_url(data.get("baseurl"))
                        with self._lock:
                            if bot_token and bot_id and ilink_user_id:
                                session.result = {
                                    "bot_token": bot_token,
                                    "bot_id": bot_id,
                                    "ilink_user_id": ilink_user_id,
                                    "base_url": base_url,
                                }
                                session.status = "confirmed"
                            else:
                                session.status = "error"
                                session.error = "微信未返回完整的登录凭据，请重新扫码。"
                        return
                    if raw_status == "expired":
                        with self._lock:
                            session.status = "expired"
                        return
                    # "wait" or unknown transient status -> continue polling
                    if session.cancel_event.wait(0.5):
                        return

                with self._lock:
                    if session.status in {"wait", "scanned"}:
                        session.status = "expired"
        except Exception:
            logger.exception("WeChat QR poll worker failed")
            with self._lock:
                if session.status in {"wait", "scanned"}:
                    session.status = "error"
                    session.error = "检查微信扫码状态失败，请重新生成二维码。"

    def start(self, *, user_id: str, workspace_id: str) -> dict:
        try:
            with httpx.Client(timeout=15, follow_redirects=False) as client:
                resp = client.get(
                    f"{DEFAULT_BASE_URL}/ilink/bot/get_bot_qrcode",
                    params={"bot_type": BOT_TYPE},
                )
                if resp.status_code >= 500 or not resp.is_success:
                    raise AppError(503, "wechat_unavailable", "Unable to fetch WeChat login QR code")
                data = resp.json()
        except AppError:
            raise
        except (httpx.TransportError, ValueError) as exc:
            raise AppError(503, "wechat_unavailable", "Unable to reach WeChat iLink service") from exc

        if not isinstance(data, dict):
            raise AppError(503, "wechat_unavailable", "WeChat returned invalid QR code payload")
        qrcode_token = str(data.get("qrcode") or "").strip()
        qr_url = str(data.get("qrcode_img_content") or "").strip()
        if not qrcode_token or not qr_url:
            raise AppError(503, "wechat_qr_failed", "WeChat did not return a login QR code")

        session_id = secrets.token_urlsafe(18)
        now = time.monotonic()
        session = _WeChatQrSession(
            session_id=session_id,
            user_id=user_id,
            workspace_id=workspace_id,
            qrcode_token=qrcode_token,
            qr_url=qr_url,
            expires_at=now + QR_SESSION_TTL_SECONDS,
        )
        with self._lock:
            self._prune_locked(now)
            for existing_id in [
                sid
                for sid, sess in self._sessions.items()
                if sess.user_id == user_id and sess.workspace_id == workspace_id
            ]:
                self._sessions[existing_id].cancel_event.set()
                del self._sessions[existing_id]
            self._sessions[session_id] = session

        worker = threading.Thread(
            target=self._poll_worker,
            args=(session,),
            name=f"wechat-qr-{session_id[:8]}",
            daemon=True,
        )
        worker.start()

        return {
            "session_id": session_id,
            "qr_url": qr_url,
            "expires_in": QR_SESSION_TTL_SECONDS,
        }

    def get_status(self, session_id: str, *, user_id: str, workspace_id: str) -> dict:
        now = time.monotonic()
        with self._lock:
            self._prune_locked(now)
            session = self._sessions.get(session_id)
            if not session or session.user_id != user_id or session.workspace_id != workspace_id:
                return {"status": "expired"}
            payload: dict = {"status": session.status}
            if session.status == "confirmed" and session.result is not None:
                payload["result"] = dict(session.result)
            elif session.status == "error" and session.error:
                payload["message"] = session.error
            return payload

    def consume(self, session_id: str, *, user_id: str, workspace_id: str) -> None:
        self.cancel(session_id, user_id=user_id, workspace_id=workspace_id)

    def cancel(self, session_id: str, *, user_id: str, workspace_id: str) -> None:
        with self._lock:
            session = self._sessions.get(session_id)
            if session and session.user_id == user_id and session.workspace_id == workspace_id:
                session.cancel_event.set()
                del self._sessions[session_id]


wechat_qr_manager = WeChatQrLoginManager()
