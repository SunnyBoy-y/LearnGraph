import pytest

from app.core.errors import AppError
from app.providers.ports.model import ProviderStreamEvent
from app.services.genui_express import ExpressUIParser
from app.services.rich_response import InlineUIParser, UICommandEvent, rich_events
from app.services.stream_ui import compile_command


def compile_inline(text: str, chunk_size: int = 1):
    parser = InlineUIParser()
    chunks = [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    events = [event for chunk in chunks for event in parser.feed(chunk)] + list(parser.finish())
    snapshots = {}
    commands = []
    for event in events:
        if isinstance(event, UICommandEvent):
            cmd = event.command
            commands.append(cmd)
            snapshots[cmd["block_id"]] = compile_command(cmd, snapshots.get(cmd["block_id"])).model_dump(mode="json")
    return events, commands, snapshots


def test_express_dsl_streams_incremental_patches_and_completes():
    dsl = "\n".join([
        "先看结论。",
        ":::ui demo express",
        "@title \"量子纠缠概览\"",
        "@density editorial",
        "@toc true",
        "section#intro label=\"核心概念\"",
        "  h2#h1: 什么是量子纠缠",
        "  paragraph#p1: 两个粒子之间存在非局域关联。",
        "  | 测量其中一个会立即决定另一个的状态。",
        "  step_list#steps label=\"理解步骤\"",
        "    rail_item#s1 icon=fluent:light-bulb: 准备纠缠对 | 产生自旋单态光子对 | 可由 SPDC 生成",
        "    rail_item#s2: 分别测量 | 在空间分离后独立测量",
        "  metric#m title=\"实验参数\" data_kind=example",
        "    item#dist label=\"分发距离\": 1200 | 公里（卫星链路）",
        "  chart#c title=\"关联强度\" columns=\"v:保真度\" unit=\"%\" data_kind=example",
        "    row#r1 label=\"100km\" v=96",
        "    row#r2 label=\"500km\" v=91",
        "  graph#g title=\"概念关系\" layout=mindmap",
        "    node#a label=\"纠缠源\": BBO晶体",
        "    node#b label=\"探测器\": 单光子计数",
        "    edge#ab from=a to=b: 分发光子",
        ":::",
        "后续文字。",
    ])
    for chunk_size in (1, 7, len(dsl)):
        events, commands, snapshots = compile_inline(dsl, chunk_size=chunk_size)
        assert len(snapshots) == 1
        snap = next(iter(snapshots.values()))
        assert snap["state"] == "completed"
        assert snap["props"]["title"] == "量子纠缠概览"
        assert snap["props"]["density"] == "editorial"
        assert snap["props"]["toc"] is True
        # Incremental patches arrived before complete
        patches = [c for c in commands if c["op"] == "patch"]
        assert len(patches) >= 6
        assert commands[-1]["op"] == "complete"
        by_id = {b["id"]: b for b in snap["blocks"]}
        assert by_id["p1"]["text"] == "两个粒子之间存在非局域关联。\n测量其中一个会立即决定另一个的状态。"
        assert by_id["s1"]["parent_id"] == "steps"
        assert by_id["s1"]["label"] == "准备纠缠对"
        assert by_id["s1"]["caption"] == "可由 SPDC 生成"
        assert by_id["m"]["visual"]["items"][0]["value"] == 1200
        assert by_id["c"]["visual"]["rows"][1]["values"]["v"] == 91
        assert by_id["g"]["visual"]["props"]["graph_layout"] == "mindmap"
        assert by_id["g"]["visual"]["edges"][0]["source"] == "a"
        prose = "".join(e.content or "" for e in events if e.type == "text_delta")
        assert prose.startswith("先看结论。") and prose.endswith("后续文字。")
        assert "量子纠缠概览" not in prose


def test_express_dsl_auto_detects_without_express_keyword():
    dsl = "\n".join([
        ":::ui auto_doc",
        "@title 自动识别模式",
        "paragraph#p1: 第一段内容",
        "figure#fig: 图示说明",
        "  image#img query=\"solar eclipse\" alt=\"日食\"",
        ":::",
    ])
    _, commands, snapshots = compile_inline(dsl, chunk_size=5)
    snap = next(iter(snapshots.values()))
    assert snap["state"] == "completed"
    assert snap["props"]["title"] == "自动识别模式"
    by_id = {b["id"]: b for b in snap["blocks"]}
    assert by_id["img"]["parent_id"] == "fig"
    assert by_id["img"]["query"] == "solar eclipse"
    assert commands[-1]["op"] == "complete"


def test_express_visual_stays_pending_until_visual_children_finish():
    parser = ExpressUIParser("doc1")
    cmds_open = parser.feed_line('chart#c title="指标" columns="v:值"\n')
    assert cmds_open[0]["upsert"]["blocks"][0]["state"] == "pending"
    assert "visual" not in cmds_open[0]["upsert"]["blocks"][0]
    assert parser.feed_line('  row#r1 label="一月" v=42\n') == []
    cmds_next = parser.feed_line("paragraph#p1: 解释完成。\n")
    assert cmds_next[0]["upsert"]["blocks"][0]["id"] == "c"
    assert cmds_next[0]["upsert"]["blocks"][0]["state"] == "completed"
    assert cmds_next[0]["upsert"]["blocks"][0]["visual"]["rows"][0]["values"]["v"] == 42
    assert cmds_next[1]["upsert"]["blocks"][0]["id"] == "p1"


@pytest.mark.parametrize("bad_line", [
    "html#x: <script>alert(1)</script>",
    "paragraph#__proto__: nope",
    "paragraph#p1 onClick=\"alert(1)\": bad",
    "@onClick bad",
    "image#img media=\"https://evil.example/x.png\"",
])
def test_express_dsl_rejects_unsafe_constructs_and_falls_back_cleanly(bad_line):
    text = f"前文\n:::ui bad express\n@title 安全测试\n{bad_line}\n:::\n后文"
    events, commands, snapshots = compile_inline(text, chunk_size=4)
    snap = next(iter(snapshots.values()))
    assert snap["state"] == "removed"
    assert commands[-1]["op"] == "remove"
    prose = "".join(e.content or "" for e in events if e.type == "text_delta")
    assert "这部分图文未能生成" in prose
    assert "alert(1)" not in prose


def test_express_json_backward_compatibility_and_rich_events_rollback():
    def provider():
        yield ProviderStreamEvent("text_delta", content=":::ui x express\n@title 标题\nparagraph#p1: 正文\n:::\n")
        raise RuntimeError("stream cut")

    events = []
    with pytest.raises(RuntimeError, match="stream cut"):
        for ev in rich_events(provider()):
            events.append(ev)
    assert events[-1].command["op"] == "remove"
    assert events[-1].command["base_revision"] == 4


def test_express_supports_data_checks_weight_and_image_variants():
    from app.services.genui_express import ExpressUIParser

    parser = ExpressUIParser("doc_exp")
    cmds = []
    for line in [
        "@title 监控看板\n",
        '@data {"cpu": 92}\n',
        '@check#cpu_hi fn=max_value path=@/cpu args=[80] severity=warning: CPU 使用率偏高\n',
        "row#r1 columns=2\n",
        "  paragraph#p1 weight=2: 当前 CPU 为 @{/cpu}%\n",
        "  image#img1 image_variant=hero fit=cover: server rack\n",
    ]:
        cmds.extend(parser.feed_line(line))
    cmds.extend(parser.finish())

    assert any(c.get("op") == "data" and c["data_model"] == {"cpu": 92} for c in cmds)
    assert any(c.get("checks") and c["checks"][0]["id"] == "cpu_hi" for c in cmds)
    assert parser.blocks[1]["weight"] == 2
    assert parser.blocks[2]["image_variant"] == "hero"
    assert parser.blocks[2]["fit"] == "cover"
