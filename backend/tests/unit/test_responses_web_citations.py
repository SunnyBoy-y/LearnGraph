import json
from types import SimpleNamespace

import httpx
import pytest

from app.providers.ports.model import ProviderChatMessage
from app.providers.remote.openai import OpenAIResponsesProvider
from app.services.chat import _normalize_web_sources, _render_native_web_citations


URL = "https://example.org/source"
RAW = "\ue200cite\ue202turn923153search3\ue201"


def provider_for(events):
    def handle(_request):
        return httpx.Response(200, text="".join(f"data: {json.dumps(event)}\n\n" for event in events))

    return OpenAIResponsesProvider(
        provider_id="test-responses", provider_type="openai_responses", model_id="test-model",
        base_url="https://example.org/v1", api_key="test-key", transport=httpx.MockTransport(handle),
    )


def text_part(text, annotations=()):
    return {"type": "output_text", "text": text, "annotations": list(annotations)}


def message(*parts):
    return {"type": "message", "role": "assistant", "content": list(parts)}


def completion(*output):
    return {"type": "response.completed", "response": {"status": "completed", "output": list(output)}}


def delta(text, content_index=0, output_index=0):
    return {"type": "response.output_text.delta", "output_index": output_index, "content_index": content_index, "delta": text}


def citation(start, end, url=URL):
    return {"type": "url_citation", "url": url, "title": "Primary source", "start_index": start, "end_index": end}


def render(provider, text, existing=()):
    native = _normalize_web_sources(provider.last_sources)
    merged = _normalize_web_sources([*existing, *native])
    return _render_native_web_citations(text, native, merged, provider)


@pytest.mark.parametrize("mode", ["chat", "answer"])
@pytest.mark.parametrize("citation_event", ["added", "part", "item", "completed"])
def test_merges_stream_and_final_annotations_and_rebases_all_uses(mode, citation_event):
    first = "甲" + RAW + "。"
    second = "乙。"
    partial = citation(0, 2)
    final = citation(0, 1)
    early = {
        "added": {"type": "response.output_text.annotation.added", "output_index": 1, "content_index": 0, "annotation": partial},
        "part": {"type": "response.content_part.done", "output_index": 1, "content_index": 0, "part": text_part(first, [partial])},
        "item": {"type": "response.output_item.done", "output_index": 1, "item": message(text_part(first, [partial]))},
    }.get(citation_event)
    events = [delta(first, output_index=1), delta(second, content_index=1, output_index=1)]
    if early is not None:
        events.append(early)
    events.append(completion(
        {"type": "web_search_call", "id": "ws_1", "status": "completed", "action": {
            "type": "search", "sources": [{"type": "url", "url": "https://example.org/unrelated"}],
        }},
        message(text_part(first, [partial] if early is None else []), text_part(second, [final, final])),
    ))
    provider = provider_for(events)
    if mode == "chat":
        output = "".join(event.content or "" for event in provider.stream_chat([ProviderChatMessage("user", "Search")]) if event.type == "text_delta")
    else:
        output = "".join(provider.stream_answer("Search"))
    assert output == first + second
    assert [s["url"] for s in provider.last_sources] == [URL]
    assert provider.last_sources[0]["end_indexes"] == [2, len(first) + 1]
    assert render(provider, output, [{"url": "https://example.org/existing"}, {"url": URL, "end_index": 99}]) == (
        f"甲{RAW}（网页引用：2）。乙（网页引用：2）。"
    )


def test_no_inline_links_are_fabricated_from_search_hits_or_invalid_annotations():
    text = "答" + RAW
    provider = provider_for([delta(text), completion(message(text_part(text, [
        {**citation(0, 1), "type": "file_citation"},
        citation(0, 1, "javascript:alert(1)"),
        citation(0, 1, "https://user:pass@example.org"),
    ])), {"type": "web_search_call", "action": {"type": "search", "sources": [{"url": URL}]}})])
    assert "".join(provider.stream_answer("Search")) == text
    assert provider.last_sources == []
    assert render(provider, text) == text


def test_misaligned_offsets_only_get_an_unanchored_source():
    provider = provider_for([delta("streamed"), completion(message(text_part("rewritten", [citation(0, 2)])))])
    assert "".join(provider.stream_answer("Search")) == "streamed"
    assert provider.last_sources[0]["end_indexes"] == []
    assert render(provider, "streamed") == "streamed\n\n（网页引用：1）"
    different_text = SimpleNamespace(last_citation_text="streamed", last_sources=[{"url": URL, "end_indexes": [2]}])
    assert render(different_text, "edited") == "edited\n\n（网页引用：1）"
