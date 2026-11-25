"""Deterministic temporal normalization and query time-window parsing.

The extraction model identifies the original time expression; this module
computes display text, granularity and intervals. It is a pure function module
with no database dependency so tests can assert deterministic results.

Contract output (``TemporalSemantics.as_dict``):

- ``original_expression``: the raw user expression for audit/correction.
- ``normalized_display``: a human-readable Chinese display.
- ``timezone``: resolved IANA timezone.
- ``granularity``: one of ``year/month/week/date/day_period/hour/minute/interval/
  recurrence/open_ended``.
- ``start_at`` / ``end_at``: normalized interval bounds, or ``None`` when the
  expression is recurrence/open-ended.
- ``observed_at``: the message occurrence time supplied by the caller.
- ``normalizer_version``: ``temporal-v2``.
- ``timezone_source``: ``user`` or ``system_default``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

NORMALIZER_VERSION = "temporal-v2"
_DEFAULT_TIMEZONE = "Asia/Shanghai"

_DAY_PERIODS: dict[str, tuple[time, time]] = {
    "凌晨": (time(0, 0), time(6, 0)),
    "清晨": (time(5, 0), time(8, 0)),
    "早上": (time(6, 0), time(12, 0)),
    "上午": (time(6, 0), time(12, 0)),
    "中午": (time(11, 0), time(14, 0)),
    "下午": (time(12, 0), time(18, 0)),
    "傍晚": (time(17, 0), time(20, 0)),
    "晚上": (time(18, 0), time(23, 59, 59)),
}

_WEEKDAY_MAP: dict[str, int] = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
}

_TEMPORAL_WHAT_HAPPENED_RE = re.compile(
    r"(?:"
    r"发生(?:了|过)?什么|干(?:了|过)?什么|做(?:了|过)?什么|学(?:了|过)?什么|聊(?:了|过)?什么|"
    r"说(?:了|过)?什么|记(?:了|过)?什么|有(?:什么|哪些)(?:记忆|记录|事|事情|安排|计划|活动)|"
    r"回忆|回顾|当时|那天|那时候|期间|"
    r"what\s+happened|what\s+did\s+(?:i|we)\s+(?:do|learn|talk|discuss)|events?\s+(?:on|in|during)"
    r")",
    re.I,
)

_TEMPORAL_WHEN_RE = re.compile(
    r"(?:什么时候|何时|哪(?:一)?天|几月几[日号]|几号|几点|when\s+did|what\s+date|what\s+time)",
    re.I,
)

_QUERY_TIME_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Full YYYY年M月D日 / YYYY/M/D / YYYY-M-D / YYYY.M.D (+ optional period/time)
    re.compile(
        r"\d{4}\s*年\s*[0-9一二三四五六七八九十]+\s*月\s*[0-9一二三四五六七八九十]+\s*[日号]"
        r"(?:\s*(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上))?"
        r"(?:\s*[0-9一二两三四五六七八九十]+[点點時时](?:半|[0-9一二两三四五六七八九十]+分?)?)?"
    ),
    re.compile(
        r"\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
        r"(?:[T\s]\d{1,2}:\d{2}(?::\d{2})?)?"
    ),
    # Relative year/month + M月D日 or standalone M月D日
    re.compile(
        r"(?:今年|去年|前年|明年|后年|上个月|上月|本月|这个月|下个月|下月)?"
        r"[0-9一二三四五六七八九十]+\s*月\s*[0-9一二三四五六七八九十]+\s*[日号]"
        r"(?:\s*(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上))?"
    ),
    # YYYY年M月 or 今年M月 / 去年M月
    re.compile(r"(?:\d{4}\s*年|今年|去年|前年|明年|后年)\s*[0-9一二三四五六七八九十]+\s*月"),
    # Relative ranges: 最近N天 / 过去一周 / 最近一个月 / 这几天 / 最近几天
    re.compile(r"(?:最近|近|过去|前)\s*(?:[0-9一二两三四五六七八九十]+|几)\s*(?:天|日|周|星期|个月|月|年)"),
    re.compile(r"这几天|最近几天|近期"),
    # N天前 / N周前 / N个月前 / N年前
    re.compile(r"[0-9一二两三四五六七八九十]+\s*(?:天|日|周|星期|个月|月|年)\s*[前后]"),
    re.compile(r"\b\d+\s+(?:days?|weeks?|months?|years?)\s+ago\b", re.I),
    # Weekdays: 上周三 / 这周五 / 下周一
    re.compile(r"(?:上个星期|上星期|上周|本星期|本周|这个星期|这周|下个星期|下星期|下周)[一二三四五六日天]"),
    # Day words + optional period/hour
    re.compile(
        r"(?:大前天|前天|昨天|昨日|今天|今日|明天|明日|后天|大后天)"
        r"(?:\s*(?:凌晨|清晨|早上|上午|中午|下午|傍晚|晚上))?"
        r"(?:\s*[0-9一二两三四五六七八九十]+[点點時时](?:半|[0-9一二两三四五六七八九十]+分?)?)?"
    ),
    re.compile(r"\b(?:today|yesterday|tomorrow)\b", re.I),
    # Week / month / year words
    re.compile(r"上个星期|上星期|上周|本星期|本周|这个星期|这周|下个星期|下星期|下周"),
    re.compile(r"\b(?:last|this|next)\s+(?:week|month|year)\b", re.I),
    re.compile(r"上个月|上月|本月|这个月|当月|下个月|下月"),
    re.compile(r"(?:\d{4}年|今年|去年|前年|明年|后年)"),
    re.compile(r"[0-9一二三四五六七八九十]+\s*月"),
)


@dataclass(frozen=True, slots=True)
class TemporalSemantics:
    original_expression: str
    normalized_display: str
    timezone: str
    granularity: str
    start_at: datetime | None
    end_at: datetime | None
    observed_at: datetime
    normalizer_version: str = NORMALIZER_VERSION
    timezone_source: str = "user"

    def as_dict(self) -> dict[str, Any]:
        def _iso(value: datetime | None) -> str | None:
            return value.isoformat() if value is not None else None

        return {
            "original_expression": self.original_expression,
            "time_expression": self.original_expression,
            "normalized_display": self.normalized_display,
            "timezone": self.timezone,
            "timezone_source": self.timezone_source,
            "granularity": self.granularity,
            "start_at": _iso(self.start_at),
            "end_at": _iso(self.end_at),
            "observed_at": _iso(self.observed_at),
            "normalizer_version": self.normalizer_version,
        }


@dataclass(frozen=True, slots=True)
class TemporalQuery:
    """Structured temporal intent extracted from a natural-language query."""

    original_expression: str
    normalized_display: str
    timezone: str
    granularity: str
    start_at: datetime | None
    end_at: datetime | None
    is_temporal_recall: bool
    asks_when: bool
    residual_query: str


class TemporalNormalizer:
    """Parse common Chinese and English date/time expressions deterministically."""

    def __init__(self, *, version: str = NORMALIZER_VERSION) -> None:
        self.version = version

    def normalize(
        self,
        expression: str,
        *,
        observed_at: datetime,
        timezone_name: str | None = None,
        prefer_past: bool = False,
    ) -> TemporalSemantics:
        return replace(self._normalize(
            expression, observed_at=observed_at, timezone_name=timezone_name,
            prefer_past=prefer_past,
        ), original_expression=expression or "")

    def _normalize(
        self,
        expression: str,
        *,
        observed_at: datetime,
        timezone_name: str | None,
        prefer_past: bool,
    ) -> TemporalSemantics:
        text = (expression or "").strip()
        timezone_name = timezone_name or _DEFAULT_TIMEZONE
        tz, timezone_source = self._resolve_timezone(timezone_name)
        obs = observed_at if observed_at.tzinfo is not None else observed_at.replace(tzinfo=timezone.utc)
        local_obs = obs.astimezone(tz)
        local_date = local_obs.date()

        # 1. Absolute ISO / slash / dot dates.
        iso = self._try_iso_or_slash(text, tz)
        if iso is not None:
            start, end, granularity, display = iso
            return TemporalSemantics(
                text,
                display,
                tz.key,
                granularity,
                start,
                end,
                obs,
                self.version,
                timezone_source,
            )

        # 2. Chinese full / partial dates: 2026年10月18日, 10月18日, 今年10月, etc.
        cn_date = self._try_chinese_date(text, local_date, tz, prefer_past=prefer_past)
        if cn_date is not None:
            start, end, granularity, display = cn_date
            return TemporalSemantics(
                text,
                display,
                tz.key,
                granularity,
                start,
                end,
                obs,
                self.version,
                timezone_source,
            )

        # 3. Relative recent ranges: 最近N天 / 过去一周 / 这几天 / 最近一个月.
        recent_range = self._try_recent_range(text, local_date, tz)
        if recent_range is not None:
            start, end, granularity, display = recent_range
            return TemporalSemantics(
                text,
                display,
                tz.key,
                granularity,
                start,
                end,
                obs,
                self.version,
                timezone_source,
            )

        # 4. N天前 / N周前 / N个月前 / N days ago.
        relative_offset = self._try_relative_offset(text, local_date, tz)
        if relative_offset is not None:
            start, end, granularity, display = relative_offset
            return TemporalSemantics(
                text,
                display,
                tz.key,
                granularity,
                start,
                end,
                obs,
                self.version,
                timezone_source,
            )

        # 5. Specific weekday in week: 上周三 / 这周五 / 下周一.
        weekday_target = self._try_week_weekday(text, local_date, tz)
        if weekday_target is not None:
            start, end, granularity, display = weekday_target
            return TemporalSemantics(
                text,
                display,
                tz.key,
                granularity,
                start,
                end,
                obs,
                self.version,
                timezone_source,
            )

        # 6. 今天/明天/后天/昨天/前天/yesterday/today/tomorrow + optional day period / hour.
        day_offset = self._day_offset(text)
        if day_offset is not None:
            return self._day(text, local_date, day_offset, tz, obs, timezone_source)

        # 7. Year + Month expressions without day: 2026年10月 / 去年10月 / 10月.
        cn_month = self._try_chinese_year_month(text, local_date, tz, prefer_past=prefer_past)
        if cn_month is not None:
            start, end, granularity, display = cn_month
            return TemporalSemantics(
                text,
                display,
                tz.key,
                granularity,
                start,
                end,
                obs,
                self.version,
                timezone_source,
            )

        # 8. Year expressions.
        year = self._year_expression(text, local_date)
        if year is not None:
            year_value = year
            start = datetime(year_value, 1, 1, tzinfo=tz)
            end = datetime(year_value + 1, 1, 1, tzinfo=tz)
            display = f"{year_value}年"
            return TemporalSemantics(
                text, display, tz.key, "year", start, end, obs, self.version, timezone_source
            )

        # 9. 下个月 / 本月 / 这个月 / 上个月 / last month / this month / next month.
        month_shift = self._month_shift(text)
        if month_shift is not None:
            month_start = self._shift_month_start(local_date.replace(day=1), month_shift)
            month_end = self._shift_month_start(month_start, 1)
            display = f"{month_start.year}年{month_start.month}月"
            return TemporalSemantics(
                text,
                display,
                tz.key,
                "month",
                datetime.combine(month_start, time.min, tzinfo=tz),
                datetime.combine(month_end, time.min, tzinfo=tz),
                obs,
                self.version,
                timezone_source,
            )

        # 10. 月底前 / 月末前.
        if "月底" in text or "月末" in text or "月底前" in text:
            month_end = self._shift_month_start(local_date.replace(day=1), 1)
            display = f"截至{month_end.year}年{month_end.month}月{month_end.day}日"
            return TemporalSemantics(
                text,
                display,
                tz.key,
                "interval",
                datetime.combine(local_date, time.min, tzinfo=tz),
                datetime.combine(month_end, time.min, tzinfo=tz),
                obs,
                self.version,
                timezone_source,
            )

        # 11. 下周 / 本周 / 上周 / 下个星期 / last week / this week / next week.
        week_shift = self._week_shift(text)
        if week_shift is not None:
            this_monday = local_date - timedelta(days=local_date.weekday())
            week_start = this_monday + timedelta(days=7 * week_shift)
            week_end = week_start + timedelta(days=7)
            display = (
                f"{week_start.year}年{week_start.month}月{week_start.day}日"
                f"至{week_end.year}年{week_end.month}月{week_end.day}日"
            )
            return TemporalSemantics(
                text,
                display,
                tz.key,
                "week",
                datetime.combine(week_start, time.min, tzinfo=tz),
                datetime.combine(week_end, time.min, tzinfo=tz),
                obs,
                self.version,
                timezone_source,
            )

        # 12. 每周X/每天/每周末 -> recurrence (no fake dates).
        if self._is_recurrence(text):
            return TemporalSemantics(
                text, text, tz.key, "recurrence", None, None, obs, self.version, timezone_source
            )

        # 13. 下午三点 / 3点.
        hour = self._hour_expression(text)
        if hour is not None:
            hour_value, minute_value, period_name = hour
            target = datetime.combine(local_date, time(hour_value, minute_value), tzinfo=tz)
            granularity = "hour" if minute_value == 0 else "minute"
            display_hour = (
                (hour_value % 12 or 12)
                if period_name
                else hour_value
            )
            display = (
                f"{target.year}年{target.month}月{target.day}日"
                f"{period_name}{display_hour}点"
                if minute_value == 0
                else (
                    f"{target.year}年{target.month}月{target.day}日"
                    f"{period_name}{display_hour}点{minute_value}分"
                )
            )
            return TemporalSemantics(
                text,
                display,
                tz.key,
                granularity,
                target,
                target + timedelta(minutes=max(1, minute_value or 60)),
                obs,
                self.version,
                timezone_source,
            )

        # 14. 以后 / 今后 -> interval from now onward.
        if any(marker in text for marker in ("以后", "今后", "之后")):
            return TemporalSemantics(
                text,
                text,
                tz.key,
                "interval",
                local_obs,
                None,
                obs,
                self.version,
                timezone_source,
            )

        return self._open_ended(text, obs, tz.key, timezone_source)

    def _day(
        self,
        text: str,
        local_date: date,
        day_offset: int,
        tz: ZoneInfo,
        observed_at: datetime,
        timezone_source: str,
    ) -> TemporalSemantics:
        target_date = local_date + timedelta(days=day_offset)
        return self._build_date_semantics(text, target_date, tz, observed_at, timezone_source)

    def _build_date_semantics(
        self,
        text: str,
        target_date: date,
        tz: ZoneInfo,
        observed_at: datetime,
        timezone_source: str,
    ) -> TemporalSemantics:
        hour = self._hour_expression(text)
        if hour is not None:
            hour_value, minute_value, period_name = hour
            target = datetime.combine(target_date, time(hour_value, minute_value), tzinfo=tz)
            granularity = "hour" if minute_value == 0 else "minute"
            display_hour = (hour_value % 12 or 12) if period_name else hour_value
            display = (
                f"{target_date.year}年{target_date.month}月{target_date.day}日"
                f"{period_name}{display_hour}点"
                if minute_value == 0
                else (
                    f"{target_date.year}年{target_date.month}月{target_date.day}日"
                    f"{period_name}{display_hour}点{minute_value}分"
                )
            )
            return TemporalSemantics(
                text,
                display,
                tz.key,
                granularity,
                target,
                target + timedelta(minutes=max(1, minute_value or 60)),
                observed_at,
                self.version,
                timezone_source,
            )
        period = self._extract_period(text)
        if period is not None:
            period_name, period_start, period_end = period
            display = f"{target_date.year}年{target_date.month}月{target_date.day}日{period_name}"
            return TemporalSemantics(
                text,
                display,
                tz.key,
                "day_period",
                datetime.combine(target_date, period_start, tzinfo=tz),
                datetime.combine(target_date, period_end, tzinfo=tz),
                observed_at,
                self.version,
                timezone_source,
            )
        display = f"{target_date.year}年{target_date.month}月{target_date.day}日"
        return TemporalSemantics(
            text,
            display,
            tz.key,
            "date",
            datetime.combine(target_date, time.min, tzinfo=tz),
            datetime.combine(target_date + timedelta(days=1), time.min, tzinfo=tz),
            observed_at,
            self.version,
            timezone_source,
        )

    def _open_ended(
        self, text: str, observed_at: datetime, timezone_name: str, timezone_source: str
    ) -> TemporalSemantics:
        display = text or "未指定"
        return TemporalSemantics(
            text or display,
            display,
            timezone_name,
            "open_ended",
            None,
            None,
            observed_at,
            self.version,
            timezone_source,
        )

    @staticmethod
    def _resolve_timezone(timezone_name: str) -> tuple[ZoneInfo, str]:
        if not timezone_name or not timezone_name.strip():
            return ZoneInfo(_DEFAULT_TIMEZONE), "system_default"
        try:
            return ZoneInfo(timezone_name.strip()), "user"
        except Exception:
            return ZoneInfo(_DEFAULT_TIMEZONE), "system_default"

    @staticmethod
    def _shift_month_start(first_of_month: date, shift: int) -> date:
        total_months = first_of_month.year * 12 + (first_of_month.month - 1) + shift
        year, month_zero = divmod(total_months, 12)
        return date(year, month_zero + 1, 1)

    @staticmethod
    def _day_offset(text: str) -> int | None:
        lowered = text.casefold()
        if text.startswith(("大前天",)):
            return -3
        if text.startswith(("大后天",)):
            return 3
        if text.startswith(("今天", "今日")) or re.match(r"^today\b", lowered):
            return 0
        if text.startswith(("明天", "明日")) or re.match(r"^tomorrow\b", lowered):
            return 1
        if text.startswith("后天"):
            return 2
        if text.startswith(("昨天", "昨日")) or re.match(r"^yesterday\b", lowered):
            return -1
        if text.startswith("前天"):
            return -2
        return None

    @staticmethod
    def _extract_period(text: str) -> tuple[str, time, time] | None:
        for name, (start, end) in _DAY_PERIODS.items():
            if name in text:
                return (name, start, end)
        return None

    @staticmethod
    def _year_expression(text: str, local_date: date) -> int | None:
        match = re.search(r"(?:(\d{4})年|今年|明年|后年|去年|前年)", text)
        if match is None:
            return None
        if match.group(1):
            return int(match.group(1))
        literal = match.group(0)
        if literal == "今年":
            return local_date.year
        if literal == "明年":
            return local_date.year + 1
        if literal == "后年":
            return local_date.year + 2
        if literal == "去年":
            return local_date.year - 1
        if literal == "前年":
            return local_date.year - 2
        return local_date.year

    @staticmethod
    def _month_shift(text: str) -> int | None:
        lowered = text.casefold()
        if any(marker in text for marker in ("下个月", "下月", "下个月份")) or "next month" in lowered:
            return 1
        if any(marker in text for marker in ("本月", "这个月", "当月")) or "this month" in lowered:
            return 0
        if any(marker in text for marker in ("上个月", "上月")) or "last month" in lowered:
            return -1
        return None

    @staticmethod
    def _week_shift(text: str) -> int | None:
        lowered = text.casefold()
        if any(marker in text for marker in ("下个星期", "下星期", "下周")) or "next week" in lowered:
            return 1
        if any(marker in text for marker in ("本星期", "本周", "这个星期", "这周")) or "this week" in lowered:
            return 0
        if any(marker in text for marker in ("上个星期", "上星期", "上周")) or "last week" in lowered:
            return -1
        return None

    @staticmethod
    def _is_recurrence(text: str) -> bool:
        return bool(
            re.search(r"每(天|日|周|月|年|星期|周末|[一二三四五六日天])", text)
            or re.search(r"(每天|每日|每周|每月|每年|每个星期|每个周末)", text)
        )

    @staticmethod
    def _try_iso_or_slash(text: str, tz: ZoneInfo) -> tuple[datetime, datetime, str, str] | None:
        cleaned = text.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            parsed = None
        if parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=tz)
            if parsed.hour or parsed.minute or parsed.second:
                display = f"{parsed.year}年{parsed.month}月{parsed.day}日{parsed.hour:02d}:{parsed.minute:02d}"
                return parsed, parsed + timedelta(minutes=1), "minute", display
            display = f"{parsed.year}年{parsed.month}月{parsed.day}日"
            return parsed, parsed + timedelta(days=1), "date", display

        match = re.fullmatch(
            r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:[T\s](\d{1,2}):(\d{2})(?::(\d{2}))?)?",
            text.strip(),
        )
        if match is None:
            return None
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            if match.group(4) is not None:
                hour_val, minute_val = int(match.group(4)), int(match.group(5))
                second_val = int(match.group(6) or 0)
                dt = datetime(year, month, day, hour_val, minute_val, second_val, tzinfo=tz)
                display = f"{year}年{month}月{day}日{hour_val:02d}:{minute_val:02d}"
                return dt, dt + timedelta(minutes=1), "minute", display
            dt = datetime(year, month, day, tzinfo=tz)
        except ValueError:
            return None
        display = f"{year}年{month}月{day}日"
        return dt, dt + timedelta(days=1), "date", display

    def _try_chinese_date(
        self, text: str, local_date: date, tz: ZoneInfo, *, prefer_past: bool
    ) -> tuple[datetime, datetime, str, str] | None:
        match = re.search(
            r"(?:(\d{4})\s*年|(今年|去年|前年|明年|后年|上个月|上月|本月|这个月|下个月|下月))?"
            r"\s*(?:([0-9一二三四五六七八九十]+)\s*月\s*)?"
            r"([0-9一二三四五六七八九十]+)\s*[日号]",
            text,
        )
        if match is None:
            return None
        year_digits, year_or_month_word, month_token, day_token = match.groups()
        if not month_token and year_or_month_word not in {"上个月", "上月", "本月", "这个月", "下个月", "下月"}:
            return None
        day_val = self._parse_chinese_number(day_token)
        if day_val is None or not (1 <= day_val <= 31):
            return None
        year_val = local_date.year
        month_val: int | None = None
        if year_digits:
            year_val = int(year_digits)
        elif year_or_month_word in {"今年", "去年", "前年", "明年", "后年"}:
            year_val = self._year_expression(year_or_month_word, local_date) or local_date.year
        elif year_or_month_word in {"上个月", "上月", "本月", "这个月", "下个月", "下月"}:
            shift = self._month_shift(year_or_month_word) or 0
            shifted = self._shift_month_start(local_date.replace(day=1), shift)
            year_val, month_val = shifted.year, shifted.month
        if month_token:
            month_val = self._parse_chinese_number(month_token)
        if month_val is None or not (1 <= month_val <= 12):
            return None
        try:
            target_date = date(year_val, month_val, day_val)
        except ValueError:
            return None
        if (
            prefer_past
            and not year_digits
            and not year_or_month_word
            and target_date > local_date + timedelta(days=1)
        ):
            try:
                target_date = date(year_val - 1, month_val, day_val)
            except ValueError:
                pass
        semantics = self._build_date_semantics(text, target_date, tz, datetime.combine(local_date, time.min, tzinfo=tz), "user")
        return semantics.start_at, semantics.end_at, semantics.granularity, semantics.normalized_display  # type: ignore[return-value]

    def _try_chinese_year_month(
        self, text: str, local_date: date, tz: ZoneInfo, *, prefer_past: bool
    ) -> tuple[datetime, datetime, str, str] | None:
        match = re.search(
            r"(?:(\d{4})\s*年|(今年|去年|前年|明年|后年))?\s*([0-9一二三四五六七八九十]+)\s*月(?!底|末|[0-9一二三四五六七八九十]*[日号]|前|份)",
            text,
        )
        if match is None:
            return None
        year_digits, year_word, month_token = match.groups()
        month_val = self._parse_chinese_number(month_token)
        if month_val is None or not (1 <= month_val <= 12):
            return None
        if year_digits:
            year_val = int(year_digits)
        elif year_word:
            year_val = self._year_expression(year_word, local_date) or local_date.year
        else:
            year_val = local_date.year
            if prefer_past and month_val > local_date.month:
                year_val -= 1
        month_start = date(year_val, month_val, 1)
        month_end = self._shift_month_start(month_start, 1)
        display = f"{year_val}年{month_val}月"
        return (
            datetime.combine(month_start, time.min, tzinfo=tz),
            datetime.combine(month_end, time.min, tzinfo=tz),
            "month",
            display,
        )

    def _try_recent_range(
        self, text: str, local_date: date, tz: ZoneInfo
    ) -> tuple[datetime, datetime, str, str] | None:
        if text in {"这几天", "最近几天", "近期"}:
            start_date = local_date - timedelta(days=6)
            end_date = local_date + timedelta(days=1)
            return (
                datetime.combine(start_date, time.min, tzinfo=tz),
                datetime.combine(end_date, time.min, tzinfo=tz),
                "interval",
                f"{start_date.year}年{start_date.month}月{start_date.day}日至{local_date.year}年{local_date.month}月{local_date.day}日",
            )
        match = re.fullmatch(
            r"(?:最近|近|过去|前)\s*([0-9一二两三四五六七八九十]+|几)\s*(天|日|周|星期|个月|月|年)",
            text,
        )
        if match is None:
            return None
        count_token, unit = match.groups()
        count = 3 if count_token == "几" else self._parse_chinese_number(count_token)
        if count is None or count <= 0:
            return None
        if unit in {"天", "日"}:
            start_date = local_date - timedelta(days=max(0, count - 1))
        elif unit in {"周", "星期"}:
            start_date = local_date - timedelta(days=max(0, count * 7 - 1))
        elif unit in {"个月", "月"}:
            start_date = self._shift_month_start(local_date.replace(day=1), -(count - 1))
        else:
            start_date = date(local_date.year - (count - 1), 1, 1)
        end_date = local_date + timedelta(days=1)
        display = f"{start_date.year}年{start_date.month}月{start_date.day}日至{local_date.year}年{local_date.month}月{local_date.day}日"
        return (
            datetime.combine(start_date, time.min, tzinfo=tz),
            datetime.combine(end_date, time.min, tzinfo=tz),
            "interval",
            display,
        )

    def _try_relative_offset(
        self, text: str, local_date: date, tz: ZoneInfo
    ) -> tuple[datetime, datetime, str, str] | None:
        match = re.match(
            r"^([0-9一二两三四五六七八九十]+)\s*(天|日|周|星期|个月|月|年)\s*([前后])",
            text,
        )
        en_match = re.match(
            r"^(\d+)\s+(day|days|week|weeks|month|months|year|years)\s+ago\b",
            text,
            re.I,
        )
        if match is not None:
            count_token, unit, direction = match.groups()
            count = self._parse_chinese_number(count_token)
            if count is None:
                return None
            sign = -1 if direction == "前" else 1
        elif en_match is not None:
            count = int(en_match.group(1))
            unit_raw = en_match.group(2).casefold()
            unit = "天" if "day" in unit_raw else "周" if "week" in unit_raw else "个月" if "month" in unit_raw else "年"
            sign = -1
        else:
            return None

        if unit in {"天", "日"}:
            target_date = local_date + timedelta(days=sign * count)
            semantics = self._build_date_semantics(
                text, target_date, tz, datetime.combine(local_date, time.min, tzinfo=tz), "user"
            )
            return semantics.start_at, semantics.end_at, semantics.granularity, semantics.normalized_display  # type: ignore[return-value]
        if unit in {"周", "星期"}:
            this_monday = local_date - timedelta(days=local_date.weekday())
            week_start = this_monday + timedelta(days=7 * sign * count)
            week_end = week_start + timedelta(days=7)
            display = f"{week_start.year}年{week_start.month}月{week_start.day}日至{week_end.year}年{week_end.month}月{week_end.day}日"
            return (
                datetime.combine(week_start, time.min, tzinfo=tz),
                datetime.combine(week_end, time.min, tzinfo=tz),
                "week",
                display,
            )
        if unit in {"个月", "月"}:
            month_start = self._shift_month_start(local_date.replace(day=1), sign * count)
            month_end = self._shift_month_start(month_start, 1)
            display = f"{month_start.year}年{month_start.month}月"
            return (
                datetime.combine(month_start, time.min, tzinfo=tz),
                datetime.combine(month_end, time.min, tzinfo=tz),
                "month",
                display,
            )
        year_val = local_date.year + sign * count
        return (
            datetime(year_val, 1, 1, tzinfo=tz),
            datetime(year_val + 1, 1, 1, tzinfo=tz),
            "year",
            f"{year_val}年",
        )

    def _try_week_weekday(
        self, text: str, local_date: date, tz: ZoneInfo
    ) -> tuple[datetime, datetime, str, str] | None:
        match = re.search(
            r"(上个星期|上星期|上周|本星期|本周|这个星期|这周|下个星期|下星期|下周)([一二三四五六日天])",
            text,
        )
        if match is None:
            return None
        week_token, day_char = match.groups()
        shift = self._week_shift(week_token)
        weekday_idx = _WEEKDAY_MAP.get(day_char)
        if shift is None or weekday_idx is None:
            return None
        this_monday = local_date - timedelta(days=local_date.weekday())
        target_date = this_monday + timedelta(days=7 * shift + weekday_idx)
        semantics = self._build_date_semantics(
            text, target_date, tz, datetime.combine(local_date, time.min, tzinfo=tz), "user"
        )
        return semantics.start_at, semantics.end_at, semantics.granularity, semantics.normalized_display  # type: ignore[return-value]

    @staticmethod
    def _parse_chinese_number(value: str) -> int | None:
        digits = {
            "零": 0,
            "〇": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if value.isdigit():
            return int(value)
        if value in digits:
            return digits[value]
        total = 0
        current = 0
        for char in value:
            if char in digits:
                current = digits[char]
            elif char == "十":
                total += (current or 1) * 10
                current = 0
            else:
                return None
        return total + current

    @staticmethod
    def _hour_expression(text: str) -> tuple[int, int, str] | None:
        match = re.search(
            r"(上午|早上|下午|中午|晚上)?([0-9一二两三四五六七八九十]+)[点點時时](半|([0-9一二两三四五六七八九十]+)分?)?",
            text,
        )
        if match is None:
            return None
        period = match.group(1) or ""
        hour = TemporalNormalizer._parse_chinese_number(match.group(2))
        if hour is None:
            return None
        minute = (
            TemporalNormalizer._parse_chinese_number(match.group(4))
            if match.group(4)
            else (30 if match.group(3) == "半" else 0)
        )
        if minute is None:
            minute = 0
        if period in {"下午", "晚上"} and hour < 12:
            hour += 12
        if period == "中午" and hour < 11:
            hour += 12
        display_period = period or ("下午" if hour >= 12 else "上午")
        return hour, minute, display_period


def parse_temporal_query(
    query: str,
    *,
    now: datetime | None = None,
    timezone_name: str | None = None,
) -> TemporalQuery | None:
    """Extract temporal filter and recall intent from a natural-language question."""

    text = (query or "").strip()
    if not text:
        return None
    ref = now or datetime.now(timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    asks_when = bool(_TEMPORAL_WHEN_RE.search(text))
    asks_what = bool(_TEMPORAL_WHAT_HAPPENED_RE.search(text))
    normalizer = TemporalNormalizer()

    matched_expr = ""
    matched_span: tuple[int, int] | None = None
    for pattern in _QUERY_TIME_PATTERNS:
        found = pattern.search(text)
        if found:
            matched_expr = found.group(0).strip()
            matched_span = found.span()
            break

    if not matched_expr and not asks_when and not asks_what:
        return None

    if matched_expr:
        # Ignore bare "最近" when it's just conversational filler unless paired with what-happened.
        if matched_expr == "近期" and not asks_what and not asks_when:
            return None
        semantics = normalizer.normalize(
            matched_expr,
            observed_at=ref,
            timezone_name=timezone_name,
            prefer_past=True,
        )
        if semantics.start_at is not None:
            residual = (
                f"{text[:matched_span[0]]} {text[matched_span[1]:]}".strip()
                if matched_span
                else text
            )
            return TemporalQuery(
                original_expression=matched_expr,
                normalized_display=semantics.normalized_display,
                timezone=semantics.timezone,
                granularity=semantics.granularity,
                start_at=semantics.start_at,
                end_at=semantics.end_at,
                is_temporal_recall=bool(asks_what or asks_when or semantics.start_at is not None),
                asks_when=asks_when,
                residual_query=residual,
            )

    if asks_when or asks_what:
        tz, _ = TemporalNormalizer._resolve_timezone(timezone_name or _DEFAULT_TIMEZONE)
        return TemporalQuery(
            original_expression="",
            normalized_display="",
            timezone=tz.key,
            granularity="open_ended",
            start_at=None,
            end_at=None,
            is_temporal_recall=True,
            asks_when=asks_when,
            residual_query=text,
        )
    return None


def _as_aware_utc(value: datetime | None, default_tz: str = _DEFAULT_TIMEZONE) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def temporal_overlap_score(
    query_start: datetime | None,
    query_end: datetime | None,
    candidate_start: datetime | None,
    candidate_end: datetime | None = None,
) -> float:
    """Return a [0.0, 1.0] temporal match score between a query window and a memory event."""

    if query_start is None or candidate_start is None:
        return 0.0
    qs = _as_aware_utc(query_start)
    qe = _as_aware_utc(query_end) or (qs + timedelta(days=1))  # type: ignore[operator]
    cs = _as_aware_utc(candidate_start)
    ce = _as_aware_utc(candidate_end)
    assert qs is not None and qe is not None and cs is not None
    if ce is None or ce <= cs:
        # Point event: strictly inside [qs, qe)
        if qs <= cs < qe:
            return 1.0
        gap_seconds = (qs - cs).total_seconds() if cs < qs else (cs - qe).total_seconds()
    else:
        # Interval overlap: cs < qe and ce > qs
        if cs < qe and ce > qs:
            return 1.0
        gap_seconds = (qs - ce).total_seconds() if ce <= qs else (cs - qe).total_seconds()
    # Mild boundary tolerance (up to 24h) for timezone/near-day queries.
    if gap_seconds <= 86_400:
        return max(0.0, 0.45 * (1.0 - gap_seconds / 86_400))
    return 0.0


def temporal_semantics_payload(semantics: TemporalSemantics) -> dict[str, Any]:
    return {"temporal_semantics": semantics.as_dict()}
