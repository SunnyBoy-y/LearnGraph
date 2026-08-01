from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_artifact_gateway_has_immutable_version_and_hashed_token_models() -> None:
    models = (ROOT / "app" / "domain" / "models.py").read_text(encoding="utf-8")
    assert "class Artifact(Base, TimestampMixin)" in models
    assert "class ArtifactVersion(Base, TimestampMixin)" in models
    assert "class ArtifactShareToken(Base, TimestampMixin)" in models
    assert 'ForeignKey("files.id", ondelete="RESTRICT")' in models
    assert 'UniqueConstraint("token_hash"' in models


def test_artifact_gateway_share_token_is_hashed_and_public_download_isolated() -> None:
    service = (ROOT / "app" / "services" / "artifact_gateway.py").read_text(encoding="utf-8")
    router = (ROOT / "app" / "api" / "routers" / "artifact_gateway.py").read_text(encoding="utf-8")

    assert "secrets.token_urlsafe" in service
    assert "hashlib.sha256(raw_token.encode" in service
    assert "token.revoked_at is not None" in service
    assert "token.download_count >= token.max_downloads" in service
    assert "token.download_count += 1" in service
    assert 'GET /artifact-share' not in router  # FastAPI decorator form below
    assert '@router.get("/artifact-share/{raw_token}")' in router
    assert "db: DB, settings: AppSettings" in router
    assert "CurrentWorkspace" not in router.split("def download_shared_artifact", 1)[1]
    assert "Cache-Control\": \"public, immutable" in router


def test_artifact_gateway_router_is_registered() -> None:
    api_router = (ROOT / "app" / "api" / "router.py").read_text(encoding="utf-8")
    assert "artifact_gateway" in api_router
    assert "artifact_gateway.router" in api_router
