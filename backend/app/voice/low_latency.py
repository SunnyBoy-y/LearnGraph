"""Short acknowledgement plans; no extra inference or tool execution."""
from __future__ import annotations

import re

from app.voice.turn_plan import plan_turn, small_talk


def complex_request_acknowledgement(text: str) -> str | None:
    plan = plan_turn(text)
    query = plan.request_text.strip()
    if plan.verbatim is not None or not query or small_talk(query):
        return None
    if len(query) < 24 and re.match(r"^(?:什么是|什么叫|今天几号|现在几点)", query):
        return None
    if re.search(r"编写|写代码|写程序|生成代码|调试|修复|排查|重构|优化", query):
        return "嗯……我来看看怎么处理。"
    if re.search(r"查资料|查找资料|检索|文献|调研|研究", query):
        return "嗯……我来梳理一下相关资料。"
    if re.search(r"分析|对比|比较|推导|证明|评估", query):
        return "嗯……我来分析一下。"
    if re.search(r"整理|生成|设计|制定|规划|方案|计划|写一份|写一个", query):
        return "嗯……我来整理一下。"
    if len(query) >= 48:
        return "嗯……我来仔细看看。"
    return None


def tool_acknowledgement(function_name: str) -> str | None:
    if function_name == "search_web":
        return "嗯……我来核对一下相关资料。"
    if function_name.startswith("delegate_"):
        return "嗯……我来处理这个任务。"
    return None
