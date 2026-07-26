from __future__ import annotations

"""Workspace SMTP alert-mail configuration and best-effort delivery.

The SMTP password is stored inside a dedicated WorkspaceSetting value,
encrypted with the master key when one is configured. The setting key is
excluded from the generic settings listing so the password never leaves the
backend; the usage API exposes only a masked view.
"""

import logging
import smtplib
import threading
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.security import SecretCipher
from app.domain.models import WorkspaceSetting
from app.repositories.audit import AuditRepository


logger = logging.getLogger(__name__)

ALERT_EMAIL_SETTING_KEY = "usage.alert_email"
SMTP_TIMEOUT_SECONDS = 20.0


@dataclass
class AlertEmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_security: str = "ssl"
    smtp_username: str = ""
    smtp_password: str = ""
    has_password: bool = False
    from_address: str = ""
    to_addresses: list[str] = field(default_factory=list)

    @property
    def deliverable(self) -> bool:
        return bool(self.smtp_host and self.to_addresses)

    def view(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "smtp_host": self.smtp_host,
            "smtp_port": self.smtp_port,
            "smtp_security": self.smtp_security,
            "smtp_username": self.smtp_username,
            "has_password": self.has_password,
            "from_address": self.from_address,
            "to_addresses": list(self.to_addresses),
        }


def _setting_row(db: Session, workspace_id: str) -> WorkspaceSetting | None:
    return db.scalar(
        select(WorkspaceSetting).where(
            WorkspaceSetting.workspace_id == workspace_id,
            WorkspaceSetting.key == ALERT_EMAIL_SETTING_KEY,
        )
    )


def _decrypt_password(raw: dict[str, Any]) -> str:
    cipher = raw.get("password_cipher")
    if cipher:
        settings = get_settings()
        if not settings.has_master_key:
            return ""
        try:
            return SecretCipher(settings.master_key).decrypt(str(cipher))
        except Exception:  # noqa: BLE001 - a stale cipher reads as "not set"
            return ""
    return str(raw.get("password_plain") or "")


def load_config(db: Session, workspace_id: str) -> AlertEmailConfig:
    setting = _setting_row(db, workspace_id)
    raw = setting.value if setting is not None and isinstance(setting.value, dict) else {}
    password = _decrypt_password(raw)
    try:
        port = int(raw.get("smtp_port") or 465)
    except (TypeError, ValueError):
        port = 465
    security = str(raw.get("smtp_security") or "ssl")
    if security not in {"ssl", "starttls", "none"}:
        security = "ssl"
    to_addresses = [
        str(item).strip()
        for item in (raw.get("to_addresses") or [])
        if str(item).strip()
    ]
    return AlertEmailConfig(
        enabled=bool(raw.get("enabled")),
        smtp_host=str(raw.get("smtp_host") or "").strip(),
        smtp_port=port,
        smtp_security=security,
        smtp_username=str(raw.get("smtp_username") or "").strip(),
        smtp_password=password,
        has_password=bool(password),
        from_address=str(raw.get("from_address") or "").strip(),
        to_addresses=to_addresses,
    )


def save_config(
    db: Session,
    workspace_id: str,
    actor_id: str,
    *,
    enabled: bool,
    smtp_host: str,
    smtp_port: int,
    smtp_security: str,
    smtp_username: str,
    smtp_password: str | None,
    from_address: str,
    to_addresses: list[str],
) -> AlertEmailConfig:
    cleaned_to = [item.strip() for item in to_addresses if item.strip()]
    for address in cleaned_to:
        if "@" not in address:
            raise AppError(
                422,
                "invalid_alert_email_recipient",
                f"'{address}' is not a valid recipient address",
            )
    if enabled and (not smtp_host.strip() or not cleaned_to):
        raise AppError(
            422,
            "alert_email_config_incomplete",
            "Enabling email alerts requires an SMTP host and at least one recipient",
        )
    setting = _setting_row(db, workspace_id)
    previous = setting.value if setting is not None and isinstance(setting.value, dict) else {}
    raw: dict[str, Any] = {
        "enabled": enabled,
        "smtp_host": smtp_host.strip(),
        "smtp_port": smtp_port,
        "smtp_security": smtp_security,
        "smtp_username": smtp_username.strip(),
        "from_address": from_address.strip(),
        "to_addresses": cleaned_to,
    }
    if smtp_password is None:
        for key in ("password_cipher", "password_plain"):
            if key in previous:
                raw[key] = previous[key]
    elif smtp_password:
        settings = get_settings()
        if settings.has_master_key:
            raw["password_cipher"] = SecretCipher(settings.master_key).encrypt(
                smtp_password
            )
        else:
            raw["password_plain"] = smtp_password
    if setting is None:
        setting = WorkspaceSetting(
            workspace_id=workspace_id,
            key=ALERT_EMAIL_SETTING_KEY,
            value=raw,
        )
        db.add(setting)
    else:
        setting.value = raw
    AuditRepository(db, workspace_id).record(
        actor_id=actor_id,
        action="usage.alert_email.updated",
        resource_type="setting",
        resource_id=ALERT_EMAIL_SETTING_KEY,
        details={"enabled": enabled, "recipient_count": len(cleaned_to)},
    )
    db.commit()
    return load_config(db, workspace_id)


def send_mail(config: AlertEmailConfig, subject: str, body: str) -> None:
    """Deliver one message synchronously; raises on any SMTP failure."""

    if not config.deliverable:
        raise AppError(
            422,
            "alert_email_config_incomplete",
            "SMTP host and at least one recipient are required",
        )
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config.from_address or config.smtp_username or "learngraph@localhost"
    message["To"] = ", ".join(config.to_addresses)
    message.set_content(body)
    if config.smtp_security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(
            config.smtp_host, config.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
        )
    else:
        server = smtplib.SMTP(
            config.smtp_host, config.smtp_port, timeout=SMTP_TIMEOUT_SECONDS
        )
    try:
        if config.smtp_security == "starttls":
            server.starttls()
        if config.smtp_username:
            server.login(config.smtp_username, config.smtp_password)
        server.send_message(message)
    finally:
        try:
            server.quit()
        except Exception:  # noqa: BLE001 - closing failures are irrelevant
            pass


def notify_budget_alert(
    db: Session,
    workspace_id: str,
    *,
    policy_name: str,
    level: str,
    scope: str,
    spent_cny: float,
    projected_cny: float,
    limit_cny: float,
) -> None:
    """Fire-and-forget alert mail; never raises into the billing path."""

    try:
        config = load_config(db, workspace_id)
    except Exception:  # noqa: BLE001 - configuration faults must not block calls
        logger.exception("loading alert email config failed")
        return
    if not config.enabled or not config.deliverable:
        return
    level_label = "硬阻断（已停用后续调用）" if level == "hard" else "软告警（仅提醒，不停用）"
    subject = f"[LearnGraph] 预算告警：{policy_name} · {'硬阻断' if level == 'hard' else '软告警'}"
    body = (
        f"工作区预算策略「{policy_name}」已触发{level_label}。\n\n"
        f"匹配范围：{scope}\n"
        f"周期已用：¥{spent_cny:.4f}\n"
        f"本次调用预估：¥{projected_cny:.4f}\n"
        f"配置限额：¥{limit_cny:.2f}\n\n"
        + (
            "达到硬限额后，后续远程调用会在执行前被阻断，直到本周期结束或调高限额。\n"
            if level == "hard"
            else "软告警不会阻断调用；如需自动停用，请为该范围配置硬限额。\n"
        )
        + "\n此邮件由 LearnGraph 用量预算模块自动发送。"
    )

    def _deliver() -> None:
        try:
            send_mail(config, subject, body)
        except Exception:  # noqa: BLE001 - background delivery is best-effort
            logger.exception("budget alert email delivery failed")

    threading.Thread(target=_deliver, name="budget-alert-mail", daemon=True).start()
