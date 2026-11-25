"""Default artwork and compatibility with already-saved automatic covers."""
from urllib.parse import quote, unquote
from xml.etree import ElementTree

import pytest

from app.services import graph_cover

NS = "{http://www.w3.org/2000/svg}"


def _root(cover: str):
    return ElementTree.fromstring(unquote(cover.split(",", 1)[1]))


def _legacy_cover(frontend: bool = False) -> str:
    curve = "M80 230 Q180 90 280 210 T470 180" if frontend else "M70 230 Q170 90 280 210 T480 180"
    x = "500" if frontend else "510"
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 300">
<rect width="640" height="300" fill="hsl(120 18% 94%)"/>
<circle cx="{x}" cy="120" r="82" fill="hsl(120 35% 78%)"/>
<path d="{curve}" fill="none" stroke="hsl(120 30% 35%)" stroke-width="8"/>
<rect x="40" y="260" width="560" height="10" rx="5" fill="#d8dadd"/>
</svg>'''
    return "data:image/svg+xml," + quote(svg)


def test_default_artwork_is_full_bleed_and_keeps_the_title_as_safe_metadata():
    title = '曲奇 <script> & "烘焙"\x00\ud800'
    cover = graph_cover.generate_graph_cover(title, progress=float("nan"), node_labels=["材料"])
    root = _root(cover)
    assert root.get("data-cover-version") == "2"
    assert root.get("data-cover-kind") == "generated"
    assert root.find(NS + "title").text == '曲奇 <script> & "烘焙"'
    assert root.find(NS + "text") is None
    assert root.find(NS + "script") is None
    image = root.find(NS + "image")
    assert image.get("href").startswith("data:image/jpeg;base64,")
    assert image.get("preserveAspectRatio") == "xMidYMid slice"
    assert not graph_cover.is_legacy_generated_cover(cover)
    assert cover == graph_cover.generate_graph_cover(title)
    assert cover != graph_cover.default_graph_cover(title)


def test_topic_artwork_uses_bundled_subjects_and_generic_art_for_other_titles():
    assert graph_cover._default_artwork_data_url("文学入门") == graph_cover._load_artwork_data_url("wen")
    assert graph_cover._default_artwork_data_url("曲奇烘焙") == graph_cover._load_artwork_data_url("default")
    assert graph_cover._default_artwork_data_url("羽毛球") == graph_cover._load_artwork_data_url("default")
    assert graph_cover._default_artwork_data_url("文学入门") != graph_cover._default_artwork_data_url("曲奇烘焙")


@pytest.mark.parametrize("frontend", [False, True])
def test_saved_legacy_covers_upgrade_on_read(frontend):
    stored = _legacy_cover(frontend)
    assert graph_cover.is_legacy_generated_cover(stored)
    assert graph_cover.resolve_graph_cover(stored, "烘焙") == graph_cover.generate_graph_cover("烘焙")


@pytest.mark.parametrize("stored", [
    "data:image/jpeg;base64,YXJ0d29yaw==",
    graph_cover.generate_template_cover("封面", "paper"),
    graph_cover.generate_template_cover("封面", "science"),
    graph_cover.validate_custom_svg('<svg xmlns="http://www.w3.org/2000/svg"><circle cx="500" cy="120" r="82"/></svg>'),
    "data:image/svg+xml,%3Csvg",
], ids=["image", "paper-template", "science-template", "custom-svg", "invalid-svg"])
def test_saved_custom_template_and_image_covers_are_preserved(stored):
    assert not graph_cover.is_legacy_generated_cover(stored)
    assert graph_cover.resolve_graph_cover(stored, "图谱") == stored


def test_automatic_cover_is_standalone_vector_with_escaped_concept_labels():
    title = "曲奇 & 烘焙\x00"
    concepts = ["材料 < 配比", "温度", "温度"]
    cover = graph_cover.generate_automatic_graph_cover(title, node_labels=concepts)
    root = _root(cover)
    assert root.tag == NS + "svg"
    assert root.get("data-cover-kind") == "automatic"
    assert root.find(NS + "title").text == "曲奇 & 烘焙"
    assert [node.text for node in root.iter(NS + "text")] == ["材料 < 配比", "温度"]
    assert not list(root.iter(NS + "image"))
    assert cover == graph_cover.generate_automatic_graph_cover(title, node_labels=concepts)
    assert cover != graph_cover.generate_automatic_graph_cover(title, node_labels=["整形"])
    assert graph_cover.resolve_graph_cover(cover, title) == cover


def test_unset_cover_and_missing_image_have_usable_offline_fallbacks(monkeypatch):
    monkeypatch.setattr(graph_cover, "_default_artwork_data_url", lambda title: "")
    root = _root(graph_cover.resolve_graph_cover(None, "图谱"))
    assert root.find(NS + "image") is None
    assert root.find(NS + "rect").get("fill") == "url(#glow)"
    assert len(root.findall(".//" + NS + "circle")) >= 6
    assert not graph_cover.is_legacy_generated_cover(graph_cover.default_graph_cover("图谱"))
