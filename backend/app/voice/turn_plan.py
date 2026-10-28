"""Deterministic per-turn intent and context selection; no provider or DB IO."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class TurnPlan:
    request_text: str = ""
    controls: tuple[str, ...] = ()
    verbatim: str | None = None

    @property
    def kind(self) -> str:
        if self.verbatim is not None:
            return "verbatim"
        return "answer" if self.request_text else (self.controls[-1] if self.controls else "answer")

    @property
    def allow_repeat(self) -> bool:
        # A repeated question is a new request, not a duplicate network event.
        return self.verbatim is not None or bool(re.search(r"再说|再讲|重说|重复|没听清|没听懂|重新解释", self.request_text))


_VERBATIM = re.compile(r"^(?:请(?:你)?|麻烦你)?(?:原样复述|原样回复|只回复|只说|回复|说一遍|复述|重复一遍)\s*[：:]?\s*(.+)$", re.S)
_CONTROLS = (
    ("suppress_past_errors", r"(?:请(?:你)?)?(?:别|不要)(?:再|一直|总是|老是)?(?:提起?|说|抓着|揪着|针对)(?:我)?(?:以前|之前|过去|历史|的)?(?:的)?(?:错(?:误|题)?|问题)(?:不放|了)?"),
    ("current_only", r"(?:请(?:你)?)?(?:针对我(?:现在|当前)的问题|只回答(?:我)?(?:现在|当前)(?:的)?问题|别扯(?:我)?之前(?:的事)?)"),
    ("no_repeat", r"(?:我知道了)?(?:你说过了|说过就不要说了|说过了|(?:别|不要)(?:一直|再)?重复(?:说(?:一样的内容)?)?了?)"),
    ("dismiss_scene", r"(?:请(?:你)?)?(?:别(?:管|担心|说)(?:我|这个|那个|这|那|的)?页面(?:了)?|不用(?:管)?页面了?|不要(?:管)?页面了?)"),
    ("display", r"(?:给我看呢\s*)?(?:我没看到(?:你就)?|没看到)(?:就)?(?:不要|别)播报|(?:我要|给我看|打开|展示|显示)(?:一下)?(?:页面|图表|产物)|我咋没看到|给我看呢"),
    ("concise", r"(?:请(?:你)?)?(?:你好啰嗦啊?|太啰嗦了?|简短点|少说点)"),
    ("auto_delivery", r"(?:请(?:你)?)?(?:恢复|开启|允许)自动播报"),
    ("silence", r"(?:别说|别讲)(?:那个|呢个)了\s*闭嘴|(?:请你|请|你)?(?:闭嘴|别说了|先别说了?|不要说话|停止播报|停止说话|安静|停一下|停)"),
)
_CONTROL_PREFIXES = tuple((name, re.compile(r"^(?:" + pattern + r")")) for name, pattern in _CONTROLS)
_FRUSTRATION = re.compile(r"^(?:你)?(?:好烦啊?|烦死了|真烦|别说(?:那个|呢个)了)$")


def _clauses(text: str) -> list[str]:
    """Only punctuation outside quotes separates actions; retain quoted text."""
    result, buf, closing = [], [], []
    pairs = {'“': '”', '「': '」', '"': '"', '‘': '’'}
    for index, char in enumerate(text):
        if closing and char == closing[-1]:
            closing.pop()
        elif char in pairs:
            closing.append(pairs[char])
        decimal = char in '.,' and text[max(0,index-1):index].isdigit() and text[index+1:index+2].isdigit()
        if char in "，。！？、,.!?；;：:\n" and not closing and not decimal:
            if buf:
                result.append("".join(buf).strip())
                buf = []
        else:
            buf.append(char)
    if buf:
        result.append("".join(buf).strip())
    return result


def plan_turn(text: str) -> TurnPlan:
    value = str(text or "").strip()
    match = _VERBATIM.match(value)
    if match:
        # Chinese callers commonly use either a colon or a comma after
        # “说一遍”. It separates the instruction from the literal; it is not
        # part of what should be voiced.
        tail = match[1].lstrip(" \t，,：:").strip()
        if len(tail) > 1 and (tail[0], tail[-1]) in {('“', '”'), ('「', '」'), ('"', '"')}:
            tail = tail[1:-1]
        return TurnPlan(verbatim=tail)
    leading, remainder = [], value
    while remainder:
        prefix = next(((kind, found) for kind, pattern in _CONTROL_PREFIXES if (found := pattern.match(remainder))), None)
        if prefix is None:
            break
        leading.append(prefix[0])
        remainder = remainder[prefix[1].end():].lstrip(" \t，。！？、,.!?；;：:\n")
    if leading and _VERBATIM.match(remainder):
        literal = plan_turn(remainder)
        return TurnPlan(controls=tuple(dict.fromkeys(leading)), verbatim=literal.verbatim)
    controls, requests = [], []
    for clause in _clauses(value):
        rest = clause
        while rest:
            found = next(((kind, match) for kind, pattern in _CONTROL_PREFIXES if (match := pattern.match(rest))), None)
            if found is None:
                if not _FRUSTRATION.fullmatch(rest):
                    requests.append(rest)
                break
            kind, match = found
            controls.append(kind)
            rest = rest[match.end():].lstrip(" \t：:，,")
            rest = re.sub(r"^(?:然后|接下来|现在|那就|那)(?=请|给|帮|讲|说|告诉|回答|解释|什么|今天)", "", rest)
    # Keep the original words and punctuation when there were no controls.
    return TurnPlan(request_text="，".join(requests) if controls else value, controls=tuple(dict.fromkeys(controls)))


def small_talk(query: str) -> bool:
    return bool(re.fullmatch(r"(?:你好|您好|嗨|哈喽|在吗|嗯+|哦+|好的?|谢谢|知道了)[\s。！!？?～~]*", query.strip()))


def switches_topic(query: str) -> bool:
    return bool(re.search(r"换(?:个|一个)?(?:话题|问题)|先不(?:聊|说|讲|管)|聊点别的|讲(?:个|一个|一段)?(?:童话|故事|笑话)|今天天气|今天几号|什么是LearnGraph", query, re.I))


def refers_to_task(query: str) -> bool:
    if switches_topic(query) or small_talk(query):
        return False
    return bool(re.search(r"后台|任务|产物|表格|图表|这张表|那张表|刚才.*(?:结果|生成)|继续做|继续执行|继续任务|进度|完成了吗|sa_[\w-]+", query, re.I))


def follows_topic(query: str) -> bool:
    text = query.strip()
    if switches_topic(text) or re.match(r"^(?:那|那么)?(?:什么是|解释一下|介绍一下)", text):
        return False
    # Elliptical questions about the current conversation need the preceding
    # turns. Keep explicit topic changes separate from these references.
    contextual = (
        r"^(?:那|那么|所以|也就是|如果|还是|然后呢|继续|接着|再说|再讲|"
        r"这个|那个|它|这张|那张|对于|n\s*[=从])"
        r"|^(?:当前|现在)(?:的)?(?:上下文|问题|意思|需求|说法|对话|情况)"
        r"|^(?:刚才|上文|上面|前面|此前|前述|上述)(?:的)?"
        r"(?:问题|内容|结论|回答|话|上下文)?"
        r"|^我(?:现在|当前|刚才|之前)?的(?:问题|意思|需求|说法|上下文)"
        r"|^(?:找到|检查|分析|看看|排查)(?:一下)?(?:这个|当前|刚才)?(?:问题|原因)"
        r"|^问题(?:到底)?出在哪|^哪里出了问题"
        r"|^我(?:刚才|之前|叫什么|的名字)"
        r"|^你(?:刚才|之前)"
    )
    return bool(re.search(contextual, text, re.I)
                or re.fullmatch(r"(?:为什么|为啥)(?:呢|呀|会这样|是这样)?[？?。]*", text))


def topic_turn_ids(turns: Sequence[Mapping[str, Any]]) -> set[str]:
    """Newest-first turns. A related follow-up inherits, a new request fences."""
    ids: set[str] = set()
    for turn in turns:
        plan = plan_turn(str(turn.get("user_text") or ""))
        text = plan.request_text
        if not text and plan.verbatim is None:
            continue
        # A greeting fences old automatic delivery, even though it is brief.
        turn_id = str(turn.get("turn_id") or turn.get("id") or "")
        if turn_id:
            ids.add(turn_id)
        if plan.verbatim is not None or not follows_topic(text):
            break
    return ids


_OLD_ERROR = re.compile(r"错题|误区|答错|错误|搞反|混淆|薄弱|不会|没掌握|mistake|misconception|weakness", re.I)


def filter_memory_block(block: str, query: str, *, suppress_past_errors: bool = True) -> str:
    """Conservative: do not expose a recalled error profile outside a review."""
    if not block or small_talk(query) or switches_topic(query) or plan_turn(query).verbatim is not None:
        return ""
    review = bool(re.search(r"复盘|复习.*(?:错|薄弱)|(?:我的|之前的|以前的)错题|我(?:以前|之前).*错|我哪里错", query))
    if 'suppress_past_errors' in plan_turn(query).controls:
        review = False
    if suppress_past_errors and not review and _OLD_ERROR.search(block):
        return ""
    return block


def project_request_messages(messages: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Keep recent dialogue for every request, independently of task relevance."""
    if not messages:
        return list(messages)
    # Tool calls must retain their matching results. Only cut at an ordinary
    # user boundary, and preserve the latest request even when text repeats.
    # Async tool results may be converted from developer to user by a provider
    # adapter. Locate the actual request using the universal context's query.
    candidates = [i for i, item in enumerate(messages) if item.get("role") == "user" and item.get("content") == query]
    if not candidates:
        return list(messages)
    current = candidates[-1]
    users = [i for i, item in enumerate(messages[:current+1]) if item.get("role") == "user"]
    # Topic recognition controls optional task data and automatic delivery,
    # not whether the tutor can remember its own conversation. Treating every
    # unrecognised phrase as a fresh conversation reduced ordinary requests
    # to system + current user and broke name recall, edits and confirmations.
    # Retain the existing bounded window for *all* requests.
    cut = users[max(0, len(users) - 4)]
    rules = [item for item in messages[:cut] if item.get("role") == "system" and (
        str(item.get("content", "")).startswith("[voice-conversation-policy]") or item is messages[0])]
    return [*rules, *messages[cut:]]
