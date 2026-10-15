"""练习结构化生成回归测试。

现象：「自由练习」点「开始练习」报
``Model structured generation failed after 3 attempts``。

根因预期：``_strict_json_schema`` 把 Pydantic 模型里**有默认值的可选字段**
也标成 required，并且禁止任何额外字段；而这份"严格 schema"又被用来
**校验模型返回的 JSON**。于是 json_object / 提示词模式下真实模型的常见输出
（省略空数组、或多带一个解释性字段）会被判为不合法，重试 3 次后直接 502。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.domain.schemas.learning import ModelGeneratedExerciseSet
from app.providers.remote.openai import OpenAICompatibleChatProvider

ITEMS_FULL = [
    {
        "question_type": "single_choice",
        "prompt": "B+ 树相比哈希索引的主要优势是？",
        "options": ["支持范围查询", "占用内存更少", "插入更快", "不需要磁盘"],
        "answer_key": "支持范围查询",
        "explanation": "B+ 树叶子节点有序链表，天然支持范围扫描。",
        "rubric_points": [],
        "hint": "想想 ORDER BY / BETWEEN 这类查询。",
        "source_chunk_ids": [],
    }
]


def _provider(payload_content: dict) -> OpenAICompatibleChatProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(payload_content, ensure_ascii=False)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 200},
            },
        )

    return OpenAICompatibleChatProvider(
        provider_id="repro",
        provider_type="openai_compatible_chat",
        model_id="repro-model",
        base_url="https://provider.invalid/v1",
        api_key="test-key",
        capabilities={"supports_structured_output": False},
        transport=httpx.MockTransport(handler),
    )


def _schema() -> dict:
    return ModelGeneratedExerciseSet.model_json_schema()


def _generate(content: dict) -> dict:
    return _provider(content).generate_json(
        "请出题", "exercise_generate", _schema()
    )


def test_complete_output_is_accepted():
    """模型把每个字段都写全时，当前实现是能通过的（对照组）。"""

    result = _generate({"items": ITEMS_FULL})
    assert result["items"][0]["prompt"] == ITEMS_FULL[0]["prompt"]


def test_output_omitting_empty_optional_fields_is_accepted():
    """真实模型常省略空数组 / 空字符串——这必须算成功，而不是重试 3 次。"""

    slim = {
        key: value
        for key, value in ITEMS_FULL[0].items()
        if value not in ([], "")
    }
    result = _generate({"items": [slim]})
    assert result["items"][0]["question_type"] == "single_choice"


def test_output_with_one_extra_explanatory_field_is_accepted():
    """模型多带一个解释性字段时不能整轮失败（Pydantic 侧会忽略它）。"""

    noisy = {**ITEMS_FULL[0], "difficulty": "medium"}
    result = _generate({"items": [noisy]})
    assert result["items"][0]["prompt"] == ITEMS_FULL[0]["prompt"]


def test_schema_mismatch_still_fails_loudly():
    """真正不合法的结构仍必须报错，不能被"宽容"吞掉。"""

    from app.providers.remote.openai import ProviderResponseError

    with pytest.raises(ProviderResponseError):
        _generate({"items": [{"prompt": "缺少题型与答案"}]})
