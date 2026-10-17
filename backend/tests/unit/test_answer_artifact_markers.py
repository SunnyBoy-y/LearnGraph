from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.chat import ChatService


def test_bare_card_marker_is_removed_without_hiding_surrounding_answer():
    service = ChatService.__new__(ChatService)
    service._find_referenced_part = Mock()
    segments, markers = service._resolve_answer_markers(
        "已更新。\n\n[[artifact:card_inst_6f265bd7c31c]]\n\n这次改动的内容：", "version"
    )
    assert "".join(segments) == "已更新。\n\n\n\n这次改动的内容："
    assert markers == [{"kind": "drop"}]
    service._find_referenced_part.assert_not_called()


@pytest.mark.parametrize("kind", ["file_id", "bundle_id", "block_id"])
def test_typed_marker_still_places_the_referenced_artifact(kind):
    service = ChatService.__new__(ChatService)
    part = SimpleNamespace(id="artifact-part")
    service._find_referenced_part = Mock(return_value=part)
    segments, markers = service._resolve_answer_markers(
        f"前文[[artifact:{kind}:demo|layout=side]]后文", "version"
    )
    assert segments == ["前文", "后文"]
    assert markers == [{"kind": "artifact", "part": part, "layout": "side"}]
    service._find_referenced_part.assert_called_once_with(kind, "demo", "version")
