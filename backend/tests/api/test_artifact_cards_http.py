"""卡片索引（artifact_cards）测试：产卡自动聚合、筛选排序、预览、删除。"""
from __future__ import annotations

from app.domain.models import ArtifactCard, Message, MessagePartRecord, MessageVersion
from app.services.artifact_cards import ArtifactCardIndexer, ArtifactCardService

def _magic_card_data(*, title="路线图卡片", card_id="card_abc123", runtime="html-srcdoc-sandbox-v1"):
    return {
        "card_instance_id": f"card_inst_{card_id}",
        "card_id": card_id,
        "title": title,
        "status": "ready",
        "runtime": runtime,
        "fallback_text": title,
        "preview_html": "<html><body>hello</body></html>",
        "preferred_height": 360,
        "viewport": {"mode": "inline", "preferred_height": 360, "max_height": 720},
    }


def _component_data(*, component_type="single_choice", component_id="comp_1", events=("submit",)):
    return {
        "component_type": component_type,
        "component_id": component_id,
        "schema_version": "1.0",
        "props": {"title": "选择题"},
        "allowed_events": list(events),
    }


def _upsert(db, workspace_id, part_type="magic_card", data=None, session_id="s1", tenant_id=None):
    from app.domain.models import Workspace

    workspace = db.get(Workspace, workspace_id)
    tenant_id = tenant_id or (workspace.tenant_id if workspace is not None else "local-tenant")
    return ArtifactCardIndexer.upsert_from_part(
        db,
        workspace_id=workspace_id,
        tenant_id=tenant_id,
        chat_session_id=session_id,
        message_id="msg-1",
        message_version_id="ver-1",
        part_id="part-1",
        part_type=part_type,
        data=data,
    )


def test_indexer_magic_card_and_component(client, register_user):
    """magic_card 静态 / 双向、component 有事件 → 正确标 interactive 并落库。"""
    _, ws, _, _ = register_user()
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        static = _upsert(db, ws, data=_magic_card_data())
        assert static is not None
        assert static.card_type == "magic_card"
        assert static.interactive is False
        assert static.status == "draft"
        assert static.title == "路线图卡片"

        bidirectional = _upsert(
            db, ws, data=_magic_card_data(card_id="card_react", runtime="react-sandbox-v1")
        )
        assert bidirectional.interactive is True

        component = _upsert(
            db, ws, part_type="component", data=_component_data()
        )
        assert component.card_type == "component"
        assert component.interactive is True

        # 无事件的组件为静态
        static_component = _upsert(
            db,
            ws,
            part_type="component",
            data=_component_data(component_id="comp_static", events=()),
        )
        assert static_component.interactive is False


def test_indexer_reemit_updates_row(client, register_user):
    """同一 card_id 重复 emit 只更新一行（草稿语义）。"""
    _, ws, _, _ = register_user()
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(title="v1"))
        updated = _upsert(db, ws, data=_magic_card_data(title="v2"))
        assert updated.title == "v2"
        rows = db.query(ArtifactCard).filter(ArtifactCard.workspace_id == ws).all()
        assert len(rows) == 1


def test_indexer_deleted_card_revived(client, register_user):
    """已删除卡片被 agent 重新 emit 时复活为草稿。"""
    _, ws, _, _ = register_user()
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        from app.domain.models import Workspace

        tenant_id = db.get(Workspace, ws).tenant_id
        row = _upsert(db, ws, data=_magic_card_data())
        ArtifactCardService(db, ws, tenant_id).delete_card(row.card_id)
        revived = _upsert(db, ws, data=_magic_card_data(title="new draft"))
        assert revived.status == "draft"
        assert revived.deleted_at is None
        assert revived.title == "new draft"


def test_list_preview_delete_api(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(title="可预览卡", card_id="card_api"))
        db.commit()

    r = client.get("/api/v1/artifacts/cards", headers=headers)
    assert r.status_code == 200, r.text
    items = r.json()
    assert len(items) == 1
    card = items[0]
    assert card["card_id"] == "card_api"
    assert card["interactive"] is False
    assert card["status"] == "draft"
    # 列表不携带 preview_snapshot
    assert "preview_snapshot" not in card

    # 预览返回完整渲染数据
    r = client.get(f"/api/v1/artifacts/cards/{card['card_id']}/preview", headers=headers)
    assert r.status_code == 200, r.text
    preview = r.json()
    assert preview["preview_snapshot"]["title"] == "可预览卡"
    assert "preview_html" in preview["preview_snapshot"]
    assert preview["chat_session_id"] == "s1"

    # 筛选
    r = client.get(
        "/api/v1/artifacts/cards", headers=headers,
        params={"status": "published"},
    )
    assert r.json() == []
    r = client.get(
        "/api/v1/artifacts/cards", headers=headers,
        params={"interactive": "true"},
    )
    assert r.json() == []

    # 删除
    r = client.delete(f"/api/v1/artifacts/cards/{card['card_id']}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "deleted"
    r = client.get("/api/v1/artifacts/cards", headers=headers)
    assert r.json() == []
    r = client.get(f"/api/v1/artifacts/cards/{card['card_id']}/preview", headers=headers)
    assert r.status_code == 404


def test_card_workspace_isolation(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(card_id="isolated_card"))
        db.commit()

    token2, ws2, _, _ = register_user()
    r = client.get(
        f"/api/v1/artifacts/cards/isolated_card/preview",
        headers=auth_headers(token2, ws2),
    )
    assert r.status_code == 404


def test_indexer_skips_non_card_parts(client, register_user):
    """sandbox_artifact 等非卡片 part 不进索引。"""
    _, ws, _, _ = register_user()
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        result = _upsert(
            db, ws, part_type="sandbox_artifact",
            data={"title": "文件", "path": "a.txt"},
        )
        assert result is None
        assert db.query(ArtifactCard).filter(ArtifactCard.workspace_id == ws).count() == 0
