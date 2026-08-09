"""Deterministic temporal normalization for memory extraction.

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
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

NORMALIZER_VERSION = "temporal-v2"
_DEFAULT_TIMEZONE = "Asia/Shanghai"

_DAY_PERIODS: dict[str, tuple[time, time]] = {
    "清晨": (time(5, 0), time(8, 0)),
    "早上": (time(6, 0), time(12, 0)),
    "上午": (time(6, 0), time(12, 0)),
    "中午": (time(11, 0), time(14, 0)),
    "下午": (time(12, 0), time(18, 0)),
    "傍晚": (time(17, 0), time(20, 0)),
    "晚上": (time(18, 0), time(23, 59, 59)),
}


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
            "normalized_display": self.normalized_display,
            "timezone": self.timezone,
            "timezone_source": self.timezone_source,
            "granularity": self.granularity,
            "start_at": _iso(self.start_at),
            "end_at": _iso(self.end_at),
            "observed_at": _iso(self.observed_at),
            "normalizer_version": self.normalizer_version,
        }


class TemporalNormalizer:
    """Parse common Chinese relative time expressions deterministically."""

    def __init__(self, *, version: str = NORMALIZER_VERSION) -> None:
        self.version = version

    def normalize(
        self,
        expression: str,
        *,
        observed_at: datetime,
        timezone_name: str | None = None,
    ) -> TemporalSemantics:
        text = (expression or "").strip()
        timezone_name = timezone_name or _DEFAULT_TIMEZONE
        tz, timezone_source = self._resolve_timezone(timezone_name)
        local_obs = observed_at.astimezone(tz)
        local_date = local_obs.date()

        # Absolute ISO-ish inputs.
        iso = self._try_iso(text)
        if iso is not None:
            start, end, granularity, display = iso
            return TemporalSemantics(
                text,
                display,
                tz.key,
                granularity,
                start,
                end,
                observed_at,
                self.version,
                timezone_source,
            )

        # 今天/明天/后天/昨天 + optional day period.
        day_offset = self._day_offset(text)
        if day_offset is not None:
            return self._day(text, local_date, day_offset, tz, observed_at, timezone_source)

        # Year expressions.
        year = self._year_expression(text, local_date)
        if year is not None:
            year_value = year
            start = datetime(year_value, 1, 1, tzinfo=tz)
            end = datetime(year_value + 1, 1, 1, tzinfo=tz)
            display = f"{year_value}年"
            return TemporalSemantics(
                text, display, tz.key, "year", start, end, observed_at, self.version, timezone_source
            )

        # 下个月 / 本月 / 这个月 / 上个月.
        month_shift = self._month_shift(text)
        if month_shift is not None:
            first = local_date.replace(day=1)
            if month_shift == 1:
                month_start = (first + timedelta(days=32)).replace(day=1)
            elif month_shift == -1:
                month_start = (first - timedelta(days=1)).replace(day=1)
            else:
                month_start = first
            month_end = (month_start.replace(day=1) + timedelta(days=32)).replace(day=1)
            display = f"{month_start.year}年{month_start.month}月"
            return TemporalSemantics(
                text,
                display,
                tz.key,
                "month",
                datetime.combine(month_start, time.min, tzinfo=tz),
                datetime.combine(month_end, time.min, tzinfo=tz),
                observed_at,
                self.version,
                timezone_source,
            )

        # 月底前 / 月末前.
        if "月底" in text or "月末" in text or "月底前" in text:
            month_end = (local_date.replace(day=1) + timedelta(days=32)).replace(day=1)
            display = f"截至{month_end.year}年{month_end.month}月{month_end.day}日"
            return TemporalSemantics(
                text,
                display,
                tz.key,
                "interval",
                datetime.combine(local_date, time.min, tzinfo=tz),
                datetime.combine(month_end, time.min, tzinfo=tz),
                observed_at,
                self.version,
                timezone_source,
            )

        # 下周 / 本周 / 上周 / 下个星期.
        week_shift = self._week_shift(text)
        if week_shift is not None:
            week_start = local_date + timedelta(days=(7 - local_date.weekday()) + 7 * week_shift)
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
                observed_at,
                self.version,
                timezone_source,
            )

        # 每周X/每天/每周末 -> recurrence (no fake dates).
        if self._is_recurrence(text):
            return TemporalSemantics(
                text, text, tz.key, "recurrence", None, None, observed_at, self.version, timezone_source
            )

        # 下午三点 / 3点 / 15:00-style already handled above.
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
                observed_at,
                self.version,
                timezone_source,
            )

        # 以后 / 今后 -> interval from now onward.
        if any(marker in text for marker in ("以后", "今后", "之后")):
            return TemporalSemantics(
                text,
                text,
                tz.key,
                "interval",
                local_obs,
                None,
                observed_at,
                self.version,
                timezone_source,
            )

        return self._open_ended(text, observed_at, tz.key, timezone_source)

    def _day(
        self,
        text: str,
        local_date: datetime.date,
        day_offset: int,
        tz: ZoneInfo,
        observed_at: datetime,
        timezone_source: str,
    ) -> TemporalSemantics:
        target_date = local_date + timedelta(days=day_offset)
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
    def _day_offset(text: str) -> int | None:
        if text.startswith(("今天", "今日")):
            return 0
        if text.startswith(("明天", "明日")):
            return 1
        if text.startswith("后天"):
            return 2
        if text.startswith(("昨天", "昨日")):
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
    def _year_expression(text: str, local_date: datetime.date) -> int | None:
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
        if any(marker in text for marker in ("下个月", "下月", "下个月份")):
            return 1
        if any(marker in text for marker in ("本月", "这个月", "当月")):
            return 0
        if any(marker in text for marker in ("上个月", "上月")):
            return -1
        return None

    @staticmethod
    def _week_shift(text: str) -> int | None:
        if any(marker in text for marker in ("下个星期", "下星期", "下周")):
            return 1
        if any(marker in text for marker in ("本星期", "本周", "这个星期", "这周")):
            return 0
        if any(marker in text for marker in ("上个星期", "上星期", "上周")):
            return -1
        return None

    @staticmethod
    def _is_recurrence(text: str) -> bool:
        return bool(
            re.search(r"每(天|日|周|月|年|星期|周末|[一二三四五六日天])", text)
            or re.search(r"(每天|每日|每周|每月|每年|每个星期|每个周末)", text)
        )

    @staticmethod
    def _try_iso(text: str) -> tuple[datetime, datetime, str, str] | None:
        cleaned = text.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(cleaned)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=ZoneInfo(_DEFAULT_TIMEZONE))
        if parsed.hour or parsed.minute or parsed.second:
            display = f"{parsed.year}年{parsed.month}月{parsed.day}日{parsed.hour:02d}:{parsed.minute:02d}"
            return parsed, parsed + timedelta(minutes=1), "minute", display
        display = f"{parsed.year}年{parsed.month}月{parsed.day}日"
        return parsed, parsed + timedelta(days=1), "date", display

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


def temporal_semantics_payload(semantics: TemporalSemantics) -> dict[str, Any]:
    return {"temporal_semantics": semantics.as_dict()}
