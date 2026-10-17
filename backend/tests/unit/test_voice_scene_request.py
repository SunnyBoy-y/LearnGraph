from app.voice.task_context import requested_scene


def result(task='task', block='table', title='分组对比'):
    return {'task_id':task,'scene':{'available_anchors':['summary',f'visual-{block}'],
        'visuals':[{'block_id':block,'component':'table','props':{'title':title}}]}}


def test_explicit_explanation_reveals_the_known_component_without_model_tool_selection():
    assert requested_scene([result()], '请讲解刚才生成的交互表格，解释两行内容。') == ('task','visual-table')
    assert requested_scene([result()], '请解释这张表。') == ('task','visual-table')


def test_ordinary_questions_and_unrelated_instructions_do_not_reveal_tasks():
    assert requested_scene([result()], '解释一下光合作用。') is None
    assert requested_scene([result()], '在后台生成一张表格。') is None


def test_ambiguous_outputs_require_a_specific_reference():
    rows=[result(),result('second','other','课程表')]
    assert requested_scene(rows,'请讲解表格。') is None
    assert requested_scene(rows,'请讲解课程表。') == ('second','visual-other')
    assert requested_scene(rows,'请讲解刚才的表格。') == ('task','visual-table')
