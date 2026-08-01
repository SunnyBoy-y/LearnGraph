"""Tests for the pure-rule episode boundary detector.

The module under test imports nothing from the app backend, so these tests
need no database, no fixtures, and run in milliseconds.
"""

from __future__ import annotations

import pytest

from app.services.episode_boundary import (
    ENTITY_OVERLAP_MIN,
    IDLE_SECONDS_MIN,
    TOKEN_THRESHOLD_MAX,
    TOKEN_THRESHOLD_MIN,
    BoundaryInputs,
    BoundaryReason,
    BoundarySignal,
    boundary_reason_value,
    detect_boundary,
    reason_label,
    top_boundary_signal,
)


def test_no_signals_on_quiet_inputs():
    signals = detect_boundary(BoundaryInputs())
    assert signals == ()


def test_explicit_topic_switch_fires_highest_priority():
    signals = detect_boundary(BoundaryInputs(explicit_topic_switch=True))
    assert signals[0].reason == BoundaryReason.EXPLICIT_SWITCH
    assert signals[0].priority == 1


def test_task_stage_completed_fires():
    signal = top_boundary_signal(BoundaryInputs(task_stage_completed=True))
    assert signal is not None
    assert signal.reason == BoundaryReason.STAGE_COMPLETED


def test_conversation_close_fires():
    signal = top_boundary_signal(BoundaryInputs(conversation_closed=True))
    assert signal is not None
    assert signal.reason == BoundaryReason.CONVERSATION_CLOSE


def test_token_threshold_below_min_does_not_fire():
    signals = detect_boundary(
        BoundaryInputs(cumulative_tokens_since_episode=TOKEN_THRESHOLD_MIN - 1)
    )
    assert all(s.reason != BoundaryReason.TOKEN_THRESHOLD for s in signals)


def test_token_threshold_at_min_fires_with_low_strength():
    signal = top_boundary_signal(
        BoundaryInputs(cumulative_tokens_since_episode=TOKEN_THRESHOLD_MIN)
    )
    assert signal is not None
    assert signal.reason == BoundaryReason.TOKEN_THRESHOLD
    assert signal.strength == pytest.approx(0.0, abs=1e-9)


def test_token_threshold_at_max_fires_with_full_strength():
    signal = top_boundary_signal(
        BoundaryInputs(cumulative_tokens_since_episode=TOKEN_THRESHOLD_MAX)
    )
    assert signal is not None
    assert signal.reason == BoundaryReason.TOKEN_THRESHOLD
    assert signal.strength == pytest.approx(1.0, abs=1e-9)


def test_token_threshold_mid_window_ramps_strength():
    mid = (TOKEN_THRESHOLD_MIN + TOKEN_THRESHOLD_MAX) / 2
    signal = top_boundary_signal(BoundaryInputs(cumulative_tokens_since_episode=mid))
    assert signal is not None
    assert 0.0 < signal.strength < 1.0


def test_idle_below_threshold_does_not_fire():
    signals = detect_boundary(BoundaryInputs(idle_seconds=IDLE_SECONDS_MIN - 1))
    assert all(s.reason != BoundaryReason.IDLE for s in signals)


def test_idle_at_threshold_fires():
    signal = top_boundary_signal(BoundaryInputs(idle_seconds=IDLE_SECONDS_MIN))
    assert signal is not None
    assert signal.reason == BoundaryReason.IDLE


def test_low_entity_overlap_fires_when_below_threshold():
    signal = top_boundary_signal(
        BoundaryInputs(entity_overlap=ENTITY_OVERLAP_MIN - 0.1)
    )
    assert signal is not None
    assert signal.reason == BoundaryReason.LOW_ENTITY_OVERLAP


def test_high_entity_overlap_does_not_fire():
    signals = detect_boundary(
        BoundaryInputs(entity_overlap=ENTITY_OVERLAP_MIN + 0.1)
    )
    assert all(s.reason != BoundaryReason.LOW_ENTITY_OVERLAP for s in signals)


def test_pre_compression_fires():
    signal = top_boundary_signal(BoundaryInputs(pre_compression=True))
    assert signal is not None
    assert signal.reason == BoundaryReason.PRE_COMPRESSION


def test_multiple_signals_sorted_by_priority():
    """Idle + low overlap + explicit switch must come back in priority order."""
    signals = detect_boundary(
        BoundaryInputs(
            explicit_topic_switch=True,
            idle_seconds=IDLE_SECONDS_MIN + 60,
            entity_overlap=ENTITY_OVERLAP_MIN - 0.2,
        )
    )
    reasons = [s.reason for s in signals]
    assert reasons == [
        BoundaryReason.EXPLICIT_SWITCH,
        BoundaryReason.IDLE,
        BoundaryReason.LOW_ENTITY_OVERLAP,
    ]


def test_top_boundary_signal_returns_highest():
    signal = top_boundary_signal(
        BoundaryInputs(
            explicit_topic_switch=True,
            conversation_closed=True,
        )
    )
    assert signal is not None
    assert signal.reason == BoundaryReason.EXPLICIT_SWITCH


def test_reason_labels_are_stable_snake_case():
    assert reason_label(BoundaryReason.TOKEN_THRESHOLD) == "token_threshold"
    assert reason_label(BoundaryReason.LOW_ENTITY_OVERLAP) == "low_entity_overlap"
    assert reason_label(BoundaryReason.PRE_COMPRESSION) == "pre_compression"


def test_boundary_reason_value_none_when_no_signal():
    assert boundary_reason_value(BoundaryInputs()) == "none"
    assert boundary_reason_value(BoundaryInputs(task_stage_completed=True)) == "task_stage_completed"


def test_strength_out_of_range_rejected():
    with pytest.raises(ValueError):
        BoundarySignal(BoundaryReason.IDLE, 5, 1.5)
