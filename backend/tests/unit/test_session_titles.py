from app.services.session_titles import (
    SESSION_TITLE_FALLBACK_MAX_CHARS,
    fallback_session_title,
    is_question_fallback_title,
)


def test_question_fallback_is_single_line_and_fixed_width():
    question = "  如何\n设计一个可扩展的学习图谱，并避免重复节点？还有更多背景  "

    title = fallback_session_title(question)

    assert title == "如何 设计一个可扩展的学习图谱，并避免重复节点？"
    assert len(title) == SESSION_TITLE_FALLBACK_MAX_CHARS
    assert "\n" not in title
    assert is_question_fallback_title(title, question)


def test_empty_question_does_not_create_a_title():
    assert fallback_session_title(" \n\t ") == ""
    assert not is_question_fallback_title("新会话", "")
