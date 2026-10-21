from __future__ import annotations

import io
import json
import zipfile
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.core.database import SessionLocal
from app.domain.models import DurableJob, ProviderConfig, StickerAsset, StickerIndexGeneration, StickerIndexState, StickerVector, User
from app.domain.models import utc_now
from app.providers.remote.embedding import OpenAICompatibleEmbeddingProvider
from app.services.durable_queue import run_one_durable_job
from app.services.stickers import StickerService, clean_semantics, digest, run_sticker_index_batch, validate_vectors
from app.services.sticker_market import catalog, market_archive
from app.services.sticker_pipeline import StickerPipeline, image_parts


def image_bytes(color="red"):
    content = io.BytesIO()
    Image.new("RGBA", (24, 24), color).save(content, format="PNG")
    return content.getvalue()


def create_archive(entries):
    content = io.BytesIO()
    with zipfile.ZipFile(content, "w") as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    return content.getvalue()


def identity(client, register_user):
    token, workspace, *_ = register_user("stickers")
    headers = {"Authorization": f"Bearer {token}", "X-Workspace-ID": workspace}
    profile = client.get("/api/v1/auth/me", headers=headers).json()
    with SessionLocal() as db:
        actor = db.scalar(select(User).order_by(User.created_at.desc())).id
    return workspace, headers, actor


def configured(workspace, actor, monkeypatch, model="model-a", revision="1"):
    with SessionLocal() as db:
        provider = ProviderConfig(id=str(uuid4()), workspace_id=workspace, display_name="Embedding fixture", provider_type="openai_compatible_embedding", base_url="http://embedding.invalid/v1", enabled=True, remote_capability=True, capabilities={"default_embedding_model_id": model, "embedding_model_revision": revision})
        db.add(provider)
        db.commit()
        provider_id = provider.id
        service = StickerService(db, workspace, actor)
        service.configure({"mode": "independent", "provider_id": provider_id, "model_id": model})
    class Provider:
        last_usage = {}
    monkeypatch.setattr("app.services.stickers.embedding_provider_for_workspace", lambda *args, **kwargs: Provider())
    monkeypatch.setattr(StickerService, "embed", lambda self, provider, texts, profile, role: [[1.0, float(len(text) % 7), 0.5] for text in texts])
    return provider_id


def finish_index(workspace):
    for _ in range(100):
        with SessionLocal() as db:
            state = db.get(StickerIndexState, workspace)
            generation = db.get(StickerIndexGeneration, state.desired_generation_id)
            if generation.status == "ready":
                return generation.id
        with SessionLocal() as db:
            for queued in db.scalars(select(DurableJob).where(DurableJob.status == "queued")).all():
                queued.available_at = utc_now() - timedelta(seconds=1)
            db.commit()
        assert run_one_durable_job("sticker-test-worker")
    pytest.fail("Index failed to become ready")


def test_builtin_keyword_search_and_authenticated_assets(client, register_user):
    workspace, headers, _ = identity(client, register_user)
    status = client.get("/api/v1/stickers", headers=headers)
    assert status.status_code == 200, status.text
    assert status.json()["keyword_available"] is True
    assert status.json()["index"]["status"] == "keyword"
    assert status.json()["packs"][0]["builtin"] is True
    response = client.get("/api/v1/stickers/search?q=被抓包", headers=headers)
    candidate = response.json()["candidates"][0]
    assert candidate["name"] == "得意坏笑"
    assert response.json()["method"] == "keyword"
    image = client.get(f"/api/v1/stickers/{candidate['id']}/content", headers=headers)
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")
    assert client.get(f"/api/v1/stickers/{candidate['id']}/content").status_code == 401


def test_private_import_dedup_edit_and_foreign_workspace_denial(client, register_user):
    workspace, headers, actor = identity(client, register_user)
    uploaded = client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("我的小猫.png", image_bytes(), "image/png"))])
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["imported"] == 1
    again = client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("我的小猫.png", image_bytes(), "image/png"))])
    assert again.json()["duplicates"] == 1
    candidates = client.get("/api/v1/stickers/search?q=小猫", headers=headers).json()["candidates"]
    assert candidates and candidates[0]["needs_description"]
    sticker_id = candidates[0]["id"]
    updated = client.put(f"/api/v1/stickers/{sticker_id}", headers=headers, json={"name": "坏笑猫", "semantics": {"description": "得意的调侃", "tags": ["中计", "小秘密"], "avoid_when": ["悲伤"]}})
    assert updated.status_code == 200
    assert client.get("/api/v1/stickers/search?q=小秘密", headers=headers).json()["candidates"][0]["id"] == sticker_id
    _, other_headers, _ = identity(client, register_user)
    assert client.get(f"/api/v1/stickers/{sticker_id}/content", headers=other_headers).status_code == 404
    with SessionLocal() as db:
        assert sticker_id not in [item.id for item in StickerService(db, workspace, "different-user").visible_assets()]
    disabled = client.put(f"/api/v1/stickers/packs/{uploaded.json()['pack_id']}", headers=headers, json={"enabled": False})
    assert disabled.status_code == 200
    assert client.get(f"/api/v1/stickers/{sticker_id}/content", headers=headers).status_code == 404


@pytest.mark.parametrize("entries", [
    {"../escape.png": image_bytes()},
    {"/absolute.png": image_bytes()},
    {"fake.png": b"not an image"},
    {"manifest.json": b"{"},
])
def test_import_rejects_malformed_or_unsafe_archives(client, register_user, entries):
    _, headers, _ = identity(client, register_user)
    response = client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("pack.zip", create_archive(entries), "application/zip"))])
    assert response.status_code == 422, response.text


def test_zip_manifest_and_negative_scenarios_do_not_pollute_recall(client, register_user):
    _, headers, _ = identity(client, register_user)
    archive = create_archive({"cat.png": image_bytes(), "manifest.json": json.dumps({"name": "私有猫咪", "stickers": [{"file": "cat.png", "name": "小猫", "description": "友好调侃", "tags": ["计谋得逞"], "avoid_when": ["资产清算"]}]}, ensure_ascii=False)})
    response = client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("cats.zip", archive, "application/zip"))])
    assert response.status_code == 201, response.text
    assert client.get("/api/v1/stickers/search?q=计谋得逞", headers=headers).json()["candidates"]
    assert client.get("/api/v1/stickers/search?q=资产清算", headers=headers).json()["candidates"] == []


def test_provider_configuration_automatically_builds_and_switches_indexes(client, register_user, monkeypatch):
    workspace, headers, actor = identity(client, register_user)
    provider_id = configured(workspace, actor, monkeypatch)
    first = finish_index(workspace)
    assert client.get("/api/v1/stickers/search?q=庆祝&semantic=true", headers=headers).json()["method"] == "hybrid"
    with SessionLocal() as db:
        service = StickerService(db, workspace, actor)
        service.configure({"model_id": "model-b"})
        state = service.state()
        second = state.desired_generation_id
        assert second != first and state.active_generation_id == first
        assert db.get(StickerIndexGeneration, second).fingerprint != db.get(StickerIndexGeneration, first).fingerprint
    assert finish_index(workspace) == second
    with SessionLocal() as db:
        assert len(db.scalars(select(StickerVector).where(StickerVector.generation_id == first)).all()) == 12
        service = StickerService(db, workspace, actor)
        service.configure({"ai_enabled": False})
        assert service.state().desired_generation_id == second


def test_follow_mode_builds_when_single_embedding_provider_is_added(client, register_user, monkeypatch):
    workspace, _, actor = identity(client, register_user)
    configured(workspace, actor, monkeypatch)
    with SessionLocal() as db:
        service = StickerService(db, workspace, actor)
        service.configure({"mode": "follow"})
        generation = db.get(StickerIndexGeneration, service.state().desired_generation_id)
        assert generation.profile["model_id"] == "model-a"
    finish_index(workspace)


def test_revision_change_forces_new_space_and_old_profile_is_not_queried(client, register_user, monkeypatch):
    workspace, headers, actor = identity(client, register_user)
    provider_id = configured(workspace, actor, monkeypatch)
    first = finish_index(workspace)
    with SessionLocal() as db:
        provider = db.get(ProviderConfig, provider_id)
        provider.capabilities = {**provider.capabilities, "embedding_model_revision": "2"}
        db.commit()
        service = StickerService(db, workspace, actor)
        service.reconcile()
        assert service.state().desired_generation_id != first
    response = client.get("/api/v1/stickers/search?q=庆祝&semantic=true", headers=headers)
    assert response.json()["method"] == "keyword"
    assert finish_index(workspace) != first


def test_concurrent_configuration_change_cannot_publish_obsolete_index(client, register_user, monkeypatch):
    workspace, _, actor = identity(client, register_user)
    configured(workspace, actor, monkeypatch)
    called = False
    def embed(self, provider, texts, profile, role):
        nonlocal called
        if not called:
            called = True
            with SessionLocal() as second_db:
                StickerService(second_db, workspace, actor).configure({"model_id": "model-c"})
        return [[1.0, 2.0] for _ in texts]
    monkeypatch.setattr(StickerService, "embed", embed)
    with SessionLocal() as db:
        old = db.get(StickerIndexState, workspace).desired_generation_id
    assert run_one_durable_job("sticker-test-worker")
    with SessionLocal() as db:
        state = db.get(StickerIndexState, workspace)
        assert state.desired_generation_id != old
        assert state.active_generation_id != old
    finish_index(workspace)


def test_import_during_rebuild_is_included_before_activation(client, register_user, monkeypatch):
    workspace, _, actor = identity(client, register_user)
    configured(workspace, actor, monkeypatch)
    called = False
    def embed(self, provider, texts, profile, role):
        nonlocal called
        if not called:
            called = True
            with SessionLocal() as second_db:
                StickerService(second_db, workspace, actor).import_pack([("新增.png", image_bytes())], "动态导入")
        return [[1.0, 2.0] for _ in texts]
    monkeypatch.setattr(StickerService, "embed", embed)
    generation_id = finish_index(workspace)
    with SessionLocal() as db:
        generation = db.get(StickerIndexGeneration, generation_id)
        assert generation.completed == generation.total == 13


def test_semantic_failure_preserves_keyword_search(client, register_user, monkeypatch):
    workspace, headers, actor = identity(client, register_user)
    configured(workspace, actor, monkeypatch)
    finish_index(workspace)
    def fail(*args, **kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(StickerService, "embed", fail)
    result = client.get("/api/v1/stickers/search?q=庆祝&semantic=true", headers=headers)
    assert result.status_code == 200 and result.json()["method"] == "keyword"
    assert result.json()["candidates"][0]["name"] == "庆祝"


@pytest.mark.parametrize("vectors", [[[0.0, 0.0]], [[float("nan"), 1.0]], [[1.0], [1.0, 2.0]], []])
def test_invalid_embeddings_are_rejected(vectors):
    with pytest.raises(ValueError):
        validate_vectors(vectors, 2 if len(vectors) == 2 else 1)


def test_embedding_dimension_parameter_is_forwarded_and_response_order_preserved():
    captured = []
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]})
    provider = OpenAICompatibleEmbeddingProvider(provider_id="fixture", model_id="any-model", base_url="https://embedding.invalid/v1", api_key="fixture", transport=httpx.MockTransport(respond))
    provider.dimensions = 2
    assert provider.embed(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert captured[0]["dimensions"] == 2


def test_sticker_history_projection_is_bounded_data():
    from app.services.chat import ChatService
    projected = ChatService._sticker_history_stub([{"type": "sticker", "data": {"meaning": "友好调侃"}}, {"type": "text", "content": "hello"}])
    assert "友好调侃" in projected and "仅为历史资料" in projected


def test_user_sticker_persists_and_is_replayed_to_model(client, register_user, stub_chat_service):
    _, headers, _ = identity(client, register_user)
    candidate = client.get("/api/v1/stickers/search?q=庆祝", headers=headers).json()["candidates"][0]
    session_id = client.post("/api/v1/sessions", headers=headers, json={"title": "贴纸对话"}).json()["id"]
    response = client.post(f"/api/v1/sessions/{session_id}/messages/stream", headers=headers, json={"content": "[表情]", "sticker_ids": [candidate["id"]]})
    assert response.status_code == 200, response.text
    assert '"type":"message.completed"' in response.text.replace(" ", ""), response.text[-3000:]
    rows = client.get(f"/api/v1/sessions/{session_id}/messages", headers=headers).json()["items"]
    user = next(row for row in rows if row["role"] == "user")
    assert user["parts"][0]["type"] == "sticker"
    assert user["parts"][0]["data"]["sticker_id"] == candidate["id"]


@pytest.mark.parametrize("with_text", [False, True])
def test_agent_sticker_uses_real_runtime_sse_and_history(client, register_user, monkeypatch, with_text):
    from app.api.routers import chat as router
    from app.providers.ports.model import ProviderStreamEvent
    from app.services.chat import ChatService
    from app.services.chat_service_factory import build_agent_tool_runtime
    _, headers, _ = identity(client, register_user)
    sticker_id = client.get("/api/v1/stickers/search?q=庆祝", headers=headers).json()["candidates"][0]["id"]

    class StickerProvider:
        available = True
        remote_capability = True
        provider_id = "stub-provider"
        model_id = "stub-model"
        supports_structured_chat = True
        supports_agent_tools = True
        thinking_mode = "off"
        actual_reasoning_effort = None
        search_route = "disabled"
        last_usage = {}

        def stream_chat(self, messages, **kwargs):
            if messages[-1].role != "tool":
                yield ProviderStreamEvent("tool_calls", tool_calls=[{"id": "sticker-call", "type": "function", "function": {"name": "send_sticker", "arguments": json.dumps({"sticker_id": sticker_id})}}])
                yield ProviderStreamEvent("completed", finish_reason="tool_calls")
            else:
                if with_text:
                    yield ProviderStreamEvent("text_delta", content="太好了，终于上线了！")
                yield ProviderStreamEvent("completed", finish_reason="stop")

    def factory(db, context, settings, **kwargs):
        service = ChatService(db, context.workspace_id, context.principal.user_id, StickerProvider(), settings=settings)
        service._build_fast_preface_provider = lambda: None
        service.agent_tool_runtime = build_agent_tool_runtime(db, context, settings)
        return service
    monkeypatch.setattr(router, "service", factory)
    session_id = client.post("/api/v1/sessions", headers=headers, json={"title": "Agent 贴纸"}).json()["id"]
    response = client.post(f"/api/v1/sessions/{session_id}/messages/stream", headers=headers, json={"content": "项目上线了，发个庆祝表情", "agent_mode": True})
    assert response.status_code == 200, response.text
    events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
    assert events[-1]["type"] == "message.completed", response.text[-5000:]
    rows = client.get(f"/api/v1/sessions/{session_id}/messages", headers=headers).json()["items"]
    answer = next(row for row in rows if row["role"] == "assistant")
    stickers = [part for part in answer["parts"] if part["type"] == "sticker"]
    assert len(stickers) == 1, answer
    assert stickers[0]["data"]["sticker_id"] == sticker_id
    assert ("太好了" in answer["content"]) is with_text
    assert any(event.get("payload", {}).get("part", {}).get("type") == "sticker" for event in events)


def test_incremental_index_copies_unchanged_vectors_and_keeps_old_generation_active(client, register_user, monkeypatch):
    workspace, _, actor = identity(client, register_user)
    configured(workspace, actor, monkeypatch)
    first = finish_index(workspace)
    calls = []
    def embed(self, provider, texts, profile, role):
        calls.append((role, len(texts)))
        return [[1.0, 2.0, 0.5] for _ in texts]
    monkeypatch.setattr(StickerService, "embed", embed)
    with SessionLocal() as db:
        service = StickerService(db, workspace, actor)
        service.import_pack([("增量.png", image_bytes())], "新增素材")
        state = service.state()
        target = state.desired_generation_id
        assert target != first and state.active_generation_id == first
        assert db.get(StickerIndexGeneration, first).status == "ready"
        assert len(db.scalars(select(StickerVector).where(StickerVector.generation_id == target)).all()) == 12
    assert finish_index(workspace) == target
    assert calls == [("document", 1)]


def test_provider_configuration_repair_automatically_retries_failed_index(client, register_user, monkeypatch):
    workspace, _, actor = identity(client, register_user)
    provider_id = configured(workspace, actor, monkeypatch)
    def offline(*args, **kwargs):
        raise RuntimeError("fixture offline")
    monkeypatch.setattr(StickerService, "embed", offline)
    for _ in range(3):
        with SessionLocal() as db:
            for queued in db.scalars(select(DurableJob).where(DurableJob.workspace_id == workspace, DurableJob.status == "queued")).all():
                queued.available_at = utc_now() - timedelta(seconds=1)
            db.commit()
        assert run_one_durable_job("sticker-test-worker")
    with SessionLocal() as db:
        service = StickerService(db, workspace, actor)
        generation_id = service.state().desired_generation_id
        assert db.get(StickerIndexGeneration, generation_id).status == "failed"
        service.reconcile()
        assert db.get(StickerIndexGeneration, generation_id).status == "failed"
        provider = db.get(ProviderConfig, provider_id)
        provider.updated_at = utc_now() + timedelta(seconds=1)
        db.commit()
        service.reconcile()
        assert service.state().desired_generation_id == generation_id
        assert db.get(StickerIndexGeneration, generation_id).status == "queued"
    monkeypatch.setattr(StickerService, "embed", lambda self, provider, texts, profile, role: [[1.0, 2.0] for _ in texts])
    assert finish_index(workspace) == generation_id


def test_market_download_verified_images_and_full_license(client, register_user):
    _, headers, _ = identity(client, register_user)
    listing = client.get("/api/v1/stickers/market", headers=headers).json()
    assert len(listing["packs"]) == 5
    assert listing["sources"] == ["fluent", "noto", "openmoji"]
    assert listing["total_assets"] == 72
    assert listing["packs"][0]["installed_pack_id"]
    for pack in catalog():
        response = client.get(f"/api/v1/stickers/market/{pack['id']}/download", headers=headers)
        assert response.status_code == 200 and "attachment" in response.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            assert len(archive.read("LICENSE.txt")) > 500
            assert pack["author"].encode() in archive.read("NOTICE.txt") or pack["source_id"] == "openmoji"
            manifest = json.loads(archive.read("manifest.json"))
            assert manifest["source"] == pack["source"]
            assert manifest["approval_required"] is False
            assert all(entry["description"] and entry["avoid_when"] for entry in manifest["stickers"])
        preview = client.get(f"/api/v1/stickers/market/{pack['id']}/preview/0", headers=headers)
        assert preview.content.startswith(b"\x89PNG")
    assert client.get("/api/v1/stickers/market?q=抱抱", headers=headers).json()["packs"][0]["id"] == "fluent-warm"
    assert client.get("/api/v1/stickers/market/fluent-warm/preview/99", headers=headers).status_code == 404
    assert client.get("/api/v1/stickers/market/unknown/download", headers=headers).status_code == 404
    assert client.get("/api/v1/stickers/market/fluent-warm/download").status_code == 401


def test_market_install_idempotent_private_and_roundtrip_export(client, register_user, monkeypatch):
    workspace, headers, actor = identity(client, register_user)
    configured(workspace, actor, monkeypatch)
    response = client.post("/api/v1/stickers/market/fluent-warm/install", headers=headers)
    assert response.status_code == 201, response.text
    installed = response.json()
    assert installed["imported"] == 6
    assert client.post("/api/v1/stickers/market/fluent-warm/install", headers=headers).json()["imported"] == 0
    assert client.get("/api/v1/stickers/market?q=温暖陪伴", headers=headers).json()["packs"][0]["installed_pack_id"] == installed["pack_id"]
    found = client.get("/api/v1/stickers/search?q=抱抱", headers=headers).json()["candidates"][0]
    assert found["semantics"]["description"] and found["semantics"]["avoid_when"]
    finish_index(workspace)
    assert client.get("/api/v1/stickers/search?q=抱抱&semantic=true", headers=headers).json()["method"] == "hybrid"
    exported = client.get(f"/api/v1/stickers/packs/{installed['pack_id']}/download", headers=headers)
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
        assert b"MIT License" in archive.read("LICENSE.txt")
        assert len(json.loads(archive.read("manifest.json"))["stickers"]) == 6
    assert client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("roundtrip.zip", exported.content))]).json()["imported"] == 0
    _, other_headers, _ = identity(client, register_user)
    assert client.get(f"/api/v1/stickers/packs/{installed['pack_id']}/download", headers=other_headers).status_code == 404
    assert client.get("/api/v1/stickers/market?q=温暖陪伴", headers=other_headers).json()["packs"][0]["installed_pack_id"] is None
    imported = client.post("/api/v1/stickers/import", headers=other_headers, files=[("files", ("market.zip", exported.content))]).json()
    reexport = client.get(f"/api/v1/stickers/packs/{imported['pack_id']}/download", headers=other_headers)
    with zipfile.ZipFile(io.BytesIO(reexport.content)) as archive:
        assert b"Permission is hereby granted" in archive.read("LICENSE.txt")
    assert client.post("/api/v1/stickers/packs/unknown/analyze", headers=headers, json={"consent": False}).status_code == 422


def test_market_archive_detects_modified_asset(monkeypatch, tmp_path):
    from app.services import sticker_market
    pack = catalog()[1]
    (tmp_path / pack["stickers"][0]["file"]).write_bytes(b"tampered")
    monkeypatch.setattr(sticker_market, "ASSET_ROOT", tmp_path)
    monkeypatch.setattr(sticker_market, "market_pack", lambda _: pack)
    with pytest.raises(Exception, match="校验失败"):
        market_archive(pack["id"])


def vision_fixture(workspace, monkeypatch, callback=None, output=None):
    from app.providers.ports.model import ProviderStreamEvent
    with SessionLocal() as db:
        row = ProviderConfig(id=str(uuid4()), workspace_id=workspace, display_name="Vision fixture", provider_type="openai_compatible_vision", base_url="http://vision.invalid/v1", enabled=True, remote_capability=True, capabilities={"default_vision_model_id": "vision-fixture"})
        db.add(row)
        db.commit()
        provider_id = row.id
    calls = []
    class Vision:
        available = True
        supports_image_input = True
        remote_capability = True
        model_id = "vision-fixture"
        last_usage = {"input_tokens": 20, "output_tokens": 20}

        def stream_chat(self, messages):
            calls.append(messages)
            if callback:
                callback()
            yield ProviderStreamEvent("text_delta", content=output or json.dumps({"description": "一只猫眯眼坏笑，表示熟人间友好的得意和调侃", "ocr": "嘿嘿", "tags": ["坏笑", "得意"], "emotion": ["得意"], "intent": ["友好调侃"], "use_when": ["朋友间玩笑"], "avoid_when": ["严肃道歉"], "standalone": True}, ensure_ascii=False))
    provider = Vision()
    provider.provider_id = provider_id
    monkeypatch.setattr("app.services.sticker_pipeline.vision_provider_for_workspace", lambda *args, **kwargs: provider)
    return provider, calls


def finish_semantics(workspace):
    for _ in range(100):
        with SessionLocal() as db:
            pending = db.scalars(select(DurableJob).where(DurableJob.workspace_id == workspace, DurableJob.status.in_(["queued", "leased"]))).all()
            if not pending:
                return
            for job in pending:
                job.available_at = utc_now() - timedelta(seconds=1)
            db.commit()
        assert run_one_durable_job("sticker-pipeline-test")
    pytest.fail("Semantic pipeline did not finish")


def test_vision_pipeline_requires_consent_and_reindexes_semantic_cards(client, register_user, monkeypatch):
    workspace, headers, actor = identity(client, register_user)
    configured(workspace, actor, monkeypatch)
    _, calls = vision_fixture(workspace, monkeypatch)
    uploaded = client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("猫.png", image_bytes()))]).json()
    assert client.get("/api/v1/stickers/pipeline", headers=headers).json()["queued"] == 0
    response = client.post(f"/api/v1/stickers/packs/{uploaded['pack_id']}/analyze", headers=headers, json={"consent": True})
    assert response.status_code == 202 and response.json()["queued"] == 1
    assert client.post(f"/api/v1/stickers/packs/{uploaded['pack_id']}/analyze", headers=headers, json={"consent": True}).json()["queued"] == 0
    finish_semantics(workspace)
    found = client.get(f"/api/v1/stickers/search?q=嘿嘿&pack_id={uploaded['pack_id']}", headers=headers).json()["candidates"][0]
    assert found["semantics"]["ocr"] == "嘿嘿" and found["semantics"]["avoid_when"] == ["严肃道歉"]
    assert calls and calls[0][1].content_parts[0]["image_url"].startswith("data:image/png;base64,")
    assert "不可信数据" in calls[0][0].content
    assert client.get("/api/v1/stickers/pipeline", headers=headers).json()["completed"] == 1
    with SessionLocal() as db:
        state = db.get(StickerIndexState, workspace)
        vector = db.scalar(select(StickerVector).where(StickerVector.generation_id == state.active_generation_id, StickerVector.sticker_id == found["id"]))
        assert vector.semantic_hash == db.get(StickerAsset, found["id"]).semantic_hash
    assert client.post(f"/api/v1/stickers/packs/{uploaded['pack_id']}/analyze", headers=headers, json={"consent": True}).json()["queued"] == 0


@pytest.mark.parametrize("mutation", ["edit", "cancel"])
def test_semantic_worker_never_overwrites_manual_edits_or_cancelled_lease(client, register_user, monkeypatch, mutation):
    workspace, headers, _ = identity(client, register_user)
    uploaded = client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("猫.png", image_bytes()))]).json()
    asset_id = client.get("/api/v1/stickers/search?q=猫", headers=headers).json()["candidates"][0]["id"]
    def change_during_call():
        with SessionLocal() as db:
            if mutation == "edit":
                asset = db.get(StickerAsset, asset_id)
                asset.semantics = clean_semantics({"description": "人工确认的描述"})
                asset.semantic_hash = digest([asset.name, asset.semantics])
            else:
                row = db.scalar(select(DurableJob).where(DurableJob.kind == "sticker.semantic", DurableJob.workspace_id == workspace))
                row.status = "cancelled"
                row.lease_token = None
            db.commit()
    vision_fixture(workspace, monkeypatch, change_during_call)
    client.post(f"/api/v1/stickers/packs/{uploaded['pack_id']}/analyze", headers=headers, json={"consent": True})
    finish_semantics(workspace)
    found = client.get("/api/v1/stickers/search?q=猫", headers=headers).json()["candidates"][0]
    assert found["semantics"]["description"] == ("人工确认的描述" if mutation == "edit" else "")


def test_auto_pipeline_waits_for_model_and_enqueues_after_configuration(client, register_user, monkeypatch):
    workspace, headers, actor = identity(client, register_user)
    assert client.put("/api/v1/stickers/pipeline", headers=headers, json={"auto_analyze": True}).json()["available"] is False
    uploaded = client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("收藏.png", image_bytes()))]).json()
    assert client.post(f"/api/v1/stickers/packs/{uploaded['pack_id']}/analyze", headers=headers, json={"consent": True}).status_code == 409
    vision_fixture(workspace, monkeypatch)
    from app.services.stickers import reconcile_sticker_indexes
    reconcile_sticker_indexes()
    assert client.get("/api/v1/stickers/pipeline", headers=headers).json()["queued"] == 1
    assert client.put("/api/v1/stickers/pipeline", headers=headers, json={"auto_analyze": False}).json()["queued"] == 0
    with SessionLocal() as db:
        assert StickerPipeline(StickerService(db, workspace, actor)).enqueue(automatic=True)["queued"] == 0
    assert client.put("/api/v1/stickers/pipeline", headers=headers, json={"auto_analyze": True}).json()["queued"] == 1


def test_bad_vision_output_does_not_destroy_original_and_can_retry(client, register_user, monkeypatch):
    workspace, headers, _ = identity(client, register_user)
    uploaded = client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("收藏.png", image_bytes()))]).json()
    provider, _ = vision_fixture(workspace, monkeypatch, output='{"description": "secret-sensitive", "execute": "bad"}')
    client.post(f"/api/v1/stickers/packs/{uploaded['pack_id']}/analyze", headers=headers, json={"consent": True})
    finish_semantics(workspace)
    assert client.get("/api/v1/stickers/pipeline", headers=headers).json()["failed"] == 1
    with SessionLocal() as db:
        job = db.scalar(select(DurableJob).where(DurableJob.workspace_id == workspace, DurableJob.kind == "sticker.semantic"))
        assert "secret-sensitive" not in job.last_error
    found = client.get("/api/v1/stickers/search?q=收藏", headers=headers).json()["candidates"][0]
    assert found["semantics"]["description"] == ""
    assert client.get(f"/api/v1/stickers/{found['id']}/content", headers=headers).content == image_bytes()
    assert client.post(f"/api/v1/stickers/packs/{uploaded['pack_id']}/analyze", headers=headers, json={"consent": True}).json()["queued"] == 1


def test_animation_pipeline_samples_bounded_keyframes():
    output = io.BytesIO()
    frames = [Image.new("RGBA", (20, 20), color) for color in ["red", "blue", "green", "yellow"]]
    frames[0].save(output, format="GIF", save_all=True, append_images=frames[1:])
    assert len(image_parts(output.getvalue())) == 3
    assert len(image_parts(image_bytes())) == 1


@pytest.mark.parametrize("source_id,license_marker", [("noto-reactions", b"Apache License"), ("openmoji-reactions", b"Attribution-ShareAlike 4.0")])
def test_licensed_sources_install_export_and_reimport_preserve_notices(client, register_user, source_id, license_marker):
    workspace, headers, actor = identity(client, register_user)
    installed = client.post(f"/api/v1/stickers/market/{source_id}/install", headers=headers).json()
    assert installed["imported"] == 24
    downloaded = client.get(f"/api/v1/stickers/packs/{installed['pack_id']}/download", headers=headers)
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert license_marker in archive.read("LICENSE.txt")
        notice = archive.read("NOTICE.txt")
        assert b"SHA-256" in notice
        assert source_id.split('-')[0].encode() in notice.lower()
    _, other_headers, _ = identity(client, register_user)
    imported = client.post("/api/v1/stickers/import", headers=other_headers, files=[("files", ("licensed.zip", downloaded.content))]).json()
    again = client.get(f"/api/v1/stickers/packs/{imported['pack_id']}/download", headers=other_headers)
    with zipfile.ZipFile(io.BytesIO(again.content)) as archive:
        assert license_marker in archive.read("LICENSE.txt")
        assert archive.read("NOTICE.txt") == notice
    if source_id == "openmoji-reactions":
        with SessionLocal() as db:
            service = StickerService(db, workspace, actor)
            asset = next(asset for asset in service.visible_assets() if asset.pack_id == installed["pack_id"])
            part = service.part_data(asset.id)
            assert "OpenMoji" in part["attribution"]
            assert part["license_url"] == "https://creativecommons.org/licenses/by-sa/4.0/"


def test_single_sticker_analysis_only_queues_selected_asset_and_is_private(client, register_user, monkeypatch):
    workspace, headers, _ = identity(client, register_user)
    _, calls = vision_fixture(workspace, monkeypatch)
    uploaded = client.post("/api/v1/stickers/import", headers=headers, files=[("files", ("猫.png", image_bytes())), ("files", ("狗.png", image_bytes("blue")))]).json()
    assets = client.get("/api/v1/stickers/search", headers=headers, params={"pack_id": uploaded["pack_id"]}).json()["candidates"]
    selected = assets[0]
    response = client.post(f"/api/v1/stickers/{selected['id']}/analyze", headers=headers, json={"consent": True})
    assert response.status_code == 202 and response.json()["queued"] == 1
    assert client.post(f"/api/v1/stickers/{selected['id']}/analyze", headers=headers, json={"consent": True}).json()["queued"] == 0
    state = client.get("/api/v1/stickers/pipeline", headers=headers).json()
    assert state["assets"] == [{"sticker_id": selected["id"], "status": "queued"}]
    finish_semantics(workspace)
    assert len(calls) == 1
    assert client.get(f"/api/v1/stickers/{selected['id']}", headers=headers).json()["needs_description"] is False
    assert client.get(f"/api/v1/stickers/{assets[1]['id']}", headers=headers).json()["needs_description"] is True
    client.put(f"/api/v1/stickers/{selected['id']}", headers=headers, json={"name": selected["name"], "semantics": selected["semantics"]}).raise_for_status()
    assert client.post(f"/api/v1/stickers/{selected['id']}/analyze", headers=headers, json={"consent": True}).json()["queued"] == 1
    finish_semantics(workspace)
    assert len(calls) == 2
    _, other_headers, _ = identity(client, register_user)
    assert client.get(f"/api/v1/stickers/{selected['id']}", headers=other_headers).status_code == 404
    assert client.post(f"/api/v1/stickers/{selected['id']}/analyze", headers=other_headers, json={"consent": True}).status_code == 404
    assert client.get("/api/v1/stickers/pipeline", headers=other_headers).json()["assets"] == []
