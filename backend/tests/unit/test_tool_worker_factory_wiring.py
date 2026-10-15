"""Verify the HTTP session path wires tool_worker_factory (方案 A).

The `api/routers/chat.py` service() previously constructed ChatService WITHOUT
tool_worker_factory, so `_can_parallelize_batch` short-circuited to False and
parallel-safe tools always ran serially. These tests pin the wiring behavior:
a set factory enables parallel batch scheduling; None disables it.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.services.chat import ChatService


def _make_service(factory, parallel_enabled: bool = True) -> ChatService:
    # __new__ 绕过 __init__，只设置 _can_parallelize_batch 依赖的两个字段。
    svc = ChatService.__new__(ChatService)
    svc.settings = SimpleNamespace(agent_parallel_tools_enabled=parallel_enabled)
    svc.tool_worker_factory = factory
    return svc


def _two_parallel_tools() -> list[dict]:
    return [
        {"function": {"name": "search_web", "arguments": "{}"}},
        {"function": {"name": "get_current_time", "arguments": "{}"}},
    ]


def test_factory_none_disables_parallel() -> None:
    svc = _make_service(None)
    assert svc._can_parallelize_batch(_two_parallel_tools()) is False


def test_factory_set_enables_parallel() -> None:
    svc = _make_service(lambda worker_db: None)
    assert svc._can_parallelize_batch(_two_parallel_tools()) is True


def test_factory_set_but_feature_disabled_stays_serial() -> None:
    svc = _make_service(lambda worker_db: None, parallel_enabled=False)
    assert svc._can_parallelize_batch(_two_parallel_tools()) is False


def test_factory_set_single_tool_stays_serial() -> None:
    svc = _make_service(lambda worker_db: None)
    single = [{"function": {"name": "search_web", "arguments": "{}"}}]
    assert svc._can_parallelize_batch(single) is False
