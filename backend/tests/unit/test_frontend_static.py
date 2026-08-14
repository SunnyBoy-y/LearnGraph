"""Same-origin SPA serving used by the production container image."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.frontend_static import install_frontend_static


def _write_dist(root: Path) -> Path:
    dist = root / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>LearnGraph</title>", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
    (assets / "app.js").write_text("console.log('ok')", encoding="utf-8")
    return dist


def test_resolved_frontend_dist_requires_index(tmp_path: Path) -> None:
    missing = Settings(frontend_dist=str(tmp_path / "missing"))
    assert missing.resolved_frontend_dist is None

    empty = tmp_path / "empty"
    empty.mkdir()
    assert Settings(frontend_dist=str(empty)).resolved_frontend_dist is None

    dist = _write_dist(tmp_path)
    resolved = Settings(frontend_dist=str(dist)).resolved_frontend_dist
    assert resolved == dist.resolve()


def test_spa_serves_index_assets_and_reserves_api(tmp_path: Path) -> None:
    dist = _write_dist(tmp_path)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    install_frontend_static(app, dist)
    client = TestClient(app)

    home = client.get("/")
    assert home.status_code == 200
    assert "LearnGraph" in home.text
    assert home.headers.get("cache-control") == "no-cache"

    deep = client.get("/w/demo/home")
    assert deep.status_code == 200
    assert "LearnGraph" in deep.text

    icon = client.get("/favicon.svg")
    assert icon.status_code == 200
    assert "svg" in icon.headers.get("content-type", "")

    asset = client.get("/assets/app.js")
    assert asset.status_code == 200
    assert "console.log" in asset.text

    assert client.get("/api/v1/health").json() == {"status": "ok"}
    assert client.get("/api/v1/missing").status_code == 404
    assert client.get("/docs").status_code == 404


def test_spa_rejects_path_escape(tmp_path: Path) -> None:
    dist = _write_dist(tmp_path)
    secret = tmp_path / "secret.txt"
    secret.write_text("nope", encoding="utf-8")
    app = FastAPI()
    install_frontend_static(app, dist)
    client = TestClient(app)

    escaped = client.get("/../secret.txt")
    assert escaped.status_code == 200
    assert "nope" not in escaped.text
    assert "LearnGraph" in escaped.text
