"""Request-scoped memory applicability. Importance never substitutes for relevance.

No provider calls: this gate deliberately abstains when historical context would
invent a new requirement. Task evidence and user preferences remain distinct.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import unicodedata


_PREFERENCE = re.compile(r"喜欢|偏好|习惯|更愿意|总是|热衷|钟爱|爱好|最爱|喜爱|感兴趣|\b(?:prefers?|likes?|enjoys?|favorite|favourite)\b", re.I)
_STYLE = re.compile(r"简洁|简短|详细|分步|步骤|讲解|解释|例子|示例|字幕|字号|无障碍|减少动态|减少动画|中文|英文|\b(?:concise|brief|detailed|explanation|subtitles|accessible)\b", re.I)
_TASK = re.compile(r"做过|完成过|开发过|制作过|正在|这次|本次|当前|项目|任务|客户|作业|帮朋友|替别人|\b(?:project|assignment|client|working on|built|created)\b", re.I)
_RECALL = re.compile(r"继续|接着|上次|之前.{0,8}(?:做|聊|说|项目|任务)|做过什么|回顾|\b(?:resume|continue|previous|last time|recall)\b", re.I)
_PERSONALIZE = re.compile(r"(?:按|根据|结合|参考).{0,12}(?:我的|我).{0,8}(?:喜好|偏好|口味|喜欢)|(?:我喜欢|我的偏好).{0,8}(?:题材|主题|风格)|\b(?:my preferences|my taste|personaliz\w*)\b", re.I)
_NO_PERSONALIZE = re.compile(r"(?:不要|不用|别|无需).{0,16}(?:偏好|喜好|个性化)|\b(?:don't|do not|without)\b.{0,20}(?:personaliz|preferences)", re.I)
_NEW_TASK = re.compile(r"换个(?:任务|主题|话题)|另一个(?:任务|主题)|重新开始|从头开始|(?:这次|现在|今天)(?:做|制作|改做|换)|(?:不要|别|不再).{0,4}继续|\b(?:new task|new topic|start over|don't continue|do not continue)\b", re.I)
_STOP = {"用户", "喜欢", "偏好", "请你", "帮我", "做个", "一个", "制作", "生成", "实现", "需要", "希望", "之前", "上次", "继续", "记忆", "内容", "the", "and", "for", "user", "likes", "prefer", "please", "make", "create", "want"}


def _presentation_only(content: str) -> bool:
    remaining = _STYLE.sub("", content)
    for filler in ("用户", "喜欢", "偏好", "习惯", "希望", "总是", "回答", "回复", "尽量", "应该", "保持", "采用", "要求", "使用", "通过", "具体", "先", "再", "理解", "概念", "学习", "方式", "教学", "进行", "提供", "内容", "语言", "请", "用", "我", "更", "的", "地", "和", "与", "一点"):
        remaining = remaining.replace(filler, "")
    remaining = re.sub(r"\b(?:user|i|prefer|prefers|like|likes|use|keep|responses?|answers?|explanations?|and|with|a|the)\b", "", remaining, flags=re.I)
    return not re.search(r"[\w\u3400-\u9fff]", remaining)


def terms(text: str) -> set[str]:
    value = unicodedata.normalize("NFKC", text).casefold()
    tokens = set(re.findall(r"[a-z0-9_]{2,}", value))
    for part in re.findall(r"[\u3400-\u9fff]+", value):
        tokens.update(part[index:index + 2] for index in range(len(part) - 1))
    return tokens - _STOP


def lexical_relevance(query: str, content: str) -> float:
    left, right = terms(query), terms(content)
    return len(left & right) / math.sqrt(len(left) * len(right)) if left and right else 0.0


def classify_memory(content: str, memory_type: str = "semantic_memory") -> dict[str, str | int]:
    if memory_type == "external_reference":
        return {"version": 1, "role": "fact", "status": "observed", "allowed_effect": "context"}
    if memory_type in {"event_summary", "decision", "strategy_effectiveness"}:
        return {"version": 1, "role": "task_episode", "status": "observed", "allowed_effect": "task_context"}
    if (_PREFERENCE.search(content) or (memory_type == "learning_preference" and _STYLE.search(content) and _presentation_only(content))) and not _TASK.search(content):
        effect = "presentation" if _STYLE.search(content) and _presentation_only(content) else "theme_suggestion"
        return {"version": 1, "role": "preference", "status": "explicit", "allowed_effect": effect}
    if memory_type == "learning_preference":
        return {"version": 1, "role": "hypothesis", "status": "candidate", "allowed_effect": "none"}
    if _TASK.search(content):
        return {"version": 1, "role": "task_episode", "status": "observed", "allowed_effect": "task_context"}
    return {"version": 1, "role": "fact", "status": "observed", "allowed_effect": "context"}


def _explicit_theme(query: str) -> set[str]:
    match = re.search(r"(?:做|制作|生成|设计)(?:一个|一段|一部|个)?([^，。！？\n]{1,18}?)(?:动画|游戏|视频|故事|海报|网站)", query)
    if not match:
        return set()
    text = match.group(1)
    for filler in ("一个", "一段", "个", "简单", "基础", "好看", "我的", "喜欢", "主题", "题材", "按", "根据", "偏好", "喜好", "的"):
        text = text.replace(filler, "")
    return terms(text)


@dataclass(frozen=True)
class UsageDecision:
    allowed: bool
    reason: str
    relevance: float
    allowed_effect: str = "none"


def memory_usage(
    query: str, content: str, *, memory_type: str = "semantic_memory",
    policy: dict | None = None, same_task: bool = False,
    same_conversation: bool = False, semantic: float = 0.0,
    preference_verified: bool = True,
    lexical_override: float = 0.0,
) -> UsageDecision:
    # Classify the text too: metadata may only narrow permissions, never turn
    # an obvious subject preference into a universal presentation rule.
    inferred = classify_memory(content, memory_type)
    stored = policy if isinstance(policy, dict) else {}
    role = str(stored.get("role") or inferred["role"])
    effect = str(inferred["allowed_effect"])
    if stored.get("allowed_effect") == "none" or role == "hypothesis":
        return UsageDecision(False, "unconfirmed_preference", 0.0)
    if stored.get("role") == "task_episode":
        effect = "task_context"
    lexical = max(lexical_relevance(query, content), max(0.0, min(1.0, lexical_override)))
    relevance = max(lexical, max(0.0, min(1.0, semantic)))
    for denied in re.findall(r"(?:不要|不用|别用|排除|不想要|\bwithout\b)\s*([^，。！？\n]{1,18})", query, re.I):
        if terms(denied) & terms(content):
            return UsageDecision(False, "current_request_excludes_memory", relevance)
    recalling = bool(_RECALL.search(query)) and not _NEW_TASK.search(query)
    personalized = bool(_PERSONALIZE.search(query)) and not _NO_PERSONALIZE.search(query)
    if inferred["role"] == "preference" or role == "preference":
        if _NO_PERSONALIZE.search(query):
            return UsageDecision(False, "personalization_disabled_by_current_request", relevance)
        if not preference_verified:
            return UsageDecision(False, "unconfirmed_preference", relevance)
        if effect == "presentation":
            return UsageDecision(True, "presentation_only", max(relevance, 0.35), effect)
        # A request to use established tastes explicitly authorizes suggestions,
        # not a claim that the chosen subject was specified by the user.
        if personalized:
            theme = _explicit_theme(query)
            if theme and not theme.intersection(terms(content)):
                return UsageDecision(False, "current_topic_overrides_preference", relevance)
            return UsageDecision(True, "personalized_suggestion", max(relevance, 0.35), "theme_suggestion")
        preference_recall = re.search(r"(?:我|我的).{0,8}(?:喜欢什么|偏好是什么|喜好是什么|口味是什么)|(?:回顾|列出).{0,8}(?:偏好|喜好)|what.{0,12}(?:like|prefer)", query, re.I)
        if preference_recall or (recalling and lexical_relevance(query, content) >= 0.12):
            return UsageDecision(True, "explicit_preference_recall", max(relevance, 0.35), "context")
        return UsageDecision(False, "theme_requires_current_authorization", relevance)
    if role == "task_episode" or effect == "task_context":
        if not recalling and not (same_task and relevance >= 0.2 and not _NEW_TASK.search(query)):
            return UsageDecision(False, "episode_requires_task_continuation", relevance)
        if same_task or (same_conversation and recalling):
            return UsageDecision(True, "task_continuation", max(relevance, 0.35), "task_context")
    if recalling and (same_task or same_conversation):
        return UsageDecision(True, "scoped_recall", max(relevance, 0.35), effect)
    if lexical < 0.12 and semantic < 0.65:
        return UsageDecision(False, "irrelevant_to_current_request", relevance)
    return UsageDecision(True, "relevant_context", relevance, effect)


def evidence_has_preference(text: str, statement: str) -> bool:
    """Do not promote task frequency, silence or assistant output to taste."""
    substance = terms(statement) - terms("用户喜欢用户偏好")
    return any(_PREFERENCE.search(clause) and not _TASK.search(clause) and bool(substance & terms(clause))
               for clause in re.split(r"[，,。.!?；;\n]", text))


def usage_instruction(effect: str) -> str:
    if effect == "presentation":
        return "仅可调整表达、解释或无障碍呈现，不得据此改变主题、功能和当前需求。"
    if effect == "theme_suggestion":
        return "只可作为用户已授权的个性化建议，不得声称用户本次指定了该主题。"
    if effect == "task_context":
        return "仅用于恢复被明确继续的任务，不得推断成用户长期偏好。"
    return "仅作与当前请求相关的事实背景，不得增加用户未提出的需求。"
