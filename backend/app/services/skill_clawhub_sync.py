"""ClawHub catalog sync into the local skill market cache (phase 3 aggregation).

Pulls the top ClawHub skills (documented API, anonymous reads) into
``skill_market_cache`` as discovery-only cards: no third-party files are
mirrored (``files_json`` stays empty, so the UI install button is disabled)
and every card links back to its ClawHub page for review.  ``nonSuspiciousOnly``
is requested so entries ClawHub itself has flagged never enter the cache.

Installs still resolve through the GitHub-pinned or archive import flows so
content stays hash-locked and reviewable.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError
from app.domain.extension_models import SkillMarketCacheEntry
from app.domain.models import new_id, utc_now

MAX_RESPONSE_BYTES = 512 * 1024
CLAWHUB_MARKET_PREFIX = "clawhub:"
CLAWHUB_RANK_BASE = 200


class ClawHubMarketSync:
    """Best-effort, settings-gated pull of ClawHub's catalog into the cache."""

    def __init__(self, db: Session, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    def refresh(self, *, limit: int = 24) -> dict[str, Any]:
        if not getattr(self.settings, "clawhub_enabled", False):
            return {"enabled": False, "fetched": 0, "upserted": 0}
        limit = max(1, min(int(limit or 24), 48))
        base = self.settings.clawhub_api_url.rstrip("/")
        url = f"{base}/skills?" + urllib.parse.urlencode(
            {"sort": "downloads", "limit": limit, "nonSuspiciousOnly": "true"}
        )
        payload = self.fetch_json(url)
        raw_items = None
        if isinstance(payload, dict):
            raw_items = payload.get("items") or payload.get("skills") or payload.get("results")
        elif isinstance(payload, list):
            raw_items = payload
        upserted = 0
        fetched = 0
        for index, raw in enumerate(list(raw_items or [])[:limit]):
            if not isinstance(raw, dict):
                continue
            slug = str(raw.get("slug") or raw.get("id") or "").strip()
            if not slug:
                continue
            fetched += 1
            if self._upsert(raw, slug, index):
                upserted += 1
        self.db.commit()
        return {"enabled": True, "fetched": fetched, "upserted": upserted}

    def _upsert(self, raw: dict[str, Any], slug: str, index: int) -> bool:
        market_id = f"{CLAWHUB_MARKET_PREFIX}{slug}"[:200]
        name = str(raw.get("displayName") or raw.get("name") or slug)[:200]
        owner = str(raw.get("ownerHandle") or raw.get("owner") or "").strip()
        description = str(raw.get("description") or raw.get("summary") or "")[:4000]
        version = str(raw.get("version") or "")
        downloads = raw.get("downloads")
        if not isinstance(downloads, int):
            stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
            downloads = stats.get("downloads") if isinstance(stats.get("downloads"), int) else 0
        homepage = f"https://clawhub.ai/skills/{urllib.parse.quote(slug)}"
        origin_hash = hashlib.sha256(f"{market_id}@{version}".encode("utf-8")).hexdigest()
        row = self.db.scalar(
            select(SkillMarketCacheEntry).where(SkillMarketCacheEntry.market_id == market_id)
        )
        if row is None:
            row = SkillMarketCacheEntry(
                id=new_id(),
                market_id=market_id,
                slug=slug[:160],
                files_json=[],
            )
            self.db.add(row)
        row.name = name
        row.source = (f"clawhub/{owner}" if owner else "clawhub")[:255]
        row.description = description
        row.install_url = homepage[:500]
        row.homepage_url = homepage[:500]
        row.installs = int(downloads or 0)
        # Discovery-only entry: no mirrored files, install resolves at source.
        row.source_type = "clawhub"
        row.fetch_status = "external"
        row.fetch_error = None
        row.fetched_at = utc_now()
        row.origin_hash = origin_hash
        row.rank = CLAWHUB_RANK_BASE + index
        return True

    def fetch_json(self, url: str) -> Any:
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "LearnGraph-ClawHubSync/1.0",
                "Accept": "application/json",
            },
            method="GET",
        )
        timeout = float(getattr(self.settings, "external_catalog_timeout_seconds", 12.0) or 12.0)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                data = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise AppError(502, "catalog_unavailable", f"ClawHub returned HTTP {exc.code}") from exc
        except Exception as exc:  # noqa: BLE001 — network failures must not 500
            raise AppError(502, "catalog_unavailable", str(exc)[:500]) from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise AppError(502, "catalog_response_too_large", "ClawHub response too large")
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AppError(502, "catalog_invalid_response", "ClawHub returned invalid JSON") from exc
