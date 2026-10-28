import json
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import Mock

import httpx
import pytest
from pydantic import ValidationError
from app.core.errors import AppError
from app.providers import realtime_information as info


def weather():
    return {"timezone": "UTC", "current": {"time": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "temperature_2m": 20}, "daily": {k: [v] for k, v in {"time": "2026-10-21", "weather_code": 0, "temperature_2m_max": 25, "temperature_2m_min": 15, "precipitation_probability_max": 0}.items()}}


ENV = {"timezone": "Asia/Shanghai", "location_status": "available", "location": {"city": "Los Angeles", "latitude": 34.05, "longitude": -118.24, "timezone": "America/Los_Angeles", "source": "client_ip"}}


def test_default_weather_uses_client_coordinates_not_server_or_browser_timezone(monkeypatch):
    request = Mock(return_value=weather())
    monkeypatch.setattr(info, "_json", request)
    result = info.execute_information_tool("get_weather", {}, ENV)
    assert request.call_count == 1
    assert request.call_args.args[1]["longitude"] == -118.24
    assert request.call_args.args[1]["timezone"] == "auto"
    assert result["location"]["source"] == "client_ip"


def test_explicit_city_overrides_ip_and_has_provenance(monkeypatch):
    request = Mock(side_effect=[{"results": [{"name": "北京", "latitude": 39.9, "longitude": 116.4, "country_code": "CN"}]}, weather()])
    monkeypatch.setattr(info, "_json", request)
    result = info.execute_information_tool("get_weather", {"city": "北京"}, ENV)
    assert result["location"]["source"] == "user_city"
    assert request.call_args.args[1]["longitude"] == 116.4


@pytest.mark.parametrize("environment", [None, {}, {**ENV, "location_status": "unavailable"}])
def test_no_location_never_calls_source_or_guesses(environment, monkeypatch):
    request = Mock()
    monkeypatch.setattr(info, "_read", request)
    with pytest.raises(AppError, match="所在城市"):
        info.execute_information_tool("get_weather", {}, environment)
    request.assert_not_called()


@pytest.mark.parametrize("change", [{"latitude": float("nan")}, {"longitude": 181}, {"timezone": "bad/zone"}, {"source": "server_ip"}, {"ip": "127.0.0.1"}])
def test_environment_rejects_invalid_or_unneeded_location_data(change):
    with pytest.raises(ValidationError):
        info.ClientEnvironment.model_validate({**ENV, "location": {**ENV["location"], **change}})


def test_expired_weather_not_presented_as_current(monkeypatch):
    data = weather()
    data["current"]["time"] = "2020-01-01T00:00"
    monkeypatch.setattr(info, "_json", lambda *a: data)
    with pytest.raises(AppError, match="过期"):
        info.execute_information_tool("get_weather", {}, ENV)


def test_ambiguous_city_requests_country(monkeypatch):
    monkeypatch.setattr(info, "_json", lambda *a: {"results": [{"name": "London", "country_code": c} for c in ["GB", "CA"]]})
    with pytest.raises(AppError, match="歧义"):
        info.execute_information_tool("get_weather", {"city": "London"})


def rss_item(title, age=1, url="https://www.ithome.com/1/001.htm"):
    date = format_datetime(datetime.now(timezone.utc) - timedelta(hours=age))
    return f"<item><title>{title}</title><link>{url}</link><pubDate>{date}</pubDate></item>"


def test_news_keeps_title_date_source_together_filters_stale_foreign_and_duplicates(monkeypatch):
    raw = "<rss><channel>" + rss_item("fresh") + rss_item("duplicate") + rss_item("stale", 200, "https://www.ithome.com/old") + rss_item("foreign", 1, "http://localhost/private") + "</channel></rss>"
    monkeypatch.setattr(info, "_read", lambda *a: raw.encode())
    result = info.execute_information_tool("get_news", {"category": "technology"})
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "fresh"
    assert result["items"][0]["source"] == "IT之家"
    assert result["sources"] == result["items"]


@pytest.mark.parametrize("raw", [b"<!DOCTYPE rss><rss/>", b"not XML", b"<html/>", b"<rss><channel/></rss>"])
def test_bad_or_empty_news_is_explicit_failure(monkeypatch, raw):
    monkeypatch.setattr(info, "_read", lambda *a: raw)
    with pytest.raises(AppError):
        info.execute_information_tool("get_news", {})


def test_source_timeout_has_no_alternate_request_or_secret_in_error(monkeypatch):
    client = Mock(side_effect=httpx.ReadTimeout("secret-key"))
    monkeypatch.setattr(info.httpx, "Client", client)
    with pytest.raises(AppError) as caught:
        info.execute_information_tool("get_news", {})
    assert "secret-key" not in str(caught.value)
    assert client.call_count == 1


def test_runtime_configuration_failure_does_not_fall_through_to_search(monkeypatch):
    from app.providers.remote.fetch import FetchRuntimeUnavailable
    from app.services.agent_runtime import AgentToolRuntime
    runtime = AgentToolRuntime.__new__(AgentToolRuntime)
    runtime.fetch_provider = Mock(provider_id="sandbox_web_fetch", reason="", available=True)
    runtime.fetch_provider.fetch.side_effect = FetchRuntimeUnavailable("credentials unavailable")
    runtime.search_provider = Mock(provider_id="search")
    monkeypatch.setattr("app.services.agent_runtime.require_public_http_url", lambda *a: None)
    with pytest.raises(AppError, match="sandboxd"):
        runtime._fetch_web_page({"url": "https://example.com"}, ["example.com"])
    runtime.search_provider.fetch.assert_not_called()
