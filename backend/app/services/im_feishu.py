from __future__ import annotations

import json
import os

import httpx

from app.core.errors import AppError


BASE = "https://open.feishu.cn/open-apis"


def send_text(sender: str, text: str, nonce: str) -> str:
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            token = client.post(f"{BASE}/auth/v3/tenant_access_token/internal", json={
                "app_id": os.environ["LEARNGRAPH_FEISHU_APP_ID"].strip(),
                "app_secret": os.environ["LEARNGRAPH_FEISHU_APP_SECRET"],
            })
            credentials = _result(token)
            if not credentials.get("tenant_access_token"):
                raise AppError(422, "feishu_auth_failed", "Feishu bot credentials were rejected")
            reply = client.post(f"{BASE}/im/v1/messages", params={"receive_id_type": "open_id"},
                                headers={"Authorization": f"Bearer {credentials['tenant_access_token']}"},
                                json={"receive_id": sender, "msg_type": "text", "uuid": nonce,
                                      "content": json.dumps({"text": text}, ensure_ascii=False)})
            receipt = _result(reply).get("data", {}).get("message_id")
            if not receipt:
                raise AppError(503, "feishu_receipt_unknown", "Feishu did not return a message receipt")
            return receipt
    except (httpx.TransportError, ValueError) as exc:
        raise AppError(503, "feishu_unavailable", "Feishu is unavailable") from exc


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


def main() -> None:
    from app.core.config import get_settings
    from app.core.database import SessionLocal
    from app.services.im_channels import feishu_connection, intake

    connection = feishu_connection()
    if not connection or not get_settings().durable_queue_enabled:
        raise SystemExit("Configure the Feishu bot and enable the durable queue before running the listener.")
    import lark_oapi as lark

    def on_message(data):
        parsed = parse_event(data)
        if parsed:
            with SessionLocal() as db:
                intake(db, connection, *parsed)

    handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(on_message).build()
    lark.ws.Client(connection.removeprefix("feishu:"), os.environ["LEARNGRAPH_FEISHU_APP_SECRET"],
                   event_handler=handler, log_level=lark.LogLevel.WARNING).start()


if __name__ == "__main__":
    main()
