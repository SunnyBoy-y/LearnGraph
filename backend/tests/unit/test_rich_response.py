import json

import pytest

from app.core.errors import AppError
from app.providers.ports.model import ProviderStreamEvent
from app.services.rich_response import InlineUIParser, UICommandEvent, rich_events
from app.services.stream_ui import compile_command

FALLBACK_SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="20"/></svg>'



def declaration(blocks=None):
    return {"props": {"title": "追赶悖论"}, "upsert": {"blocks": blocks or [
        {"id": "layout", "type": "columns"},
        {"id": "text", "parent_id": "layout", "type": "paragraph", "text": "追赶时间有限。"},
        {"id": "sim", "parent_id": "layout", "type": "simulation", "engine": "chase"},
    ]}}


def consume(chunks):
    parser = InlineUIParser()
    events = [event for chunk in chunks for event in parser.feed(chunk)] + list(parser.finish())
    snapshots = {}
    for event in events:
        if isinstance(event, UICommandEvent):
            command = event.command
            snapshots[command["block_id"]] = compile_command(command, snapshots.get(command["block_id"])).model_dump(mode="json")
    return events, snapshots


def test_every_chunk_boundary_and_single_character_stream():
    text = "先解释。\n:::ui chase\n" + json.dumps(declaration(), ensure_ascii=False) + "\n:::\n继续解释。"
    for chunks in [list(text), *[[text[:index], text[index:]] for index in range(len(text) + 1)]]:
        events, snapshots = consume(chunks)
        assert len(snapshots) == 1
        snapshot = next(iter(snapshots.values()))
        assert snapshot["state"] == "completed" and len(snapshot["blocks"]) == 3
        prose = "".join(event.content or "" for event in events if event.type == "text_delta")
        assert prose.startswith("先解释。") and prose.endswith("继续解释。")
        assert "追赶时间有限" not in prose


def test_first_text_and_placeholder_do_not_wait_for_json():
    parser = InlineUIParser()
    assert next(parser.feed("第一段" )).content == "第一段"
    events = list(parser.feed("\n:::ui first\n{"))
    assert any(isinstance(event, UICommandEvent) and event.command["op"] == "mount" for event in events)


@pytest.mark.parametrize("body", ["{bad json}", '{"upsert":{"blocks":[{"id":"x","type":"html"}]}}', '{"props":{"title":"X","onClick":"alert(1)"}}'])
def test_invalid_and_incomplete_declarations_fall_back_to_readable_notice(body):
    for ending in ["\n:::\n", ""]:
        events, snapshots = consume(["前文\n:::ui bad\n", body + ending])
        assert next(iter(snapshots.values()))["state"] == "removed"
        prose = "".join(event.content or "" for event in events if event.type == "text_delta")
        assert body not in prose and "这部分图文未能生成" in prose


def test_literal_examples_inside_code_are_not_interpreted():
    text = "```text\n:::ui example\n{}\n:::\n```\n普通文字"
    events, snapshots = consume(list(text))
    assert not snapshots
    assert "".join(event.content for event in events) == text


def test_failed_attempt_removes_ui_before_raising_for_retry():
    def provider():
        yield ProviderStreamEvent("text_delta", content=":::ui x\n" + json.dumps(declaration()) + "\n:::\n")
        raise RuntimeError("upstream disconnected")
    events = []
    with pytest.raises(RuntimeError):
        for event in rich_events(provider()):
            events.append(event)
    assert events[-1].command["op"] == "remove" and events[-1].command["base_revision"] == 2


@pytest.mark.parametrize("blocks", [
    [{"id": "x", "type": "columns", "parent_id": "x"}],
    [{"id": "x", "type": "paragraph", "parent_id": "missing"}],
    [{"id": "x", "type": "paragraph"}, {"id": "y", "type": "paragraph", "parent_id": "x"}],
    [{"id": f"n{i}", "type": "stack", **({"parent_id": f"n{i-1}"} if i else {})} for i in range(8)],
    [{"id": "x", "type": "image", "media": [{"url": "https://evil.example/x.png"}]}],
    [{"id": "x", "type": "paragraph", "text": "X" * 16001}],
    [{"id": "x", "type": "simulation", "engine": "eval"}],
    [{"id": "x", "type": "chart", "visual": {"blocks": []}}],
])
def test_document_security_and_complexity_bounds(blocks):
    with pytest.raises(AppError):
        compile_command({"op": "mount", "block_id": "d", "component": "document", **declaration(blocks)})


def test_deleting_layout_cascades_and_resources_survive_unrelated_patch():
    state = compile_command({"op": "mount", "block_id": "d", "component": "document", **declaration()}).model_dump(mode="json")
    state = compile_command({"op": "patch", "block_id": "d", "delete": {"blocks": ["layout"]}}, state)
    assert state.blocks == []
    image = {"id": "image", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "Saturn", "media": [{"url": "https://example.com/saturn.png"}], "resource_state": "ready"}
    state = compile_command({"op": "mount", "block_id": "d", "component": "document", **declaration([{"id": "image", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "Saturn"}])}).model_dump(mode="json")
    state["blocks"] = [image]
    state = compile_command({"op": "patch", "block_id": "d", "upsert": {"blocks": [{"id": "image", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "Saturn", "caption": "新说明"}]}}, state)
    assert state.blocks[0].media[0].url.endswith("saturn.png")


def test_resolved_image_labels_are_grounded_and_survive_later_model_patches():
    from app.domain.schemas.stream_ui import Snapshot
    node = {"id":"pic", "type":"image", "parent_id":"figure", "fallback_svg":FALLBACK_SVG,
        "query":"bird", "alt":"站在岩壁的鸟", "caption":"图中鸟站在岩壁"}
    command = {"op":"mount", "block_id":"d", "component":"document", **declaration([
        {"id":"figure", "type":"figure", "caption":"图中都是站立姿态"}, node])}
    state = compile_command(command).model_dump(mode="json")
    state['blocks'][1].update(media=[{"url":"https://photo.example/bird.jpg", "title":"Bird in flight"}], resource_state="ready")
    state = Snapshot.model_validate(state).model_dump(mode="json")
    assert state['blocks'][1]['alt'] == 'Bird in flight'
    assert state['blocks'][1]['caption'] == state['blocks'][0]['caption'] == ''
    patched = compile_command({"op":"patch", "block_id":"d", "upsert":{"blocks":[{**node, "caption":"仍然站立"}]}}, state)
    assert patched.blocks[1].alt == 'Bird in flight' and patched.blocks[1].caption == ''


def test_resource_queries_reuse_provider_connection_until_last_lease_exits():
    import copy
    import httpx
    from app.providers.remote.image_search import ImageSearchProvider
    provider = ImageSearchProvider(provider_id='one', provider_type='openverse_image_search',
        base_url='https://api.example', transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    parent = provider.resource_http_session()
    client = parent.__enter__()
    child = copy.copy(provider).resource_http_session()
    assert child.__enter__() is client
    parent.__exit__(None, None, None)
    assert not client.is_closed  # a late query still owns its lease
    child.__exit__(None, None, None)
    assert client.is_closed
    with provider.resource_http_session() as next_client:
        assert next_client is not client and not next_client.is_closed


def test_parallel_image_nodes_can_send_metadata_without_pool_starvation():
    import copy
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Event, Lock, Thread
    from app.providers.remote.image_search import ImageSearchProvider

    arrived, lock = [], Lock()
    release = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            with lock:
                arrived.append(self.path)
                if len(arrived) == 3:
                    release.set()
            ok = release.wait(2)
            body = b'{"ok":true}'
            self.send_response(200 if ok else 503)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
    thread.start()
    provider = ImageSearchProvider(provider_id='parallel', provider_type='openverse_image_search',
        base_url=f'http://127.0.0.1:{server.server_port}', timeout_seconds=3)

    def request(index):
        worker = copy.copy(provider)
        with worker.resource_http_session() as client:
            worker.resource_http_client = client
            return worker._get_json(f'{provider.base_url}/{index}')

    try:
        with provider.resource_http_session(), ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(request, i) for i in range(3)]
            assert [f.result(timeout=4) for f in futures] == [{'ok': True}] * 3
        assert len(arrived) == 3
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_media_jobs_only_search_when_authorized_and_do_not_block(monkeypatch):
    from app.services.rich_media import MediaJobs, search_media
    import app.services.rich_media as module
    calls = []
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    content = BytesIO()
    Image.new("RGB", (2, 2), "yellow").save(content, format="PNG")
    monkeypatch.setattr(module.PublicImageDownloadClient, "_download", lambda *_args, **_kwargs: SimpleNamespace(data=content.getvalue()))
    class Provider:
        def image_search(self, query):
            calls.append(query)
            return [{"url": "https://images.example.com/saturn.jpg", "source_url": "https://example.com/saturn", "title": "Saturn"}]
    monkeypatch.setattr(module, "require_public_http_url", lambda *_: "93.184.216.34")
    jobs = MediaJobs()
    snapshot = {"block_id": "doc", "blocks": [{"id": "image", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "Saturn", "resource_state": "pending"}]}
    jobs.schedule(snapshot, Provider(), False, set())
    assert list(jobs.poll())[0][1] == [] and not calls
    assert search_media(Provider(), "Saturn", set())[0]["title"] == "Saturn"
    monkeypatch.setattr(module, "require_public_http_url", lambda *_: (_ for _ in ()).throw(ValueError("private host")))
    assert search_media(Provider(), "private", set()) == []


def test_bad_search_result_does_not_hide_later_thumbnail_and_bytes_are_verified(monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    import app.services.rich_media as media
    content = BytesIO()
    Image.new("RGB", (3, 2), "green").save(content, format="PNG")
    calls = []
    class Provider:
        def image_search(self, query):
            return [{"url": "http://unsafe.example/image.jpg"},
                {"url": "https://photos.example/original.jpg", "thumbnail_url": "https://search.example/thumb",
                 "source_url": "https://photos.example/author", "title": "Pollination", "attribution": "Author · CC BY 2.0"}]
    def download(_self, url, **kwargs):
        calls.append((url, kwargs["allowed_hosts"]))
        return SimpleNamespace(data=content.getvalue())
    monkeypatch.setattr(media, "require_public_http_url", lambda *_: "93.184.216.34")
    monkeypatch.setattr(media.PublicImageDownloadClient, "_download", download)
    result = media.search_media(Provider(), "flower", set(), limit=1)
    assert calls == [("https://search.example/thumb", {"photos.example", "search.example"})]
    assert result[0]["attribution"] == "Author · CC BY 2.0"
    assert result[0]["_mime"] == "image/png"
    with Image.open(BytesIO(result[0]["_data"])) as image:
        assert image.size == (3, 2)
    monkeypatch.setattr(media.PublicImageDownloadClient, "_download", lambda *_a, **_kw: SimpleNamespace(data=b"<svg onload='alert(1)'/>"))
    assert media.search_media(Provider(), "flower", set(), limit=1) == []


def test_media_fallback_preserves_model_queries_and_reports_real_failure(monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    import app.services.rich_media as media
    content = BytesIO()
    Image.new("RGB", (3, 2), "green").save(content, format="PNG")
    calls = []
    class Provider:
        def image_search(self, query):
            calls.append(query)
            return [] if query == "preferred" else [{"url": "https://photos.example/image.jpg"}]
    monkeypatch.setattr(media, "require_public_http_url", lambda *_: "93.184.216.34")
    monkeypatch.setattr(media.PublicImageDownloadClient, "_download", lambda *_a, **_kw: SimpleNamespace(data=content.getvalue()))
    result = media.search_media(Provider(), "preferred", set(), alternatives=["same subject", "unused"], limit=1)
    assert result.reason == "ready" and len(result) == 1
    assert calls == ["preferred", "same subject"]
    assert media.search_media(Provider(), "preferred", set()).reason == "no_results"
    monkeypatch.setattr(media.PublicImageDownloadClient, "_download", lambda *_a, **_kw: SimpleNamespace(data=b"not an image"))
    assert media.search_media(Provider(), "same subject", set()).reason == "download_failed"


def test_resource_diagnostic_cannot_be_forged_and_alternative_change_invalidates_cache():
    raw = {"op": "mount", "block_id": "doc", "component": "document", "props": {"title": "Subject"}, "upsert": {"blocks": [
        {"id": "pic", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "subject", "resource_reason": "no_results"}]}}
    with pytest.raises(AppError):
        compile_command(raw)
    raw["upsert"]["blocks"][0].pop("resource_reason")
    state = compile_command(raw).model_dump(mode="json")
    state["blocks"][0].update(resource_state="unavailable", resource_reason="no_results")
    patched = compile_command({"op": "patch", "block_id": "doc", "upsert": {"blocks": [
        {"id": "pic", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "subject", "query_alternatives": ["variant"]}]}}, state)
    assert patched.blocks[0].resource_state == "pending" and not patched.blocks[0].resource_reason


def test_unexpected_worker_failure_and_missing_provider_do_not_break_answer():
    from concurrent.futures import Future
    import time
    from app.services.rich_media import MediaJobs, search_media
    class Broken:
        def image_search(self, query):
            return None
    assert search_media(Broken(), "subject", set()).reason == "search_failed"
    jobs = MediaJobs()
    future = Future()
    future.set_exception(RuntimeError("private provider error"))
    jobs.jobs[("doc", "pic", "subject", ())] = (future, time.monotonic())
    assert list(jobs.poll())[0][1].reason == "search_failed"
    jobs.schedule({"block_id": "doc", "blocks": [{"id": "pic", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "subject", "resource_state": "pending"}]}, object(), True, set())
    assert list(jobs.poll())[0][1].reason == "provider_unavailable"


def test_openverse_rejects_unrelated_description_hits_and_ranks_explicit_titles():
    from app.services.rich_media import _relevant_openverse_images
    images = [{"title": "Blue Flowers"}, {"title": "Potato Plants 001"}, {"title": "Prosperity for Ukraine"},
              {"title": "Tomato flower"}, {"title": "Tomato flower cluster"}]
    assert [item["title"] for item in _relevant_openverse_images(images, "tomato flower cluster")] == ["Tomato flower cluster", "Tomato flower"]
    assert _relevant_openverse_images([{"title": "Green Tomatoes on Vine"}], "green tomato fruit cluster")
    assert not _relevant_openverse_images(images, "unrelated subject")
    assert _relevant_openverse_images([{"title": "Maple leaf icon"}], "maple leaf") == []
    assert _relevant_openverse_images([{"title": "Maple leaf icon"}], "maple leaf icon")
    # Equally matching variants must not outrank the plain queried subject
    # just because the provider listed a branded edition first.
    candidates = [{"title": "Custom Subject front view edition"}, {"title": "Subject front view"}]
    assert _relevant_openverse_images(candidates, "subject front view")[0] == candidates[1]
    # A city's different bridge contains the same words, in a different role.
    candidates = [{"title":"Hungerford Bridge, River Thames, London, England"},
        {"title":"London Bridge over the Thames"}]
    assert _relevant_openverse_images(candidates, 'London Bridge London') == [candidates[1]]
    candidates = [{"title":"London Bridge (Tower Bridge): Reflection"}, {"title":"London Bridge"}]
    assert _relevant_openverse_images(candidates, 'London Bridge London', ['Tower Bridge London']) == [candidates[1]]
    # An unrelated subject's location token must not exclude an actual object.
    assert _relevant_openverse_images([{"title":"Big Ben London"}], 'Big Ben', ['Houses Parliament London'])


def test_illustration_source_preference_falls_back_without_changing_explicit_search():
    import httpx
    from app.providers.remote.image_search import ImageSearchProvider
    calls = []
    def respond(request):
        calls.append(dict(request.url.params))
        return httpx.Response(200, json={"results": [] if request.url.params.get("source") else [
            {"title": "Subject stage", "url": "https://images.example/photo.jpg"}]})
    provider = ImageSearchProvider(provider_id="test", provider_type="openverse_image_search",
        base_url="https://api.example", transport=httpx.MockTransport(respond))
    assert provider.image_search("subject stage")
    assert "source" not in calls[0]
    provider.prefer_catalogued_images = True
    assert provider.image_search("subject stage")
    assert calls[1]["source"] == "wikimedia" and "category" not in calls[1] and "source" not in calls[2]


def test_commons_photo_with_null_category_does_not_fall_back_to_stock():
    import httpx
    from app.providers.remote.image_search import ImageSearchProvider
    calls = []
    def respond(request):
        calls.append(dict(request.url.params))
        # Actual catalogue shape: Commons photographs are not categorized.
        return httpx.Response(200, json={"results": [] if request.url.params.get("category") else [
            {"title": "Subject", "category": None, "url": "https://images.example/subject.jpg"}]})
    provider = ImageSearchProvider(provider_id="test", provider_type="openverse_image_search",
        base_url="https://api.example", transport=httpx.MockTransport(respond))
    provider.prefer_catalogued_images = True
    assert provider.image_search("subject")[0]["title"] == "Subject"
    assert calls == [{"q": "subject", "page_size": "12", "source": "wikimedia"}]


def test_candidate_budget_is_independent_of_display_and_explicit_search_limit(monkeypatch):
    import app.services.rich_media as media
    import httpx
    from io import BytesIO
    from PIL import Image
    from types import SimpleNamespace
    from app.providers.remote.image_search import ImageSearchProvider
    calls = []
    candidates = [{"title": "Custom Subject variant", "url": "https://photo.example/other.jpg"}] * 5 + [
        {"title": "Subject", "url": "https://photo.example/plain.jpg"}]
    def respond(request):
        size = int(request.url.params['page_size'])
        calls.append(size)
        return httpx.Response(200, json={"results": candidates[:size]})
    provider = ImageSearchProvider(provider_id="test", provider_type="openverse_image_search",
        base_url="https://api.example", max_results=5, transport=httpx.MockTransport(respond))
    content = BytesIO()
    Image.new("RGB", (3, 2), "green").save(content, format="PNG")
    monkeypatch.setattr(media, "require_public_http_url", lambda *_: "93.184.216.34")
    monkeypatch.setattr(media.PublicImageDownloadClient, "_download", lambda *_a, **_kw: SimpleNamespace(data=content.getvalue()))
    result = media.search_media(provider, "subject", set(), limit=1)
    assert len(result) == 1 and result[0]['title'] == 'Subject'
    assert calls == [12] and provider.max_results == 5


def test_unrequested_artifacts_do_not_masquerade_as_subject_photographs():
    from app.services.rich_media import _relevant_openverse_images
    for kind in ['sticker', 'label', 'packaging', 'painting', 'toy']:
        item = {"title": f"Subject {kind}"}
        assert not _relevant_openverse_images([item], "subject")
        assert _relevant_openverse_images([item], f"subject {kind}")


def test_stalled_primary_does_not_block_first_response_alternative(monkeypatch):
    import threading
    import time
    import httpx
    from io import BytesIO
    from PIL import Image
    from types import SimpleNamespace
    from app.providers.remote.image_search import ImageSearchProvider
    import app.services.rich_media as media
    release = threading.Event()
    entered = threading.Event()
    calls = []
    def respond(request):
        query = request.url.params['q']
        calls.append(query)
        if query == 'subject primary':
            entered.set()
            release.wait(2)
            return httpx.Response(200, json={"results": []})
        return httpx.Response(200, json={"results": [
            {"title": "Subject alternative", "url": "https://photo.example/plain.jpg"}]})
    provider = ImageSearchProvider(provider_id="test", provider_type="openverse_image_search",
        base_url="https://api.example", transport=httpx.MockTransport(respond))
    content = BytesIO()
    Image.new("RGB", (3, 2), "green").save(content, format="PNG")
    monkeypatch.setattr(media, "_SEARCH_HEDGE_SECONDS", .01)
    monkeypatch.setattr(media, "require_public_http_url", lambda *_: "93.184.216.34")
    monkeypatch.setattr(media.PublicImageDownloadClient, "_download", lambda *_a, **_kw: SimpleNamespace(data=content.getvalue()))
    start = time.monotonic()
    try:
        result = media.search_media(provider, "subject primary", set(), alternatives=["subject alternative"], limit=1)
        assert entered.is_set() and not release.is_set()
        assert result.reason == "ready" and result[0]['title'] == 'Subject alternative'
        assert time.monotonic() - start < 1
        assert calls[:2] == ['subject primary', 'subject alternative']
    finally:
        release.set()


def test_rate_limited_catalogue_switches_origin_within_first_response(monkeypatch):
    import httpx
    from io import BytesIO
    from PIL import Image
    from types import SimpleNamespace
    from app.core.errors import AppError
    from app.providers.remote.image_search import ImageSearchProvider
    import app.services.rich_media as media
    monkeypatch.setattr(media, '_HOST_COOLDOWNS', {})
    calls, downloads = [], []
    def respond(request):
        source = request.url.params.get('source')
        calls.append(source)
        origin = 'catalogue.example' if source else 'photo.example'
        return httpx.Response(200, json={"results": [
            {"title": "Subject", "url": f"https://{origin}/plain.jpg"}]})
    provider = ImageSearchProvider(provider_id="test", provider_type="openverse_image_search",
        base_url="https://api.example", transport=httpx.MockTransport(respond))
    content = BytesIO()
    Image.new("RGB", (3, 2), "green").save(content, format="PNG")
    def download(self, url, **_kwargs):
        downloads.append(url)
        if 'catalogue.example' in url:
            raise AppError(502, 'external_download_http_error', 'Remote server returned HTTP 429',
                {'remote_status':429, 'retry_after':'120'})
        return SimpleNamespace(data=content.getvalue())
    monkeypatch.setattr(media, 'require_public_http_url', lambda *_: '93.184.216.34')
    monkeypatch.setattr(media.PublicImageDownloadClient, '_download', download)
    result = media.search_media(provider, 'subject', set(), limit=1)
    assert result.reason == 'ready' and result[0]['url'] == 'https://photo.example/plain.jpg'
    assert calls == ['wikimedia', None]
    assert downloads == ['https://catalogue.example/plain.jpg', 'https://photo.example/plain.jpg']
    # A second resource observes the cooldown without another blocked download.
    assert media.search_media(provider, 'subject', set(), limit=1).reason == 'ready'
    assert downloads.count('https://catalogue.example/plain.jpg') == 1


def test_download_host_cooldown_expires_and_honors_http_date(monkeypatch):
    import app.services.rich_media as media
    from email.utils import format_datetime
    from datetime import datetime, timezone, timedelta
    monkeypatch.setattr(media, '_HOST_COOLDOWNS', {})
    clock = [100.0]
    monkeypatch.setattr(media.time, 'monotonic', lambda: clock[0])
    media._cool_down_host('photos.example', '120')
    clock[0] = 219
    assert media._host_cooling_down('photos.example')
    clock[0] = 221
    assert not media._host_cooling_down('photos.example')
    media._cool_down_host('dated.example', format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120)))
    assert 110 < media._HOST_COOLDOWNS['dated.example'] - clock[0] <= 120


def test_api_thumbnail_negotiation_and_failed_thumbnail_preserve_original(monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    import app.services.rich_media as media
    content = BytesIO()
    Image.new('RGB', (3, 2), 'green').save(content, format='PNG')
    downloads = []
    class Provider:
        def image_search(self, query):
            return [{'url':'https://photos.example/original.jpg', 'thumbnail_url':'https://api.example/thumb/'}]
    def download(self, url, **kwargs):
        downloads.append(url)
        assert '*/*' in kwargs['accept']  # API negotiation must not force a 406.
        if 'api.example' in url:
            # A negotiated endpoint can still return a JSON error body.
            return SimpleNamespace(data=b'{"detail":"upstream unavailable"}')
        return SimpleNamespace(data=content.getvalue())
    monkeypatch.setattr(media, '_HOST_COOLDOWNS', {})
    monkeypatch.setattr(media, 'require_public_http_url', lambda *_: '93.184.216.34')
    monkeypatch.setattr(media.PublicImageDownloadClient, '_download', download)
    progress = {}
    result = media.search_media(Provider(), 'subject', set(), limit=1, progress=progress)
    assert result.reason == 'ready' and len(result) == 1
    assert downloads == ['https://api.example/thumb/', 'https://photos.example/original.jpg']
    assert progress['last_error'] == 'external_image_invalid'


def test_metadata_rate_limit_stops_alternatives_and_honors_shared_cooldown(monkeypatch):
    import httpx
    from app.providers.remote.image_search import ImageSearchProvider
    import app.services.rich_media as media
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(429, headers={'retry-after':'120'}, json={'detail':'rate limited'})
    provider = ImageSearchProvider(provider_id='test', provider_type='openverse_image_search',
        base_url='https://api.example', transport=httpx.MockTransport(respond))
    monkeypatch.setattr(media, '_HOST_COOLDOWNS', {})
    for _ in range(2):
        progress = {}
        result = media.search_media(provider, 'subject', set(), alternatives=['other subject'], progress=progress)
        assert result.reason == 'search_failed' and not result
        assert progress['last_error'] == 'HTTP429' and progress['search_rate_limited']
    assert len(calls) == 1
    assert media._HOST_COOLDOWNS['api.example'] > media.time.monotonic() + 110


def test_metadata_http_error_survives_worker_boundary(monkeypatch):
    import httpx
    from app.providers.remote.image_search import ImageSearchProvider
    import app.services.rich_media as media
    monkeypatch.setattr(media, '_HOST_COOLDOWNS', {})
    provider = ImageSearchProvider(provider_id='test', provider_type='openverse_image_search',
        base_url='https://api.example', transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    progress = {}
    assert media.search_media(provider, 'private subject', set(), progress=progress).reason == 'search_failed'
    assert progress['last_error'] == 'HTTP503'


def test_openverse_api_thumbnail_does_not_delay_a_healthy_original(monkeypatch):
    import httpx
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    from app.providers.remote.image_search import ImageSearchProvider
    import app.services.rich_media as media
    content = BytesIO()
    Image.new('RGB', (3, 2), 'green').save(content, format='PNG')
    provider = ImageSearchProvider(provider_id='test', provider_type='openverse_image_search',
        base_url='https://api.example', transport=httpx.MockTransport(lambda _: httpx.Response(200,json={'results':[
            {'title':'Subject', 'url':'https://photos.example/original.jpg','thumbnail':'https://api.example/thumb/'}]})))
    downloads = []
    def download(self, url, **kwargs):
        downloads.append(url)
        return SimpleNamespace(data=content.getvalue())
    monkeypatch.setattr(media, '_HOST_COOLDOWNS', {})
    monkeypatch.setattr(media, 'require_public_http_url', lambda *_: '93.184.216.34')
    monkeypatch.setattr(media.PublicImageDownloadClient, '_download', download)
    assert media.search_media(provider,'subject',set(),limit=1).reason == 'ready'
    assert downloads == ['https://photos.example/original.jpg']


def test_search_fallback_cannot_consume_reserved_download_budget(monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    import app.services.rich_media as media
    clock = [0.0]
    monkeypatch.setattr(media.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(media, "require_public_http_url", lambda *_: "93.184.216.34")
    content = BytesIO()
    Image.new("RGB", (3, 2), "green").save(content, format="PNG")
    class Provider:
        def image_search(self, query):
            assert self.resource_deadline == media.SEARCH_BUDGET_SECONDS
            clock[0] = media.SEARCH_BUDGET_SECONDS - .1
            return [{"url": "https://photos.example/subject.jpg"}]
    def download(self, *_args, **_kwargs):
        assert self.deadline > media.SEARCH_BUDGET_SECONDS
        clock[0] += 2
        return SimpleNamespace(data=content.getvalue())
    monkeypatch.setattr(media.PublicImageDownloadClient, "_download", download)
    assert media.search_media(Provider(), "subject", set(), limit=1).reason == "ready"


def test_query_on_layout_is_rejected_instead_of_silently_losing_the_picture():
    with pytest.raises(AppError, match="child image node"):
        compile_command({"op": "mount", "block_id": "d", "component": "document", "props": {"title": "Test"},
            "upsert": {"blocks": [{"id": "layout", "type": "media_text", "query": "subject stage"}]}})
    state = compile_command({"op": "mount", "block_id": "d", "component": "document", "props": {"title": "Test"},
        "upsert": {"blocks": [{"id": "layout", "type": "media_text"},
            {"id": "pic", "parent_id": "layout", "type": "image", "fallback_svg": FALLBACK_SVG, "query": "subject stage"}]}})
    assert state.blocks[1].query == "subject stage"


def test_slow_thumbnail_leaves_budget_for_another_candidate(monkeypatch):
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    import app.services.rich_media as media
    clock = [0.0]
    monkeypatch.setattr(media.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(media, "require_public_http_url", lambda *_: "93.184.216.34")
    content = BytesIO()
    Image.new("RGB", (3, 2), "green").save(content, format="PNG")
    deadlines = []
    class Provider:
        def image_search(self, query):
            return [{"url": "https://photo.example/original.jpg", "thumbnail_url": "https://photo.example/thumb.jpg"}]
    def download(self, url, **kwargs):
        deadlines.append(self.deadline)
        if len(deadlines) == 1:
            clock[0] = self.deadline + .01
            raise TimeoutError()
        return SimpleNamespace(data=content.getvalue())
    monkeypatch.setattr(media.PublicImageDownloadClient, "_download", download)
    result = media.search_media(Provider(), "subject", set(), limit=1)
    assert result.reason == "ready" and len(result) == 1
    assert deadlines[0] == 3 and 3 < deadlines[1] < media.RESOURCE_BUDGET_SECONDS


def test_provider_source_fallback_cannot_start_after_resource_deadline(monkeypatch):
    import httpx
    import app.providers.remote.image_search as module
    from app.providers.remote.search import SearchProviderTimeout
    clock = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    calls = []
    def respond(request):
        calls.append(request)
        clock[0] = 26.0
        return httpx.Response(200, json={"results": []})
    provider = module.ImageSearchProvider(provider_id="test", provider_type="openverse_image_search",
        base_url="https://api.example", transport=httpx.MockTransport(respond))
    provider.prefer_catalogued_images = True
    provider.resource_deadline = 25.0
    with pytest.raises(SearchProviderTimeout):
        provider.image_search("subject stage")
    assert len(calls) == 1


def test_diagnostic_timeout_distinguishes_queued_and_running_search(caplog, monkeypatch):
    from concurrent.futures import Future
    import app.services.rich_media as media
    clock = [0.0]
    monkeypatch.setattr(media.time, "monotonic", lambda: clock[0])
    jobs = media.MediaJobs()
    key = ("doc", "pic", "private query must not appear in logs", ())
    jobs.jobs[key] = (Future(), 0.0)
    jobs.progress[key] = {"phase": "search", "started": 2.0}
    clock[0] = media.RESOURCE_BUDGET_SECONDS + 2
    assert list(jobs.poll())[0][1].reason == "timeout"
    assert "phase=search" in caplog.text and "queue=2.00" in caplog.text
    assert key[2] not in caplog.text and not jobs.progress


def test_document_catalog_example_compiles_with_the_runtime_schema():
    import json
    from app.services.rich_response import _DOCUMENT_CATALOG
    from app.services.stream_ui import compile_command
    example = next(line for line in _DOCUMENT_CATALOG.splitlines() if line.startswith('{"props":{"title":"分析报告"'))
    snapshot = compile_command({"op": "mount", "block_id": "example", "component": "document", **json.loads(example)})
    assert snapshot.props.toc is True
    assert {"metric", "chart", "heading"} <= {node.type for node in snapshot.blocks}
