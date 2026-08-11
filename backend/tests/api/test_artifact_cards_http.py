"""卡片索引（artifact_cards）测试：产卡自动聚合、筛选排序、预览、删除。"""
from __future__ import annotations

from app.domain.models import ArtifactCard, Message, MessagePartRecord, MessageVersion
from app.services.artifact_cards import ArtifactCardIndexer, ArtifactCardService

def _magic_card_data(*, title="路线图卡片", card_id="card_abc123", runtime="html-srcdoc-sandbox-v1", preview_html=None):
    return {
        "card_instance_id": f"card_inst_{card_id}",
        "card_id": card_id,
        "title": title,
        "status": "ready",
        "runtime": runtime,
        "fallback_text": title,
        "preview_html": preview_html or "<html><body>hello</body></html>",
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


# --------------------------------------------------------------------------- #
# 阶段二：发版（版本化不可变快照）、版本记录/切换、分享令牌改造
# --------------------------------------------------------------------------- #

def _publish(client, headers, card_id, notes="v1"):
    r = client.post(
        f"/api/v1/artifacts/cards/{card_id}/versions",
        headers=headers,
        json={"release_notes": notes},
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_publish_version_and_history(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(title="v1 草稿", card_id="pub_card"))
        db.commit()

    v1 = _publish(client, headers, "pub_card", notes="第一版")
    assert v1["version"] == 1
    assert v1["publish_source"] == "user"

    # 再次发版（草稿更新后）
    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(title="v2 草稿", card_id="pub_card"))
        db.commit()
    v2 = _publish(client, headers, "pub_card", notes="第二版")
    assert v2["version"] == 2

    # 版本历史（新→旧）
    r = client.get("/api/v1/artifacts/cards/pub_card/versions", headers=headers)
    assert r.status_code == 200, r.text
    versions = r.json()
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["release_notes"] == "第二版"

    # 发版后再修改草稿（不发布）→ 有未发布的更新
    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(title="v3 草稿", card_id="pub_card"))
        db.commit()

    # 列表统计：version_count / latest_version / draft_dirty
    r = client.get("/api/v1/artifacts/cards", headers=headers)
    card = next(c for c in r.json() if c["card_id"] == "pub_card")
    assert card["version_count"] == 2
    assert card["latest_version"] == 2
    assert card["status"] == "published"
    assert card["draft_dirty"] is True  # v3 草稿未发布


def test_version_switch_preview(client, register_user, auth_headers):
    """preview?version=N 返回冻结快照而非最新草稿（版本切换语义）。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(title="初版", card_id="sw_card"))
        db.commit()
    _publish(client, headers, "sw_card", notes="v1")
    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(title="改后的草稿", card_id="sw_card"))
        db.commit()

    # 默认预览 = 草稿
    r = client.get("/api/v1/artifacts/cards/sw_card/preview", headers=headers)
    assert r.json()["preview_snapshot"]["title"] == "改后的草稿"
    # 版本切换 = v1 冻结快照（不可变）
    r = client.get("/api/v1/artifacts/cards/sw_card/preview", headers=headers, params={"version": 1})
    assert r.json()["preview_snapshot"]["title"] == "初版"
    # 不存在的版本 404
    r = client.get("/api/v1/artifacts/cards/sw_card/preview", headers=headers, params={"version": 99})
    assert r.status_code == 404


def test_delete_version_and_share(client, register_user, auth_headers):
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(card_id="delv_card"))
        db.commit()
    v1 = _publish(client, headers, "delv_card")
    v2 = _publish(client, headers, "delv_card", notes="v2")

    # 删除 v1：历史只剩 v2
    r = client.delete(f"/api/v1/artifacts/cards/versions/{v1['id']}", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "deleted"
    r = client.get("/api/v1/artifacts/cards/delv_card/versions", headers=headers)
    assert [v["version"] for v in r.json()] == [2]

    # 被删版本的预览 404
    r = client.get(
        "/api/v1/artifacts/cards/delv_card/preview", headers=headers,
        params={"version": 1},
    )
    assert r.status_code == 404


def test_card_share_token_flow(client, register_user, auth_headers):
    """分享令牌：创建→公开页面→计数→撤销→失效。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        _upsert(
            db, ws,
            data=_magic_card_data(
                title="分享卡", card_id="share_card",
                preview_html="<html><body><h1>hello shared</h1></body></html>",
            ),
        )
        db.commit()
    version = _publish(client, headers, "share_card")

    # 创建令牌（上限 2 次查看）
    r = client.post(
        f"/api/v1/artifacts/cards/versions/{version['id']}/share-tokens",
        headers=headers,
        json={"label": "给朋友", "max_views": 2},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["token"]
    assert created["token_prefix"] == created["token"][:12]

    # 公开页面可访问且为 HTML viewer（magic_card 内嵌 sandbox iframe）
    r = client.get(f"/api/v1/card-share/{created['token']}")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert "hello shared" in r.text
    assert "sandbox" in r.text

    # 令牌列表可见（前缀 + 计数）
    r = client.get(
        f"/api/v1/artifacts/cards/versions/{version['id']}/share-tokens",
        headers=headers,
    )
    assert r.status_code == 200
    listed = r.json()
    assert len(listed) == 1
    assert listed[0]["view_count"] == 1

    # 达到上限后失效
    r = client.get(f"/api/v1/card-share/{created['token']}")
    assert r.status_code == 200
    r = client.get(f"/api/v1/card-share/{created['token']}")
    assert r.status_code == 404

    # 撤销后失效
    r = client.post(
        f"/api/v1/artifacts/cards/versions/{version['id']}/share-tokens",
        headers=headers,
        json={},
    )
    token2 = r.json()["token"]
    r = client.delete(
        f"/api/v1/artifacts/cards/share-tokens/{r.json()['id']}",
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["revoked_at"] is not None
    r = client.get(f"/api/v1/card-share/{token2}")
    assert r.status_code == 404


def test_card_share_component_fallback(client, register_user, auth_headers):
    """component 卡分享为只读信息页（无 React 环境）。"""
    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    from app.core.database import SessionLocal

    with SessionLocal() as db:
        _upsert(db, ws, part_type="component", data=_component_data(component_id="comp_share"))
        db.commit()
    version = _publish(client, headers, "comp_share")
    r = client.post(
        f"/api/v1/artifacts/cards/versions/{version['id']}/share-tokens",
        headers=headers,
        json={},
    )
    raw = r.json()["token"]
    r = client.get(f"/api/v1/card-share/{raw}")
    assert r.status_code == 200
    assert "交互式学习组件" in r.text


def test_agent_publish_card_tool(client, register_user, auth_headers):
    """agent 工具 artifact_publish_card 发版（publish_source=agent）。"""
    from app.services.agent_runtime import AgentToolRuntime

    token, ws, _, _ = register_user()
    headers = auth_headers(token, ws)
    from app.core.database import SessionLocal
    from app.domain.models import Workspace

    with SessionLocal() as db:
        _upsert(db, ws, data=_magic_card_data(card_id="agent_card"))
        workspace = db.get(Workspace, ws)
        db.commit()
        tenant_id = workspace.tenant_id

    # 工具内部通过 ArtifactCardService 发版（publish_source=agent）
    from app.services.artifact_cards import ArtifactCardService

    with SessionLocal() as db:
        version = ArtifactCardService(db, ws, tenant_id).publish_version(
            "agent_card",
            release_notes="agent 发版",
            actor_id="test-agent",
            publish_source="agent",
        )
        assert version.publish_source == "agent"
        assert version.version == 1

    # 工具定义已注册（agent 可见）
    names = [d["function"]["name"] for d in AgentToolRuntime._canvas_tool_definitions()]
    assert "artifact_publish_card" in names

    # API 层验证发版后卡片 published
    # API 层验证发版后卡片 published
    r = client.get("/api/v1/artifacts/cards", headers=headers)
    card = next(c for c in r.json() if c["card_id"] == "agent_card")
    assert card["status"] == "published"
    assert card["version_count"] == 1
