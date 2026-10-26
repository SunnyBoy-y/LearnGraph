import copy
import pytest
from app.core.errors import AppError
from app.services.genui_inline import canonical_inline_document
from app.services.stream_ui import compile_command


def compile_doc(raw):
    return compile_command({"op":"mount","block_id":"test","component":"document",**canonical_inline_document(raw)})


def test_known_nesting_variants_preserve_ids_values_captions_and_source():
    raw={"props":{"title":"报告","toc":True},"upsert":{"blocks":[
        {"id":"chart","type":"chart","props":{"title":"趋势","source_note":"用户给定","x_label":"月份","y_label":"人数","columns":[{"key":"v","label":"人数"}]},"rows":[{"id":"may","label":"5月","values":{"v":1200}}]},
        {"id":"table","type":"table","visual":{"props":{"title":"补充数据"},"columns":[{"key":"v","label":"说明"}],"rows":[{"id":"a","label":"渠道","values":{"v":"需要补充"}}]}},
        {"id":"steps","type":"document","node_type":"step_list","props":{"title":"验证步骤"},"items":[{"id":"first","label":"确认","description":"统一口径"}]},
        {"id":"metric","type":"metric","visual":{"props":{"title":"指标"},"items":[{"id":"latest","label":"月活","value":3180,"unit":"人"}]}},
    ]}}
    original=copy.deepcopy(raw)
    result=compile_doc(raw)
    assert raw==original
    by_id={n.id:n for n in result.blocks}
    assert by_id["chart"].visual["rows"][0]["values"]=={"v":1200}
    assert "横轴：月份" in by_id["chart"].visual["props"]["description"]
    assert by_id["chart"].visual["props"]["source_note"]=="用户给定"
    assert by_id["table"].visual["props"]["columns"][0]["key"]=="v"
    assert by_id["steps"].type=="step_list" and by_id["first"].parent_id=="steps"
    assert by_id["steps"].label=="验证步骤" and by_id["first"].text=="统一口径"
    assert by_id["metric"].visual["items"][0]["value"]==3180
    assert by_id["metric"].visual["items"][0]["label"]=="月活（人）"


def test_single_metric_row_can_be_represented_by_items_without_changing_measurements():
    raw={"props":{"title":"指标"},"upsert":{"blocks":[{"id":"m","type":"metric","props":{"title":"指标","columns":[{"key":"n","label":"人数","unit":"人"},{"key":"g","label":"增幅","unit":"%"}]},"rows":[{"id":"row","label":"示意值","values":{"n":3180,"g":165}}]}]}}
    values=compile_doc(raw).blocks[0].visual["items"]
    assert [(i["id"],i["value"]) for i in values]==[("n",3180),("g",165)]


@pytest.mark.parametrize("change",[
    {"props":{"title":"趋势","onClick":"execute()"}},
    {"visual":{"props":{"title":"趋势"}},"props":{"title":"不同标题"}},
])
def test_compatibility_does_not_accept_unknown_code_or_resolve_conflicting_fields(change):
    raw={"props":{"title":"报告"},"upsert":{"blocks":[{"id":"chart","type":"chart",**change}]}}
    with pytest.raises((ValueError,AppError)):
        compile_doc(raw)


def test_mixed_or_excessive_metric_rows_are_not_guessed():
    raw={"props":{"title":"报告"},"upsert":{"blocks":[{"id":"m","type":"metric","props":{"title":"指标","columns":[{"key":"v","label":"人数"}]},"rows":[{"id":"a","values":{"v":1}},{"id":"b","values":{"v":2}}]}]}}
    with pytest.raises(ValueError): compile_doc(raw)
