"""TTS 侧切句：在 pipecat 的 SENTENCE 聚合器上再加"逗号也切"。

## 为什么不用基类默认的 ``SimpleTextAggregator``

* 它只认**句末标点**（非拉丁 ``。！？；`` 等，拉丁 ``. ! ? ; …``），**逗号一律留在
  缓冲里**。于是一个逗号长句必须等整句（甚至等下一个句末标点）到齐才开始合成——
  首声、字幕、账本全跟着整句一起拖后。
* 它要求**每一个**句末标点后面再出现一个非空白字符才确认边界。那段前瞻是为拉丁句号
  消歧设计的（``$29.`` 不是句子、``$29. Next`` 才是），中文标点并不需要，白等一个
  token。本模块对非拉丁标点改成"标点到达即切"。

## 这里定下的三条线

1. **逗号也是切点**。全角 ``，`` 立即切；半角 ``,`` 也切，但排除数字千分位
   （``1,000`` 保持整块——否则会被读成"1"+"000"，也会让字幕断在数字中间）。
2. **非拉丁（中/日/韩等）标点即时切**，不再等前瞻字符。集合直接复用 pipecat 的
   ``UNAMBIGUOUS_SENTENCE_ENDING_PUNCTUATION``，不自己另抄一份。
3. **顿号 ``、`` 不切**。它是并列成分的分隔符（"红、黄、蓝"），切出来只是碎片。
   它本来就不在 pipecat 的标点集合里，``NEVER_BREAK_CHARS`` 把这个决定写下来并由
   测试钉住，免得以后被"顺便加上"。

拉丁那一路（``. ! ? ; …`` + 千分位逗号）原样保留"前瞻 + NLTK 消歧"，只在中日韩标点上
省掉等待：``match_endofsentence`` 的 NLTK 回退分支本来就只在"没有拉丁标点"时生效，
两条路互不影响。

## 下游影响（改颗粒度也就是改它们）

一次聚合 = 一次 ``run_tts`` = 一个火山 session，同时也是官方字幕
（``AggregatedTextFrame`` → ``bot-output{new}`` / ``TTSTextFrame`` → ``{completed}``）
与账本 ``VoiceLedgerFrame`` 的"一句"。所以切得更细意味着字幕、转录、记忆的颗粒度
同步变细，且每轮的 session 数变多（session 之间是串行的，见
``embedded_volcengine_tts._enqueue_sentence``）。

护栏：**纯标点不成句**。``……`` ``！！`` 这种连写若各自领一个 session，会白开一次合成、
多出一条只有标点的字幕；这种切点只是不切，文本继续留在缓冲里，最终随下一个真切点或
``flush()`` 一起出去，一个字都不会丢。
"""

from __future__ import annotations

import re
from typing import Any

from pipecat.utils.string import (
    SENTENCE_ENDING_PUNCTUATION,
    UNAMBIGUOUS_SENTENCE_ENDING_PUNCTUATION,
)
from pipecat.utils.text.base_text_aggregator import Aggregation, AggregationType
from pipecat.utils.text.simple_text_aggregator import SimpleTextAggregator

#: 标点到达即切，不需要前瞻字符：非拉丁标点 + 全角逗号。
IMMEDIATE_BREAK_CHARS: frozenset[str] = frozenset(
    UNAMBIGUOUS_SENTENCE_ENDING_PUNCTUATION | {"，"}
)

#: 半角逗号。它要防的是数字千分位（``1,000``），所以前置字符是数字时先等一下一位。
HALFWIDTH_COMMA = ","

#: 永不切句的标点。顿号在列，而且是这里唯一需要显式声明的：它不在任何切点集合里。
NEVER_BREAK_CHARS: frozenset[str] = frozenset({"、", "､"})

#: "这段文本值得单开一次合成吗"。只有标点/空白不算——见模块 docstring 的护栏一节。
_HAS_CONTENT = re.compile(r"\w")


class CommaSentenceAggregator(SimpleTextAggregator):
    """句级聚合器 + 逗号切点（顿号不切，非拉丁标点即时切）。

    只覆盖 ``_check_sentence_with_lookahead``：基类逐字符调用它，返回非空即为一个
    切点。其余（``flush`` / ``reset`` / ``handle_interruption`` / 文本类型）全部沿用
    基类——输出仍然是 ``AggregationType.SENTENCE``，所以押在"句级"上的字幕与账本路径
    一行都不用改。
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

        if last == HALFWIDTH_COMMA:
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
