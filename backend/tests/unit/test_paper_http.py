"""Isolated API contract tests; never use application/user databases."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import WorkspaceContext, workspace_context
from app.api.routers import papers
from app.core.config import Settings, get_settings
from app.core.database import get_db
from app.core.errors import AppError
from app.domain.models import Base, Workspace
from app.domain.schemas.papers import Paper, PaperSearchResponse
from app.providers.remote.papers import PaperSearchProvider


@pytest.fixture
def api(tmp_path, monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    db = Session(engine)
    workspace = Workspace(id="paper-test", tenant_id="tenant", owner_user_id="owner", name="Test")
    db.add(workspace)
    db.commit()
    context = WorkspaceContext(workspace=workspace,
        principal=SimpleNamespace(user_id="owner", tenant_id="tenant", is_system_admin=False), permissions=frozenset({"workspace.read", "workspace.write"}))
    settings = Settings(storage_root=tmp_path)
    paper = Paper(provider="arxiv", paper_id="2401.12345v1", title="Real scholarly metadata", authors=["A Researcher"],
        year=2024, url="https://arxiv.org/abs/2401.12345v1", abstract="Abstract only", pdf_url="https://arxiv.org/pdf/2401.12345v1",
        open_access=True, publication_type="preprint", evidence_kind="abstract", fetched_at=datetime.now(timezone.utc))
    resolve = MagicMock(return_value=paper)
    monkeypatch.setattr(PaperSearchProvider, "resolve", resolve)
    monkeypatch.setattr(PaperSearchProvider, "search", lambda _self, request: PaperSearchResponse(query=request.query, provider="arxiv", results=[paper]))
    app = FastAPI()
    @app.exception_handler(AppError)
    async def error_handler(_request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=exc.status_code, content={"error": {"code": exc.code, "message": exc.message}})
    app.include_router(papers.router, prefix="/api/v1")
    app.dependency_overrides[workspace_context] = lambda: context
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app) as client:
        yield client, context, resolve
    db.close()
    engine.dispose()


def test_search_api_returns_metadata_not_full_text(api):
    client, _, _ = api
    response = client.post("/api/v1/papers/search", json={"query": "attention", "provider": "arxiv"})
    assert response.status_code == 200
    assert response.json()["results"][0]["page_read"] is False
    assert response.json()["results"][0]["evidence_kind"] == "abstract"


def test_save_library_bibtex_and_citations_are_separate(api):
    client, _, _ = api
    payload = {"provider": "arxiv", "paper_id": "2401.12345v1"}
    first = client.post("/api/v1/papers/save", json=payload)
    assert first.status_code == 200
    record = first.json()
    second = client.post("/api/v1/papers/save", json=payload)
    assert second.json()["id"] == record["id"]
    assert record["metadata_json"]["kind"] == "paper" and not record["metadata_json"]["page_read"]
    assert len(client.get("/api/v1/papers/library").json()) == 1
    export = client.get(f'/api/v1/papers/library/{record["id"]}/bibtex')
    assert export.status_code == 200 and "@misc{" in export.text and "A Researcher" in export.text
    assert "application/x-bibtex" in export.headers["content-type"]
    assert client.get(f'/api/v1/papers/library/{record["id"]}/citations').json() == []


def test_read_requires_domain_consent_without_saving(api):
    client, _, _ = api
    response = client.post("/api/v1/papers/read", json={"provider": "arxiv", "paper_id": "2401.12345v1"})
    assert response.status_code == 403 and response.json()["error"]["code"] == "paper_domain_authorization_required"
    assert client.get("/api/v1/papers/library").json() == []


def test_library_other_workspace_cannot_read_export_or_citations(api):
    client, context, _ = api
    record = client.post("/api/v1/papers/save", json={"provider": "arxiv", "paper_id": "2401.12345v1"}).json()
    context.workspace.id = "another-workspace"
    for suffix in ("bibtex", "citations"):
        assert client.get(f'/api/v1/papers/library/{record["id"]}/{suffix}').status_code == 404


def test_http_rejects_unknown_parameters_and_invalid_year_order(api):
    client, _, _ = api
    assert client.post("/api/v1/papers/search", json={"query": "paper", "year_from": 2025, "year_to": 2024}).status_code == 422
    assert client.post("/api/v1/papers/read", json={"provider": "arxiv", "paper_id": "2401.12345v1", "url": "http://127.0.0.1"}).status_code == 422
