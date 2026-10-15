"""Cover persistence and failure contracts against the isolated API test DB."""
import base64
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.models import AuditEvent, Graph
from app.services import graph_cover
from test_goals_graphs_http import _insert_goal_via_sql, _insert_graph_via_sql


@pytest.fixture()
def cover_graph(register_user, auth_headers):
    token, ws, _, _ = register_user()
    graph_id, _ = _insert_graph_via_sql(ws, _insert_goal_via_sql(ws))
    return graph_id, auth_headers(token, ws)


@pytest.mark.parametrize("payload", [
    {"mode": "generated"},
    *[{"mode": "template", "template": key} for key in ("paper", "midnight", "sunrise")],
    {"mode": "svg", "svg": '<svg xmlns="http://www.w3.org/2000/svg"><rect width="640" height="300" fill="red"/></svg>'},
])
def test_cover_save_persists_without_changing_graph(client, cover_graph, payload):
    graph_id, headers = cover_graph
    url = f"/api/v1/graphs/{graph_id}"
    before = client.get(url, headers=headers).json()
    response = client.patch(url + "/cover", headers=headers, json=payload)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert not saved["used_default"]
    assert client.get(url + "/cover", headers=headers).json()["cover_svg"] == saved["cover_svg"]
    after = client.get(url, headers=headers).json()
    assert after["revision"] == before["revision"]
    assert after["nodes"] == before["nodes"]
    assert after["edges"] == before["edges"]
    listed = client.get("/api/v1/graphs", headers=headers).json()
    assert next(item for item in listed if item["id"] == graph_id)["cover_svg"] == saved["cover_svg"]
    with SessionLocal() as db:
        assert db.get(Graph, graph_id).cover_svg == saved["cover_svg"]
        audit = db.scalar(select(AuditEvent).where(AuditEvent.resource_id == graph_id, AuditEvent.action == "graph.cover_updated"))
        assert audit.details["mode"] == payload["mode"]


def test_uploaded_image_is_normalized_and_bad_upload_does_not_overwrite(client, cover_graph):
    graph_id, headers = cover_graph
    url = f"/api/v1/graphs/{graph_id}/cover"
    output = BytesIO()
    Image.new("RGB", (32, 16), "#d9e8ff").save(output, format="PNG")
    response = client.patch(url, headers=headers, json={"mode": "image", "image_data_url": "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()})
    assert response.status_code == 200, response.text
    saved = response.json()["cover_svg"]
    with Image.open(BytesIO(base64.b64decode(saved.split(",", 1)[1]))) as image:
        assert image.format == "JPEG"
        assert image.size == (640, 300)
    invalid = client.patch(url, headers=headers, json={"mode": "image", "image_data_url": "data:image/png;base64,bm90LWFuLWltYWdl"})
    assert invalid.status_code == 422, invalid.text
    assert client.get(url, headers=headers).json()["cover_svg"] == saved


@pytest.mark.parametrize("svg", ['<svg', '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'])
def test_invalid_svg_uses_default_without_breaking_graph(client, cover_graph, svg):
    graph_id, headers = cover_graph
    response = client.patch(f"/api/v1/graphs/{graph_id}/cover", headers=headers, json={"mode": "svg", "svg": svg})
    assert response.status_code == 200, response.text
    assert response.json()["used_default"]
    assert response.json()["cover_svg"] == graph_cover.default_graph_cover("补测图谱")
    assert client.get(f"/api/v1/graphs/{graph_id}", headers=headers).status_code == 200


@pytest.mark.parametrize("broken", [None, "data:image/svg+xml,%3Csvg", "data:image/svg+xml,%3Chtml/%3E"])
def test_renderer_failure_falls_back_and_can_still_save(client, cover_graph, monkeypatch, broken):
    def fail(*args, **kwargs):
        if broken is None:
            raise RuntimeError("test renderer failure")
        return broken
    monkeypatch.setattr(graph_cover, "_render_graph_cover", fail)
    graph_id, headers = cover_graph
    response = client.patch(f"/api/v1/graphs/{graph_id}/cover", headers=headers, json={"mode": "generated"})
    assert response.status_code == 200, response.text
    assert response.json()["used_default"]
    assert response.json()["cover_svg"] == graph_cover.default_graph_cover("补测图谱")


def test_other_workspace_cannot_read_or_change_cover(client, cover_graph, register_user, auth_headers):
    graph_id, owner_headers = cover_graph
    token, ws, _, _ = register_user("other")
    headers = auth_headers(token, ws)
    url = f"/api/v1/graphs/{graph_id}/cover"
    original = client.get(url, headers=owner_headers).json()["cover_svg"]
    assert client.get(url, headers=headers).status_code == 404
    assert client.patch(url, headers=headers, json={"mode": "template", "template": "midnight"}).status_code == 404
    assert client.get(url, headers=owner_headers).json()["cover_svg"] == original
