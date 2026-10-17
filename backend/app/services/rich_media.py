"""Bounded, asynchronous image search using the workspace's existing provider.

Workers receive no database session. Only the streaming thread may publish a
result, after rechecking its node id, query, and current snapshot revision.
"""
from __future__ import annotations

import copy
import hashlib
import logging
import re
import time
import httpx
from threading import BoundedSemaphore, Lock
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from urllib.parse import urlsplit

from app.providers.remote.fetch import UnsafeFetchURL, require_public_http_url
from app.core.config import get_settings
from app.services.external_acquisition import PublicImageDownloadClient

_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rich-media")
_CAPACITY = BoundedSemaphore(32)
_SEARCH_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="rich-image-query")
_SEARCH_HEDGE_SECONDS = 1.5
_HOST_COOLDOWNS = {}
_HOST_COOLDOWN_LOCK = Lock()


def _host_cooling_down(host):
    with _HOST_COOLDOWN_LOCK:
        return _HOST_COOLDOWNS.get(host, 0) > time.monotonic()


def _cool_down_host(host, retry_after):
    try:
        seconds = float(retry_after)
    except (TypeError, ValueError):
        try:
            seconds = (parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            seconds = 60
    with _HOST_COOLDOWN_LOCK:
        now = time.monotonic()
        for key in list(_HOST_COOLDOWNS):
            if _HOST_COOLDOWNS[key] <= now:
                del _HOST_COOLDOWNS[key]
        if len(_HOST_COOLDOWNS) >= 128 and host not in _HOST_COOLDOWNS:
            del _HOST_COOLDOWNS[min(_HOST_COOLDOWNS, key=_HOST_COOLDOWNS.get)]
        _HOST_COOLDOWNS[host] = max(_HOST_COOLDOWNS.get(host, 0), now + max(1, seconds))


class MediaResult(list):
    """Private worker outcome; only an enumerated reason enters the snapshot."""
    def __init__(self, values=(), *, reason="no_results"):
        super().__init__(values)
        self.reason = reason


RESOURCE_BUDGET_SECONDS = 25
SEARCH_BUDGET_SECONDS = 20


def _error_name(exc):
    """Keep diagnostic status/codes, never query URLs, credentials or bodies."""
    status = getattr(exc, "details", {}).get("remote_status")
    if isinstance(status, int):
        return f"HTTP{status}"
    code = getattr(exc, "code", None)
    status = getattr(exc, "status_code", None)
    return code or (f"HTTP{status}" if isinstance(status, int) else type(exc.__cause__ or exc).__name__)


def _relevant_openverse_images(images, query, contrast_queries=()):
    """Descriptions can mention unrelated subjects. Prefer explicit titles.

    Conservative lexical evidence, not a claim of vision verification. No
    topic-specific words or replacement pictures are supplied by the host.
    """
    def words(text):
        return [word[:-2] if word.endswith("oes") else word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
                for word in re.findall(r"[a-z0-9]+", text.casefold())
                if word not in {"a", "an", "the", "of", "in", "on", "with", "for", "to", "from", "and", "at", "by"}]
    terms = words(query)
    if not terms:
        return []
    def score(image):
        title_words = words(str(image.get("title", "")))
        title = set(title_words)
        # Names of flags, logos and icons can contain a physical subject's
        # name. Keep those artifacts only when the query requests their type.
        if (title - set(terms)) & {"icon", "logo", "flag", "symbol", "emblem", "map",
                "sticker", "label", "packaging", "advertisement", "wallpaper", "painting",
                "illustration", "sculpture", "toy", "replica"}:
            return 0
        matches = len(set(terms) & title)
        # For separately requested subjects of the same kind, reject titles
        # that also name the other subject. A misleading alias or a composite
        # comparison is not a clear individual example. No entities are fixed
        # in host code: contrasts come from this document's other queries.
        for contrast in contrast_queries:
            other = words(contrast)
            if (len(terms) >= 2 and len(other) >= 2 and terms[1] == other[1] and terms[0] != other[0]
                    and any(title_words[i:i + 2] == other[:2] for i in range(len(title_words) - 1))):
                return 0
        # A multiword subject is not a bag of unrelated words: e.g. a
        # different bridge followed by a city name must not match that city's
        # named bridge. Preserve the query's leading subject word order.
        ordered = terms[0] in title and (len(terms) < 2 or terms[1] in title_words[title_words.index(terms[0]) + 1:])
        return matches if ordered and matches >= min(2, len(set(terms))) else 0
    return sorted((item for item in images if isinstance(item, dict) and score(item)),
        key=lambda item: (-score(item), len(set(words(str(item.get("title", "")))) - set(terms))))


def _candidate_batches(provider, queries, progress):
    """Hedge a stalled catalogue request with a model-supplied alternative.

    At most two searches are in flight per resource, across a bounded pool.
    Fast primary requests incur no additional search. Each worker owns its
    HTTP client, so cancelled/late work cannot use a closed client or a DB.
    """
    if getattr(provider, "provider_type", "") != "openverse_image_search":
        for query in queries:
            remaining = provider.resource_deadline - time.monotonic()
            if remaining <= 0:
                break
            provider.timeout_seconds = min(provider.timeout_seconds, remaining)
            try:
                yield query, provider.image_search(query)
            except Exception:
                yield query, None
        return

    def search(worker, query):
        worker.resource_progress = {}
        if time.monotonic() >= worker.resource_deadline:
            raise TimeoutError("Image search deadline exceeded")
        session = getattr(worker, "resource_http_session", None)
        with (session() if callable(session) else httpx.Client(transport=getattr(worker, "transport", None), follow_redirects=True)) as client:
            worker.resource_http_client = client
            return worker.image_search(query)

    pending = {}
    index = 0
    search_host = urlsplit(provider.base_url).hostname
    try:
        while pending or index < len(queries):
            if _host_cooling_down(search_host):
                progress.update(last_error="HTTP429", search_rate_limited=True)
                break
            remaining = provider.resource_deadline - time.monotonic()
            if remaining <= 0:
                break
            if not pending:
                pending[_SEARCH_POOL.submit(search, copy.copy(provider), queries[index])] = index
                index += 1
            progress["phase"] = "search_wait"
            done, _ = wait(pending, timeout=min(_SEARCH_HEDGE_SECONDS, remaining), return_when=FIRST_COMPLETED)
            if not done:
                if index < len(queries) and len(pending) < 2:
                    pending[_SEARCH_POOL.submit(search, copy.copy(provider), queries[index])] = index
                    index += 1
                continue
            for future in sorted(done, key=lambda item: pending[item]):
                query = queries[pending.pop(future)]
                try:
                    images = future.result()
                except Exception as exc:
                    progress["last_error"] = _error_name(exc)
                    if getattr(exc, "status_code", None) == 429:
                        _cool_down_host(search_host, getattr(exc, "retry_after", ""))
                        progress["search_rate_limited"] = True
                    images = None
                yield query, images
                if progress.get("search_rate_limited"):
                    return
    finally:
        for future in pending:
            future.cancel()


def search_media(provider, query: str, allowed_domains: set[str], *, limit=6, settings=None, alternatives=(), progress=None, contrast_queries=()) -> list[dict]:
    """Resolve real search results into sanitized bytes, without a DB session.

    Prefer CDN thumbnails; API thumbnail proxies remain a backup when their
    originals are healthy. Provenance and every download hop stay checked.
    Bytes remain private to the worker result; only file IDs reach SSE.
    """
    settings = settings or get_settings()
    results = MediaResult()
    progress = progress if progress is not None else {}
    progress.update(started=time.monotonic(), phase="initializing")
    deadline = time.monotonic() + RESOURCE_BUDGET_SECONDS
    search_deadline = time.monotonic() + SEARCH_BUDGET_SECONDS
    downloader = PublicImageDownloadClient(settings, deadline=deadline)
    batches = None
    search_session = None
    try:
        # A separate shallow provider copy avoids mutating shared configuration.
        provider = copy.copy(provider)
        if getattr(provider, "provider_type", "") == "openverse_image_search":
            provider.prefer_catalogued_images = True
        provider.timeout_seconds = min(getattr(provider, "timeout_seconds", 8), 8)
        # Metadata lookup must not spend the download budget. In particular,
        # source fallback and alternate queries share this deadline.
        provider.resource_deadline = search_deadline
        provider.resource_progress = progress
        progress["provider"] = type(provider).__name__
        session = getattr(provider, "resource_http_session", None)
        if getattr(provider, "provider_type", "") == "openverse_image_search" and callable(session):
            progress["phase"] = "search_client"
            search_session = session()
            search_session.__enter__()
        if hasattr(provider, "max_results"):
            # Inspect metadata before downloading: Openverse can rank an
            # unrelated description match ahead of an explicit title match.
            # This is a metadata candidate budget, not the number of pictures
            # shown. A user's small explicit-search limit must not truncate
            # candidates before relevance ranking (often hiding the plain
            # subject behind several named variants).
            provider.max_results = min(20, max(12, limit)) if getattr(provider, "provider_type", "") == "openverse_image_search" else min(provider.max_results, max(3, limit))
        queries = list(dict.fromkeys([query, *alternatives]))[:3]
        def source_batches():
            # A catalogue can return accurate metadata while its image origin
            # is rate limited. Reuse the configured provider's general lane,
            # rather than repeatedly downloading from that same origin.
            for catalogued in ([True, False] if getattr(provider, "provider_type", "") == "openverse_image_search" else [False]):
                provider.prefer_catalogued_images = catalogued
                provider.resource_deadline = min(search_deadline, time.monotonic() + 10) if catalogued else search_deadline
                lane = _candidate_batches(provider, queries, progress)
                try:
                    for batch in lane:
                        yield batch
                        if catalogued and progress.pop("origin_limited", False):
                            break
                finally:
                    lane.close()
                if progress.get("search_rate_limited"):
                    break
        batches = source_batches()
        for candidate_query, images in batches:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                results.reason = "timeout"
                break
            provider.timeout_seconds = min(provider.timeout_seconds, remaining)
            try:
                progress["phase"] = "search"
                if not isinstance(images, list):
                    raise ValueError("Invalid image search response")
                if getattr(provider, "provider_type", "") == "openverse_image_search":
                    images = _relevant_openverse_images(images, candidate_query, contrast_queries)
            except Exception:
                if results.reason == "no_results":
                    results.reason = "search_failed"
                continue
            if images:
                results.reason = "download_failed"
            for image in images[:12]:
                if time.monotonic() >= deadline:
                    break
                try:
                    url = image.get("url", "")
                    source = image.get("source_url", "")
                    def validate(value):
                        parsed = urlsplit(value)
                        if (len(value) > 2000 or parsed.scheme != "https" or parsed.username or parsed.password
                                or parsed.port or "." not in (parsed.hostname or "")
                                or parsed.path.lower().endswith(".svg")):
                            raise ValueError("Unsupported image URL")
                        require_public_http_url(value, allowed_domains)
                        return parsed.hostname
                    progress["phase"] = "host_validation"
                    hosts = {validate(url)}
                    if source:
                        hosts.add(validate(source))
                    candidates = [url]
                    thumbnail = image.get("thumbnail_url")
                    if thumbnail:
                        try:
                            hosts.add(validate(thumbnail))
                            # Openverse's API thumbnail proxy can fail with
                            # 424 even while the origin is healthy. Keep it as
                            # a backup; other providers' CDN thumbnails still
                            # come first to avoid fetching oversized originals.
                            if (getattr(provider, "provider_type", "") == "openverse_image_search"
                                    and urlsplit(thumbnail).hostname == urlsplit(provider.base_url).hostname):
                                candidates.append(thumbnail)
                            else:
                                candidates.insert(0, thumbnail)
                        except (ValueError, TypeError, UnsafeFetchURL):
                            pass
                    for candidate in dict.fromkeys(candidates):
                        host = urlsplit(candidate).hostname
                        if _host_cooling_down(host):
                            progress["origin_limited"] = True
                            continue
                        try:
                            progress["phase"] = "download"
                            # One slow host must leave time for later images
                            # and the model's alternative queries.
                            downloader.deadline = min(deadline, time.monotonic() + 3)
                            response = downloader._download(candidate, allowed_hosts=hosts,
                                # API-backed thumbnail endpoints negotiate a
                                # renderer before returning image bytes. An
                                # image-only Accept causes an avoidable 406;
                                # decoded raster validation remains mandatory.
                                max_bytes=min(settings.external_image_download_max_bytes, 2 * 1024 * 1024), accept="image/*,*/*;q=0.8")
                            data, mime, *_ = downloader._sanitize_image(response.data)
                            if len(data) > 2 * 1024 * 1024:
                                raise ValueError("Image resource too large")
                            results.reason = "ready"
                            results.append({"url": url, "source_url": source, "title": str(image.get("title", ""))[:500],
                                "attribution": str(image.get("attribution") or image.get("license") or "")[:500],
                                "_data": data, "_mime": mime})
                            break
                        except Exception as exc:
                            progress["last_error"] = _error_name(exc)
                            details = getattr(exc, "details", {})
                            if details.get("remote_status") == 429:
                                _cool_down_host(host, details.get("retry_after"))
                                progress["last_error"] = "HTTP429"
                                progress["origin_limited"] = True
                            if time.monotonic() >= deadline:
                                break
                except Exception as exc:
                    progress["last_error"] = _error_name(exc)
                    # A bad result must not discard safe later search results.
                    continue
                if len(results) >= limit:
                    break
                if provider.prefer_catalogued_images and progress.get("origin_limited"):
                    break
            if results:
                break
        if not results and progress.get("search_rate_limited"):
            results.reason = "search_failed"
        if not results and (time.monotonic() >= deadline or
                (time.monotonic() >= search_deadline and results.reason in {"no_results", "search_failed"})):
            results.reason = "timeout"
    finally:
        progress["phase_at_finish"] = progress.get("phase", "unknown")
        progress["phase"] = "finished"
        downloader.close()
        if batches is not None:
            batches.close()
        if search_session is not None:
            search_session.__exit__(None, None, None)
    return results


def persist_media(db, workspace_id, actor_id, session_id, media, settings, *, retry_locked=False):
    """Publish through the existing workspace/blob/file services on the caller thread."""
    from sqlalchemy import func, select
    from app.domain.models import FileRecord
    from app.services.session_workspace import SessionWorkspaceService
    def store():
        with db.begin_nested():
            quota = int(settings.workspace_storage_quota_bytes or 0)
            if quota:
                used = db.scalar(select(func.coalesce(func.sum(FileRecord.size_bytes), 0)).where(
                    FileRecord.workspace_id == workspace_id, FileRecord.lifecycle_status != "deleted")) or 0
                if used + sum(len(item["_data"]) for item in media) > quota:
                    return []
            workspace = SessionWorkspaceService(db, workspace_id, actor_id, settings)
            result = []
            for item in media:
                data, mime = item["_data"], item["_mime"]
                extension = {"image/jpeg": "jpg", "image/webp": "webp", "image/png": "png"}[mime]
                digest = hashlib.sha256(data).hexdigest()
                view = workspace.put_bytes(chat_session_id=session_id,
                    path=f"resources/rich/{digest}.{extension}", data=data, mime_type=mime,
                    role="input", source="external_download", publish_file=True, commit=False)
                result.append({**{k: v for k, v in item.items() if not k.startswith("_")}, "file_id": view["file_id"]})
            return result
    try:
        if retry_locked:
            # Caller has committed its event buffer, so restarting this small
            # content-addressed write cannot discard uncommitted chat events.
            from app.core.database import retry_sqlite_locked
            return retry_sqlite_locked(db, store)
        return store()
    except Exception as exc:
        logging.getLogger(__name__).warning("Image resource storage failed: %s sqlite=%s",
            type(exc).__name__, getattr(getattr(exc, "orig", None), "sqlite_errorname", "none"))
        return []


class MediaJobs:
    def __init__(self):
        self.jobs: dict[tuple, tuple[Future, float]] = {}
        self.seen: set[tuple] = set()
        self.progress: dict[tuple, dict] = {}

    def schedule(self, snapshot: dict, provider, enabled: bool, domains: set[str], *, settings=None):
        for node in snapshot.get("blocks", []):
            if node["type"] not in {"image", "image_group", "gallery"} or node["resource_state"] != "pending":
                continue
            key = (snapshot["block_id"], node["id"], node["query"], tuple(node.get("query_alternatives", [])))
            if key in self.seen:
                continue
            self.seen.add(key)
            future = Future()
            progress = self.progress[key] = {"phase": "queued"}
            if enabled and provider is not None and callable(getattr(provider, "image_search", None)) and node["query"] and len(self.seen) <= 8 and _CAPACITY.acquire(blocking=False):
                future = _POOL.submit(search_media, provider, node["query"], domains, alternatives=node.get("query_alternatives", []),
                    limit=1 if node["type"] == "image" else 6, settings=settings, progress=progress,
                    contrast_queries=tuple(other.get("query", "") for other in snapshot.get("blocks", [])
                        if other["id"] != node["id"] and other["type"] in {"image", "image_group", "gallery"} and other.get("query")))
                future.add_done_callback(lambda _: _CAPACITY.release())
            else:
                reason = ("disabled" if not enabled else "provider_unavailable"
                          if provider is None or not callable(getattr(provider, "image_search", None))
                          else "no_results" if not node["query"] else "capacity")
                future.set_result(MediaResult(reason=reason))
            self.jobs[key] = (future, time.monotonic())

    def poll(self, *, finish=False):
        for key, (future, started) in list(self.jobs.items()):
            if future.done() or finish or time.monotonic() - started > RESOURCE_BUDGET_SECONDS + 1:
                del self.jobs[key]
                try:
                    result = future.result() if future.done() and not future.cancelled() else MediaResult(reason="timeout")
                except Exception:
                    result = MediaResult(reason="search_failed")
                future.cancel()
                progress = self.progress.pop(key, {})
                logger = logging.getLogger(__name__)
                (logger.info if result else logger.warning)(
                    "Rich image resource outcome=%s phase=%s elapsed=%.2f queue=%.2f provider=%s error=%s",
                    getattr(result, "reason", "ready" if result else "no_results"),
                    progress.get("phase_at_finish", progress.get("phase", "unknown")),
                    time.monotonic() - started, max(0, progress.get("started", time.monotonic()) - started),
                    progress.get("provider", "unknown"), progress.get("last_error", "none"),
                )
                yield key, result
