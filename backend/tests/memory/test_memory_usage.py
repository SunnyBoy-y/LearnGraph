"""Ordinary requests must not inherit unrequested historical themes."""
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.domain.models import utc_now
from app.services.memory_usage import memory_usage
from app.services.memory_batching import extraction_readiness


@pytest.mark.parametrize("theme", ["赛车", "烹饪", "音乐", "足球", "科幻"])
@pytest.mark.parametrize("query", ["做个动画", "做一个海浪动画", "帮我写个故事", "设计一个网页"])
def test_unrequested_theme_is_never_inserted(theme, query):
    decision = memory_usage(query, f"用户喜欢{theme}题材。", memory_type="learning_preference", semantic=0.99)
    assert not decision.allowed


@pytest.mark.parametrize("query", ["按我喜欢的主题做个动画", "根据我的偏好推荐题材"])
def test_user_can_authorize_personalized_suggestions(query):
    decision = memory_usage(query, "用户喜欢赛车题材。", memory_type="learning_preference")
    assert decision.allowed and decision.allowed_effect == "theme_suggestion"


@pytest.mark.parametrize("query", ["按我的偏好做海浪动画", "按我的喜好做动画，但不要赛车", "做动画，不要按我的偏好"])
def test_current_topic_and_exclusion_override_personalization(query):
    assert not memory_usage(query, "用户喜欢赛车题材。", memory_type="learning_preference").allowed


def test_mixed_theme_and_style_cannot_bypass_theme_gate():
    assert not memory_usage("做个动画", "用户喜欢赛车和简洁讲解。", policy={"allowed_effect": "presentation"}).allowed


def test_real_presentation_preference_keeps_working():
    decision = memory_usage("做个动画", "用户喜欢简洁讲解。", memory_type="learning_preference")
    assert decision.allowed and decision.allowed_effect == "presentation"


def test_english_presentation_preference_is_preserved():
    decision = memory_usage("Create an animation", "I prefer concise explanations", memory_type="learning_preference")
    assert decision.allowed and decision.allowed_effect == "presentation"


@pytest.mark.parametrize("statement", ["回复尽量简洁", "Use concise explanations"])
def test_explicitly_saved_presentation_instruction_is_preserved(statement):
    decision = memory_usage("做个动画", statement, memory_type="learning_preference", preference_verified=True)
    assert decision.allowed and decision.allowed_effect == "presentation"


def test_weak_semantic_similarity_does_not_count_as_applicability():
    assert not memory_usage("做个动画", "PostgreSQL 事务隔离", semantic=0.3).allowed


def test_user_correction_does_not_turn_into_global_dislike():
    decision = memory_usage("这次不要太简洁，详细讲", "用户喜欢简洁讲解。", memory_type="learning_preference")
    assert not decision.allowed


def test_task_id_alone_cannot_reuse_previous_theme():
    assert not memory_usage("做个动画", "用户完成过赛车游戏项目。", memory_type="event_summary", same_task=True, same_conversation=True).allowed
    assert memory_usage("继续上次的任务", "用户完成过赛车游戏项目。", memory_type="event_summary", same_task=True, same_conversation=True).allowed
    assert not memory_usage("换个任务，做个动画", "用户完成过赛车游戏项目。", memory_type="event_summary", same_task=True, same_conversation=True).allowed


@pytest.mark.parametrize("query", ["继续讲微积分", "继续上次", "上次做赛车，这次做个动画", "不要继续上次项目，做个动画"])
def test_continuation_is_not_blanket_authorization_for_tastes(query):
    assert not memory_usage(query, "用户喜欢赛车题材。", memory_type="learning_preference", same_conversation=True).allowed


def test_preference_evidence_cannot_borrow_a_topic_from_another_clause():
    from app.services.memory_usage import evidence_has_preference
    assert not evidence_has_preference("我喜欢烹饪，赛车很难", "用户喜欢赛车")


def test_explicit_question_can_recall_preferences():
    assert memory_usage("我喜欢什么题材？", "用户喜欢赛车题材。", memory_type="learning_preference").allowed


def test_disable_personalization_also_excludes_presentation_preferences():
    assert not memory_usage("做个动画，不要按我的偏好", "用户喜欢简洁讲解。", memory_type="learning_preference").allowed


def test_referenced_document_is_not_the_users_preference():
    assert memory_usage("用户偏好研究", "本文讨论用户偏好，作者喜欢赛车", memory_type="external_reference", preference_verified=False).allowed


def _batch(count=1, *, age=0, text="调整这个项目", closed=False):
    now = utc_now()
    messages = [SimpleNamespace(content=text, created_at=now - timedelta(seconds=age)) for _ in range(count)]
    session = SimpleNamespace(status="closed" if closed else "active", closed_at=now if closed else None, updated_at=messages[-1].created_at)
    settings = SimpleNamespace(memory_extraction_idle_seconds=900, memory_extraction_min_user_turns=10)
    return messages, session, settings, now


def test_batch_waits_for_enough_turns_or_idle_without_model():
    messages, session, settings, now = _batch(9)
    result = extraction_readiness(messages, session, settings, now=now)
    assert not result.ready and result.reason == "batch_deferred" and result.retry_after_seconds == 900
    messages, session, settings, now = _batch(10)
    assert extraction_readiness(messages, session, settings, now=now).ready
    messages, session, settings, now = _batch(1, age=901)
    assert extraction_readiness(messages, session, settings, now=now).reason == "idle"


@pytest.mark.parametrize("text,closed", [("请记住我喜欢简洁讲解", False), ("这个任务完成了", False), ("换个任务做动画", False), ("项目完成", True)])
def test_explicit_edits_and_task_boundaries_do_not_wait(text, closed):
    messages, session, settings, now = _batch(text=text, closed=closed)
    assert extraction_readiness(messages, session, settings, now=now).ready


def test_later_turn_cannot_postpone_an_unprocessed_explicit_memory_edit():
    messages, session, settings, now = _batch(2)
    messages[0].content = "请记住我喜欢简洁讲解"
    assert extraction_readiness(messages, session, settings, now=now).reason == "explicit_memory_intent"
