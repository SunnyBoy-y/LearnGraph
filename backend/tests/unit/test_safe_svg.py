import json
from pathlib import Path

import pytest

from app.domain.safe_svg import validate_illustration_svg
from app.domain.schemas.stream_ui import Snapshot
from app.services.stream_ui import compile_command
from app.core.errors import AppError

CASES = json.loads((Path(__file__).parents[1] / "fixtures/illustration-svg.json").read_text(encoding="utf-8"))
SVG = CASES[0]["svg"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_shared_static_svg_security_contract(case):
    if case["valid"]:
        assert validate_illustration_svg(case["svg"]) == case["svg"]
    else:
        with pytest.raises(ValueError):
            validate_illustration_svg(case["svg"])


@pytest.mark.parametrize("body", ["<circle r='1'/>" * 100, "<g>" * 9 + "<circle r='1'/>" + "</g>" * 9, "<text>" + "字" * 9000 + "</text>"], ids=["element-count", "depth", "bytes"])
def test_svg_complexity_bounds(body):
    with pytest.raises(ValueError):
        validate_illustration_svg(f'<svg viewBox="0 0 100 100">{body}</svg>')


def test_image_queries_allow_later_fallback_while_old_snapshots_still_load():
    command = {"op": "mount", "component": "document", "block_id": "fallback", "props": {"title": "形态"},
               "upsert": {"blocks": [{"id": "pic", "type": "image", "query": "leaf shape"}]}}
    assert compile_command(command).blocks[0].fallback_svg == ""
    command["upsert"]["blocks"][0]["fallback_svg"] = SVG
    state = compile_command(command).model_dump(mode="json")
    assert Snapshot.model_validate(state).blocks[0].fallback_svg == SVG
    state["blocks"][0].pop("fallback_svg")
    assert Snapshot.model_validate(state).blocks[0].fallback_svg == ""
