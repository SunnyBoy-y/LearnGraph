"""Tests for zero-latency voice noise / bystander-fragment silent dropping."""

from __future__ import annotations

import asyncio
from typing import Any

from pipecat.frames.frames import (
    BotStartedSpeakingFrame,
    InterimTranscriptionFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
)
from pipecat.processors.frame_processor import FrameDirection

from app.voice.embedded_noise_filter import VoiceNoiseFilterProcessor
from app.voice.embedded_turn_intent import classify_noise_utterance, is_noise_utterance
from tests.unit.test_voice_runtime_upgrade import _FakeWS, _STT


def test_noise_filter_drops_bystander_and_filler_fragments():
    for text in (
        "我爸。",
        "我妈。",
        "爸爸",
        "老婆。",
        "嗯。",
        "哦……",
        "那个。",
        "就是。",
        "然后",
        "哎呀。",
        "喂喂",
        "的。",
        "了。",
        "我的。",
        "这是。",
        "um",
        "hmm",
        "  ... ",
    ):
        assert is_noise_utterance(text) is True, f"expected noise drop for {text!r}"


def test_noise_filter_preserves_commands_questions_answers_and_short_topics():
    for text in (
        "停",
        "停一下",
        "别说了",
        "算了",
        "不过",
        "不是",
        "好的。",
        "对。",
        "北京。",
        "高数",
        "42",
        "写一个鹦鹉骑自行车的html",
        "我爸生日怎么准备礼物",
        "我的电脑为什么卡了",
        "嗯，我想问一下",
    ):
        assert is_noise_utterance(text) is False, f"expected pass for {text!r}"


def test_noise_filter_holds_ambiguous_short_interims_until_final():
    assert classify_noise_utterance("我", is_interim=True) is not None
    assert classify_noise_utterance("我爸", is_interim=True) == "bystander_fragment"
    assert classify_noise_utterance("北", is_interim=True) is not None
    assert classify_noise_utterance("北京", is_interim=True) == "pending_short_interim"
    assert classify_noise_utterance("北京", is_interim=False) is None
    assert classify_noise_utterance("北京天气", is_interim=True) is None
    assert classify_noise_utterance("停", is_interim=True) is None


def test_voice_noise_filter_processor_silently_drops_before_downstream():
    async def run() -> None:
        proc = VoiceNoiseFilterProcessor()
        forwarded: list[Any] = []

        async def push_frame(frame, direction=FrameDirection.DOWNSTREAM):
            forwarded.append(frame)

        proc.push_frame = push_frame

        await proc.process_frame(
            InterimTranscriptionFrame("我爸", "u", "t"),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TranscriptionFrame("我爸。", "u", "t", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TranscriptionFrame("那个。", "u", "t", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        assert forwarded == []
        assert proc.dropped_count == 3

        await proc.process_frame(
            VADUserStartedSpeakingFrame(),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TranscriptionFrame("北京。", "u", "t", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        await proc.process_frame(
            TranscriptionFrame("搜一下记忆相关的论文", "u", "t", finalized=True),
            FrameDirection.DOWNSTREAM,
        )
        assert [getattr(f, "text", type(f).__name__) for f in forwarded] == [
            "VADUserStartedSpeakingFrame",
            "北京。",
            "搜一下记忆相关的论文",
        ]

    asyncio.run(run())


def test_stt_silently_drops_bystander_noise_even_when_bot_is_idle():
    async def run() -> None:
        stt = _STT(api_key="test")
        stt._ws = _FakeWS()
        frames: list[Any] = []

        async def push_frame(frame, direction=None):
            if isinstance(frame, (TranscriptionFrame, InterimTranscriptionFrame)):
                frames.append(frame)

        stt.push_frame = push_frame

        assert stt._is_backchannel_filler("我爸", is_interim=True) is True
        stt._speech_seen = True
        await stt._send_commit("vad-noise")
        await stt._handle_final({"text": "我爸。"})
        assert frames == []

        stt._speech_seen = True
        await stt._send_commit("vad-real")
        await stt._handle_final({"text": "北京天气怎么样"})
        assert [f.text for f in frames] == ["北京天气怎么样"]

        await stt.process_frame(BotStartedSpeakingFrame(), FrameDirection.DOWNSTREAM)
        stt._speech_seen = True
        await stt._send_commit("vad-backchannel")
        await stt._handle_final({"text": "好的。"})
        assert [f.text for f in frames] == ["北京天气怎么样"]

    asyncio.run(run())
