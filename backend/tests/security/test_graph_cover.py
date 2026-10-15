"""Graph cover contract and safety regressions."""

from __future__ import annotations

import base64
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import create_engine, inspect

from app.core.migrations import _graph_cover_column
from app.domain.schemas.graphs import GraphCoverUpdateRequest
from app.services.graph_cover import (
    default_graph_cover,
    generate_graph_cover,
    generate_template_cover,
    validate_custom_svg,
)
from app.services.mcp_skills import BUILTIN_TOOL_SPECS, CORE_BUILTIN_TOOL_NAMES


def _image_data_url() -> str:
    output = BytesIO()
    Image.new("RGB", (32, 16), "#d9e8ff").save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def test_generated_and_template_covers_are_valid_data_urls() -> None:
    generated = generate_graph_cover(
        'XML <title> & "quotes"',
        node_labels=["root", "child"],
        progress=1.4,
    )
    assert generated.startswith("data:image/svg+xml,")
    assert "%3Cscript" not in generated.lower()

    for template in ("paper", "midnight", "sunrise", "unknown"):
        cover = generate_template_cover("图谱", template)
        assert cover.startswith("data:image/svg+xml,")


def test_custom_svg_is_sanitized_and_rejected_when_active() -> None:
    safe = validate_custom_svg(
        '<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10" fill="#fff"/></svg>'
    )
    assert safe.startswith("data:image/svg+xml,")

    with pytest.raises(ValueError):
        validate_custom_svg(
            '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
        )


def test_cover_request_requires_mode_specific_content() -> None:
    assert GraphCoverUpdateRequest(mode="generated").mode == "generated"
    assert GraphCoverUpdateRequest(
        mode="template", template="paper"
    ).template == "paper"
    assert GraphCoverUpdateRequest(
        mode="image", image_data_url=_image_data_url()
    ).image_data_url
    with pytest.raises(ValueError):
        GraphCoverUpdateRequest(mode="image")
    with pytest.raises(ValueError):
        GraphCoverUpdateRequest(mode="svg")


def test_cover_tools_are_discoverable_and_update_is_core() -> None:
    assert BUILTIN_TOOL_SPECS["builtin.graph.read"]["function_name"] == "lg_graph_read"
    assert (
        BUILTIN_TOOL_SPECS["builtin.graph.cover.read"]["function_name"]
        == "lg_graph_cover_read"
    )
    assert (
        BUILTIN_TOOL_SPECS["builtin.graph.cover.update"]["function_name"]
        == "lg_graph_cover_update"
    )
    assert "builtin.graph.cover.read" in CORE_BUILTIN_TOOL_NAMES
    assert "builtin.graph.cover.update" in CORE_BUILTIN_TOOL_NAMES


def test_graph_cover_migration_adds_column_to_existing_graph_table() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE graphs (id VARCHAR(36) PRIMARY KEY)")
        _graph_cover_column(connection)
    assert "cover_svg" in {
        column["name"] for column in inspect(engine).get_columns("graphs")
    }
    engine.dispose()


def test_default_cover_is_independent_fallback() -> None:
    assert default_graph_cover("Fallback").startswith("data:image/svg+xml,")
