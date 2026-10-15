"""SEC-04 regression tests: MCP credentials share the versioned secret store.

New MCP ciphertext is stored as a self-describing envelope carrying the
algorithm, key provider and key version, so a keyring rotation can resolve the
exact key used at write time. Legacy ciphertext produced by
``SecretCipher(settings.master_key)`` remains readable through the fallback
path, and the OAuth static-bearer helper no longer stores a base64 encoding of
the token as if it were encrypted.
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.core.errors import AppError
from app.core.security import SecretCipher
from app.services.mcp_oauth import MCPOAuthLifecycle
from app.services.mcp_secret_store import decrypt_mcp_secret, encrypt_mcp_secret

SECRET = "mcp-test-bearer-token-123456"
MASTER_KEY = "test-master-key-for-mcp-secret-store"


def make_settings() -> Settings:
    return Settings(
        master_key=MASTER_KEY,
        master_key_version=1,
        secret_provider="environment",
    )


def test_round_trip_uses_versioned_envelope() -> None:
    settings = make_settings()
    ciphertext = encrypt_mcp_secret(settings, SECRET)

    envelope = json.loads(ciphertext)
    assert envelope["v"] == 1
    assert envelope["alg"] == "fernet_sha256_v1"
    assert envelope["provider"] == "environment"
    assert envelope["key_version"] == 1
    assert envelope["cipher"]
    assert envelope["cipher"] != ciphertext

    assert decrypt_mcp_secret(settings, ciphertext, label="test credential") == SECRET


def test_legacy_master_key_ciphertext_still_reads() -> None:
    settings = make_settings()
    legacy = SecretCipher(MASTER_KEY).encrypt(SECRET)

    assert decrypt_mcp_secret(settings, legacy, label="legacy credential") == SECRET


def test_empty_secret_is_rejected() -> None:
    with pytest.raises(ValueError):
        encrypt_mcp_secret(make_settings(), "")


def test_missing_ciphertext_raises_conflict() -> None:
    with pytest.raises(AppError) as exc_info:
        decrypt_mcp_secret(make_settings(), None, label="missing")
    assert exc_info.value.status_code == 409
    assert exc_info.value.code == "mcp_credential_missing"


def test_invalid_envelope_raises_server_error() -> None:
    settings = make_settings()
    malformed = json.dumps({"v": 1, "alg": "fernet_sha256_v1"})
    with pytest.raises(AppError) as exc_info:
        decrypt_mcp_secret(settings, malformed, label="malformed")
    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "mcp_credential_invalid"


def test_unknown_envelope_version_is_rejected() -> None:
    settings = make_settings()
    future = json.dumps(
        {
            "v": 99,
            "alg": "fernet_sha256_v1",
            "provider": "environment",
            "key_version": 1,
            "cipher": "x",
        }
    )
    with pytest.raises(AppError) as exc_info:
        decrypt_mcp_secret(settings, future, label="future envelope")
    assert exc_info.value.status_code == 500
    assert exc_info.value.code == "mcp_credential_invalid"


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        yield session


def test_static_bearer_is_encrypted_not_base64(db: Session) -> None:
    settings = make_settings()
    lifecycle = MCPOAuthLifecycle(db, "ws-sec-04", "actor-sec-04", settings=settings)
    server = SimpleNamespace(id="server-sec-04")

    record = lifecycle.store_static_bearer(server, SECRET)

    assert record.ciphertext != base64.b64encode(SECRET.encode("utf-8")).decode("ascii")
    assert json.loads(record.ciphertext)["provider"] == "environment"
    assert json.loads(record.ciphertext)["key_version"] == 1
    assert decrypt_mcp_secret(settings, record.ciphertext, label="stored bearer") == SECRET
