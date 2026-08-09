"""M3 deterministic temporal normalizer and plan state machine regressions."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.memory_plan import (
    canonical_plan_key,
    ensure_plan_canonical_key,
    plan_change_text,
    resolve_target_for_operation,
)
from app.services.temporal_normalizer import (
    NORMALIZER_VERSION,
    TemporalNormalizer,
)

OBSERVED = datetime(2026, 8, 7, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_normalizer_version_and_observed_at() -> None:
    normalizer = TemporalNormalizer()
    semantics = normalizer.normalize("下个月", observed_at=OBSERVED)
    payload = semantics.as_dict()
    assert payload["normalizer_version"] == "temporal-v2"
    assert NORMALIZER_VERSION == "temporal-v2"
    assert payload["original_expression"] == "下个月"
    assert datetime.fromisoformat(payload["observed_at"]) == OBSERVED


def test_month_keeps_display_granularity_without_fake_day() -> None:
    semantics = TemporalNormalizer().normalize("下个月", observed_at=OBSERVED)
    assert semantics.normalized_display == "2026年9月"
    assert semantics.granularity == "month"
    assert semantics.start_at is not None
    assert semantics.start_at.day == 1
    assert semantics.start_at.hour == 0


def test_tomorrow_afternoon_uses_day_period_bounds() -> None:
    semantics = TemporalNormalizer().normalize("明天下午", observed_at=OBSERVED)
    assert semantics.normalized_display == "2026年8月8日下午"
    assert semantics.granularity == "day_period"
    assert semantics.start_at is not None
    assert semantics.end_at is not None
    assert semantics.start_at.hour == 12
    assert semantics.end_at.hour == 18


def test_remaining_granularities_are_deterministic() -> None:
    normalizer = TemporalNormalizer()
    cases = {
        "下周": "week",
        "月底前": "interval",
        "明年": "year",
        "每周五": "recurrence",
        "以后": "interval",
        "最近在学": "open_ended",
    }
    for expression, granularity in cases.items():
        semantics = normalizer.normalize(expression, observed_at=OBSERVED)
        assert semantics.granularity == granularity, expression
        assert semantics.normalizer_version == "temporal-v2"


def test_hour_expression_does_not_round_to_minute() -> None:
    semantics = TemporalNormalizer().normalize("下午三点", observed_at=OBSERVED)
    assert semantics.granularity == "hour"
    assert semantics.start_at is not None
    assert semantics.start_at.hour == 15
    assert semantics.start_at.minute == 0
    assert "下午3点" in semantics.normalized_display


def test_invalid_timezone_falls_back_to_system_default_and_marks_source() -> None:
    semantics = TemporalNormalizer().normalize(
        "明天",
        observed_at=OBSERVED,
        timezone_name="Not/AZone",
    )
    assert semantics.timezone == "Asia/Shanghai"
    assert semantics.timezone_source == "system_default"


def test_plan_canonical_key_is_stable() -> None:
    key = canonical_plan_key("项目评审会议")
    assert key.startswith("plan:")
    assert key == canonical_plan_key(" 项目评审会议 ")


def test_plan_target_resolves_by_canonical_key_not_orphan_create() -> None:
    key = canonical_plan_key("项目评审会议")
    records = [{"id": "m1", "canonical_key": key}]
    assert (
        resolve_target_for_operation(
            "RESCHEDULE",
            target_memory_id=None,
            canonical_key="项目评审会议",
            records=records,
        )
        == "m1"
    )
    assert (
        resolve_target_for_operation(
            "RESCHEDULE",
            target_memory_id=None,
            canonical_key=None,
            records=records,
        )
        is None
    )


def test_plan_change_text_is_natural_language() -> None:
    assert plan_change_text("rescheduled") == "原计划 → 改期"
    assert plan_change_text("cancelled") == "原计划 → 取消"
    assert plan_change_text("completed") == "原计划 → 完成"


def test_plan_key_is_generated_for_plan_like_payload() -> None:
    structured = {"temporal_status": "scheduled"}
    enriched = ensure_plan_canonical_key(structured, title="学习 Rust")
    assert enriched["canonical_key"] == canonical_plan_key("学习 Rust")
