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
# 仅匹配整句纯语气词；不包含「好/对/是」等有实际确认含义的词。
# 分隔符仅允许空白和常见标点，不能吞掉数字、字母或其他正文。
_ASR_FILLER_RE = re.compile(
    r"[\s，。！？、；：,.!?;:…—~～]*"
    r"(?:(?:嗯哼|啊哈|[嗯唔哦噢喔啊呃额])[\s，。！？、；：,.!?;:…—~～]*)+"
)


def is_asr_filler(text: str) -> bool:
    """纯语气词（含重复、标点）无条件滤除，实义句子不匹配。"""
    return bool(_ASR_FILLER_RE.fullmatch(unicodedata.normalize("NFKC", text or "")))


_NOISE_INTERJECTIONS = frozenset(
    {
        "那个",
        "这个",
        "就是",
        "然后",
        "啊这",
        "呃那个",
        "嗯那个",
        "那个啥",
        "就是说",
        "哎呀",
        "哎哟",
        "哎",
        "唉",
        "哼",
        "哈",
        "哈哈",
        "哈哈哈",
        "呵呵",
        "嘿",
        "嘿嘿",
        "嘶",
        "咳",
        "呸",
        "哇",
        "呀",
        "啦",
        "嘛",
        "喂",
        "喂喂",
        "卧槽",
        "我靠",
        "我擦",
        "妈呀",
        "天哪",
        "我的天",
        "我的妈",
        "um",
        "uh",
        "er",
        "ah",
        "oh",
        "hmm",
        "hm",
        "mm",
        "mmm",
        "shh",
        "tsk",
        "ha",
        "haha",
        "hey",
        "wow",
        "oops",
        "ugh",
    }
)
_BYSTANDER_FRAGMENT_RE = re.compile(
    r"^(?:"
    r"(?:我|你|他|她|咱|老)?(?:爸|妈|爹|娘|爷|奶|哥|姐|弟|妹|婆|公)"
    r"|(?:爸爸|妈妈|老爸|老妈|爷爷|奶奶|外公|外婆|姥爷|姥姥|哥哥|姐姐|弟弟|妹妹|老婆|老公|儿子|女儿|宝贝|宝宝)"
    r")$"
)
_BROKEN_TWO_CHAR_FRAGMENTS = frozenset(
    {
        "我的",
        "你的",
        "他的",
        "她的",
        "它的",
        "这是",
        "那是",
        "因为",
        "所以",
        "虽然",
        "如果",
        "或者",
        "而且",
        "并且",
        "了的",
        "的了",
        "吧的",
        "啊的",
        "我这",
        "你这",
        "他这",
        "我也",
        "你也",
        "我都",
        "你都",
        "我有",
        "你有",
        "我就",
        "你就",
        "我在",
        "你在",
        "他在",
    }
)
_VALID_SINGLE_CJK = frozenset("好对是行不没停换查搜写做看听讲说读画算试懂会能要啥谁哪几")
_INTERIM_ALLOW_SHORT_CJK = _VALID_SINGLE_CJK | frozenset("吗呢")


def _is_cjk_char(ch: str) -> bool:
    return "\u3400" <= ch <= "\u9fff"


def classify_noise_utterance(text: str, *, is_interim: bool = False) -> str | None:
    """判定是否为应静默丢弃的噪音/旁人插话/残片；合法语句返回 None。"""
    raw = unicodedata.normalize("NFKC", str(text or "")).strip()
    compact = _compact(raw)
    if not compact:
        return "empty"
    if is_asr_filler(raw):
        return "asr_filler"
    intent = DEFAULT_TURN_INTENT_CLASSIFIER.classify(raw)
    if intent is TurnIntent.INTERRUPTION or intent is TurnIntent.BACKCHANNEL:
        return None
    if any(ch.isdigit() for ch in compact):
        return None
    if compact in _NOISE_INTERJECTIONS:
        return "hesitation_or_interjection"
    if _BYSTANDER_FRAGMENT_RE.fullmatch(compact):
        return "bystander_fragment"
    if len(compact) >= 2 and len(set(compact)) == 1 and compact[0] not in {"好", "对", "是", "行"}:
        return "repeated_char"
    if compact in _BROKEN_TWO_CHAR_FRAGMENTS:
        return "broken_fragment"
    if len(compact) == 1 and _is_cjk_char(compact[0]) and compact[0] not in _VALID_SINGLE_CJK:
        return "isolated_char"
    if (
        is_interim
        and len(compact) <= 2
        and all(_is_cjk_char(ch) for ch in compact)
        and not any(ch in _INTERIM_ALLOW_SHORT_CJK for ch in compact)
    ):
        return "pending_short_interim"
    return None


def is_noise_utterance(text: str, *, is_interim: bool = False) -> bool:
    """是否应静默丢弃该语音识别片段。"""
    return classify_noise_utterance(text, is_interim=is_interim) is not None


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
