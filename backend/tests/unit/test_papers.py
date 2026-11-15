from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
import hashlib
import json

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.models import Base, SourceCitation, SourceRecord
from app.domain.schemas.papers import Paper, PaperReadRequest, PaperReadResponse, PaperSaveRequest, PaperSearchRequest, PaperReference, PaperExcerpt
from app.providers.remote.papers import PaperSearchProvider, arxiv_papers, normalized_paper_id, openalex_paper
from app.services.document_parsers import ParsedChunk, ParsedDocument
from app.services.papers import PaperService, bibtex
from app.services.web_tool_policy import web_tool_allowed, web_tool_activity


def work():
    return {"id": "https://openalex.org/W123", "title": "Attention & learning", "publication_year": 2024,
            "publication_date": "2024-05-03", "doi": "https://doi.org/10.1234/example",
            "authorships": [{"author": {"display_name": "Ada Researcher"}}],
            "abstract_inverted_index": {"A": [0], "study": [1]}, "open_access": {"is_oa": True},
            "best_oa_location": {"is_oa": True, "pdf_url": "https://papers.example/test.pdf"},
            "primary_location": {"source": {"display_name": "Example journal"}}, "type": "article", "cited_by_count": 8}


def paper():
    return openalex_paper(work())


ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
<entry><id>http://arxiv.org/abs/2401.12345v2</id><title> A\n paper </title><summary>Abstract only</summary>
<published>2024-01-22T00:00:00Z</published><author><name>Test Author</name></author>
<arxiv:doi>10.1234/example</arxiv:doi></entry></feed>'''


def test_openalex_keeps_structured_metadata_without_claiming_body_read():
    result = paper()
    assert result.abstract == "A study" and result.doi == "10.1234/example"
    assert result.authors == ["Ada Researcher"] and result.year == 2024
    assert result.pdf_url == "https://papers.example/test.pdf" and not result.page_read
    assert result.evidence_kind == "abstract"
    row = work()
    row["best_oa_location"]["is_oa"] = False
    assert openalex_paper(row).pdf_url is None


def test_openalex_search_passes_filters_and_author_candidates_without_anysearch(monkeypatch):
    calls = []
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    def handle(request):
        calls.append(request)
        if request.url.path == "/authors":
            return httpx.Response(200, json={"results": [{"id": "https://openalex.org/A456"}]})
        return httpx.Response(200, json={"results": [work(), work()]})
    provider = PaperSearchProvider(transport=httpx.MockTransport(handle))
    response = provider.search(PaperSearchRequest(query="attention", author="Ada", year_from=2023, year_to=2025,
                                                  open_access_only=True, sort="newest"))
    assert len(response.results) == 1 and len(calls) == 2
    assert all(r.url.host == "api.openalex.org" for r in calls)
    assert calls[-1].url.params["filter"] == "publication_year:2023-2025,is_oa:true,authorships.author.id:A456"
    assert calls[-1].url.params["sort"] == "publication_date:desc"


def test_arxiv_preserves_version_and_preprint_status():
    result = arxiv_papers(ATOM)[0]
    assert result.paper_id == "2401.12345v2" and result.publication_type == "preprint"
    assert result.evidence_kind == "abstract" and not result.page_read
    assert result.pdf_url == "https://arxiv.org/pdf/2401.12345v2"


def test_arxiv_query_escapes_user_filters_and_passes_dates():
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=ATOM)
    provider = PaperSearchProvider(transport=httpx.MockTransport(handle))
    provider.search(PaperSearchRequest(query="attention OR cat:cs.AI", provider="arxiv", year_from=2024, year_to=2024,
                                       author='Ada" OR all:secret', sort="newest"))
    query = calls[0].url.params["search_query"]
    assert 'all:"OR"' in query and 'cat:cs.AI' not in query
    assert "submittedDate:[202401010000 TO 202412312359]" in query
    assert calls[0].url.params["sortBy"] == "submittedDate"


@pytest.mark.parametrize("provider,value", [("openalex", "../../secret"), ("openalex", "W1?api_key=x"),
    ("arxiv", "https://evil.example/x"), ("arxiv", "2401.12345/../../x")])
def test_paper_ids_cannot_select_arbitrary_urls(provider, value):
    with pytest.raises(AppError) as exc:
        normalized_paper_id(provider, value)
    assert exc.value.code == "invalid_paper_id"


@pytest.mark.parametrize("status,code", [(429, "paper_provider_rate_limited"), (403, "paper_provider_access_denied"), (503, "paper_provider_failed")])
def test_provider_errors_are_explicit_and_never_switch_providers(status, code):
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(status)
    with pytest.raises(AppError) as exc:
        PaperSearchProvider(transport=httpx.MockTransport(handle)).search(PaperSearchRequest(query="attention"))
    assert exc.value.code == code and len(calls) == 1


def test_timeout_is_not_treated_as_empty_success():
    def timeout(request):
        raise httpx.ReadTimeout("upstream timeout")
    with pytest.raises(AppError) as exc:
        PaperSearchProvider(transport=httpx.MockTransport(timeout)).search(PaperSearchRequest(query="attention"))
    assert exc.value.code == "paper_provider_timeout"


def test_invalid_xml_and_mismatched_lookup_are_rejected():
    with pytest.raises(AppError):
        arxiv_papers(b'<!DOCTYPE x [<!ENTITY x "bad">]><feed/>')
    provider = PaperSearchProvider(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=work())))
    with pytest.raises(AppError):
        provider.resolve(PaperReference(provider="openalex", paper_id="W999"))


def test_malformed_nested_provider_metadata_fails_explicitly():
    row = work()
    row["primary_location"] = "invalid"
    provider = PaperSearchProvider(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"results": [row]})))
    with pytest.raises(AppError) as exc:
        provider.search(PaperSearchRequest(query="attention"))
    assert exc.value.code == "paper_provider_response_invalid"


def test_search_request_checks_year_range_and_unknown_fields():
    with pytest.raises(ValidationError):
        PaperSearchRequest(query="paper", year_from=2025, year_to=2024)
    with pytest.raises(ValidationError):
        PaperSearchRequest(query="paper", anysearch=True)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def service(db, tmp_path):
    provider = MagicMock()
    provider.resolve.return_value = paper()
    return PaperService(db, "test-workspace", "test-user", Settings(storage_root=tmp_path), provider=provider)


def request(**kwargs):
    return PaperReadRequest(provider="openalex", paper_id="W123", **kwargs)


def stub_download(monkeypatch, payload=b"%PDF-1.4\nmock", chunks=None):
    from app.services.external_acquisition import PublicImageDownloadClient
    download = MagicMock(return_value=SimpleNamespace(data=payload, final_url="https://papers.example/test.pdf"))
    monkeypatch.setattr(PublicImageDownloadClient, "_download", download)
    monkeypatch.setattr("app.services.papers._parse_pdf", lambda *_a, **_k: ParsedDocument("pypdf", "test", chunks or [ParsedChunk("page:1", "Original page one"), ParsedChunk("page:3", "Original page three")]))
    return download


def test_no_domain_consent_means_no_download(service, monkeypatch):
    download = stub_download(monkeypatch)
    with pytest.raises(AppError) as exc:
        service.read(request())
    assert exc.value.code == "paper_domain_authorization_required"
    download.assert_not_called()


def test_read_returns_real_locators_and_hash_without_saving(service, db, monkeypatch):
    stub_download(monkeypatch)
    result = service.read(request(authorized_domains=["papers.example"]))
    assert result.page_read and result.evidence_kind == "full_text"
    assert [e.locator for e in result.excerpts] == ["page:1", "page:3"]
    assert result.content_hash == hashlib.sha256(b"%PDF-1.4\nmock").hexdigest()
    assert db.scalar(select(func.count()).select_from(SourceRecord)) == 0


def test_redirect_to_existing_approved_parent_domain_does_not_reprompt(service, monkeypatch):
    from app.services.external_acquisition import AcquisitionApprovalRequired
    download = stub_download(monkeypatch)
    download.side_effect = [AcquisitionApprovalRequired("cdn.papers.example"),
        SimpleNamespace(data=b"%PDF-1.4\\nmock", final_url="https://cdn.papers.example/test.pdf")]
    result = service.read(request(authorized_domains=["papers.example"]))
    assert result.url == "https://cdn.papers.example/test.pdf" and download.call_count == 2
    assert "cdn.papers.example" in download.call_args.kwargs["allowed_hosts"]


def test_truncated_pages_are_marked(service, monkeypatch):
    stub_download(monkeypatch, chunks=[ParsedChunk("page:7", "a" * 9000)])
    result = service.read(request(authorized_domains=["papers.example"]))
    assert result.truncated and result.excerpts[0].locator == "page:7"
    assert len(result.excerpts[0].content) == 6000


def test_html_access_page_is_not_marked_as_pdf_read(service, monkeypatch):
    stub_download(monkeypatch, payload=b"<html>Sign in</html>")
    with pytest.raises(AppError) as exc:
        service.read(request(authorized_domains=["papers.example"]))
    assert exc.value.code == "paper_not_pdf"


def test_closed_access_cannot_be_read(service, monkeypatch):
    service.provider.resolve.return_value = paper().model_copy(update={"open_access": False})
    download = stub_download(monkeypatch)
    with pytest.raises(AppError) as exc:
        service.read(request(authorized_domains=["papers.example"]))
    assert exc.value.code == "paper_full_text_unavailable"
    download.assert_not_called()


def test_metadata_save_is_idempotent_and_not_body_evidence(service, db):
    payload = PaperSaveRequest(provider="openalex", paper_id="W123")
    first = service.save(payload)
    second = service.save(payload)
    assert first.id == second.id and first.content == "A study"
    assert first.metadata_json["evidence_kind"] == "abstract" and not first.metadata_json["page_read"]
    assert db.scalar(select(func.count()).select_from(SourceRecord)) == 1
    assert db.scalar(select(func.count()).select_from(SourceCitation)) == 0
    assert service.library()[0].id == first.id


def test_save_upgrade_preserves_id_and_creates_exact_page_citations(service, db, monkeypatch):
    payload = PaperSaveRequest(provider="openalex", paper_id="W123")
    source_id = service.save(payload).id
    stub_download(monkeypatch)
    upgraded = service.save(payload.model_copy(update={"include_full_text": True, "authorized_domains": ["papers.example"]}))
    assert upgraded.id == source_id and upgraded.metadata_json["page_read"]
    rows = db.scalars(select(SourceCitation).where(SourceCitation.source_id == source_id).order_by(SourceCitation.locator)).all()
    assert [(r.locator, r.quote) for r in rows] == [("page:1", "Original page one"), ("page:3", "Original page three")]
    assert service.save(payload).cache_status == "full_text"  # never downgrade


def test_library_is_workspace_scoped_and_respects_acl(service, db):
    record = service.save(PaperSaveRequest(provider="openalex", paper_id="W123"))
    other = PaperService(db, "other-workspace", "test-user", service.settings, provider=service.provider)
    assert other.library() == []
    service.source_access = lambda *_: False
    assert service.library() == []
    with pytest.raises(AppError) as exc:
        service.save(PaperSaveRequest(provider="openalex", paper_id="W123"))
    assert exc.value.code == "source_not_found"


@pytest.mark.parametrize("route,allowed", [("disabled", False), ("local", False), ("model_native", False), ("external", True), ("auto", True)])
def test_paper_tools_respect_existing_routes_independently_of_anysearch(route, allowed):
    runtime = SimpleNamespace(search_route=route, search_provider=None)
    for name in ("search_papers", "read_paper"):
        assert web_tool_allowed(runtime, name) is allowed
    assert web_tool_allowed(runtime, "browser_read_page")


def test_tool_progress_names_actual_scholarly_source():
    result = web_tool_activity(SimpleNamespace(), "search_papers", {"query": "x", "provider": "arxiv"})
    assert result["web_activity"]["provider_label"] == "arXiv"
    assert result["web_activity"]["operation"] == "search"


def test_bibtex_uses_real_metadata_and_escapes_special_characters():
    value = bibtex(paper())
    assert r"Attention \& learning" in value and "Ada Researcher" in value
    assert "10.1234/example" in value and "year = {2024}" in value
    preprint = arxiv_papers(ATOM)[0]
    assert "@misc{" in bibtex(preprint) and "eprint = {2401.12345v2}" in bibtex(preprint)


def test_concurrent_upgrade_never_mixes_body_and_quotes(service, db, monkeypatch):
    payload = PaperSaveRequest(provider="openalex", paper_id="W123", include_full_text=False)
    record = service.save(payload)
    winner = PaperReadResponse(paper=paper(), url="https://papers.example/winner.pdf", content_hash="a" * 64,
        excerpts=[PaperExcerpt(locator="page:1", content="Winning snapshot")])
    loser = winner.model_copy(update={"content_hash": "b" * 64,
        "excerpts": [PaperExcerpt(locator="page:1", content="Stale snapshot")]})
    other = PaperService(db, service.workspace_id, service.actor_id, service.settings, provider=service.provider)
    monkeypatch.setattr(other, "read", lambda *_a, **_k: winner)
    def interleave(*_a, **_k):
        other.save(payload.model_copy(update={"include_full_text": True}))
        return loser
    monkeypatch.setattr(service, "read", interleave)
    result = service.save(payload.model_copy(update={"include_full_text": True}))
    assert result.metadata_json["document_sha256"] == "a" * 64
    assert "Winning snapshot" in result.content and "Stale snapshot" not in result.content
    quotes = db.scalars(select(SourceCitation).where(SourceCitation.source_id == record.id)).all()
    assert len(quotes) == 1 and quotes[0].quote == "Winning snapshot"


def test_agent_redirect_authorization_retains_approved_hops(db, tmp_path, monkeypatch):
    from app.domain.models import ChatSession, FetchAuthorizationRequest
    from app.services.paper_tools import execute
    db.add(ChatSession(id="chat", workspace_id="test-workspace", title="Test"))
    db.commit()
    def read(request, **_):
        hosts = set(request.authorized_domains)
        if "papers.example" not in hosts:
            raise AppError(403, "paper_domain_authorization_required", "approve origin", {"hostname": "papers.example"})
        if "cdn.example" not in hosts:
            raise AppError(403, "paper_redirect_authorization_required", "approve redirect", {"hostname": "cdn.example"})
        return PaperReadResponse(paper=paper(), url="https://cdn.example/paper.pdf", content_hash="c" * 64,
            excerpts=[PaperExcerpt(locator="page:3", content="Real page three")])
    fake = MagicMock()
    fake._resolve.return_value = paper()
    fake.read.side_effect = read
    monkeypatch.setattr("app.services.paper_tools.PaperService", lambda *_a, **_k: fake)
    runtime = SimpleNamespace(extensions=SimpleNamespace(db=db), workspace_id="test-workspace", actor_id="test-user", settings=Settings(storage_root=tmp_path),
        _failure=lambda code, message, data: (json.dumps(data), {"error": code}, []),
        _success=lambda data, meta, sources: (json.dumps(data), meta, sources))
    args = {"provider": "openalex", "paper_id": "W123"}
    def call(call_id):
        return execute(runtime, "read_paper", args, tool_call={"id": call_id}, allowed_domains=[], chat_session_id="chat")
    _, meta, _ = call("origin-call")
    assert meta["error"] == "fetch_domain_authorization_required"
    origin = db.scalar(select(FetchAuthorizationRequest).where(FetchAuthorizationRequest.tool_call_id == "origin-call"))
    origin.status, origin.decision = "approved", "allow_once"
    db.commit()
    _, meta, _ = call("redirect-call")
    assert meta["error"] == "fetch_domain_authorization_required"
    db.refresh(origin)
    assert origin.status == "approved"
    redirect = db.scalar(select(FetchAuthorizationRequest).where(FetchAuthorizationRequest.tool_call_id == "redirect-call"))
    assert redirect.hostname == "cdn.example" and redirect.resume_payload is None
    redirect.status, redirect.decision = "approved", "allow_once"
    db.commit()
    content, meta, sources = call("complete-call")
    assert meta["page_read"] is True
    assert sources[0]["url"] == "https://cdn.example/paper.pdf#page=3"
    db.refresh(origin)
    db.refresh(redirect)
    assert origin.status == redirect.status == "consumed"


def test_pdf_physical_page_numbers_survive_blank_pages():
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    from app.services.document_parsers import _parse_pdf
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    page = writer.add_blank_page(width=200, height=200)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 20 100 Td (Actual second page) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    parsed = _parse_pdf(output.getvalue(), max_pages=200)
    assert len(parsed.chunks) == 1 and parsed.chunks[0].locator == "page:2"
    assert "Actual second page" in parsed.chunks[0].content
