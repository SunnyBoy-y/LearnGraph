"""Language-extensible turn intent classification for duplex voice."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class TurnIntent(str, Enum):
    BACKCHANNEL = "backchannel"
    INTERRUPTION = "interruption"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class LanguageProfile:
    code: str
    backchannels: frozenset[str]
    interruptions: frozenset[str]
    compact: bool = False


_ZH = LanguageProfile(
    code="zh",
    compact=True,
    backchannels=frozenset(
        {
            "嗯",
            "嗯嗯",
            "嗯嗯嗯",
            "嗯哼",
            "唔",
            "对",
            "对的",
            "是",
            "是的",
            "啊",
            "哦",
            "哦哦",
            "好",
            "好的",
            "好吧",
            "啊哈",
        }
    ),
    interruptions=frozenset(
        {
            "等等",
            "等一下",
            "停",
            "停一下",
            "不是",
            "不对",
            "听我说",
            "打断一下",
            "别说了",
            "先听我",
            "我问",
            "但是",
            "不过",
        }
    ),
)

_EN = LanguageProfile(
    code="en",
    backchannels=frozenset(
        {
            "uh huh",
            "uh-huh",
            "mm hmm",
            "mm-hmm",
            "yeah",
            "yep",
            "yes",
            "right",
            "okay",
            "ok",
            "sure",
            "got it",
            "i see",
        }
    ),
    interruptions=frozenset(
        {
            "wait",
            "hold on",
            "stop",
            "actually",
            "no",
            "not",
            "listen to me",
            "let me speak",
            "excuse me",
            "one moment",
        }
    ),
)

_JA = LanguageProfile(
    code="ja",
    compact=True,
    backchannels=frozenset({"うん", "はい", "ええ", "そう", "なるほど", "わかった"}),
    interruptions=frozenset(
        {"ちょっと", "待って", "止まって", "違う", "聞いて", "私の話"}
    ),
)

_KO = LanguageProfile(
    code="ko",
    compact=True,
    backchannels=frozenset({"응", "네", "맞아", "그래", "알겠어"}),
    interruptions=frozenset({"잠깐", "기다려", "멈춰", "아니", "내 말"}),
)

DEFAULT_LANGUAGE_PROFILES: tuple[LanguageProfile, ...] = (_ZH, _EN, _JA, _KO)
_PUNCTUATION_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")
_SPACE_RE = re.compile(r"\s+")


class TurnIntentClassifier:
    def __init__(self, profiles: Iterable[LanguageProfile] | None = None) -> None:
        self._profiles: list[LanguageProfile] = list(
            profiles if profiles is not None else DEFAULT_LANGUAGE_PROFILES
        )

    @property
    def profiles(self) -> tuple[LanguageProfile, ...]:
        return tuple(self._profiles)

    def register(self, profile: LanguageProfile) -> None:
        self._profiles = [item for item in self._profiles if item.code != profile.code]
        self._profiles.append(profile)

    def classify(self, text: str) -> TurnIntent:
        normalized = normalize_transcript(text)
        if not normalized:
            return TurnIntent.UNCERTAIN
        saw_backchannel = False
        for profile in self._profiles:
            if _is_backchannel(normalized, profile):
                saw_backchannel = True
            if _contains_interruption(normalized, profile):
                return TurnIntent.INTERRUPTION
        return TurnIntent.BACKCHANNEL if saw_backchannel else TurnIntent.UNCERTAIN


DEFAULT_TURN_INTENT_CLASSIFIER = TurnIntentClassifier()


def is_backchannel(
    text: str, *, classifier: TurnIntentClassifier | None = None
) -> bool:
    """整句都是背声词/填充词（「嗯。」「哦。」「对。」「uh huh」…）时为真。

    "整句都是"是刻意的强条件：``classify`` 只在规范化后的全文恰好（或逐词）
    命中背声词表时才返回 ``BACKCHANNEL``，所以「嗯，我想问一下」这类带实义的
    发言不算——它是一次真实抢断。

    判据与 ``AdaptiveUserTurnStartStrategy`` 共用同一个分类器实例族，避免
    "这一层认为它是背声词、那一层认为它是发言"的分裂。
    """
    return (classifier or DEFAULT_TURN_INTENT_CLASSIFIER).classify(
        text
    ) is TurnIntent.BACKCHANNEL


def normalize_transcript(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("'", "").replace("\u2019", "")
    value = _PUNCTUATION_RE.sub(" ", value.casefold())
    return _WHITESPACE_RE.sub(" ", value).strip()


def merge_transcript_segments(existing: str, incoming: str) -> str:
    left = " ".join(str(existing or "").split())
    right = " ".join(str(incoming or "").split())
    if not left:
        return right
    if not right:
        return left

    left_norm = _compact(left)
    right_norm = _compact(right)
    if not left_norm or not right_norm:
        return right if not left_norm else left
    if right_norm == left_norm or left_norm.endswith(right_norm):
        return left
    if right_norm.startswith(left_norm) or right_norm.endswith(left_norm):
        return right
    if left_norm in right_norm:
        return right
    if right_norm in left_norm:
        return left
    if _is_compact_script(left) and _is_compact_script(right):
        return left + right
    return left + " " + right


def _compact(text: str) -> str:
    return _SPACE_RE.sub("", normalize_transcript(text))


def _is_compact_script(text: str) -> bool:
    return bool(text) and " " not in text and any(
        "\u3400" <= char <= "\u9fff"
        or "\u3040" <= char <= "\u30ff"
        or "\uac00" <= char <= "\ud7af"
        for char in text
    )


def _is_backchannel(normalized: str, profile: LanguageProfile) -> bool:
    phrases = profile.backchannels
    if not phrases:
        return False
    normalized_phrases = {normalize_transcript(item) for item in phrases}
    if profile.compact:
        compact = _compact(normalized)
        return compact in {_compact(item) for item in normalized_phrases}
    tokens = tuple(normalized.split())
    phrase_tokens = {tuple(item.split()) for item in normalized_phrases}
    if tuple(tokens) in phrase_tokens:
        return True
    single_words = {item[0] for item in phrase_tokens if len(item) == 1}
    return bool(tokens) and all(token in single_words for token in tokens)


def _contains_interruption(normalized: str, profile: LanguageProfile) -> bool:
    phrases = profile.interruptions
    if not phrases:
        return False
    normalized_phrases = [normalize_transcript(item) for item in phrases]
    if profile.compact:
        compact = _compact(normalized)
        return any(_compact(item) in compact for item in normalized_phrases)
    tokens = tuple(normalized.split())
    for phrase in normalized_phrases:
        phrase_tokens = tuple(phrase.split())
        if not phrase_tokens:
            continue
        width = len(phrase_tokens)
        if any(
            tokens[index : index + width] == phrase_tokens
            for index in range(len(tokens))
        ):
            return True
    return False
