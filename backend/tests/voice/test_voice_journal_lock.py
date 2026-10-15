"""语音账本的自死锁回归：``user_final`` 在持锁时调用 ``turn_interrupted``。

真机证据（2026-09-17 16:41，会话 vs_a210eff5754349c3868d85d1）：用户说完一句、
助手**已经开始回答**之后用户又开口。ASR 一直在出 final（日志里 text_len=9/3/7/2/11…），
但账本从那一刻起再也写不进一条事件（``event_seq`` 冻在 59），前端只能一直拿
``after_event_seq=59`` 空轮询 —— 用户看到的就是"用一会儿语音就又没反应了"。

根因：``user_final`` 持有 ``self._lock`` 时走到"新一句 = 打断"的分支，而它调用的
``turn_interrupted`` 自己也要 ``async with self._lock``；``asyncio.Lock`` 不可重入，
于是这把锁被永久持有——没有异常、没有告警，此后所有 journal 写入全部挂死，而音频
管线照常运行（所以从服务端日志看 ASR"一切正常"，只有账本和界面死了）。

这两个用例用超时把"挂死"变成"失败"，并在拿到 barge-in 之后**再写一条事件**，
确保日志真的还能继续用，而不是只有第一次调用返回。
"""

from __future__ import annotations

import asyncio

from app.voice import journal as journal_mod


class _FakeTurnApi:
    """只记账不落库的回合层替身。"""

    def __init__(self) -> None:
        self.interrupted: list[dict] = []
        self.accepted: list[dict] = []
        self.finalized: list[dict] = []

    def interrupt_turn(self, voice_session_id, turn_id, **kwargs):
        self.interrupted.append({"turn_id": turn_id, **kwargs})
        return {"turn_id": turn_id, "status": "interrupted"}

    def finalize_turn(self, voice_session_id, turn_id, *args, **kwargs):
        self.finalized.append({"turn_id": turn_id, **kwargs})
        return {"turn_id": turn_id, "status": "interrupted"}

    def open_turn(self, voice_session_id):
        return None

    def accept_turn(self, voice_session_id, text, **kwargs):
        self.accepted.append({"text": text, **kwargs})
        return {"turn_id": f"turn_{len(self.accepted)}", "user_text": text}


def _journal(monkeypatch):
    emitted: list[tuple[str, dict]] = []

    def _emit_event_line(voice_session_id, event_type, **kwargs):
        emitted.append((event_type, kwargs))
        return {"type": event_type, "seq": len(emitted)}

    fake = _FakeTurnApi()
    monkeypatch.setattr(journal_mod, "emit_event", _emit_event_line)
    for name in ("interrupt_turn", "finalize_turn", "open_turn", "accept_turn"):
        monkeypatch.setattr(journal_mod.turn_api, name, getattr(fake, name))
    journal = journal_mod.VoiceTurnJournal("vs_lock", idle_timeout_secs=30.0)
    return journal, fake, emitted


def test_user_final_after_the_assistant_started_does_not_wedge_the_journal(monkeypatch):
    """助手已在说话时用户又开口：必须收尾上一回合、开新回合，并且不锁死日志。"""

    async def exercise() -> None:
        journal, fake, _emitted = _journal(monkeypatch)
        state = journal_mod._TurnState(turn_id="turn_open")
        state.user_text = "第一个问题"
        state.assistant_text = "助手已经开始回答"
        state.sentence_seq = 1
        state.playback_observed = True
        journal._turn = state

        # 死锁时这一行会永远挂着：超时把它变成失败，而不是挂住整个测试进程。
        await asyncio.wait_for(journal.user_final("第二个问题"), timeout=2.0)
        await asyncio.wait_for(journal.drain_persistence(), timeout=2.0)

        assert fake.interrupted == [
            {"turn_id": "turn_open", "reason": "barge_in", "origin": "pipeline"}
        ], "上一回合必须按打断收尾（而不是把新一句拼进旧问题）"
        assert fake.finalized, "被打断的回合仍要把用户的问题留在转录里"
        assert journal.current_turn_id() == "turn_1", "新的一句必须开一个新回合"
        assert not journal._lock.locked(), "不得在持锁状态下收尾，否则日志被永久锁死"

        # 关键回归点：日志必须还能继续写（死锁时后续事件全部静默丢失）。
        journal._turn.assistant_text = ""
        journal._turn.sentence_seq = 0
        journal._turn.playback_observed = False
        await asyncio.wait_for(journal.user_final("第三句"), timeout=2.0)
        assert journal._turn.user_text == "第二个问题第三句"

    asyncio.run(exercise())


def test_consecutive_finals_inside_one_turn_still_merge(monkeypatch):
    """句中停顿仍然合并成一个回合、一条 user.final（行为不变）。"""

    async def exercise() -> None:
        journal, fake, emitted = _journal(monkeypatch)

        await asyncio.wait_for(journal.user_final("前半句"), timeout=2.0)
        await asyncio.wait_for(journal.user_final("后半句"), timeout=2.0)

        assert [item["text"] for item in fake.accepted] == ["前半句"], (
            "同一回合内的续段不得再 accept 一次（否则一句话变两个回合）"
        )
        assert journal._turn.user_text == "前半句后半句"
        assert [item[0] for item in emitted] == ["user.final"], "续段只补一条合并事件"
        assert not journal._lock.locked()

    asyncio.run(exercise())
