"""「关闭思考」在能力层的表达与后台记忆画像的快速档回归测试。

修复背景：relay（sub2api 式网关）上的 `deepseek-flash` 快照是
``reasoning_parameter="reasoning_effort"`` + ``thinking_mapping["off"]=null``。
该组合下请求「off」会退化为**不发任何思考字段**，上游便按自己的默认
（思考开启）执行——记忆报告整合因此每次都要先思考 60s+，必然撞上 60s
客户端超时而返回 502。而上游其实支持通用开关 ``enable_thinking: false``。

锁定规则：
- 默认行为不变：不显式申请回退时，off 仍然不发任何思考字段；
- 显式申请回退时：off 产出 ``enable_thinking=False``（``thinking`` 类映射
  产出 ``{"type": "disabled"}``）；
- ``thinking_required`` 的模型仍然拒绝 off；
- 既有三类映射（enable_thinking / thinking_budget / thinking）语义不变。
"""
from __future__ import annotations

import pytest

from app.providers.model_options import (
    DISABLE_THINKING_PARAMETER,
    ModelCapabilityError,
    resolve_model_call_options,
)
from app.providers.remote.openai import ProviderHTTPError
from app.services.memory_profile import _rejected_request_shape


def _relay_snapshot(**overrides: object) -> dict:
    """A relay-observed model: reasoning_effort with no "off" tier."""

    model = {
        "reasoning_efforts": ["low", "medium", "high", "xhigh"],
        "thinking_mapping": {
            "off": None,
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "xhigh",
        },
        "default_thinking_mode": "medium",
        "reasoning_parameter": "reasoning_effort",
        "thinking_required": False,
        "capability_source": "runtime_observation",
    }
    model.update(overrides)
    return {"provider_family": "openai_compatible", "models": {"deepseek-flash": model}}


class TestOffFallback:
    def test_off_without_fallback_keeps_legacy_behaviour(self) -> None:
        options = resolve_model_call_options(
            _relay_snapshot(), "deepseek-flash", thinking_mode="off"
        )
        assert options.thinking_mode == "off"
        assert options.actual_reasoning_effort is None
        assert options.provider_options == {}

    def test_off_with_fallback_emits_disable_switch(self) -> None:
        options = resolve_model_call_options(
            _relay_snapshot(),
            "deepseek-flash",
            thinking_mode="off",
            disable_thinking_fallback=True,
        )
        assert options.provider_options == {DISABLE_THINKING_PARAMETER: False}
        # The declared parameter still owns the payload when a tier is selected.
        assert options.reasoning_parameter == "reasoning_effort"
        assert options.actual_reasoning_effort is None

    def test_fallback_does_not_touch_labelled_modes(self) -> None:
        for mode in ("low", "medium", "high", "xhigh"):
            options = resolve_model_call_options(
                _relay_snapshot(),
                "deepseek-flash",
                thinking_mode=mode,
                disable_thinking_fallback=True,
            )
            assert options.provider_options == {}
            assert options.actual_reasoning_effort == mode

    def test_thinking_parameter_snapshot_gets_disabled_object(self) -> None:
        snapshot = _relay_snapshot(
            reasoning_parameter="thinking",
            thinking_mapping={"off": None, "low": "low", "medium": "medium"},
        )
        options = resolve_model_call_options(
            snapshot,
            "deepseek-flash",
            thinking_mode="off",
            disable_thinking_fallback=True,
        )
        assert options.provider_options == {"thinking": {"type": "disabled"}}

    def test_thinking_required_models_still_reject_off(self) -> None:
        snapshot = _relay_snapshot(thinking_required=True, default_thinking_mode="medium")
        with pytest.raises(ModelCapabilityError):
            resolve_model_call_options(
                snapshot,
                "deepseek-flash",
                thinking_mode="off",
                disable_thinking_fallback=True,
            )


class TestExistingMappingsUnchanged:
    def test_enable_thinking_mapping_is_untouched(self) -> None:
        snapshot = _relay_snapshot(
            reasoning_parameter="enable_thinking",
            thinking_mapping={
                "off": False,
                "low": True,
                "medium": True,
                "high": True,
                "xhigh": True,
            },
        )
        off = resolve_model_call_options(
            snapshot,
            "deepseek-flash",
            thinking_mode="off",
            disable_thinking_fallback=True,
        )
        assert off.provider_options == {"enable_thinking": False}
        high = resolve_model_call_options(
            snapshot, "deepseek-flash", thinking_mode="high"
        )
        assert high.provider_options == {"enable_thinking": True}

    def test_thinking_budget_mapping_is_untouched(self) -> None:
        snapshot = _relay_snapshot(
            reasoning_parameter="thinking_budget",
            thinking_mapping={
                "off": None,
                "low": 2048,
                "medium": 4096,
                "high": 8192,
                "xhigh": 16384,
            },
        )
        off = resolve_model_call_options(
            snapshot,
            "deepseek-flash",
            thinking_mode="off",
            disable_thinking_fallback=True,
        )
        assert off.provider_options == {"enable_thinking": False}
        low = resolve_model_call_options(
            snapshot, "deepseek-flash", thinking_mode="low"
        )
        assert low.provider_options == {"enable_thinking": True, "thinking_budget": 2048}


class TestRejectedRequestShape:
    def test_only_http_400_counts_as_rejected_request(self) -> None:
        assert _rejected_request_shape(ProviderHTTPError("bad body", status_code=400))
        assert not _rejected_request_shape(ProviderHTTPError("gateway", status_code=502))
        assert not _rejected_request_shape(
            ProviderHTTPError("Provider structured generation timed out")
        )
        assert not _rejected_request_shape(RuntimeError("boom"))
        assert not _rejected_request_shape(None)
