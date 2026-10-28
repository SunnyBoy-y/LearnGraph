"""Semantic text batching for continuous TTS; chunks never define audio boundaries."""

from __future__ import annotations

import re
from typing import Any

from pipecat.utils.string import (
    SENTENCE_ENDING_PUNCTUATION,
    UNAMBIGUOUS_SENTENCE_ENDING_PUNCTUATION,
)
from pipecat.utils.text.base_text_aggregator import Aggregation, AggregationType
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

#: 标点到达即切，不需要前瞻字符：非拉丁句末标点。
IMMEDIATE_BREAK_CHARS: frozenset[str] = frozenset(
    UNAMBIGUOUS_SENTENCE_ENDING_PUNCTUATION
)

#: 半角逗号。它要防的是数字千分位（``1,000``），所以前置字符是数字时先等一下一位。
HALFWIDTH_COMMA = ","

#: 永不切句的标点。顿号在列，而且是这里唯一需要显式声明的：它不在任何切点集合里。
NEVER_BREAK_CHARS: frozenset[str] = frozenset({"、", "､"})

#: "这段文本值得单开一次合成吗"。只有标点/空白不算——见模块 docstring 的护栏一节。
_HAS_CONTENT = re.compile(r"\w")


class CommaSentenceAggregator(SimpleTextAggregator):
    """CJK sentence endings flush immediately; commas need 24 characters.

    These chunks feed an open synthesis session. Provider subtitle timestamps
    independently determine caption and playback ledger boundaries.
    """

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("aggregation_type", AggregationType.SENTENCE)
        super().__init__(**kwargs)

    async def _check_sentence_with_lookahead(self, char: str) -> Aggregation | None:
        # 已经挂着"等一个非空白前瞻字符"（拉丁句号 / 数字后的半角逗号）→ 交回基类用
        # NLTK 消歧，行为与改动前逐字一致。
        if self._needs_lookahead:
            result = await super()._check_sentence_with_lookahead(char)
            if result is None or _HAS_CONTENT.search(result.text):
                return result
            # 基类也会切出纯标点片段（"……"）：文本还回缓冲，等真内容一起走。基类已经
            # 截断过缓冲，这里把它拼回去即可；丢掉的多半是切点后的空格，而切出去的
            # 片段本来就要 strip。
            self._text = result.text + self._text
            return None

        if not self._text:
            return None

        last = self._text[-1]

        if last in NEVER_BREAK_CHARS:
            # 顿号：并列成分的分隔符，永不当切点。缓冲一个字都不动，等下一个真切点。
            # 这道判断必须排在两个切点集合之前——``IMMEDIATE_BREAK_CHARS`` 是从
            # pipecat 的标点集合派生的，万一以后它把顿号挪进"无歧义句末标点"，这里
            # 也不会跟着把顿号当句号切。
            return None

        if last in IMMEDIATE_BREAK_CHARS:
            return self._cut(len(self._text))

        if last in {HALFWIDTH_COMMA, "，"}:
            if len(self._text) < 24:
                return None
            if self._text[-2:-1].isdigit():
                # "1," 可能是千分位：等下一位再判（``1,000`` 不切；下一位若是空格或
                # 非数字，NLTK 也确认不出句末标点，于是整块继续攒到下一个真切点）。
                self._needs_lookahead = True
                return None
            return self._cut(len(self._text))

        if last in SENTENCE_ENDING_PUNCTUATION:
            # 拉丁 . ! ? ; …：仍需一个非空白前瞻字符 + NLTK 消歧（原行为）。
            self._needs_lookahead = True

        return None

    def _cut(self, end: int) -> Aggregation | None:
        """把缓冲前 ``end`` 个字符切出去；没有可朗读内容就当没切。

        不切时**不动** ``self._text``：那些标点留在缓冲里，等下一段真内容一起出，
        所以"少切一刀"永远不会变成"丢字"。
        """
        candidate = self._text[:end].strip(" ")
        if not _HAS_CONTENT.search(candidate):
            return None
        self._text = self._text[end:]
        return Aggregation(text=candidate, type=AggregationType.SENTENCE)
