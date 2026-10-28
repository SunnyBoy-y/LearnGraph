"""语音切句颗粒度：逗号也切、顿号不切、非拉丁标点即时切。

背景：TTS 侧原本用 pipecat 默认的 ``SimpleTextAggregator``（``TextAggregationMode.
SENTENCE``），它只认句末标点，逗号一律留在缓冲里；而且每个句末标点后面都要再等一个
非空白字符才确认边界（那段前瞻是给拉丁句号消歧用的，中文白等一个 token）。于是
"逗号长句"必须等整句到齐才开始合成，首声与字幕都跟着拖后。

改动（``app/voice/embedded_tts_aggregator.py``）：
- 全角逗号 ``，`` 到达即切；半角 ``,`` 也切，但排除数字千分位 ``1,000``；
- 非拉丁标点（``。！？；`` 等，直接复用 pipecat 的 ``UNAMBIGUOUS_SENTENCE_ENDING_
  PUNCTUATION``）即时切，不再等前瞻字符；
- **顿号 ``、`` 永不切**（并列成分的分隔符，切出来只是碎片）；
- 纯标点（``……``）不单开一次合成：只是不切，文本留在缓冲里，不丢字。

拉丁句号 ``. ! ? ;`` 那一路保持"前瞻 + NLTK 消歧"不变。
"""
from __future__ import annotations

import asyncio

from pipecat.utils.string import SENTENCE_ENDING_PUNCTUATION
from pipecat.utils.text.base_text_aggregator import AggregationType
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

from app.voice.embedded_tts_aggregator import (
    IMMEDIATE_BREAK_CHARS,
    NEVER_BREAK_CHARS,
    CommaSentenceAggregator,
)
from app.voice.embedded_volcengine_tts import VolcengineTTSService


async def _feed(aggregator, piece: str) -> list[str]:
    """投喂一段流式文本，返回这段里被切出来的切片。"""
    return [item.text async for item in aggregator.aggregate(piece)]


def _chunks(pieces: list[str]) -> list[str]:
    """按给定流式片段投喂，返回全部切片（不含 flush）。"""
    aggregator = CommaSentenceAggregator()

    async def run() -> list[str]:
        out: list[str] = []
        for piece in pieces:
            out.extend(await _feed(aggregator, piece))
        return out

    return asyncio.run(run())


# --------------------------------------------------------------------------- 逗号切句


def test_fullwidth_comma_breaks_without_waiting_for_lookahead():
    """逗号到达即切：不依赖"再来一个非空白字符"。"""
    aggregator = CommaSentenceAggregator()

    async def run():
        return await _feed(aggregator, "你好，")

    assert asyncio.run(run()) == []


def test_fullwidth_comma_and_full_stop_both_break():
    assert _chunks(["你好，", "今天讲图。"]) == ["你好，今天讲图。"]


def test_halfwidth_comma_breaks_but_not_a_thousands_separator():
    """半角逗号断句，但 ``1,000`` 必须保持整块（读成 "1"+"000" 是听觉事故）。"""
    # 半角逗号切得掉；后面的 " world" 没有句末标点，要等 flush（见 flush 那条用例）。
    assert _chunks(["hello,", " world"]) == []
    assert _chunks(["价格 1,000 元。"]) == ["价格 1,000 元。"]


def test_comma_breaks_around_dunhao_keeps_list_together():
    """顿号连成的并列成分整体留在逗号前那一句里。"""
    assert _chunks(["红、黄、蓝，这三色。"]) == ["红、黄、蓝，这三色。"]


def test_base_aggregator_would_not_break_on_comma():
    """对照：同一段文本，pipecat 默认聚合器一片都不切——证明上面的断言不是空测。"""
    base = SimpleTextAggregator(aggregation_type=AggregationType.SENTENCE)

    async def run():
        return await _feed(base, "你好，")

    assert asyncio.run(run()) == []


# --------------------------------------------------------------- 顿号 / 标点集合不变量


def test_dunhao_never_breaks_a_sentence():
    assert _chunks(["红、黄、蓝三种颜色。"]) == ["红、黄、蓝三种颜色。"]


def test_dunhao_is_not_a_break_char_at_any_layer():
    assert "、" in NEVER_BREAK_CHARS
    assert NEVER_BREAK_CHARS.isdisjoint(IMMEDIATE_BREAK_CHARS)
    assert "、" not in IMMEDIATE_BREAK_CHARS
    # 上游事实：顿号不在 pipecat 的标点集合里（代码里的 NEVER_BREAK 判断是防它以后
    # 被挪进去的第二道锁）。
    assert "、" not in SENTENCE_ENDING_PUNCTUATION


def test_fullwidth_comma_is_a_break_char():
    assert "，" not in IMMEDIATE_BREAK_CHARS


# ------------------------------------------------------------- 非拉丁即时切 / 拉丁不变


def test_cjk_full_stop_breaks_immediately():
    """中文句号即时切：pad 之后不需要任何字符来"确认"。"""
    assert _chunks(["你好。"]) == ["你好。"]


def test_latin_period_still_waits_for_one_lookahead_char():
    aggregator = CommaSentenceAggregator()

    async def run():
        first = await _feed(aggregator, "3.")
        second = await _feed(aggregator, " Next")
        return first, second

    first, second = asyncio.run(run())
    assert first == []       # 只有 "3." 时判不出是不是句子（可能是小数/编号）
    assert second == ["3."]  # 非空白前瞻字符到达 + NLTK 确认后才切


# ------------------------------------------------------------------- 护栏 / 收尾


def test_punctuation_only_slice_does_not_take_its_own_session():
    """``……`` 不单开一次合成：不切、不丢，跟着后面那句一起出。"""
    aggregator = CommaSentenceAggregator()

    async def run():
        first = await _feed(aggregator, "……")
        second = await _feed(aggregator, "他还好。")
        return first, second

    first, second = asyncio.run(run())
    assert first == []
    assert second == ["……他还好。"]


def test_flush_returns_the_tail_that_has_no_punctuation():
    aggregator = CommaSentenceAggregator()

    async def run():
        before = await _feed(aggregator, "没有标点的尾巴")
        tail = await aggregator.flush()
        after = await aggregator.flush()
        return before, tail, after

    before, tail, after = asyncio.run(run())
    assert before == []
    assert tail is not None and tail.text == "没有标点的尾巴"
    assert after is None


def test_interruption_clears_the_buffer():
    aggregator = CommaSentenceAggregator()

    async def run():
        await _feed(aggregator, "被打断的半句")
        await aggregator.handle_interruption()
        return await aggregator.flush()

    assert asyncio.run(run()) is None


# --------------------------------------------------------------------------- 接线


def test_volcengine_service_installs_the_comma_aggregator():
    """适配器把它装到基类用聚合器的那唯一一处属性上（无需改 pipecat）。"""
    service = VolcengineTTSService(api_key="test-key")
    assert isinstance(service._text_aggregator, CommaSentenceAggregator)
    assert service._text_aggregator.aggregation_type == AggregationType.SENTENCE
