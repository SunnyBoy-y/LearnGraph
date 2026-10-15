"""Search result deduplication tests.

Verifies that :func:`normalize_result_url` collapses tracking variants of the
same page into one key and that :func:`dedupe_search_results` keeps the first
(highest-ranked) occurrence while preserving order. This is the root fix for
the duplicate React keys rendered by the chat source list (the same source
listed twice with an identical title+URL).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.domain.schemas.research import SearchResult
from app.providers.remote.search import (
    dedupe_search_results,
    normalize_result_url,
)


def _result(title: str, url: str) -> SearchResult:
    return SearchResult(
        title=title,
        url=url,
        snippet="",
        source_type="test_web_search",
        fetched_at=datetime.now(timezone.utc),
    )


class TestNormalizeResultUrl:
    def test_exact_duplicate_collapses(self) -> None:
        assert normalize_result_url("https://a.example/x") == normalize_result_url(
            "https://a.example/x"
        )

    def test_scheme_and_host_case_folded(self) -> None:
        assert normalize_result_url("HTTPS://A.Example/X") == normalize_result_url(
            "https://a.example/X"
        )

    def test_default_port_dropped(self) -> None:
        assert normalize_result_url("https://a.example:443/x") == normalize_result_url(
            "https://a.example/x"
        )
        assert normalize_result_url("http://a.example:80/x") == normalize_result_url(
            "http://a.example/x"
        )

    def test_trailing_slash_and_fragment_ignored(self) -> None:
        assert normalize_result_url("https://a.example/x/") == normalize_result_url(
            "https://a.example/x#section"
        )

    def test_tracking_params_stripped(self) -> None:
        assert normalize_result_url(
            "https://a.example/x?utm_source=news&utm_medium=email&gclid=abc"
        ) == normalize_result_url("https://a.example/x")

    def test_meaningful_params_sorted_not_dropped(self) -> None:
        assert normalize_result_url("https://a.example/x?b=2&a=1") == normalize_result_url(
            "https://a.example/x?a=1&b=2"
        )
        # pagination variants are different pages and must NOT collapse
        assert normalize_result_url("https://a.example/x?page=1") != normalize_result_url(
            "https://a.example/x?page=2"
        )

    def test_non_http_fallback_is_deterministic(self) -> None:
        assert normalize_result_url("file:///c:/tmp/a") == normalize_result_url(
            "file:///c:/tmp/a"
        )


class TestDedupeSearchResults:
    def test_exact_duplicates_removed_keeping_first(self) -> None:
        first = _result("杭州-天气预报", "http://www.nmc.cn/publish/forecast/AZJ/hangzhou.html")
        duplicate = _result("杭州-天气预报", "http://www.nmc.cn/publish/forecast/AZJ/hangzhou.html")
        other = _result("宁波天气-首页", "https://www.qx121.com/")
        deduped = dedupe_search_results([first, duplicate, other, duplicate])
        assert deduped == [first, other]

    def test_tracking_variant_removed(self) -> None:
        clean = _result("中国天气网", "https://www.weather.com.cn/")
        tracked = _result("中国天气网", "https://www.weather.com.cn/?utm_source=weibo")
        deduped = dedupe_search_results([clean, tracked])
        assert deduped == [clean]

    def test_distinct_pages_kept(self) -> None:
        a = _result("A", "https://a.example/")
        b = _result("B", "https://b.example/")
        assert dedupe_search_results([a, b]) == [a, b]
