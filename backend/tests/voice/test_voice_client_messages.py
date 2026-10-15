"""离线验证控制面客户端消息与口语预算（契约 §2.5 / §2.6）。

`learngraph-playback` 是浏览器自述的播放位置：它**只**用于回放进度与体验，绝不能
改变回合的终态判定。这里用替身 journal 断言"写了 ack、没写别的"，并用真实的
`VoiceGenerationGate` 断言打断去重的行为。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.voice.embedded_bot import (
    InterruptIdDeduplicator,
    SYSTEM_INSTRUCTION,
    VOICE_INTERRUPT_MESSAGE,
    VOICE_PLAYBACK_MESSAGE,
    VOICE_TYPED_TURN_MESSAGE,
    handle_learngraph_client_message,
    speech_budget_instruction,
)
from app.voice.embedded_generation import VoiceGenerationGate


class _RecordingJournal:
    """记录每一次调用的 journal 替身：断言"只写了该写的"。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def playback_ack(
        self,
        *,
        sentence_seq: int | None,
        played_ms: int,
        phase: str,
        generation_id: int | None = None,
    ) -> None:
        self.calls.append(
            (
                "playback_ack",
                {
                    "sentence_seq": sentence_seq,
                    "played_ms": played_ms,
                    "phase": phase,
                    "generation_id": generation_id,
                },
            )
        )

    def expect_typed_turn(self, client_message_id: str, text: str) -> None:
        self.calls.append(
            ("expect_typed_turn", {"client_message_id": client_message_id, "text": text})
        )

    async def sentence_ended(self, **kwargs: Any) -> None:
        self.calls.append(("sentence_ended", kwargs))


def _message(message_type: str, data: Any = None) -> Any:
    return SimpleNamespace(type=message_type, data=data)


async def _dispatch(
    message: Any,
    *,
    journal: _RecordingJournal | None = None,
    gate: VoiceGenerationGate | None = None,
    interrupts: InterruptIdDeduplicator | None = None,
    interrupted: list[int] | None = None,
) -> tuple[VoiceGenerationGate, list[int]]:
    gate = gate if gate is not None else VoiceGenerationGate()
    interrupts = interrupts if interrupts is not None else InterruptIdDeduplicator()
    calls = interrupted if interrupted is not None else []

    async def _interrupt_bot() -> None:
        calls.append(1)

    await handle_learngraph_client_message(
        message=message,
        journal=journal,  # type: ignore[arg-type]
        generation_gate=gate,
        interrupt_bot=_interrupt_bot,
        interrupts=interrupts,
    )
    return gate, calls


# ----------------------------------------------------- §2.5 播放确认


def test_playback_ack_is_forwarded_to_the_journal_verbatim():
    async def _run() -> None:
        journal = _RecordingJournal()
        await _dispatch(
            _message(
                VOICE_PLAYBACK_MESSAGE,
                {
                    "turn_id": "turn_a",
                    "sentence_seq": 2,
                    "played_ms": 1234,
                    "phase": "progress",
                    "generation_id": 5,
                },
            ),
            journal=journal,
        )

        assert journal.calls == [
            (
                "playback_ack",
                {
                    "sentence_seq": 2,
                    "played_ms": 1234,
                    "phase": "progress",
                    "generation_id": 5,
                },
            )
        ]

    asyncio.run(_run())


def test_playback_ack_only_writes_the_ack_never_turn_state():
    """体验用的确认不得参与 finalize/memory：除了 ack 不许写任何别的事件。"""
    async def _run() -> None:
        journal = _RecordingJournal()
        gate = VoiceGenerationGate()
        for phase in ("progress", "ended"):
            await _dispatch(
                _message(
                    VOICE_PLAYBACK_MESSAGE,
                    {"sentence_seq": 1, "played_ms": 10, "phase": phase, "generation_id": 0},
                ),
                journal=journal,
                gate=gate,
            )

        assert {name for name, _ in journal.calls} == {"playback_ack"}
        # 播放确认不是打断：闸门不动、远端音频不被中断。
        assert gate.generation == 0

    asyncio.run(_run())


def test_playback_ack_tolerates_missing_or_garbage_fields():
    """客户端字段缺失/非法不该打断这条通道，也不该写进一个假数字。"""
    async def _run() -> None:
        journal = _RecordingJournal()
        await _dispatch(
            _message(VOICE_PLAYBACK_MESSAGE, {"sentence_seq": "abc", "played_ms": None}),
            journal=journal,
        )
        await _dispatch(_message(VOICE_PLAYBACK_MESSAGE, None), journal=journal)

        assert journal.calls == [
            ("playback_ack", {"sentence_seq": None, "played_ms": 0, "phase": "", "generation_id": None}),
            ("playback_ack", {"sentence_seq": None, "played_ms": 0, "phase": "", "generation_id": None}),
        ]

    asyncio.run(_run())


def test_playback_ack_without_a_journal_is_a_noop():
    async def _run() -> None:
        gate, interrupted = await _dispatch(
            _message(VOICE_PLAYBACK_MESSAGE, {"sentence_seq": 1, "played_ms": 5}),
            journal=None,
        )

        assert gate.generation == 0
        assert interrupted == []

    asyncio.run(_run())


# ------------------------------------------------ §4.2 F10 打断去重


def test_duplicate_interrupt_id_interrupts_once():
    """同一个 interruptId 只能打断一次：多余的那次会把下一轮回答判成残留音频。"""
    async def _run() -> None:
        gate = VoiceGenerationGate()
        interrupts = InterruptIdDeduplicator()
        interrupted: list[int] = []
        message = _message(VOICE_INTERRUPT_MESSAGE, {"interruptId": "i-1", "generation_id": 0})

        await _dispatch(message, gate=gate, interrupts=interrupts, interrupted=interrupted)
        await _dispatch(message, gate=gate, interrupts=interrupts, interrupted=interrupted)
        await _dispatch(message, gate=gate, interrupts=interrupts, interrupted=interrupted)

        assert interrupted == [1]
        assert gate.generation == 1

    asyncio.run(_run())


def test_distinct_interrupt_ids_each_interrupt():
    async def _run() -> None:
        gate = VoiceGenerationGate()
        interrupts = InterruptIdDeduplicator()
        interrupted: list[int] = []

        for index in range(3):
            await _dispatch(
                _message(VOICE_INTERRUPT_MESSAGE, {"interruptId": f"i-{index}"}),
                gate=gate,
                interrupts=interrupts,
                interrupted=interrupted,
            )

        assert interrupted == [1, 1, 1]
        assert gate.generation == 3

    asyncio.run(_run())


def test_interrupt_without_an_id_still_interrupts_every_time():
    """老客户端（或在途的 `d: null`）没有 id：宁可按真打断处理，也不能吞掉。"""
    async def _run() -> None:
        gate = VoiceGenerationGate()
        interrupts = InterruptIdDeduplicator()
        interrupted: list[int] = []

        for _ in range(2):
            await _dispatch(
                _message(VOICE_INTERRUPT_MESSAGE, None),
                gate=gate,
                interrupts=interrupts,
                interrupted=interrupted,
            )

        assert interrupted == [1, 1]
        assert gate.generation == 2

    asyncio.run(_run())


def test_deduplicator_bounds_its_memory_and_forgets_the_oldest():
    async def _run() -> None:
        interrupts = InterruptIdDeduplicator(max_ids=2)

        assert interrupts.accept("a") is True
        assert interrupts.accept("b") is True
        assert interrupts.accept("a") is False
        assert interrupts.accept("c") is True
        # "a" 是窗口里最旧的，已被挤出去：重放它只能再打断一次，而不是永远记住。
        assert interrupts.accept("a") is True

    asyncio.run(_run())


def test_unknown_message_type_is_ignored():
    async def _run() -> None:
        journal = _RecordingJournal()
        gate, interrupted = await _dispatch(
            _message("some-other-message", {"interruptId": "i-1"}), journal=journal
        )

        assert journal.calls == []
        assert interrupted == []
        assert gate.generation == 0

    asyncio.run(_run())


def test_typed_turn_message_still_announces_its_idempotency_key():
    """原有分支不能被新的分派逻辑挤掉：它决定键入的一条消息会不会变成两个回合。"""
    async def _run() -> None:
        journal = _RecordingJournal()
        await _dispatch(
            _message(VOICE_TYPED_TURN_MESSAGE, {"client_message_id": "cm-1", "text": "你好"}),
            journal=journal,
        )

        assert journal.calls == [
            ("expect_typed_turn", {"client_message_id": "cm-1", "text": "你好"})
        ]

    asyncio.run(_run())


# --------------------------------------------------------- §2.6 口语预算


def test_speech_guidance_uses_understanding_instead_of_a_time_budget():
    instruction = speech_budget_instruction()

    assert "不以句数或时长控制回答" in instruction
    assert "必要信息讲清楚后就停止" in instruction
    assert "不必等用户明确要求详细" in instruction


def test_speech_budget_forbids_truncating_facts_or_long_code():
    """预算约束详略，不约束内容：这条是防"为了守预算而丢事实"的硬约束。"""
    instruction = speech_budget_instruction()

    assert "不得" in instruction
    assert "事实、数字、风险、限制或真实完成状态" in instruction
    assert "代码、文件和完整过程在画布展示" in instruction


def test_retired_environment_budgets_cannot_truncate_the_guidance(monkeypatch):
    baseline = speech_budget_instruction()
    monkeypatch.setenv("VOICE_SPEECH_BUDGET_SENTENCES", "1")
    monkeypatch.setenv("VOICE_SPEECH_BUDGET_SECONDS", "8")

    instruction = speech_budget_instruction()

    assert instruction == baseline
    assert "1 句" not in instruction and "8 秒" not in instruction


def test_blank_legacy_environment_values_leave_the_guidance_unchanged(monkeypatch):
    baseline = speech_budget_instruction()
    monkeypatch.setenv("VOICE_SPEECH_BUDGET_SENTENCES", "  ")
    monkeypatch.setenv("VOICE_SPEECH_BUDGET_SECONDS", "")

    instruction = speech_budget_instruction()

    assert instruction == baseline


def test_voice_system_prompt_carries_the_budget_alongside_the_voice_rules():
    assert speech_budget_instruction() in SYSTEM_INSTRUCTION
    # 语音提示词的其它约束必须还在（预算不是替换，而是追加）。
    assert "中文全双工语音 Tutor" in SYSTEM_INSTRUCTION
    assert "不使用 emoji" in SYSTEM_INSTRUCTION
