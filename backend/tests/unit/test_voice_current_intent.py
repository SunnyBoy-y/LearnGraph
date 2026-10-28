"""User-visible failures, tested at the actual input/context/TTS boundaries."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import LLMContextFrame, TextFrame, TranscriptionFrame
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection

from app.voice.conversation_control import VoiceConversationPolicy, VoiceConversationProcessor
from app.voice.embedded_context import VoiceContextAdapter
from app.voice.response_guard import SpeechContentGuard
from app.voice.task_context import select_task_rows, load_task_context
from app.voice.turn_plan import (
    filter_memory_block,
    follows_topic,
    plan_turn,
    project_request_messages,
    topic_turn_ids,
)

D = FrameDirection.DOWNSTREAM


@pytest.mark.parametrize('text,controls,expected_request', [
    ('别管页面了，现在讲一个故事。', ('dismiss_scene',), '现在讲一个故事'),
    ('针对我现在的问题：什么是LearnGraph？', ('current_only',), '什么是LearnGraph'),
    ('别重复了，告诉我今天几号？', ('no_repeat',), '告诉我今天几号'),
    ('别一直提我以前的错误，现在讲故事。', ('suppress_past_errors',), '现在讲故事'),
    ('简短点，解释二分查找。', ('concise',), '解释二分查找'),
    ('别重复了告诉我今天几号', ('no_repeat',), '告诉我今天几号'),
    ('不要重复了，请计算1.5乘2。', ('no_repeat',), '请计算1.5乘2'),
])
def test_compound_controls_keep_the_actual_request(text, controls, expected_request):
    plan = plan_turn(text)
    assert plan.controls == controls
    assert plan.request_text == expected_request
    assert plan.kind == 'answer'


@pytest.mark.parametrize('text', [
    '故事里的人为什么说闭嘴？', '什么是停止播报？', '把“别说了，讲个故事”解释一下。',
    '如果函数返回错误怎么办？', '请解释3.14与2.71的区别。',
])
def test_commands_in_content_are_not_applied(text):
    plan = plan_turn(text)
    assert plan.request_text == text
    assert plan.controls == ()


def test_control_then_verbatim_retains_punctuation():
    plan = plan_turn('别重复了，请你回复：嗯？？？？')
    assert plan.controls == ('no_repeat',)
    assert plan.verbatim == '嗯？？？？'
    assert plan.allow_repeat


def test_verbatim_strips_the_instruction_separator_but_not_literal_punctuation():
    assert plan_turn('请你说一遍，二分查找每次排除一半。').verbatim == '二分查找每次排除一半。'
    assert plan_turn('请你回复：嗯？？？？').verbatim == '嗯？？？？'


def test_input_control_and_context_apply_once_without_swallowing_question():
    async def run():
        policy, forwarded, published = VoiceConversationPolicy(), [], []
        async def publish(value): published.append(value)
        journal = AsyncMock()
        journal.current_turn_origin = lambda: 'user'
        input_stage = VoiceConversationProcessor(policy, stage='input', interrupt=AsyncMock(), publish=publish)
        context_stage = VoiceConversationProcessor(policy, stage='context', journal=journal, publish=publish)
        async def sink(frame, direction): forwarded.append(frame)
        input_stage.push_frame = context_stage.push_frame = sink
        text = '别管页面了，现在讲一个故事。'
        await input_stage.process_frame(TranscriptionFrame(text, 'user', 'now'), D)
        revision = policy.revision
        context = LLMContext([{'role':'user','content':text}])
        await context_stage.process_frame(LLMContextFrame(context), D)
        assert policy.revision == revision
        assert policy.background_muted and not policy.quiet
        assert isinstance(forwarded[-1], LLMContextFrame)
        assert context.get_messages()[-1] == {'role':'user','content':text}
        journal.assistant_completed.assert_not_awaited()
        assert len(published) == 1
    asyncio.run(run())


def test_same_words_in_a_new_admission_are_a_new_turn():
    async def run():
        policy = VoiceConversationPolicy()
        stage = VoiceConversationProcessor(policy, stage='input')
        stage.push_frame = AsyncMock()
        for _ in range(2):
            await stage.process_frame(TranscriptionFrame('什么是LearnGraph？','user','now'),D)
        assert policy.revision == 2
    asyncio.run(run())


def test_reconnect_preserves_reasked_question_and_merges_only_real_identity():
    snapshot = {'history':[
        {'id':'old-user','role':'user','content':'什么是LearnGraph？'},
        {'id':'old-answer','role':'assistant','content':'学习平台。'},
        {'id':'middle','role':'user','content':'现在聊新闻。'},
        {'id':'middle-answer','role':'assistant','content':'旧新闻回答。'},
        {'id':'latest','voice_turn_id':'new','role':'user','content':'什么是LearnGraph？'},
    ]}
    adapter = VoiceContextAdapter('voice','system',session_loader=lambda _:SimpleNamespace(context_snapshot=snapshot),
        turn_loader=lambda *a,**kw:[{'turn_id':'new','user_text':'什么是LearnGraph？','assistant_text':''}])
    messages = list(adapter.load().messages)
    assert messages[-1] == {'role':'user','content':'什么是LearnGraph？'}
    assert sum(item['content'] == '什么是LearnGraph？' for item in messages) == 2


def test_new_question_keeps_dialogue_without_reinjecting_the_old_snapshot():
    messages = [{'role':'system','content':'system'},
        {'role':'system','content':'stale persisted old question'},
        {'role':'user','content':'解释二分查找'}, {'role':'assistant','content':'你之前搞反了。'},
        {'role':'user','content':'现在讲一个童话故事。'}]
    projected = project_request_messages(messages, messages[-1]['content'])
    assert projected == [messages[0], *messages[2:]]
    assert len(messages) == 5


@pytest.mark.parametrize('query', [
    '用短剧那种风格',
    '对吧？',
    '把第二段缩短一点。',
    '我叫什么？',
    '现在讲一个童话故事。',
    '你好',
    '什么是光合作用？',
])
def test_every_ordinary_request_retains_recent_dialogue(query):
    messages = [
        {'role': 'system', 'content': 'system'},
        {'role': 'user', 'content': '我叫林川，接下来帮我改一个故事。'},
        {'role': 'assistant', 'content': '林川，故事有两段：第一段在图书馆，第二段在街上。'},
        {'role': 'user', 'content': query},
    ]
    assert project_request_messages(messages, query) == messages


def test_recent_dialogue_is_bounded_at_complete_user_turns():
    policy = {'role': 'system', 'content': '[voice-conversation-policy]优先回答当前问题'}
    messages = [{'role': 'system', 'content': 'system'}, policy]
    for number in range(8):
        messages.extend([
            {'role': 'user', 'content': f'第{number}个问题'},
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': f'call-{number}'}]},
            {'role': 'tool', 'tool_call_id': f'call-{number}', 'content': f'结果{number}'},
            {'role': 'assistant', 'content': f'第{number}个回答'},
        ])
    messages.append({'role': 'user', 'content': '把回答改短一点'})
    before = list(messages)
    projected = project_request_messages(messages, messages[-1]['content'])
    assert projected == [messages[0], policy, *messages[22:]]
    assert [item['content'] for item in projected if item['role'] == 'user'] == [
        '第5个问题', '第6个问题', '第7个问题', '把回答改短一点',
    ]
    assert messages == before


@pytest.mark.parametrize('query', [
    '当前上下文是否丢失了？',
    '我现在的问题是什么？',
    '找到问题',
    '找到原因',
    '刚才那个结论再说一下。',
    '我刚才问了什么？',
    '我叫什么名字？',
])
def test_contextual_followups_keep_the_turns_needed_to_resolve_them(query):
    messages = [
        {'role': 'system', 'content': 'system'},
        {'role': 'user', 'content': '解释二分查找'},
        {'role': 'assistant', 'content': '每轮排除一半候选。'},
        {'role': 'user', 'content': '为什么复杂度是 O(log n)？'},
        {'role': 'assistant', 'content': '因为每轮把规模除以二。'},
        {'role': 'user', 'content': query},
    ]
    assert project_request_messages(messages, query) == messages
    assert follows_topic(query)


@pytest.mark.parametrize('query', ['当前时间是什么？', '现在天气怎么样？'])
def test_current_facts_are_not_mistaken_for_contextual_followups(query):
    assert not follows_topic(query)


def test_followup_retains_tool_calls_and_results_as_a_pair():
    messages = [{'role':'system','content':'system'}, {'role':'user','content':'讲解这张表'},
        {'role':'assistant','content':None,'tool_calls':[{'id':'call'}]},
        {'role':'tool','tool_call_id':'call','content':'table'}, {'role':'assistant','content':'32变64'},
        {'role':'user','content':'那64变128呢？'}, {'role':'assistant','tool_calls':[{'id':'next'}]},
        {'role':'tool','tool_call_id':'next','content':'7'}]
    projected = project_request_messages(messages, '那64变128呢？')
    assert projected == messages


@pytest.mark.parametrize('query', ['现在讲一个童话故事。','你好','请你回复：嗯？？？？','什么是光合作用？'])
def test_unrelated_query_never_receives_old_task_body(query, monkeypatch):
    monkeypatch.setattr('app.voice.task_context.load_session', lambda *_: pytest.fail('unneeded database lookup'))
    assert load_task_context('voice',query) == ''


def test_explicit_task_and_related_followup_select_only_that_task():
    rows = [dict(task_id='new',title='其他任务'),dict(task_id='table',title='复杂度表格')]
    assert select_task_rows(rows,'解释复杂度表格') == [rows[1]]
    assert select_task_rows(rows,'那64变128呢？',('table',)) == [rows[1]]
    assert select_task_rows(rows,'为什么天空是蓝色的？',('table',)) == []


def test_same_topic_followup_does_not_expire_the_originating_result():
    assert topic_turn_ids([{'turn_id':'b','user_text':'那64变128呢？'},{'turn_id':'a','user_text':'解释二分查找'}]) == {'a','b'}
    assert topic_turn_ids([{'turn_id':'c','user_text':'讲一个故事'},{'turn_id':'a','user_text':'解释二分查找'}]) == {'c'}


def test_old_error_profile_is_not_recalled_unasked_but_review_is_available():
    block = '用户之前把 O(n) 和 O(log n) 搞反，后来已经答对。'
    assert filter_memory_block(block,'解释二分查找') == ''
    assert filter_memory_block(block,'请复盘我之前的错题') == block
    assert filter_memory_block(block,'别提我以前的错误，解释二分查找') == ''
    assert filter_memory_block('用户喜欢Python','我喜欢什么编程语言？') == '用户喜欢Python'


def test_event_replay_is_suppressed_but_equal_streaming_tokens_survive():
    async def run():
        stage = VoiceConversationProcessor(VoiceConversationPolicy(),stage='output')
        frames=[]
        async def sink(frame,direction): frames.append(frame)
        stage.push_frame=sink
        frame=TextFrame('哈')
        await stage.process_frame(frame,D)
        await stage.process_frame(frame,D)
        await stage.process_frame(TextFrame('哈'),D)
        assert [frame.text for frame in frames] == ['哈','哈']
    asyncio.run(run())


def test_sentence_guard_handles_duplicates_without_rejecting_new_facts_or_requests():
    guard=SpeechContentGuard()
    first='二分查找每次排除一半候选范围。'
    assert guard.admit(first,'response-1',no_repeat=True)
    assert not guard.admit(first,'response-1',no_repeat=True)
    assert guard.admit(first,'response-2',no_repeat=True)  # reasked question
    assert guard.admit(first,'response-2',no_repeat=True,allow_repeat=True)
    assert guard.admit('n从64变成128，对数值从6变成7。','response-2',no_repeat=True)
    assert guard.admit('嗯？？？？','response-2',no_repeat=True)
    assert guard.admit('嗯？？？？','response-2',no_repeat=True)


def test_semantic_repeat_is_observed_without_fabricating_user_playback():
    guard=SpeechContentGuard()
    text='二分查找每一轮都将候选范围排除一半，因此规模翻倍只增加一步。'
    guard.observe_released('old-turn',text)
    assert guard.admit(text,'new-response',no_repeat=True)
    assert guard.semantic_candidates == 1
    assert guard.blocked == 0
    assert guard._released[-1][2] == 'server_released'
