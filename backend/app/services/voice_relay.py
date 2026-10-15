"""Deployment-wide TURN/STUN relay for realtime voice.

One row in ``voice_relay_configs`` (its credential sealed in
``VoiceRelaySecret``) feeds two consumers:

* the embedded SmallWebRTC handler, which needs ``aiortc.RTCIceServer`` objects
  and honours only the first STUN and first TURN entry it is given;
* the browser, through ``GET /voice/ice-servers``, which receives the whole
  ordered URL list so Chrome can fall back from UDP to TCP/TLS by itself.

Cloudflare TURN credentials are *ephemeral*, so what gets stored is the
long-lived material (TURN Key ID + API token); short-lived credentials are
minted on demand and cached in-process until shortly before they expire.

Manual mode is a separate, credential-free browser STUN pool; it never calls
Cloudflare. In Cloudflare mode the pool supplements normal ICE and survives
signing failures only when the administrator explicitly enables fallback.
The server runtime remains TURN-only to avoid unreachable STUN gathering delays.

* Never configured, disabled or deleted means host-only: public defaults are
  suggestions, not an implicit network policy.
* Refresh failures retain usable old TURN credentials within their safety window.
  Editing only STUN preserves that credential but replaces its URL list; changing
  TURN identity, switching mode or disabling retires it.
* STUN fallback provides mapping discovery, not guaranteed media relay.
* The long-lived API token never leaves the server; only masked metadata and
  minted short-lived TURN credentials are returned to the appropriate callers.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.domain.models import VoiceRelayConfig, VoiceRelaySecret
from app.services.provider_secrets import (
    ProviderSecretUnavailable,
    decrypt_secret_fields,
    encrypt_provider_secret,
)

logger = logging.getLogger(__name__)

SCOPE_DEPLOYMENT = "deployment"
MODE_CLOUDFLARE = "cloudflare"
MODE_MANUAL = "manual"

# Public, unauthenticated candidates, not an availability/SLA guarantee. They
# are never enabled implicitly for an existing or unconfigured deployment.
DEFAULT_STUN_URLS: tuple[str, ...] = (
    "stun:stun.miwifi.com:3478",
    "stun:stun.hitv.com:3478",
    "stun:stun.chat.bilibili.com:3478",
)

DEFAULT_API_BASE = "https://rtc.live.cloudflare.com/v1"
DEFAULT_CREDENTIAL_TTL = 86400
MIN_CREDENTIAL_TTL = 300

# Pre-filled with Cloudflare's published endpoints.  The order is the *server's*
# transport preference: aiortc only ever honours the first TURN entry, so the
# administrator can move `?transport=udp` / `turns:` to the front to prefer a
# different path.
#
# TCP first, deliberately.  Measured from inside this deployment's container
# (2026-09, Docker Desktop on Windows): every UDP destination here -- both
# `stun:...:3478` and `turn:...:3478?transport=udp` -- times out after 5s, while
# TCP and TLS answer in 0.3-0.6s.  A default of UDP would therefore hand the
# server a transport that never works, which is silent: the call just never
# relays.  The browser receives the whole list and picks for itself.
DEFAULT_URLS: tuple[str, ...] = (
    "stun:stun.cloudflare.com:3478",
    "turn:turn.cloudflare.com:3478?transport=tcp",
    "turns:turn.cloudflare.com:443?transport=tcp",
    "turns:turn.cloudflare.com:5349?transport=tcp",
    "turn:turn.cloudflare.com:3478?transport=udp",
    "turn:turn.cloudflare.com:80?transport=tcp",
)

# How long a resolved credential is reused before we mint a new one, and how
# much of its lifetime we leave as margin so a call that starts just before
# expiry still gets a usable credential.
CACHE_TTL_SECONDS = 60.0
RENEW_MARGIN_SECONDS = 300.0


@dataclass(frozen=True)
class ResolvedIce:
    """The ICE configuration one caller should use right now."""

    urls: tuple[str, ...] = ()
    username: str | None = None
    credential: str | None = None
    source: str = "none"
    detail: str | None = None

    @property
    def configured(self) -> bool:
        return bool(self.urls)

    def as_client_config(self) -> dict[str, Any]:
        """Shape for ``RTCIceServer`` on the browser side (omitted when empty)."""
        if not self.urls:
            return {}
        config: dict[str, Any] = {"urls": list(self.urls)}
        if self.username and self.credential:
            config["username"] = self.username
            config["credential"] = self.credential
        return config

    def as_aiortc_servers(self) -> list[Any]:
        """Shape for ``handler.update_ice_servers``.

        aiortc requires real ``RTCIceServer`` objects: pipecat validates with
        ``isinstance(s, IceServer)`` where ``IceServer`` *is* that class, so a
        plain dict would be rejected.

        ``stun:`` entries are deliberately dropped here even though the browser
        still receives them.  aiortc keeps one STUN slot and waits for its reply
        during candidate gathering, so an unreachable STUN server costs a ~5s
        stall on every connection -- and because pipecat captures the answer SDP
        immediately, that stall can cost the inline server candidates the browser
        needs.  Measured in this deployment: the STUN probe times out at 5003ms
        while TURN/TCP answers in 356ms, i.e. the STUN entry contributed nothing
        but delay.  Relay is what actually makes calls work across networks.
        """

        turn_urls = [
            url for url in self.urls if str(url).startswith(("turn:", "turns:"))
        ]
        if not turn_urls:
            return []
        try:
            from aiortc.rtcconfiguration import RTCIceServer
        except Exception:  # pragma: no cover - voice extra not installed
            return []
        return [
            RTCIceServer(
                urls=turn_urls,
                username=self.username,
                credential=self.credential,
                credentialType="password",
            )
        ]


@dataclass
class _CacheEntry:
    resolved: ResolvedIce
    expires_at: float
    credential_expires_at: float = 0.0
    probes: list[dict[str, Any]] = field(default_factory=list)


_CACHE: _CacheEntry | None = None

# The last resolution that actually minted a credential, kept across failures.
#
# Minting is an *upstream* call and this deployment's egress to
# ``rtc.live.cloudflare.com`` is flaky in a way that is easy to mistake for a
# broken relay: TLS handshakes to that host (and to ``1.1.1.1``) time out for
# minutes at a time while ``turn.cloudflare.com:3478`` itself keeps answering,
# so credentials cannot be *refreshed* even though the ones already handed out
# are valid for hours.  Treating that as "relay unusable" used to clear the ICE
# servers of every *new* peer connection, which silently killed cross-network
# calls: the browser still held a valid credential from its last fetch, so its
# ICE kept advertising relay candidates while the server had none -- ICE then
# sat in "checking" until the client gave up.  The previous credential is
# therefore served until it really expires, while the config row keeps
# recording the failure so the admin page still shows it.
_LAST_GOOD: _CacheEntry | None = None
_CACHE_GENERATION = 0


def invalidate_cache(*, retain_turn_urls: tuple[str, ...] | None = None) -> None:
    """Invalidate resolutions; retain credentials only for a verified same TURN identity."""

    global _CACHE, _LAST_GOOD, _CACHE_GENERATION
    _CACHE_GENERATION += 1
    _CACHE = None
    if retain_turn_urls is not None and _LAST_GOOD is not None:
        # Rebuild even when only STUN changed, so removed/disabled pools cannot
        # leak back from the previous successful resolution. Keep original expiry.
        _LAST_GOOD = replace(
            _LAST_GOOD, resolved=replace(_LAST_GOOD.resolved, urls=retain_turn_urls)
        )
    else:
        # Mode/key/token/TURN changes, explicit disable and deletion retire it.
        _LAST_GOOD = None


def _usable_last_good(now: float) -> _CacheEntry | None:
    """The last minted credential, if it is still inside its own lifetime."""

    entry = _LAST_GOOD
    if entry is None or not entry.resolved.configured:
        return None
    if entry.credential_expires_at <= now:
        return None
    return entry


def _mask(secret: str) -> str:
    if len(secret) <= 8:
        return "••••"
    return f"{secret[:4]}…{secret[-4:]}"


def _fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:32]


def _dedupe_urls(values: Any) -> tuple[str, ...]:
    """Drop repeated ICE URLs while keeping the administrator's order.

    Duplicates are not cosmetic here: the list is what both the browser and aiortc
    are handed, and aiortc honours **only the first TURN entry**, so a list that
    silently repeats a transport is a list whose real preference is hard to read.
    Applied on write *and* on read, because rows saved before this existed still
    carry the duplicate (`turns:…:5349?transport=tcp` was stored twice).
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for item in values or ():
        url = str(item).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return tuple(ordered)


class VoiceRelayService:
    """Read/write the single deployment relay row and resolve live credentials."""

    def __init__(self, db: Session, settings: Any) -> None:
        self.db = db
        self.settings = settings

    # ------------------------------------------------------------------ config

    def get_config(self) -> VoiceRelayConfig | None:
        return self.db.scalar(
            select(VoiceRelayConfig)
            .where(VoiceRelayConfig.scope == SCOPE_DEPLOYMENT)
            .execution_options(populate_existing=True)
        )

    def get_secret_row(self, config_id: str) -> VoiceRelaySecret | None:
        return self.db.get(VoiceRelaySecret, config_id)

    def public_config(self) -> dict[str, Any]:
        config = self.get_config()
        urls = list(_dedupe_urls(config.urls)) if config else []
        stun_urls = list(_dedupe_urls(config.stun_urls)) if config else []
        return {
            "configured": config is not None and bool(
                stun_urls if config.mode == MODE_MANUAL else config.secret_fingerprint
            ),
            "enabled": bool(config.enabled) if config else False,
            "mode": config.mode if config else MODE_CLOUDFLARE,
            "urls": urls or list(DEFAULT_URLS),
            "stun_urls": stun_urls,
            "stun_fallback_enabled": bool(config and config.stun_fallback_enabled),
            "key_id": config.key_id if config else None,
            "api_base": (config.api_base if config else None) or DEFAULT_API_BASE,
            "credential_ttl_seconds": (
                config.credential_ttl_seconds if config else DEFAULT_CREDENTIAL_TTL
            ),
            "secret_masked": config.secret_masked if config else None,
            "secret_fingerprint": config.secret_fingerprint if config else None,
            "secret_configured": bool(config and config.secret_fingerprint),
            "status": config.status if config else "unconfigured",
            "status_detail": config.status_detail if config else None,
            "last_checked_at": (
                config.last_checked_at.isoformat()
                if config and config.last_checked_at
                else None
            ),
            "updated_by_user_id": config.updated_by_user_id if config else None,
            "defaults": {
                "api_base": DEFAULT_API_BASE,
                "urls": list(DEFAULT_URLS),
                "stun_urls": list(DEFAULT_STUN_URLS),
                "credential_ttl_seconds": DEFAULT_CREDENTIAL_TTL,
            },
        }

    def save(self, payload: dict[str, Any], *, actor_id: str) -> dict[str, Any]:
        """Upsert the deployment relay. Saving is what enables it (A2-1)."""

        config = self.get_config()
        mode = self._validate_mode(payload.get("mode") or (config.mode if config else MODE_CLOUDFLARE))
        fallback_enabled = payload.get(
            "stun_fallback_enabled", bool(config and config.stun_fallback_enabled)
        )
        if not isinstance(fallback_enabled, bool):
            raise AppError(400, "voice_relay_fallback_invalid", "STUN 兜底开关必须是布尔值")
        stun_raw = payload.get("stun_urls", config.stun_urls if config else None)
        if stun_raw is None:
            stun_raw = list(DEFAULT_STUN_URLS) if mode == MODE_MANUAL or fallback_enabled else []
        stun_urls = self._validate_stun_urls(
            stun_raw, required=mode == MODE_MANUAL or fallback_enabled
        )
        secret = ""
        secret_row = None
        if mode == MODE_CLOUDFLARE:
            key_id = str(payload.get("key_id", config.key_id if config else "") or "").strip()
            if not key_id:
                raise AppError(400, "voice_relay_key_id_required", "需要填写 Cloudflare TURN Key ID")
            api_base = str(payload.get("api_base") or (config.api_base if config else None) or DEFAULT_API_BASE).strip().rstrip("/")
            self._validate_api_base(api_base)
            urls = self._validate_urls(payload.get("urls", config.urls if config else None))
            ttl = self._validate_ttl(payload.get("credential_ttl_seconds", config.credential_ttl_seconds if config else None))
            secret = str(payload.get("secret") or payload.get("api_token") or "").strip()
            secret_row = self.get_secret_row(config.id) if config is not None else None
            if not secret and (secret_row is None or secret_row.revoked_at is not None):
                raise AppError(
                    400,
                    "voice_relay_secret_required",
                    "需要填写 Cloudflare API Token（留空表示沿用已保存的凭据）",
                )
        else:
            # Keep the optional Cloudflare setup for a later mode switch, but do
            # not read, decrypt or change its secret in credential-free mode.
            key_id = config.key_id if config else None
            api_base = (config.api_base if config else None) or DEFAULT_API_BASE
            urls = _dedupe_urls(config.urls) if config else DEFAULT_URLS
            ttl = config.credential_ttl_seconds if config else DEFAULT_CREDENTIAL_TTL

        retain_turn = bool(
            config and config.enabled and config.mode == MODE_CLOUDFLARE
            and mode == MODE_CLOUDFLARE and not secret
            and config.key_id == key_id
            and (config.api_base or DEFAULT_API_BASE).rstrip("/") == api_base
            and config.credential_ttl_seconds == ttl
            and tuple(url for url in _dedupe_urls(config.urls) if url.startswith(("turn:", "turns:")))
            == tuple(url for url in urls if url.startswith(("turn:", "turns:")))
        )
        if config is None:
            config = VoiceRelayConfig(scope=SCOPE_DEPLOYMENT)
            self.db.add(config)
            # ``id`` defaults are applied on flush, and the secret row's primary
            # key *is* the config id, so the flush has to happen before it can be
            # referenced.
            self.db.flush()

        config.mode = mode
        config.key_id = key_id
        config.api_base = api_base
        config.urls = list(urls)
        config.stun_urls = list(stun_urls)
        config.stun_fallback_enabled = fallback_enabled
        config.credential_ttl_seconds = ttl
        config.updated_by_user_id = actor_id
        # "有配置就自动开": a successful save is the enable gesture.  The explicit
        # disable switch stays available for emergencies.
        config.enabled = True

        if secret:
            sealed = encrypt_provider_secret(self.settings, secret)
            if secret_row is None:
                secret_row = VoiceRelaySecret(config_id=config.id)
                self.db.add(secret_row)
            else:
                secret_row.secret_version = int(secret_row.secret_version or 1) + 1
            secret_row.ciphertext = sealed.ciphertext
            secret_row.algorithm = sealed.algorithm
            secret_row.key_provider = sealed.key_provider
            secret_row.key_version = sealed.key_version
            secret_row.revoked_at = None
            secret_row.rotated_at = datetime.datetime.now(datetime.timezone.utc)
            config.secret_masked = _mask(secret)
            config.secret_fingerprint = _fingerprint(secret)

        config.status = "configured"
        config.status_detail = None
        self.db.commit()
        self.db.refresh(config)
        current_urls = _dedupe_urls((*urls, *(stun_urls if fallback_enabled else ())))
        invalidate_cache(retain_turn_urls=current_urls if retain_turn else None)
        return self.public_config()

    def set_enabled(self, enabled: bool, *, actor_id: str) -> dict[str, Any]:
        config = self.get_config()
        if config is None:
            raise AppError(404, "voice_relay_not_configured", "尚未配置语音中继")
        config.enabled = bool(enabled)
        config.updated_by_user_id = actor_id
        self.db.commit()
        invalidate_cache()
        return self.public_config()

    def clear(self) -> None:
        config = self.get_config()
        if config is None:
            return
        secret_row = self.get_secret_row(config.id)
        if secret_row is not None:
            self.db.delete(secret_row)
        self.db.delete(config)
        self.db.commit()
        invalidate_cache()

    @staticmethod
    def _validate_mode(raw: Any) -> str:
        mode = str(raw).strip().lower()
        if mode not in {MODE_CLOUDFLARE, MODE_MANUAL}:
            raise AppError(400, "voice_relay_mode_unsupported", "仅支持 Cloudflare 或手动 STUN 模式")
        return mode

    @staticmethod
    def _validate_stun_urls(raw: Any, *, required: bool = True) -> tuple[str, ...]:
        if isinstance(raw, str):
            raw = raw.replace("\r", "\n").replace(",", "\n").splitlines()
        if not isinstance(raw, (list, tuple)) or any(not isinstance(item, str) for item in raw):
            raise AppError(400, "voice_relay_stun_urls_invalid", "STUN 地址必须是字符串数组或逐行文本")
        urls = _dedupe_urls(raw)
        if not urls and required:
            raise AppError(400, "voice_relay_stun_urls_required", "至少填写一个手动 STUN 地址")
        if len(urls) > 16:
            raise AppError(400, "voice_relay_stun_urls_invalid", "最多配置 16 个手动 STUN 地址")
        for url in urls:
            # The browser and deployment probe both support UDP STUN. Do not
            # silently probe stuns: (TLS) as plaintext UDP, or accept TURN here.
            if not url.startswith("stun:") or len(url) > 512 or any(ch.isspace() for ch in url):
                raise AppError(400, "voice_relay_stun_url_invalid", "手动地址仅支持 stun:host:port（UDP，无凭据）")
            try:
                parsed = _parse_ice_url(url)
                host = str(parsed.get("host") or "")
                if not host or any(ch in host for ch in "/@?#") or not 1 <= int(parsed["port"]) <= 65535:
                    raise ValueError("主机或端口无效")
            except Exception as exc:
                raise AppError(400, "voice_relay_stun_url_invalid", f"无法解析 STUN 地址：{url}") from exc
        return urls

    @staticmethod
    def _validate_api_base(api_base: str) -> None:
        # The administrator controls this value, but it is still an outbound
        # request target: keep it https-only so a typo cannot send the Cloudflare
        # API token over plaintext.
        if not api_base.startswith("https://"):
            raise AppError(400, "voice_relay_api_base_invalid", "接口基址必须是 https:// 开头")

    @staticmethod
    def _validate_urls(raw: Any) -> tuple[str, ...]:
        if raw is None:
            return DEFAULT_URLS
        if isinstance(raw, str):
            items = [item.strip() for item in raw.split(",")]
        elif isinstance(raw, (list, tuple)):
            items = [str(item).strip() for item in raw]
        else:
            raise AppError(400, "voice_relay_urls_invalid", "urls 必须是字符串或字符串数组")
        urls = _dedupe_urls(items)
        if not urls:
            raise AppError(400, "voice_relay_urls_required", "至少需要一个 STUN 或 TURN 地址")
        for url in urls:
            if not url.startswith(("stun:", "stuns:", "turn:", "turns:")):
                raise AppError(
                    400,
                    "voice_relay_url_scheme_invalid",
                    f"不支持的地址协议：{url}（应为 stun:/stuns:/turn:/turns:）",
                )
            try:
                _parse_ice_url(url)
            except Exception as exc:  # aioice/aiortc parse failure
                raise AppError(
                    400, "voice_relay_url_invalid", f"无法解析地址：{url}（{exc}）"
                ) from exc
        return urls

    @staticmethod
    def _validate_ttl(raw: Any) -> int:
        try:
            ttl = int(raw if raw is not None else DEFAULT_CREDENTIAL_TTL)
        except (TypeError, ValueError) as exc:
            raise AppError(400, "voice_relay_ttl_invalid", "凭据有效期必须是整数秒") from exc
        if ttl < MIN_CREDENTIAL_TTL:
            raise AppError(
                400, "voice_relay_ttl_too_short", f"凭据有效期不得低于 {MIN_CREDENTIAL_TTL} 秒"
            )
        return min(ttl, DEFAULT_CREDENTIAL_TTL)

    # ----------------------------------------------------------------- resolve

    def _open_secret(self, config: VoiceRelayConfig) -> dict[str, Any]:
        secret_row = self.get_secret_row(config.id)
        if secret_row is None or secret_row.ciphertext is None:
            raise AppError(404, "voice_relay_secret_missing", "尚未保存 Cloudflare API Token")
        if secret_row.revoked_at is not None:
            raise AppError(409, "voice_relay_secret_revoked", "已保存的凭据已被吊销")
        plaintext = decrypt_secret_fields(
            self.settings,
            ciphertext=secret_row.ciphertext,
            algorithm=secret_row.algorithm,
            key_provider=secret_row.key_provider,
            key_version=secret_row.key_version,
        )
        try:
            data = json.loads(plaintext)
        except ValueError:
            data = {"api_token": plaintext}
        return data if isinstance(data, dict) else {"api_token": plaintext}

    def resolve(self, *, force: bool = False) -> ResolvedIce:
        """Return the ICE configuration to use right now (never raises)."""

        global _CACHE, _LAST_GOOD
        generation = _CACHE_GENERATION
        now = time.monotonic()
        if (
            not force and _CACHE is not None and now < _CACHE.expires_at
            and (not _CACHE.resolved.credential or now < _CACHE.credential_expires_at)
        ):
            return _CACHE.resolved

        try:
            config = self.get_config()
        except Exception:
            logger.debug("voice relay config read failed", exc_info=True)
            return self._serve_or_degrade("配置读取失败", now=now)

        if config is None or not config.enabled:
            # Explicitly off (or never configured): never enable public STUN or
            # revive credentials for the configuration that just went away.
            _LAST_GOOD = None
            _CACHE = _CacheEntry(
                resolved=ResolvedIce(source="none"),
                expires_at=now + CACHE_TTL_SECONDS,
            )
            return _CACHE.resolved

        stun_urls = tuple(url for url in _dedupe_urls(config.stun_urls) if url.startswith("stun:"))
        if config.mode == MODE_MANUAL:
            _LAST_GOOD = None
            _CACHE = _CacheEntry(
                resolved=ResolvedIce(urls=stun_urls, source="manual"),
                expires_at=now + CACHE_TTL_SECONDS,
            )
            self.db.commit()
            return _CACHE.resolved
        if config.mode != MODE_CLOUDFLARE:
            _LAST_GOOD = None
            return ResolvedIce(source="none", detail="不支持的语音中继模式")
        fallback_urls = stun_urls if config.stun_fallback_enabled else ()
        if not config.secret_fingerprint:
            _LAST_GOOD = None
            _CACHE = _CacheEntry(
                resolved=self._serve_or_degrade(
                    "尚未配置 Cloudflare 凭据", now=now, fallback_urls=fallback_urls
                ),
                expires_at=now + CACHE_TTL_SECONDS,
            )
            return _CACHE.resolved

        # Snapshot everything the upstream call needs *before* releasing the
        # transaction.  A SQLite write window held open across a slow provider
        # call starves every other writer in this process (the app instruments
        # exactly that and logs "long window" warnings), so the network call below
        # must run with no transaction open.
        try:
            api_base = (config.api_base or DEFAULT_API_BASE).rstrip("/")
            key_id = config.key_id or ""
            ttl = int(config.credential_ttl_seconds or DEFAULT_CREDENTIAL_TTL)
            configured_urls = _dedupe_urls(config.urls)
            payload = self._open_secret(config)
            self.db.commit()
        except ProviderSecretUnavailable as exc:
            self.db.rollback()
            if generation != _CACHE_GENERATION:
                return self.resolve(force=True)
            _CACHE = _CacheEntry(
                resolved=self._serve_or_degrade(f"密钥不可用：{exc}", now=now, fallback_urls=fallback_urls),
                expires_at=now + CACHE_TTL_SECONDS,
            )
            return _CACHE.resolved
        except AppError as exc:
            self.db.rollback()
            if generation != _CACHE_GENERATION:
                return self.resolve(force=True)
            _CACHE = _CacheEntry(
                resolved=self._serve_or_degrade(exc.message, now=now, fallback_urls=fallback_urls),
                expires_at=now + CACHE_TTL_SECONDS,
            )
            return _CACHE.resolved

        try:
            api_token = str(payload.get("api_token") or payload.get("credential") or "")
            if not api_token:
                raise AppError(400, "voice_relay_secret_invalid", "已保存的凭据缺少 API Token")
            minted = mint_cloudflare_credentials(
                api_base=api_base,
                key_id=key_id,
                api_token=api_token,
                ttl=ttl,
            )
            if generation != _CACHE_GENERATION:
                self.db.rollback()
                return self.resolve(force=True)
            urls = _dedupe_urls((*self._merge_minted_urls(configured_urls, minted), *fallback_urls))
            resolved = ResolvedIce(
                urls=urls,
                username=minted.get("username"),
                credential=minted.get("credential"),
                source="deployment",
            )
            lifetime = float(ttl)
            _CACHE = _CacheEntry(
                resolved=resolved,
                expires_at=now + _cache_lifetime(lifetime),
                credential_expires_at=now + max(lifetime - RENEW_MARGIN_SECONDS, 1.0),
            )
            _LAST_GOOD = _CACHE
            # Recovery is a fact too: without this the config row would keep showing
            # the last transient failure forever (the admin page has no other way to
            # learn that minting works again).  Written only on the transition, so a
            # healthy deployment does not re-commit every 60s.
            if config.status != "configured" or config.status_detail:
                config.status = "configured"
                config.status_detail = None
                try:
                    self.db.commit()
                except Exception:
                    self.db.rollback()
            return resolved
        except ProviderSecretUnavailable as exc:
            detail = f"密钥不可用：{exc}"
        except AppError as exc:
            detail = exc.message
        except Exception as exc:  # network, JSON, upstream 4xx/5xx
            detail = f"{type(exc).__name__}: {exc}"
            logger.warning("voice relay credential mint failed", exc_info=True)

        if generation != _CACHE_GENERATION:
            self.db.rollback()
            return self.resolve(force=True)
        _CACHE = _CacheEntry(
            resolved=self._serve_or_degrade(detail, now=now, fallback_urls=fallback_urls),
            expires_at=now + CACHE_TTL_SECONDS,
            credential_expires_at=(
                _LAST_GOOD.credential_expires_at if _LAST_GOOD is not None else 0.0
            ),
        )
        return _CACHE.resolved

    def _serve_or_degrade(
        self, detail: str, *, now: float, fallback_urls: tuple[str, ...] = ()
    ) -> ResolvedIce:
        """Keep relaying with the last minted credential, or degrade for real.

        A refresh failure is not evidence that the relay is unusable -- the
        credential already handed out stays valid for the rest of its TTL (a
        whole day by default).  So the previous one is served, marked
        ``source="stale"`` so callers can tell it apart from a fresh mint, while
        the failure is still recorded on the config row (and logged) so it does
        not go unnoticed.
        """

        entry = _usable_last_good(now)
        if entry is not None:
            remaining = int(max(0.0, entry.credential_expires_at - now))
            logger.warning(
                "Voice relay credential refresh failed (%s); keeping the credential "
                "minted earlier, valid for another %ss",
                detail,
                remaining,
            )
            self._record_failure(detail)
            return ResolvedIce(
                urls=_dedupe_urls((*entry.resolved.urls, *fallback_urls)),
                username=entry.resolved.username,
                credential=entry.resolved.credential,
                source="stale",
                detail=detail,
            )
        if fallback_urls:
            self._record_failure(detail)
            logger.warning("Voice TURN unavailable; serving opt-in STUN fallback: %s", detail)
            return ResolvedIce(
                urls=fallback_urls, source="fallback",
                detail=f"Cloudflare 不可用，已降级为手动 STUN（不提供媒体中继）：{detail}",
            )
        return self._degrade(detail, now=now)

    def _record_failure(self, detail: str) -> None:
        """Write "error" + the reason on the config row (own short transaction)."""

        try:
            fresh = self.get_config()
            if fresh is not None:
                fresh.status = "error"
                fresh.status_detail = detail[:400]
                self.db.commit()
            else:
                self.db.rollback()
        except Exception:
            self.db.rollback()

    def _degrade(self, detail: str, *, now: float) -> ResolvedIce:
        """Record the failure on the config row and return "no ICE servers".

        Written in its own short transaction: the failure path is reached *after*
        an upstream call, so it must never be the tail of a long-open one.
        """

        logger.warning("Voice relay unavailable, falling back to host-only ICE: %s", detail)
        self._record_failure(detail)
        del now
        return ResolvedIce(source="none", detail=detail)

    def _merge_minted_urls(
        self, configured: tuple[str, ...], minted: dict[str, Any]
    ) -> tuple[str, ...]:
        """Keep the administrator's URL order, but learn Cloudflare's real set.

        The stored order is the *server's* transport preference (aiortc honours
        only the first TURN entry), so it stays authoritative.  When Cloudflare
        reports a different URL set we log it instead of silently swapping it in
        -- that mismatch is exactly the thing worth noticing on first setup.
        """

        returned = tuple(str(item) for item in (minted.get("urls") or []) if item)
        if returned and set(returned) != set(configured):
            logger.info(
                "Cloudflare returned a different ICE URL set: returned=%s configured=%s",
                list(returned),
                list(configured),
            )
        return configured

    # -------------------------------------------------------------------- test

    @staticmethod
    def _config_signature(config: VoiceRelayConfig) -> tuple[Any, ...]:
        return (
            config.id, config.mode, config.enabled, config.key_id, config.api_base,
            config.credential_ttl_seconds, config.secret_fingerprint,
            _dedupe_urls(config.urls), _dedupe_urls(config.stun_urls),
            bool(config.stun_fallback_enabled),
        )

    def _persist_probe_status(
        self, signature: tuple[Any, ...] | None, generation: int, *, ok: bool, detail: str
    ) -> None:
        if signature is None or generation != _CACHE_GENERATION:
            return
        fresh = self.get_config()
        if fresh is None or self._config_signature(fresh) != signature:
            return
        fresh.status = "configured" if ok else "error"
        fresh.status_detail = None if ok else detail[:400]
        fresh.last_checked_at = datetime.datetime.now(datetime.timezone.utc)
        self.db.commit()
        # Diagnostic tests never invalidate a working runtime TURN credential.

    async def test(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Mint a credential and measure every URL from *this* machine.

        This is what resolves "which single TURN transport should the server
        use?" empirically: aiortc only ever honours one entry, so the answer has
        to come from a measurement rather than a guess.
        """

        config = self.get_config()
        test_generation = _CACHE_GENERATION
        signature = self._config_signature(config) if config else None
        mode = self._validate_mode(payload.get("mode") or (config.mode if config else MODE_CLOUDFLARE))
        fallback_enabled = payload.get("stun_fallback_enabled", bool(config and config.stun_fallback_enabled))
        if not isinstance(fallback_enabled, bool):
            raise AppError(400, "voice_relay_fallback_invalid", "STUN 兜底开关必须是布尔值")
        stun_raw = payload.get("stun_urls", config.stun_urls if config else None)
        if stun_raw is None:
            stun_raw = list(DEFAULT_STUN_URLS) if mode == MODE_MANUAL or fallback_enabled else []
        stun_urls = self._validate_stun_urls(stun_raw, required=mode == MODE_MANUAL or fallback_enabled)
        if mode == MODE_MANUAL:
            persist_status = bool(config and config.mode == mode and tuple(config.stun_urls or ()) == stun_urls)
            self.db.commit()
            probes = await probe_ice_urls(stun_urls)
            ok = any(item.get("ok") for item in probes)
            detail = (
                "STUN 可达；只提供公网映射，不提供 TURN 媒体中继" if ok
                else "STUN 探测失败；这是部署服务器的测试结果，不代表所有客户端网络"
            )
            if persist_status:
                self._persist_probe_status(signature, test_generation, ok=ok, detail=detail)
            return {
                "ok": ok, "detail": detail, "credential_masked": "无需凭据",
                "cloudflare_urls": [], "configured_urls": list(stun_urls),
                "probes": probes, "fallback_available": False,
            }
        key_id = str(
            payload.get("key_id") or (config.key_id if config else "") or ""
        ).strip()
        if not key_id:
            raise AppError(400, "voice_relay_key_id_required", "需要 Cloudflare TURN Key ID")
        api_base = str(
            payload.get("api_base") or (config.api_base if config else None) or DEFAULT_API_BASE
        ).strip().rstrip("/")
        self._validate_api_base(api_base)
        ttl_raw = payload.get("credential_ttl_seconds")
        if ttl_raw is None:
            ttl_raw = config.credential_ttl_seconds if config else DEFAULT_CREDENTIAL_TTL
        ttl = self._validate_ttl(ttl_raw)
        urls_raw = payload.get("urls")
        if urls_raw is None:
            urls_raw = config.urls if config else None
        urls = self._validate_urls(urls_raw)

        secret = str(payload.get("secret") or payload.get("api_token") or "").strip()
        if not secret:
            if config is None:
                raise AppError(
                    400, "voice_relay_secret_required", "首次测试需要填写 Cloudflare API Token"
                )
            stored = self._open_secret(config)
            secret = str(stored.get("api_token") or stored.get("credential") or "").strip()
        if not secret:
            raise AppError(400, "voice_relay_secret_required", "缺少 Cloudflare API Token")

        # Release the transaction before the upstream call and its probes: this
        # handler can spend tens of seconds talking to Cloudflare, and a SQLite
        # write window held open that long starves every other writer.
        persist_status = bool(
            config and config.mode == mode and not payload.get("secret") and not payload.get("api_token")
            and key_id == config.key_id
            and api_base == (config.api_base or DEFAULT_API_BASE).rstrip("/")
            and ttl == config.credential_ttl_seconds and urls == _dedupe_urls(config.urls)
            and stun_urls == _dedupe_urls(config.stun_urls)
            and fallback_enabled == bool(config.stun_fallback_enabled)
        )
        self.db.commit()

        if fallback_enabled:
            urls = _dedupe_urls((*urls, *stun_urls))
        try:
            minted = mint_cloudflare_credentials(
                api_base=api_base, key_id=key_id, api_token=secret, ttl=ttl
            )
        except Exception as exc:
            if not fallback_enabled:
                raise
            probes = await probe_ice_urls(stun_urls)
            fallback_ok = any(item.get("ok") for item in probes)
            reason = exc.message if isinstance(exc, AppError) else type(exc).__name__
            detail = f"Cloudflare 签发失败（{reason}）；手动 STUN {'可达' if fallback_ok else '不可达'}，不代表 TURN 中继可用"
            if persist_status:
                self._persist_probe_status(signature, test_generation, ok=False, detail=detail)
            return {
                "ok": False, "detail": detail, "credential_masked": "未取得新凭据",
                "cloudflare_urls": [], "configured_urls": list(urls),
                "probes": probes, "fallback_available": fallback_ok,
            }
        probes = await probe_ice_urls(
            urls,
            username=minted.get("username"),
            credential=minted.get("credential"),
        )
        turn_probes = [item for item in probes if item.get("scheme") in {"turn", "turns"}]
        stun_probes = [item for item in probes if item.get("scheme") in {"stun", "stuns"}]
        turn_ok = [item for item in turn_probes if item.get("ok")]
        ok = bool(turn_ok) or (not turn_probes and any(item.get("ok") for item in stun_probes))
        if ok:
            detail = (
                f"中继可用：{turn_ok[0]['url']}（{turn_ok[0]['elapsed_ms']}ms）"
                if turn_ok
                else "仅 STUN 可达（未配置或未验证 TURN）"
            )
        else:
            first = next((item for item in probes if not item.get("ok")), None)
            detail = f"中继不可用：{first['url'] if first else '无可用地址'}"
            if first and first.get("detail"):
                detail = f"{detail}（{first['detail']}）"

        if persist_status:
            self._persist_probe_status(signature, test_generation, ok=ok, detail=detail)

        return {
            "ok": ok,
            "detail": detail,
            "credential_masked": _mask(str(minted.get("credential") or "")),
            "cloudflare_urls": list(minted.get("urls") or []),
            "configured_urls": list(urls),
            "probes": probes,
        }


def _cache_lifetime(credential_ttl: float) -> float:
    return max(min(CACHE_TTL_SECONDS, credential_ttl - RENEW_MARGIN_SECONDS), 5.0)


# ------------------------------------------------------------------- upstream


def mint_cloudflare_credentials(
    *, api_base: str, key_id: str, api_token: str, ttl: int
) -> dict[str, Any]:
    """Exchange the long-lived TURN key for short-lived credentials.

    The exact response shape is deliberately tolerated in several forms: the
    deployment could not verify it against live documentation, and the admin
    "test" action returns the raw digest so the first real call settles it.
    """

    import httpx

    base = (api_base or DEFAULT_API_BASE).rstrip("/")
    if not key_id:
        raise AppError(400, "voice_relay_key_id_required", "缺少 Cloudflare TURN Key ID")
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    # Cloudflare's documented path is ``generate-ice-servers``; the older
    # ``generate`` path is still live and returns the same data in a different
    # shape (both verified against a real key).  Try the documented one first so
    # the integration follows the docs, and keep the legacy path as a fallback.
    last_error: AppError | None = None
    for suffix in ("generate-ice-servers", "generate"):
        url = f"{base}/turn/keys/{quote(key_id, safe='')}/credentials/{suffix}"
        response = httpx.post(url, json={"ttl": int(ttl)}, headers=headers, timeout=15.0)
        if response.status_code == 404 and suffix == "generate-ice-servers":
            last_error = AppError(
                502,
                "voice_relay_upstream_error",
                f"Cloudflare 返回 404：{response.text[:200]}",
            )
            continue
        if response.status_code >= 400:
            raise AppError(
                502,
                "voice_relay_upstream_error",
                f"Cloudflare 返回 {response.status_code}：{response.text[:200]}",
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise AppError(502, "voice_relay_upstream_invalid", "Cloudflare 响应不是合法 JSON") from exc
        return _normalize_ice_payload(data)
    raise last_error or AppError(502, "voice_relay_upstream_error", "Cloudflare 凭据签发失败")


def _normalize_ice_payload(data: Any) -> dict[str, Any]:
    """Pull ``{urls, username, credential}`` out of whichever shape came back.

    Cloudflare has two live shapes and they differ in a way that silently breaks
    relay if handled naively:

    * ``/credentials/generate`` -> ``{"iceServers": {urls, username, credential}}``
    * ``/credentials/generate-ice-servers`` -> ``{"iceServers": [{urls: [stun]},
      {urls: [turn...], username, credential}]}`` -- STUN and TURN are split into
      separate entries and only the TURN entry carries the credential.  Taking the
      first entry of that list yields a STUN-only configuration: calls would look
      configured, would never relay, and nothing would log an error.

    So entries are merged: every URL is kept, and the credential is taken from
    whichever entry actually has one.
    """

    candidate: Any = data
    if isinstance(data, dict):
        for key in ("iceServers", "ice_servers", "result", "data"):
            value = data.get(key)
            if value:
                candidate = value
                break
    entries = candidate if isinstance(candidate, list) else [candidate]

    urls: list[str] = []
    username: str | None = None
    credential: str | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        raw_urls = entry.get("urls") or entry.get("url") or []
        if isinstance(raw_urls, str):
            raw_urls = [raw_urls]
        for url in raw_urls:
            text = str(url)
            if text and text not in urls:
                urls.append(text)
        if not username and entry.get("username"):
            username = str(entry["username"])
        if not credential:
            found = entry.get("credential") or entry.get("password") or entry.get("token")
            if found:
                credential = str(found)

    if not urls:
        raise AppError(502, "voice_relay_upstream_invalid", "Cloudflare 响应未包含 ICE 地址")
    if not credential:
        raise AppError(502, "voice_relay_upstream_invalid", "Cloudflare 响应缺少 credential")
    return {"urls": urls, "username": username, "credential": credential}


# ---------------------------------------------------------------------- probes


def _parse_ice_url(url: str) -> dict[str, Any]:
    from aiortc.rtcicetransport import parse_stun_turn_uri

    parsed = parse_stun_turn_uri(url)
    if parsed.get("port") is None:
        parsed["port"] = 5349 if parsed["scheme"] in {"stuns", "turns"} else 3478
    return parsed


async def probe_ice_urls(
    urls: tuple[str, ...] | list[str],
    *,
    username: str | None = None,
    credential: str | None = None,
    timeout: float = 8.0,
) -> list[dict[str, Any]]:
    """Empirically test each URL from *this* machine.

    This is what turns "which transport should the server use?" from a guess
    into a measurement: each entry reports whether a STUN mapping (``srflx``) or
    a TURN allocation (``relay``) could actually be obtained here.
    """

    results: list[dict[str, Any]] = []
    for url in urls:
        started = time.monotonic()
        entry: dict[str, Any] = {"url": str(url), "ok": False, "detail": None, "elapsed_ms": 0}
        try:
            parsed = _parse_ice_url(str(url))
            entry["scheme"] = parsed["scheme"]
            entry["transport"] = parsed.get("transport") or ("tls" if parsed["scheme"].startswith("stuns") or parsed["scheme"] == "turns" else "udp")
            entry["candidate"] = await asyncio.wait_for(
                _probe_one(parsed, username=username, credential=credential),
                timeout=timeout,
            )
            entry["ok"] = bool(entry["candidate"])
            if not entry["ok"]:
                entry["detail"] = "未取得 srflx/relay 候选"
        except asyncio.TimeoutError:
            entry["detail"] = f"超时（>{timeout:.0f}s）"
        except Exception as exc:
            entry["detail"] = f"{type(exc).__name__}: {exc}"
        entry["elapsed_ms"] = int((time.monotonic() - started) * 1000)
        results.append(entry)
    return results


async def _probe_one(
    parsed: dict[str, Any], *, username: str | None, credential: str | None
) -> dict[str, Any] | None:
    from aioice import Connection

    scheme = str(parsed["scheme"])
    host = str(parsed["host"])
    port = int(parsed["port"])
    kwargs: dict[str, Any] = {"ice_controlling": True, "use_ipv4": True, "use_ipv6": False}
    if scheme in {"stun", "stuns"}:
        kwargs["stun_server"] = (host, port)
        wanted = "srflx"
    else:
        if not username or not credential:
            raise AppError(400, "voice_relay_credential_missing", "TURN 探测需要 username/credential")
        kwargs["turn_server"] = (host, port)
        kwargs["turn_username"] = username
        kwargs["turn_password"] = credential
        kwargs["turn_transport"] = str(parsed.get("transport") or "udp")
        kwargs["turn_ssl"] = scheme == "turns"
        wanted = "relay"

    connection = Connection(**kwargs)
    try:
        await connection.gather_candidates()
        for candidate in connection.local_candidates:
            if candidate.type == wanted:
                return {
                    "type": candidate.type,
                    "address": f"{candidate.host}:{candidate.port}",
                    "related": (
                        f"{candidate.related_address}:{candidate.related_port}"
                        if candidate.related_address
                        else None
                    ),
                }
        return None
    finally:
        try:
            await connection.close()
        except Exception:
            logger.debug("probe connection close failed", exc_info=True)


def probe_ice_urls_sync(urls: tuple[str, ...] | list[str], **kwargs: Any) -> list[dict[str, Any]]:
    """Run the probe from synchronous API handlers (own event loop)."""

    return asyncio.run(probe_ice_urls(urls, **kwargs))


def resolve_for_runtime() -> ResolvedIce:
    """Resolve the relay with a fresh DB session, for use from a worker thread.

    The offer path runs inside the event loop and must not block on a 15s
    upstream timeout, so it hands this to ``asyncio.to_thread``; the request's own
    session cannot be reused there because it would cross threads.
    """

    from app.core.config import get_settings
    from app.core.database import SessionLocal

    try:
        with SessionLocal() as db:
            return VoiceRelayService(db, get_settings()).resolve()
    except Exception:
        logger.debug("voice relay runtime resolve failed", exc_info=True)
        # Even a database hiccup (SQLite lock contention is a known failure mode
        # here) must not drop a relay that is still usable.
        entry = _usable_last_good(time.monotonic())
        if entry is not None:
            return ResolvedIce(
                urls=entry.resolved.urls,
                username=entry.resolved.username,
                credential=entry.resolved.credential,
                source="stale",
                detail="解析失败",
            )
        return ResolvedIce(source="none", detail="解析失败")
