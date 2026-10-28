"""Bounded, explicit sources for short current-information queries.

These tools do not depend on a search provider and never substitute search
results or stale cache entries when their selected source fails.
"""
from __future__ import annotations

import html
import json
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.errors import AppError


class ApproximateLocation(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    city: str = Field(min_length=1, max_length=120)
    region: str = Field(default="", max_length=120)
    country_code: str = Field(default="", max_length=2)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    timezone: str = Field(max_length=80)
    source: str = "client_ip"

    @field_validator("source")
    @classmethod
    def ip_source(cls, value):
        if value != "client_ip":
            raise ValueError("location source must be client_ip")
        return value

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown timezone") from exc
        return value


class ClientEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str = Field(default="UTC", max_length=80)
    location: ApproximateLocation | None = None
    location_status: str = "unavailable"
    _timezone = field_validator("timezone")(ApproximateLocation.valid_timezone.__func__)

    @field_validator("location_status")
    @classmethod
    def valid_status(cls, value):
        if value not in {"pending", "available", "unavailable"}:
            raise ValueError("invalid location status")
        return value


class WeatherQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    city: str | None = Field(default=None, min_length=1, max_length=120)
    country_code: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    days: int = Field(default=3, ge=1, le=7)


class NewsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category: str = "general"
    limit: int = Field(default=3, ge=1, le=5)
    max_age_hours: int = Field(default=48, ge=1, le=168)


NEWS_FEEDS = {
    "general": ("中新网", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
    "world": ("中新网", "https://www.chinanews.com.cn/rss/world.xml"),
    "business": ("中新网", "https://www.chinanews.com.cn/rss/finance.xml"),
    "technology": ("IT之家", "https://www.ithome.com/rss/"),
}


def information_tool_definitions():
    definitions = []
    for name, description, schema in (
        ("get_weather", "查询实时天气及未来 1 到 7 天天气，直连 Open-Meteo，不使用搜索。用户指定城市时传 city；未指定时省略 city，使用客户端 IP 近似位置。返回 location_required 时询问城市，不猜位置。", WeatherQuery),
        ("get_news", "从官方 RSS 获取近期新闻标题、准确发布时间和原文链接，不使用搜索。category: general 综合、world 国际、business 财经、technology 科技。只描述返回条目，不声称覆盖全部新闻。", NewsQuery),
    ):
        parameters = schema.model_json_schema()
        if name == "get_news":
            parameters["properties"]["category"]["enum"] = list(NEWS_FEEDS)
        definitions.append({"type": "function", "function": {"name": name, "description": description, "parameters": parameters}})
    return definitions


def _read(url: str, params: dict | None = None) -> bytes:
    try:
        with httpx.Client(timeout=httpx.Timeout(12, connect=5), follow_redirects=False) as client:
            with client.stream("GET", url, params=params, headers={"User-Agent": "LearnGraph/1.0 information reader"}) as response:
                response.raise_for_status()
                chunks = bytearray()
                for chunk in response.iter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 1_048_576:
                        raise ValueError("source response too large")
                return bytes(chunks)
    except (httpx.HTTPError, ValueError) as exc:
        # Do not expose request URLs: a commercial weather key can be in params.
        raise AppError(502, "information_source_unavailable", "专用信息源暂时不可用，请稍后重试。") from exc


def _json(url: str, params: dict) -> dict:
    try:
        data = json.loads(_read(url, params))
        if not isinstance(data, dict) or data.get("error"):
            raise ValueError("invalid source response")
        return data
    except (ValueError, TypeError) as exc:
        raise AppError(502, "information_source_invalid", "专用信息源返回了无效数据。") from exc


def get_weather(query: WeatherQuery, environment: dict | None) -> dict:
    if query.city:
        params = {"name": query.city.strip(), "count": 5, "language": "zh", "format": "json"}
        if query.country_code:
            params["countryCode"] = query.country_code
        candidates = _json("https://geocoding-api.open-meteo.com/v1/search", params).get("results") or []
        if not candidates:
            raise AppError(422, "location_not_found", "未找到该城市，请提供城市名称及所在国家。")
        # Geocoder orders by relevance/population. Repeated city names across
        # countries need clarification rather than silently picking a country.
        exact = [c for c in candidates if str(c.get("name", "")).casefold() == query.city.strip().casefold()]
        choices = exact or candidates
        if len({c.get("country_code") for c in choices}) > 1:
            raise AppError(422, "location_ambiguous", "城市名存在歧义，请说明国家。", {"candidates": [{"city": c.get("name"), "country_code": c.get("country_code")} for c in choices]})
        loc = choices[0]
        location = {"city": loc["name"], "country_code": loc.get("country_code", ""), "latitude": loc["latitude"], "longitude": loc["longitude"], "source": "user_city"}
    else:
        env = ClientEnvironment.model_validate(environment or {})
        if env.location_status != "available" or env.location is None:
            raise AppError(422, "location_required", "尚未取得客户端近似位置，请询问用户所在城市。")
        location = env.location.model_dump()
    params = {
        "latitude": location["latitude"], "longitude": location["longitude"], "timezone": "auto", "forecast_days": query.days,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m",
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    }
    # Free endpoint is for non-commercial use. Paid deployments set this key;
    # the customer endpoint is selected explicitly, never as a failure fallback.
    key = os.environ.get("OPEN_METEO_API_KEY", "").strip()
    endpoint = "https://customer-api.open-meteo.com/v1/forecast" if key else "https://api.open-meteo.com/v1/forecast"
    if key:
        params["apikey"] = key
    data = _json(endpoint, params)
    current = data.get("current") or {}
    try:
        observed = datetime.fromisoformat(current["time"]).replace(tzinfo=ZoneInfo(data["timezone"]))
        age = datetime.now(timezone.utc) - observed
        if age > timedelta(hours=2) or age < -timedelta(minutes=30) or current.get("temperature_2m") is None:
            raise ValueError("stale or missing weather")
        daily = data["daily"]
        if not daily.get("time") or any(len(daily.get(k, [])) != len(daily["time"]) for k in ("weather_code", "temperature_2m_max", "temperature_2m_min", "precipitation_probability_max")):
            raise ValueError("incomplete forecast")
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise AppError(502, "weather_data_invalid", "天气源返回的数据不完整或已过期。") from exc
    return {"provider": "Open-Meteo", "fetched_at": datetime.now(timezone.utc).isoformat(), "location": location, "timezone": data["timezone"], "current": current, "current_units": data.get("current_units", {}), "daily": daily, "daily_units": data.get("daily_units", {}), "weather_code_standard": "WMO: 0晴,1大部晴,2多云,3阴,45/48雾,51-57毛毛雨,61-67雨,71-77雪,80-82阵雨,85/86阵雪,95-99雷雨", "sources": [{"title": "Open-Meteo 天气预报", "url": "https://open-meteo.com/en/docs"}]}


def get_news(query: NewsQuery) -> dict:
    if query.category not in NEWS_FEEDS:
        raise AppError(422, "invalid_tool_arguments", "不支持的新闻分类。")
    source, url = NEWS_FEEDS[query.category]
    raw = _read(url)
    now = datetime.now(timezone.utc)
    try:
        if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
            raise ValueError("XML declarations are not permitted")
        root = ElementTree.fromstring(raw)
        if root.tag != "rss":
            raise ValueError("not RSS")
    except (ElementTree.ParseError, ValueError) as exc:
        raise AppError(502, "news_feed_invalid", "新闻源返回了无效订阅数据。") from exc
    items, seen = [], set()
    for item in root.findall("./channel/item"):
        title = html.unescape(item.findtext("title") or "").strip()[:400]
        link = (item.findtext("link") or "").strip()
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "")
            if published.tzinfo is None:
                continue
            host = urlsplit(link).hostname or ""
            expected = "ithome.com" if query.category == "technology" else "chinanews.com.cn"
            if urlsplit(link).scheme not in {"https", "http"} or not (host == expected or host.endswith("." + expected)):
                continue
            if not title or link in seen or not timedelta(minutes=-10) <= now - published <= timedelta(hours=query.max_age_hours):
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        seen.add(link)
        items.append({"title": title, "url": link, "source": source, "published_at": published.isoformat()})
    items.sort(key=lambda i: i["published_at"], reverse=True)
    items = items[:query.limit]
    if not items:
        raise AppError(502, "news_no_recent_items", "所选新闻源没有符合时间范围的条目，不能据此播报最新新闻。")
    return {"provider": source, "feed_url": url, "fetched_at": now.isoformat(), "coverage": "所选订阅源近期条目，不代表全部新闻", "items": items, "sources": items}


def execute_information_tool(name: str, arguments: dict, environment: dict | None = None) -> dict:
    try:
        return get_weather(WeatherQuery.model_validate(arguments), environment) if name == "get_weather" else get_news(NewsQuery.model_validate(arguments))
    except ValidationError as exc:
        raise AppError(422, "invalid_tool_arguments", "天气、新闻或位置参数无效。") from exc
