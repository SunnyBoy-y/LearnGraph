import json

from app.services.chat import _normalize_web_sources
from app.services.web_evidence import align_tool_sources, merge_citation_sources


def test_final_citation_projection_preserves_actual_page_read_and_provider():
    sources = _normalize_web_sources([{
        "url": "https://example.org/page", "title": "Page", "provider_id": "browser",
        "provider_label": "隔离浏览器", "page_read": True, "evidence_kind": "page_text",
    }])
    assert sources[0]["page_read"] is True
    assert sources[0]["provider_label"] == "隔离浏览器"
    assert sources[0]["index"] == 1


def test_native_url_citation_alone_does_not_prove_page_was_read():
    sources = _normalize_web_sources([{"url": "https://example.org", "title": "Native citation"}])
    assert sources[0]["page_read"] is False


def test_page_read_upgrades_a_discovered_url_without_losing_native_offsets():
    sources = _normalize_web_sources([
        {"url": "https://example.org", "title": "Hit", "evidence_kind": "search_snippet", "start_index": 1, "end_index": 3},
        {"url": "https://example.org", "title": "Body", "evidence_kind": "page_text", "page_read": True},
    ])
    assert len(sources) == 1
    assert sources[0]["page_read"] is True
    assert sources[0]["end_index"] == 3


def test_mcp_and_browser_citations_have_stable_global_indices():
    previous = [{"url": "https://example.org/a", "title": "A", "index": 1, "page_read": False}]
    incoming = [{"url": "https://example.org/b", "title": "B", "index": 1, "page_read": True, "evidence_kind": "page_text"}]
    content, aligned = align_tool_sources(json.dumps({"results": incoming}), incoming, previous)
    assert aligned[0]["index"] == 2
    payload = json.loads(content)
    assert payload["results"][0]["index"] == 2
    assert payload["citation_sources"][0]["index"] == 2
    merged = merge_citation_sources(previous, aligned)
    assert [row["index"] for row in merged] == [1, 2]
    assert len(merge_citation_sources(merged, aligned)) == 2
