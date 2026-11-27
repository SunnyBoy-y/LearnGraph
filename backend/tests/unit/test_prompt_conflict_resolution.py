"""Verify dynamic prompt compilation eliminates cross-rule conflicts."""

from __future__ import annotations

from types import SimpleNamespace

from app.domain.schemas.stream_ui import Snapshot
from app.prompts.compiler import (
    PromptRuntimeState,
    ResponseStyleConfig,
    build_style_instructions,
)
from app.services.chat import ChatService
from app.services.rich_response import CAPABILITY_CATALOG
from app.services.voice_context import VoiceContextService


def test_mode_tool_policy_guides_to_agent_mode_without_misleading_thinking_mode():
    policy = ChatService._mode_tool_policy(agent_mode_enabled=False)
    assert "带思考力度的模式" not in policy
    assert "智能体模式" in policy
    assert "先基于已有信息给出分析、步骤或草稿" in policy


def test_voice_channel_excludes_text_layout_emoji_and_sticker_rules():
    style = ResponseStyleConfig(
        base_style="exploratory",
        warmth=2,
        enthusiasm=1,
        headings_and_lists=2,
        emoji=2,
        verbosity=2,
    )
    compiled = build_style_instructions(
        style,
        state=PromptRuntimeState(channel="voice", interactive=True, sticker_enabled=True),
    )
    assert "语音语气偏好" in compiled
    assert "热情" in compiled
    assert "多步骤实操" not in compiled
    assert "search_stickers" not in compiled
    assert "系统性展开" not in compiled


def test_voice_context_service_requests_voice_channel():
    captured: dict = {}

    class StubChat:
        model_provider = SimpleNamespace(provider_id="p", model_id="m")

        def _session_timeline(self, _session):
            return []

        def _input_token_budget(self):
            return 1000

        def _build_model_prompt(self, _session, _query, **options):
            captured.update(options)
            return "voice-prompt", None

    VoiceContextService(StubChat()).load_snapshot("sess-1")
    assert captured["channel"] == "voice"
    assert captured["agent_mode"] is True


def test_scheduled_channel_injects_non_interactive_execution_rules():
    from app.services.scheduled_prompt import _needs_user_input

    compiled = build_style_instructions(
        ResponseStyleConfig(),
        state=PromptRuntimeState(channel="scheduled", interactive=False, sticker_enabled=False),
    )
    assert "定时任务非交互执行" in compiled
    assert "采用安全、合理的默认值直接完成" in compiled
    assert "[NEEDS_INPUT]" in compiled
    assert _needs_user_input("[NEEDS_INPUT]\n缺少目标仓库地址") is True
    assert _needs_user_input("  [needs_input] 缺少审批对象") is True
    assert _needs_user_input("已按默认范围生成本周学习总结。") is False


def test_sticker_disabled_does_not_suppress_text_emoji():
    style = ResponseStyleConfig(emoji=2)
    compiled = build_style_instructions(
        style,
        state=PromptRuntimeState(channel="chat", interactive=True, sticker_enabled=False),
    )
    assert "不要调用 search_stickers 或 send_sticker" in compiled
    assert "自然选择是否使用行内 Unicode Emoji" in compiled
    assert "不要主动发送贴纸或表情符号" not in compiled


def test_minimal_verbosity_and_low_headings_override_conflicting_defaults():
    style = ResponseStyleConfig(
        base_style="exploratory",
        headings_and_lists=-2,
        verbosity=-2,
    )
    compiled = build_style_instructions(
        style,
        state=PromptRuntimeState(channel="chat", interactive=True, sticker_enabled=True),
    )
    assert "多步骤实操：编号步骤" not in compiled
    assert "优先用连贯自然段表达" in compiled
    assert "详细度优先约束" in compiled
    assert "工作区显式设置的附加特征（非默认档位）" in compiled
    assert "优先于基础风格与默认输出适配" in compiled


def test_rich_response_graph_and_timeline_examples_match_stream_ui_schema():
    assert "chart/table/graph/diagram/timeline 使用" not in CAPABILITY_CATALOG
    assert "不要给 graph/diagram/timeline 写 rows" in CAPABILITY_CATALOG

    Snapshot.model_validate(
        {
            "component": "graph",
            "block_id": "g1",
            "props": {"title": "关系图"},
            "nodes": [{"id": "a", "label": "节点A"}, {"id": "b", "label": "节点B"}],
            "edges": [{"id": "e1", "source": "a", "target": "b", "label": "关联"}],
        }
    )
    Snapshot.model_validate(
        {
            "component": "timeline",
            "block_id": "t1",
            "props": {"title": "时间线"},
            "items": [{"id": "t1", "label": "阶段一", "date": "2026-01", "description": "说明"}],
        }
    )
