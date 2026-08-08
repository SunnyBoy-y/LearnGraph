from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import httpx
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.core.secret_store import SecretStoreUnavailable, secret_store_from_settings
from app.domain.extension_models import (
    MCPOAuthClientRegistration,
    MCPServer,
    MCPServerCredential,
)
from app.repositories.audit import AuditRepository
from app.repositories.extensions import MCPServerCredentialRepository, MCPServerRepository
from app.repositories.scoped import ScopedRepository
from app.services.mcp_secret_store import decrypt_mcp_secret, encrypt_mcp_secret


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(value: datetime | None) -> datetime | None:
    """Normalize a possibly naive (SQLite-stored) datetime to UTC-aware."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}…{value[-4:]}"


def _normalize_issuer(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise AppError(422, "mcp_oauth_invalid_issuer", "OAuth issuer cannot be empty")
    return normalized


def _validate_token_endpoint(url: str) -> str:
    """Accept only absolute https URLs (or loopback http) with no credentials.

    Redirects are disabled at the HTTP client layer; this mirrors the bounded
    endpoint policy used by the MCP HTTP adapter without doing DNS resolution
    at validation time.
    """

    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AppError(422, "mcp_oauth_invalid_endpoint", "OAuth endpoint must be an absolute http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise AppError(
            422,
            "mcp_oauth_invalid_endpoint",
            "OAuth endpoint must not contain credentials or a URL fragment",
        )
    if parsed.scheme == "http":
        host = parsed.hostname.casefold()
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise AppError(
                422,
                "mcp_oauth_invalid_endpoint",
                "Plain HTTP OAuth endpoints are allowed only on loopback addresses",
            )
    return url.strip()


def _token_exchange_request(
    *,
    url: str,
    form: dict[str, str],
    timeout_seconds: float = 12.0,
    max_response_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    """POST a form-encoded OAuth token/refresh request and parse JSON.

    Bounded like the MCP HTTP adapter: no redirects, no ambient proxy, strict
    response-size cap, and hard JSON validation.
    """

    url = _validate_token_endpoint(url)
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "POST",
                url,
                data=form,
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/x-www-form-urlencoded",
                },
            ) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_response_bytes:
                        raise AppError(
                            502,
                            "mcp_oauth_response_too_large",
                            "OAuth token endpoint response exceeded the size bound",
                        )
                try:
                    parsed = json.loads(bytes(body).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AppError(
                        502,
                        "mcp_oauth_invalid_json",
                        "OAuth token endpoint returned invalid JSON",
                    ) from exc
    except httpx.TimeoutException as exc:
        raise AppError(
            504,
            "mcp_oauth_token_timeout",
            "OAuth token endpoint timed out",
            {"endpoint": url},
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise AppError(
            502,
            "mcp_oauth_token_http_error",
            f"OAuth token endpoint returned HTTP {exc.response.status_code}",
            {"endpoint": url},
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502,
            "mcp_oauth_token_unavailable",
            "OAuth token endpoint is unavailable",
            {"endpoint": url},
        ) from exc
    if not isinstance(parsed, dict):
        raise AppError(502, "mcp_oauth_invalid_response", "OAuth token endpoint response must be a JSON object")
    error = parsed.get("error")
    if isinstance(error, str) and error:
        raise AppError(
            400,
            "mcp_oauth_token_endpoint_error",
            error,
            {"error_description": parsed.get("error_description")},
        )
    return parsed


def _dynamic_registration_request(
    *,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float = 12.0,
    max_response_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    """POST JSON to an OAuth dynamic-client-registration endpoint (bounded)."""

    url = _validate_token_endpoint(url)
    try:
        with httpx.Client(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            with client.stream(
                "POST",
                url,
                json=payload,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
            ) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > max_response_bytes:
                        raise AppError(
                            502,
                            "mcp_oauth_response_too_large",
                            "OAuth registration response exceeded the size bound",
                        )
                try:
                    parsed = json.loads(bytes(body).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise AppError(
                        502,
                        "mcp_oauth_invalid_json",
                        "OAuth registration endpoint returned invalid JSON",
                    ) from exc
    except httpx.TimeoutException as exc:
        raise AppError(
            504,
            "mcp_oauth_registration_timeout",
            "OAuth registration endpoint timed out",
            {"endpoint": url},
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise AppError(
            502,
            "mcp_oauth_registration_http_error",
            f"OAuth registration endpoint returned HTTP {exc.response.status_code}",
            {"endpoint": url},
        ) from exc
    except httpx.HTTPError as exc:
        raise AppError(
            502,
            "mcp_oauth_registration_unavailable",
            "OAuth registration endpoint is unavailable",
            {"endpoint": url},
        ) from exc
    if not isinstance(parsed, dict):
        raise AppError(
            502,
            "mcp_oauth_invalid_response",
            "OAuth registration response must be a JSON object",
        )
    return parsed


class _RefreshFlight:
    __slots__ = ("event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Any = None


class _RefreshCoordinator:
    """Per-credential single-flight: concurrent refreshes share one token call."""

    def __init__(self) -> None:
        self._mutex = threading.Lock()
        self._inflight: dict[str, _RefreshFlight] = {}

    def run(self, key: str, work: Callable[[], Any]) -> Any:
        with self._mutex:
            flight = self._inflight.get(key)
            if flight is None:
                flight = _RefreshFlight()
                self._inflight[key] = flight
                leader = True
            else:
                leader = False
        if leader:
            try:
                result = work()
            except BaseException as exc:  # noqa: BLE001 - propagated to waiters
                result = exc
            with self._mutex:
                flight.result = result
                flight.event.set()
                self._inflight.pop(key, None)
        else:
            flight.event.wait()
            with self._mutex:
                result = flight.result
        if isinstance(result, BaseException):
            raise result
        return result


@dataclass(frozen=True)
class OAuthTokenSet:
    access_token: str
    refresh_token: str | None
    token_type: str
    scope: str
    expires_at: datetime | None
    client_id: str | None = None


# Explicit trusted-issuer allowlist for dynamic client registration (P2-B).
# Empty by default: registration is closed until a deployment opts in.
MCP_OAUTH_TRUSTED_ISSUERS: frozenset[str] = frozenset()
OAUTH_PENDING_MAX_AGE_SECONDS = 600
OAUTH_DEFAULT_REFRESH_BEFORE_SECONDS = 120


class MCPOAuthLifecycle:
    """Workspace-scoped OAuth lifecycle for isolated MCP runners.

    The FastAPI process stores encrypted/static ciphertext and redacted
    projections only. Live tokens are injected into the runner envelope and
    never returned through API DTOs, agent tool traffic, or normal audit JSON.
    """

    _refresh_coordinator = _RefreshCoordinator()

    def __init__(
        self,
        db: Session,
        workspace_id: str,
        actor_id: str,
        *,
        settings: Settings,
    ) -> None:
        self.db = db
        self.workspace_id = workspace_id
        self.actor_id = actor_id
        self.settings = settings
        # The lifecycle owns a single SQLAlchemy Session that is shared by every
        # caller. ``_RefreshCoordinator`` single-flights the token-endpoint call
        # across threads, so this lock serializes all DB access on that shared
        # session (a Session is not thread-safe and must never be touched by two
        # threads concurrently). HTTP token-endpoint calls stay outside the lock.
        self._db_lock = threading.Lock()
        self.servers = MCPServerRepository(db, workspace_id)
        self.credentials = MCPServerCredentialRepository(db, workspace_id)
        self.audit = AuditRepository(db, workspace_id)
        self.client_registrations = ScopedRepository(
            db, MCPOAuthClientRegistration, workspace_id
        )

    # -- encrypted-secret helpers -------------------------------------------------

    def _require_store(self) -> None:
        try:
            secret_store_from_settings(self.settings).active_key(create=True)
        except SecretStoreUnavailable as exc:
            raise AppError(
                503,
                "secret_store_unavailable",
                "OAuth token encryption requires the configured encrypted secret store",
            ) from exc

    def _encrypt(self, value: str) -> str:
        return encrypt_mcp_secret(self.settings, value)

    def _decrypt(self, ciphertext: str | None, *, label: str) -> str:
        if not ciphertext:
            raise AppError(409, "mcp_oauth_material_missing", f"{label} is not available")
        return decrypt_mcp_secret(self.settings, ciphertext, label=label)

    def _credential(self, server: MCPServer) -> MCPServerCredential | None:
        if not server.auth_reference:
            return None
        return self.db.scalar(
            self.credentials.query().where(
                MCPServerCredential.id == server.auth_reference,
                MCPServerCredential.server_id == server.id,
            )
        )

    # -- static bearer (kept 100% intact) ----------------------------------------

    def store_static_bearer(self, server: MCPServer, bearer_token: str) -> MCPServerCredential:
        token = bearer_token.strip()
        if not token:
            raise AppError(422, "mcp_credential_empty", "MCP bearer token cannot be empty")
        existing = self.db.scalar(
            self.credentials.query().where(MCPServerCredential.server_id == server.id)
        )
        fingerprint = _fingerprint(token)
        masked = _mask_secret(token)
        # Ciphertext is opaque to ordinary responses and shares the same
        # versioned secret-store lifecycle as Provider secrets.
        ciphertext = encrypt_mcp_secret(self.settings, token)
        if existing is None:
            record = self.credentials.add(
                MCPServerCredential(
                    workspace_id=self.workspace_id,
                    server_id=server.id,
                    auth_kind="static_bearer",
                    ciphertext=ciphertext,
                    secret_masked=masked,
                    secret_fingerprint=fingerprint,
                )
            )
        else:
            existing.ciphertext = ciphertext
            existing.secret_masked = masked
            existing.secret_fingerprint = fingerprint
            existing.auth_kind = "static_bearer"
            record = existing
        server.auth_reference = record.id
        self.audit.record(
            actor_id=self.actor_id,
            action="mcp.credential_stored",
            resource_type="mcp_server",
            resource_id=server.id,
            details={
                "credential_kind": "static_bearer",
                "secret_fingerprint": fingerprint,
                "secret_masked": masked,
            },
        )
        self.db.commit()
        self.db.refresh(record)
        return record

    # -- authorization-code flow --------------------------------------------------

    def begin_authorization_code(
        self,
        server: MCPServer,
        *,
        redirect_uri: str,
        scope: str,
    ) -> dict[str, str]:
        """Create PKCE + state material for an authorization-code flow.

        The issued ``state`` and encrypted ``code_verifier`` are persisted on
        the credential so the later token exchange can bind ``state`` and replay
        PKCE S256 without trusting the client's memory.
        """

        # OAuth token persistence requires the encrypted secret store; require
        # it up-front so the whole flow fails closed rather than at exchange.
        self._require_store()
        code_verifier = base64.urlsafe_b64encode(uuid4().bytes + uuid4().bytes).decode("ascii").rstrip("=")
        code_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        state = str(uuid4())
        nonce = str(uuid4())
        credential = self._credential(server)
        if credential is None:
            credential = self.credentials.add(
                MCPServerCredential(
                    workspace_id=self.workspace_id,
                    server_id=server.id,
                    auth_kind="oauth_authorization_code",
                    ciphertext="",
                    secret_masked="",
                    secret_fingerprint="",
                )
            )
            server.auth_reference = credential.id
        else:
            credential.auth_kind = "oauth_authorization_code"
        credential.pending_state = state
        credential.pending_code_verifier_ciphertext = self._encrypt(code_verifier)
        credential.pending_scope = scope.strip()
        credential.pending_redirect_uri = redirect_uri.strip()
        credential.pending_created_at = _utc_now()
        credential.status = "pending"
        self.audit.record(
            actor_id=self.actor_id,
            action="mcp.oauth_authorization_started",
            resource_type="mcp_server",
            resource_id=server.id,
            details={
                "redirect_uri": redirect_uri,
                "scope": scope,
                "pkce": "S256",
                "state_fingerprint": _fingerprint(state),
            },
        )
        self.db.commit()
        return {
            "state": state,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "scope": scope,
            "redirect_uri": redirect_uri,
        }

    def build_authorization_url(
        self,
        server: MCPServer,
        *,
        auth_endpoint: str,
        redirect_uri: str,
        scope: str,
        client_id: str,
    ) -> dict[str, str]:
        """Start an authorization-code flow and build the PKCE authorization URL.

        The one-time ``state`` and encrypted code verifier stay bound to the
        server credential; only the URL and ``state`` are returned to callers.
        """

        material = self.begin_authorization_code(
            server,
            redirect_uri=redirect_uri,
            scope=scope,
        )
        endpoint = _validate_token_endpoint(auth_endpoint)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scope,
            "state": material["state"],
            "code_challenge": material["code_challenge"],
            "code_challenge_method": "S256",
            "nonce": material["nonce"],
        }
        separator = "&" if "?" in endpoint else "?"
        return {
            "server_id": server.id,
            "authorization_url": f"{endpoint}{separator}{urlencode(params)}",
            "state": material["state"],
            "scope": scope,
            "redirect_uri": redirect_uri,
            "code_challenge_method": "S256",
        }

    def exchange_authorization_code(
        self,
        server: MCPServer,
        *,
        authorization_code: str,
        returned_state: str,
        token_endpoint: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        timeout_seconds: float = 12.0,
    ) -> MCPServerCredential:
        """Exchange an authorization ``code`` for an encrypted ``OAuthTokenSet``.

        Binds ``state`` with a constant-time compare, applies PKCE S256 using the
        code verifier persisted by :meth:`begin_authorization_code`, restricts the
        granted ``scope`` to the intersection of the requested and returned scopes,
        and persists tokens encrypted. ``client_id`` falls back to the stored
        credential client id when available.
        """

        credential = self._credential(server)
        if credential is None or credential.auth_kind != "oauth_authorization_code":
            raise AppError(
                409,
                "mcp_oauth_flow_required",
                "Call begin_authorization_code before exchanging an authorization code",
            )
        if not credential.pending_state or not credential.pending_code_verifier_ciphertext:
            raise AppError(
                409,
                "mcp_oauth_pending_missing",
                "No pending authorization request is bound to this server",
            )
        if credential.pending_created_at is not None and (
            _utc_now() - _as_aware(credential.pending_created_at)
        ) > timedelta(seconds=OAUTH_PENDING_MAX_AGE_SECONDS):
            raise AppError(
                409,
                "mcp_oauth_pending_expired",
                "The pending authorization request has expired",
            )
        if not constant_time_equals(returned_state or "", credential.pending_state):
            raise AppError(
                400,
                "mcp_oauth_state_mismatch",
                "OAuth authorization state did not match the issued value",
            )
        code_verifier = self._decrypt(
            credential.pending_code_verifier_ciphertext, label="PKCE code verifier"
        )
        requested_scope = (credential.pending_scope or "").strip()
        redirect_uri = (credential.pending_redirect_uri or "").strip()
        if not authorization_code:
            raise AppError(422, "mcp_oauth_code_required", "An authorization code is required")
        if not requested_scope:
            raise AppError(422, "mcp_oauth_scope_required", "OAuth scope is required")

        client_id = client_id or credential.client_id or ""
        form: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": authorization_code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "code_verifier": code_verifier,
            "code_challenge_method": "S256",
            "scope": requested_scope,
        }
        if client_secret:
            form["client_secret"] = client_secret
        parsed = _token_exchange_request(url=token_endpoint, form=form, timeout_seconds=timeout_seconds)

        access_token = parsed.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise AppError(502, "mcp_oauth_missing_access_token", "OAuth token response omitted access_token")
        token_type = str(parsed.get("token_type") or "Bearer")
        refresh_token = parsed.get("refresh_token")
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise AppError(502, "mcp_oauth_invalid_refresh_token", "OAuth refresh_token must be a string")
        granted_scope = self._restrict_scope(requested_scope, parsed.get("scope"))
        expires_at = self._expires_at(parsed.get("expires_in"))

        credential.auth_kind = "oauth_authorization_code"
        credential.ciphertext = self._encrypt(access_token)
        credential.secret_masked = _mask_secret(access_token)
        credential.secret_fingerprint = _fingerprint(access_token)
        credential.refresh_token_ciphertext = (
            self._encrypt(refresh_token) if refresh_token else None
        )
        credential.token_type = token_type
        credential.scope = granted_scope
        credential.issuer = self._issuer_from_endpoint(token_endpoint)
        credential.client_id = client_id or None
        credential.expires_at = expires_at
        credential.status = "active"
        credential.revoked_at = None
        credential.revoked_reason = None
        # Clear the one-time PKCE/state material.
        credential.pending_state = None
        credential.pending_code_verifier_ciphertext = None
        credential.pending_scope = None
        credential.pending_redirect_uri = None
        credential.pending_created_at = None
        server.auth_reference = credential.id
        self.audit.record(
            actor_id=self.actor_id,
            action="mcp.oauth_token_stored",
            resource_type="mcp_server",
            resource_id=server.id,
            details={
                "auth_kind": "oauth_authorization_code",
                "issuer": credential.issuer,
                "scope": granted_scope,
                "token_type": token_type,
                "has_refresh_token": bool(refresh_token),
                "access_token_fingerprint": credential.secret_fingerprint,
                "access_token_masked": credential.secret_masked,
                "expires_at": expires_at.isoformat() if expires_at else None,
            },
        )
        self.db.commit()
        self.db.refresh(credential)
        return credential

    # -- refresh + lock + revocation ---------------------------------------------

    def refresh_access_token(
        self,
        server: MCPServer,
        *,
        token_endpoint: str,
        client_id: str | None = None,
        client_secret: str | None = None,
        requested_scope: str | None = None,
        force: bool = False,
        refresh_before_seconds: int = OAUTH_DEFAULT_REFRESH_BEFORE_SECONDS,
        timeout_seconds: float = 12.0,
    ) -> MCPServerCredential:
        """Refresh an OAuth access token under a per-credential single-flight lock.

        Refreshes before expiry (``refresh_before_seconds`` headroom) and always
        minimizes scope to the currently granted scope. Concurrent callers for
        the same credential share a single token endpoint call.
        """

        with self._db_lock:
            credential = self._credential(server)
            if credential is None or credential.auth_kind != "oauth_authorization_code":
                raise AppError(
                    409,
                    "mcp_oauth_not_configured",
                    "This MCP server does not hold an OAuth authorization-code credential",
                )
            if credential.status == "revoked" or credential.revoked_at is not None:
                raise AppError(409, "mcp_credential_revoked", "The OAuth credential has been revoked")
            if not credential.refresh_token_ciphertext:
                raise AppError(
                    409,
                    "mcp_oauth_no_refresh_token",
                    "The OAuth credential has no refresh token to rotate",
                )
            if not force and credential.expires_at is not None and (
                _as_aware(credential.expires_at)
                > _utc_now() + timedelta(seconds=refresh_before_seconds)
            ):
                return credential
        key = f"{self.workspace_id}:{credential.id}"
        result = self._refresh_coordinator.run(
            key,
            lambda: self._perform_refresh(
                credential,
                token_endpoint=token_endpoint,
                client_id=client_id or credential.client_id,
                client_secret=client_secret,
                requested_scope=requested_scope or credential.scope,
                timeout_seconds=timeout_seconds,
            ),
        )
        # Re-fetch under the per-lifecycle DB lock so a waiter that shares this
        # lifecycle's session with the leader observes the leader's commit
        # without racing on the same SQLAlchemy Session. The leader finishes its
        # commit before the coordinator releases the waiter, so the lock is
        # always free here.
        with self._db_lock:
            refreshed = self._credential(server)
        if refreshed is not None:
            return refreshed
        # A waiter that shares the leader's session may not re-observe the row;
        # fall back to the refreshed record the coordinator produced.
        if result is not None and getattr(result, "id", None) == credential.id:
            return result
        raise AppError(409, "mcp_oauth_not_configured", "The OAuth credential disappeared during refresh")

    def _perform_refresh(
        self,
        credential: MCPServerCredential,
        *,
        token_endpoint: str,
        client_id: str | None,
        client_secret: str | None,
        requested_scope: str | None,
        timeout_seconds: float,
    ) -> MCPServerCredential:
        refresh_token = self._decrypt(credential.refresh_token_ciphertext, label="refresh token")
        scope = (requested_scope or credential.scope or "").strip()
        form: dict[str, str] = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id or "",
            "scope": scope,
        }
        if client_secret:
            form["client_secret"] = client_secret
        parsed = _token_exchange_request(url=token_endpoint, form=form, timeout_seconds=timeout_seconds)
        access_token = parsed.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise AppError(502, "mcp_oauth_missing_access_token", "OAuth refresh response omitted access_token")
        new_refresh = parsed.get("refresh_token")
        if new_refresh is not None and not isinstance(new_refresh, str):
            raise AppError(502, "mcp_oauth_invalid_refresh_token", "OAuth refresh_token must be a string")
        granted_scope = self._restrict_scope(scope, parsed.get("scope"))
        expires_at = self._expires_at(parsed.get("expires_in"))

        with self._db_lock:
            credential.ciphertext = self._encrypt(access_token)
            credential.secret_masked = _mask_secret(access_token)
            credential.secret_fingerprint = _fingerprint(access_token)
            if new_refresh:
                credential.refresh_token_ciphertext = self._encrypt(new_refresh)
            credential.token_type = str(parsed.get("token_type") or credential.token_type or "Bearer")
            credential.scope = granted_scope
            credential.expires_at = expires_at
            credential.status = "active"
            credential.revoked_at = None
            credential.revoked_reason = None
            self.audit.record(
                actor_id=self.actor_id,
                action="mcp.oauth_token_refreshed",
                resource_type="mcp_server",
                resource_id=credential.server_id,
                details={
                    "issuer": credential.issuer,
                    "scope": granted_scope,
                    "has_refresh_token": bool(new_refresh),
                    "access_token_fingerprint": credential.secret_fingerprint,
                    "access_token_masked": credential.secret_masked,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
            )
            self.db.commit()
        return credential

    def revoke(
        self,
        server: MCPServer,
        *,
        reason: str = "",
    ) -> MCPServerCredential | None:
        """Mark the credential revoked, clear ``server.auth_reference``, and audit."""

        credential = self._credential(server)
        if credential is None:
            return None
        credential.status = "revoked"
        credential.revoked_at = _utc_now()
        credential.revoked_reason = (reason or "")[:500]
        server.auth_reference = None
        self.audit.record(
            actor_id=self.actor_id,
            action="mcp.credential_revoked",
            resource_type="mcp_server",
            resource_id=server.id,
            details={
                "credential_kind": credential.auth_kind,
                "revoked_reason": (reason or "")[:500],
                "credential_id": credential.id,
            },
        )
        self.db.commit()
        self.db.refresh(credential)
        return credential

    # -- dynamic client registration ---------------------------------------------

    def register_oauth_client(
        self,
        server: MCPServer,
        *,
        issuer: str,
        registration_endpoint: str,
        client_name: str,
        redirect_uris: list[str],
        grant_types: list[str] | None = None,
        trusted_issuers: frozenset[str] | None = None,
        timeout_seconds: float = 12.0,
    ) -> dict[str, Any]:
        """Register an OAuth client, but only for explicitly trusted issuers.

        The allowlist is empty by default (closed registration). On success the
        client id and encrypted client secret are persisted per (workspace,
        issuer) and the returned projection carries no secret material.
        """

        allowlist = (
            trusted_issuers
            if trusted_issuers is not None
            else frozenset(
                getattr(self.settings, "mcp_oauth_trusted_issuers", frozenset())
            )
        )
        normalized_issuer = _normalize_issuer(issuer)
        if normalized_issuer not in allowlist:
            raise AppError(
                403,
                "mcp_oauth_issuer_untrusted",
                "OAuth dynamic client registration is limited to explicitly trusted issuers",
                {"issuer": normalized_issuer},
            )
        if not redirect_uris or not all(isinstance(item, str) and item for item in redirect_uris):
            raise AppError(422, "mcp_oauth_redirect_required", "At least one redirect URI is required")
        _validate_token_endpoint(registration_endpoint)
        payload: dict[str, Any] = {
            "client_name": client_name.strip(),
            "redirect_uris": [item.strip() for item in redirect_uris],
            "grant_types": grant_types or ["authorization_code", "refresh_token"],
            "token_endpoint_auth_method": "client_secret_post",
        }
        parsed = _dynamic_registration_request(
            url=registration_endpoint,
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        client_id = parsed.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise AppError(
                502,
                "mcp_oauth_missing_client_id",
                "OAuth registration response omitted client_id",
            )
        client_secret = parsed.get("client_secret")
        if client_secret is not None and not isinstance(client_secret, str):
            raise AppError(502, "mcp_oauth_invalid_client_secret", "OAuth client_secret must be a string")
        auth_method = str(parsed.get("token_endpoint_auth_method") or "client_secret_post")

        existing = self.db.scalar(
            self.client_registrations.query().where(
                MCPOAuthClientRegistration.issuer == normalized_issuer
            )
        )
        if existing is None:
            existing = self.client_registrations.add(
                MCPOAuthClientRegistration(
                    workspace_id=self.workspace_id,
                    server_id=server.id,
                    issuer=normalized_issuer,
                    client_id=client_id,
                    client_secret_ciphertext=(
                        self._encrypt(client_secret) if client_secret else None
                    ),
                    token_endpoint_auth_method=auth_method,
                )
            )
        else:
            existing.server_id = server.id
            existing.client_id = client_id
            existing.client_secret_ciphertext = (
                self._encrypt(client_secret) if client_secret else None
            )
            existing.token_endpoint_auth_method = auth_method
        # Make the registered client the default for this server's OAuth flow.
        credential = self._credential(server)
        if credential is not None:
            credential.client_id = client_id
        self.audit.record(
            actor_id=self.actor_id,
            action="mcp.oauth_client_registered",
            resource_type="mcp_server",
            resource_id=server.id,
            details={
                "issuer": normalized_issuer,
                "client_id": client_id,
                "client_secret_present": bool(client_secret),
                "token_endpoint_auth_method": auth_method,
            },
        )
        self.db.commit()
        self.db.refresh(existing)
        return {
            "id": existing.id,
            "issuer": existing.issuer,
            "client_id": existing.client_id,
            "token_endpoint_auth_method": existing.token_endpoint_auth_method,
            "has_client_secret": bool(existing.client_secret_ciphertext),
        }

    # -- shared helpers -----------------------------------------------------------

    @staticmethod
    def _restrict_scope(requested_scope: str, returned_scope: Any) -> str:
        """Intersect returned scope with what was requested (scope minimization)."""

        requested = {item for item in (requested_scope or "").split() if item}
        if not requested:
            return ""
        returned = returned_scope
        if returned is None:
            # A missing scope field means the requested scope was granted.
            return " ".join(sorted(requested))
        if not isinstance(returned, str):
            raise AppError(502, "mcp_oauth_invalid_scope", "OAuth scope must be a string")
        granted = requested & {item for item in returned.split() if item}
        if not granted:
            raise AppError(
                400,
                "mcp_oauth_scope_denied",
                "The OAuth issuer granted no requested scope",
            )
        return " ".join(sorted(granted))

    @staticmethod
    def _expires_at(expires_in: Any) -> datetime | None:
        if expires_in is None:
            return None
        try:
            seconds = int(expires_in)
        except (TypeError, ValueError):
            return None
        if seconds <= 0:
            return None
        return _utc_now() + timedelta(seconds=seconds)

    @staticmethod
    def _issuer_from_endpoint(token_endpoint: str) -> str:
        parsed = urlsplit(token_endpoint.strip())
        return _normalize_issuer(f"{parsed.scheme}://{parsed.netloc}")

    # -- outward surfaces ---------------------------------------------------------

    def runner_credential_envelope(
        self,
        server: MCPServer,
        *,
        audience: str,
        ttl_seconds: int = 60,
    ) -> dict[str, Any] | None:
        """Build a short-lived runner-only credential envelope.

        The envelope intentionally omits raw tokens from ordinary API/agent
        surfaces. A configured runner decrypts/uses it in-process only. Revoked
        or expired credentials are refused (returns ``None``).
        """

        if not server.auth_reference:
            return None
        record = self.credentials.get(server.auth_reference)
        if record is None:
            return None
        if record.status == "revoked" or record.revoked_at is not None:
            return None
        if record.expires_at is not None and _as_aware(record.expires_at) <= _utc_now():
            return None
        expires_at = _utc_now() + timedelta(seconds=max(15, ttl_seconds))
        access_token_expires_at = _as_aware(record.expires_at)
        return {
            "server_id": server.id,
            "workspace_id": self.workspace_id,
            "credential_id": record.id,
            "auth_kind": record.auth_kind,
            "secret_fingerprint": record.secret_fingerprint,
            "secret_masked": record.secret_masked,
            "audience": audience,
            "expires_at": expires_at.isoformat(),
            "credential_status": record.status,
            "access_token_expires_at": (
                access_token_expires_at.isoformat() if access_token_expires_at else None
            ),
            "token_type": record.token_type,
            "scope": record.scope,
            "has_refresh_token": bool(record.refresh_token_ciphertext),
            # Ciphertext stays server-side; runner implementations that share the
            # process may resolve it, but responses never re-emit this field.
            "ciphertext_ref": record.id,
        }

    def runner_only_token(
        self,
        server: MCPServer,
        *,
        audience: str,
    ) -> dict[str, Any] | None:
        """Resolve the live access token for injection into the isolated runner.

        This is the ONLY seam that yields the plaintext token, and it is
        strictly runner-only: the value is written into the container's
        ``mcp-launch.json`` and never returned through API DTOs, agent tool
        input, frontend state, or ordinary audit JSON. Revoked or expired
        credentials return ``None`` so the invocation fails closed; a separate
        refresh call must re-arm the credential before the next invoke.
        """

        record = self._credential(server)
        if record is None or record.auth_kind != "oauth_authorization_code":
            return None
        if record.status == "revoked" or record.revoked_at is not None:
            return None
        if record.expires_at is not None and _as_aware(record.expires_at) <= _utc_now():
            return None
        try:
            access_token = self._decrypt(record.ciphertext, label="OAuth access token")
        except AppError:
            return None
        return {
            "type": "oauth_authorization_code",
            "server_id": server.id,
            "workspace_id": self.workspace_id,
            "credential_id": record.id,
            "audience": audience,
            "access_token": access_token,
            "token_type": record.token_type or "Bearer",
            "scope": record.scope,
            "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        }

    def credential_view(self, server: MCPServer) -> dict[str, Any] | None:
        """Return the redacted OAuth credential projection for one server."""
        view = self.redact_for_api(self._credential(server))
        if view is not None:
            self.assert_no_secret_leak(view)
        return view

    def redact_for_api(self, record: MCPServerCredential | None) -> dict[str, Any] | None:
        if record is None:
            return None
        expires_at = _as_aware(record.expires_at)
        revoked_at = _as_aware(record.revoked_at)
        return {
            "id": record.id,
            "server_id": record.server_id,
            "auth_kind": record.auth_kind,
            "secret_masked": record.secret_masked,
            "secret_fingerprint": record.secret_fingerprint,
            "token_type": record.token_type,
            "scope": record.scope,
            "issuer": record.issuer,
            "client_id": record.client_id,
            "expires_at": expires_at.isoformat() if expires_at else None,
            "has_refresh_token": bool(record.refresh_token_ciphertext),
            "status": record.status,
            "revoked_at": revoked_at.isoformat() if revoked_at else None,
        }

    def assert_no_secret_leak(self, payload: Any) -> None:
        """Reject accidental inclusion of credential material in outward surfaces."""

        if isinstance(payload, dict):
            for key, value in payload.items():
                lowered = str(key).casefold()
                if lowered in {
                    "access_token",
                    "refresh_token",
                    "bearer_token",
                    "ciphertext",
                    "client_secret",
                    "code_verifier",
                }:
                    raise AppError(
                        500,
                        "mcp_credential_leak_blocked",
                        "OAuth/MCP credential material cannot leave the runner boundary",
                        {"field": key},
                    )
                self.assert_no_secret_leak(value)
        elif isinstance(payload, list):
            for item in payload:
                self.assert_no_secret_leak(item)


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode("utf-8"), right.encode("utf-8"))
