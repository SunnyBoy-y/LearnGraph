"""未知模型的默认输出上限：至少 142000，并按上下文窗口收敛。

背景：默认值原本是 4096。教学包的试卷/教材是长结构化输出，4096 必被截断，而截断
只表现为"结构化生成失败"，与真实原因完全不符。抬高默认值的同时必须收敛，否则上下文
窗口较小的模型会开始收到自己无法满足的请求（上游 400）。
"""

from __future__ import annotations

import pytest

from app.providers.model_options import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    MIN_INPUT_HEADROOM_TOKENS,
    _CATALOG_BASE_CAPABILITIES,
    catalog_capability_snapshot,
    clamp_max_output_tokens,
    model_capabilities_for_model,
)


def test_default_output_cap_is_at_least_142000():
    assert DEFAULT_MAX_OUTPUT_TOKENS >= 142_000
    assert _CATALOG_BASE_CAPABILITIES["max_output_tokens"] == DEFAULT_MAX_OUTPUT_TOKENS


def test_unknown_model_catalog_snapshot_gets_the_new_default():
    """目录里没有这条记录时，快照必须带上够用的默认上限（而不是 4096）。"""
    snapshot = catalog_capability_snapshot("totally-unknown-model-xyz")

    assert snapshot["max_output_tokens"] >= 142_000


def test_known_model_keeps_its_own_declared_cap():
    """已知模型的目录值只能被上下文窗口收敛，不能被默认值替换。"""
    from app.providers.model_catalog import unified_model_defaults

    declared = int(unified_model_defaults("deepseek-chat").get("max_output_tokens") or 0)
    snapshot = catalog_capability_snapshot("deepseek-chat")

    assert declared > 0
    assert snapshot["max_output_tokens"] == clamp_max_output_tokens(
        declared, snapshot["context_window_tokens"]
    )


@pytest.mark.parametrize(
    ("declared", "window", "expected"),
    [
        (142_000, 200_000, 142_000),
        (142_000, 256_000, 142_000),
        (142_000, 1_000_000, 142_000),
        (142_000, 0, 142_000),
        (142_000, 32_000, 32_000 - 12_288),
        (142_000, 128_000, 96_000),
        (1_000_000, 200_000, 150_000),
        (0, 200_000, 1),
    ],
)
def test_cap_is_clamped_by_the_context_window(declared, window, expected):
    assert clamp_max_output_tokens(declared, window) == expected


def test_the_clamp_never_drops_below_one_token():
    assert clamp_max_output_tokens(142_000, 8_000) >= 1
    assert clamp_max_output_tokens(142_000, 1_000) >= 1


def test_context_window_merge_still_implies_a_sane_chat_input_budget():
    """chat.py 用 上下文窗口 − 输出上限 − 4096 估输入预算；默认值不能把它压到地板。"""
    window = 128_000
    effective = clamp_max_output_tokens(DEFAULT_MAX_OUTPUT_TOKENS, window)

    assert window - effective - 4_096 >= 8_000


def test_unknown_model_overlay_still_defers_to_the_caller_default():
    """目录覆盖层不含该键时，真正兜底的是 provider 工厂的默认值（此处锁死这个约定）。"""
    overlay = model_capabilities_for_model({}, "totally-unknown-model-xyz")

    assert overlay.get("max_output_tokens") is None
    assert overlay["models_dev_known"] is False
