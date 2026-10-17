import io
import json
import hashlib
import zipfile
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.core.errors import AppError
from app.domain.extension_models import SkillPackageFile
from app.domain.schemas.extensions import SkillMarketInstallRequest
from app.services.skill_clawhub import ClawHubSkillMarket, _CACHE, _COOLDOWNS
from app.services.skill_market import SkillMarketService


@pytest.fixture
def market(tmp_path):
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, storage_root=tmp_path, clawhub_enabled=True)
    with Session(engine) as db:
        yield ClawHubSkillMarket(db, "test-workspace", "test-user", settings)
    engine.dispose()
    _CACHE.clear()
    _COOLDOWNS.clear()


def item(slug="example"):
    return {"slug": slug, "ownerHandle": "alice", "displayName": slug,
            "stats": {"downloads": 42}, "latestVersion": {"version": "1.2.3"}}


def archive():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as z:
        z.writestr("SKILL.md", "---\nname: example\ndescription: Explain examples\n---\n\nUse examples.")
        z.writestr("references/examples.md", "Reference examples.")
    return stream.getvalue()


def test_main_market_uses_clawhub_and_forwards_cursor(market, monkeypatch):
    calls = []
    def get(self, path, params, **kwargs):
        calls.append((path, params))
        return {"items": [item()], "nextCursor": "page-two"}
    monkeypatch.setattr(ClawHubSkillMarket, "_get", get)
    result = SkillMarketService(market.db, "ws", "user", market.settings).list_cards(cursor="page-one")
    assert result.source == "clawhub" and result.next_cursor == "page-two"
    assert calls == [("skills", {"limit": 12, "nonSuspiciousOnly": "true", "sort": "downloads", "cursor": "page-one"})]
    card = result.cards[0]
    assert card.market_id == "clawhub:alice/example" and card.installable
    assert card.homepage_url == "https://clawhub.ai/alice/skills/example"
    assert card.version == "1.2.3" and card.installs == 42


def test_search_uses_remote_results_and_pages_them(market, monkeypatch):
    calls = []
    def get(path, params, **kwargs):
        calls.append((path, params))
        return {"results": [item(f"example-{i}") for i in range(15)]}
    monkeypatch.setattr(market, "_get", get)
    result = market.list_cards(query="calendar", page=2)
    assert calls[0][0] == "search" and calls[0][1]["q"] == "calendar"
    assert len(result.cards) == 3 and result.total_pages == 2
    assert result.next_cursor is None


def test_unified_search_without_version_is_still_installable(market, monkeypatch):
    raw = {**item(), "latestVersion": None, "version": None,
           "trust": {"installability": "installable"}}
    monkeypatch.setattr(market, "_get", lambda *args, **kwargs: {"results": [raw]})
    result = market.list_cards(query="calendar")
    assert result.cards[0].version == "" and result.cards[0].installable


def test_rate_limit_honors_retry_after_without_repeating_upstream_requests(market, monkeypatch):
    from urllib.error import HTTPError
    calls = []
    def open_url(request, **kwargs):
        calls.append(request.full_url)
        raise HTTPError(request.full_url, 429, "Rate limited", {"Retry-After": "60"}, io.BytesIO(b"Rate limit exceeded"))
    monkeypatch.setattr("urllib.request.urlopen", open_url)
    for _ in range(2):
        with pytest.raises(AppError) as error:
            market.list_cards(refresh=True)
        assert error.value.status_code == 429 and error.value.details["retry_after"] > 0
    assert len(calls) == 1


def test_github_handoff_imports_the_pinned_commit_and_preserves_both_origins(market, monkeypatch):
    from app.services.skill_github_import import SkillGitHubImportService
    commit = "a" * 40
    def get(path, params, **kwargs):
        if path == "download":
            return json.dumps({"sourceRef": "public-github", "repo": "alice/skills", "commit": commit, "path": "skills/example"}).encode()
        return {"owner": {"handle": "alice"}, "latestVersion": {"version": "1.2.3"}}
    monkeypatch.setattr(market, "_get", get)
    monkeypatch.setattr(SkillGitHubImportService, "_fetch_tree", lambda *args: ([{"path": "skills/example/SKILL.md", "type": "blob", "size": 80}], False))
    fetched = []
    def raw(self, owner, repo, pinned_commit, path):
        fetched.append((owner, repo, pinned_commit, path))
        return b"---\nname: example\ndescription: An example\n---\n\nExplain examples."
    monkeypatch.setattr(SkillGitHubImportService, "_fetch_raw", raw)
    skill = market.install(SkillMarketInstallRequest(market_id="clawhub:alice/example"))
    assert fetched == [("alice", "skills", commit, "skills/example/SKILL.md")]
    assert skill.source == "clawhub:alice/example" and skill.version == "1.2.3"
    assert skill.manifest_json["clawhub_origin"] == "clawhub:alice/example@1.2.3"
    assert skill.manifest_json["github"]["commit"] == commit
    assert skill.status == "authorization_required" and not skill.enabled


def test_real_import_pipeline_keeps_files_version_hash_and_authorization(market, monkeypatch):
    calls = []
    def get(path, params, **kwargs):
        calls.append((path, params.copy()))
        if path == "download":
            return archive()
        return {"owner": {"handle": "alice"}, "latestVersion": {"version": "2.0.0"}}
    monkeypatch.setattr(market, "_get", get)
    skill = market.install(SkillMarketInstallRequest(market_id="clawhub:alice/example", version="1.2.3"))
    assert calls[-1][1] == {"owner": "alice", "slug": "example", "version": "1.2.3"}
    assert skill.origin_type == "clawhub_market" and skill.origin_ref == "clawhub:alice/example@1.2.3"
    assert skill.version == "1.2.3" and skill.source == "clawhub:alice/example"
    assert skill.status == "authorization_required" and not skill.enabled
    assert len(skill.content_hash) == 64 and len(skill.origin_hash) == 64
    files = list(market.db.scalars(select(SkillPackageFile).where(SkillPackageFile.skill_id == skill.id)))
    assert {f.relative_path for f in files} == {"SKILL.md", "references/examples.md"}
    with pytest.raises(AppError, match="already exists"):
        market.install(SkillMarketInstallRequest(market_id="clawhub:alice/example"))


@pytest.mark.parametrize("detail,code", [
    ({"owner": {"handle": "bob"}}, "clawhub_owner_mismatch"),
    ({"owner": {"handle": "alice"}, "moderation": {"isMalwareBlocked": True}}, "clawhub_skill_blocked"),
    ({"owner": {"handle": "alice"}}, "clawhub_version_missing"),
])
def test_unavailable_or_wrong_owner_never_downloaded(market, monkeypatch, detail, code):
    calls = []
    def get(path, *args, **kwargs):
        calls.append(path)
        return detail
    monkeypatch.setattr(market, "_get", get)
    with pytest.raises(AppError) as error:
        market.install(SkillMarketInstallRequest(market_id="clawhub:alice/example"))
    assert error.value.code == code and calls == ["skills/example"]


def test_cache_refresh_and_disabled_setting(market, monkeypatch):
    calls = []
    class Response(io.BytesIO):
        pass
    def open_url(request, **kwargs):
        calls.append(parse_qs(urlsplit(request.full_url).query))
        return Response(b'{"items":[],"nextCursor":null}')
    monkeypatch.setattr("urllib.request.urlopen", open_url)
    market.list_cards()
    market.list_cards()
    assert len(calls) == 1
    market.list_cards(refresh=True)
    assert len(calls) == 2
    market.settings.clawhub_enabled = False
    with pytest.raises(AppError) as error:
        market.list_cards()
    assert error.value.code == "catalog_disabled"


def test_details_keep_full_body_and_exact_version_file_list(market, monkeypatch):
    text = "\ufeff---\nname: example\n---\n\n# Complete instructions\n" + "Full explanation. " * 1500 + "END OF FULL DOCUMENT"
    calls = []
    def get(path, params, **kwargs):
        calls.append((path, params.copy()))
        if path.endswith("/file"):
            assert kwargs["plain"] is True
            return text
        if "/versions/" in path:
            return {"version": {"version": "1.2.3", "changelog": "Old version changes", "files": [
                {"path": "SKILL.md", "size": len(text.encode()), "sha256": hashlib.sha256(text.encode()).hexdigest(), "contentType": "text/markdown"},
                {"path": "references/example.md", "size": 100},
            ]}}
        return {"skill": {"displayName": "Example", "summary": "Summary"}, "owner": {"handle": "alice"}, "latestVersion": {"version": "2.0.0"}}
    monkeypatch.setattr(market, "_get", get)
    result = market.detail("clawhub:alice/example", version="1.2.3")
    assert result.version == "1.2.3" and result.skill_md == text
    assert result.instructions_markdown.startswith("# Complete instructions")
    assert result.instructions_markdown.endswith("END OF FULL DOCUMENT") and len(result.instructions_markdown) > 20_000
    assert [file.path for file in result.files] == ["SKILL.md", "references/example.md"]
    assert result.changelog == "Old version changes" and result.instructions_error is None
    assert calls[-1][1] == {"owner": "alice", "path": "SKILL.md", "version": "1.2.3"}


@pytest.mark.parametrize("section", ["files", "instructions", "checksum"])
def test_detail_section_failures_are_explicit_without_losing_other_content(market, monkeypatch, section):
    def get(path, *args, **kwargs):
        if path.endswith("/file"):
            if section == "instructions":
                raise AppError(404, "not_found", "说明读取失败")
            return "# Complete description"
        if "/versions/" in path:
            if section == "files":
                raise AppError(502, "unavailable", "文件清单读取失败")
            return {"version": {"version": "1.2.3", "files": [{"path": "SKILL.md", "sha256": "bad" if section == "checksum" else ""}]}}
        return {"skill": {"displayName": "Example"}, "owner": {"handle": "alice"}, "latestVersion": {"version": "1.2.3"}}
    monkeypatch.setattr(market, "_get", get)
    result = market.detail("clawhub:alice/example")
    if section == "files":
        assert result.files_error and result.instructions_markdown == "# Complete description"
    else:
        assert result.instructions_error and result.files[0].path == "SKILL.md"
        assert not result.skill_md


def test_plain_response_preserves_bom_and_is_cached(market, monkeypatch):
    data = b"\xef\xbb\xbf# Exact UTF-8\r\n"
    calls = []
    def open_url(*args, **kwargs):
        calls.append(True)
        return io.BytesIO(data)
    monkeypatch.setattr("urllib.request.urlopen", open_url)
    for _ in range(2):
        assert market._get("skills/example/file", {"path": "SKILL.md"}, plain=True).encode() == data
    assert len(calls) == 1


def test_wrong_owner_cannot_read_detail_files(market, monkeypatch):
    calls = []
    def get(path, *args, **kwargs):
        calls.append(path)
        return {"owner": {"handle": "bob"}}
    monkeypatch.setattr(market, "_get", get)
    with pytest.raises(AppError) as error:
        market.detail("clawhub:alice/example")
    assert error.value.code == "clawhub_owner_mismatch" and calls == ["skills/example"]


def test_detail_github_handoff_reads_only_the_pinned_skill_file(market, monkeypatch):
    from app.services.skill_github_import import SkillGitHubImportService
    calls = []
    def raw(self, owner, repo, commit, path):
        calls.append((owner, repo, commit, path))
        return b"# Exact commit instructions"
    monkeypatch.setattr(SkillGitHubImportService, "_fetch_raw", raw)
    result = market._read_github_skill_md({"repo": "https://github.com/alice/skills", "commit": "a" * 40, "path": ""})
    assert result == "# Exact commit instructions"
    assert calls == [("alice", "skills", "a" * 40, "SKILL.md")]
