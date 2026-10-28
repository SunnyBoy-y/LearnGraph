import pytest
from app.voice.tts_alignment import SubtitleAudioJoin


def subtitle(text, start, end):
    return {"text": text, "words": [{"startTime": start, "endTime": end}]}


def test_late_subtitles_split_audio_exactly_without_losing_samples():
    join = SubtitleAudioJoin(1000)
    original = bytes(range(250)) * 32
    join.add_audio(original)
    assert list(join.ready()) == []
    join.add_subtitle(subtitle("第一句", .2, 1.5))
    join.add_subtitle(subtitle("第二句", 2, 3.8))
    spans = list(join.ready())
    assert [span.text for span in spans] == ["第一句", "第二句"]
    assert [len(span.prefix) for span in spans] == [400, 1000]
    assert b"".join(span.prefix + span.audio for span in spans) + join.finish() == original


def test_subtitle_before_audio_waits_until_its_pcm_is_complete():
    join = SubtitleAudioJoin(1000)
    join.add_subtitle(subtitle("一句话", 0, 1))
    join.add_audio(b"\0" * 1000)
    assert list(join.ready()) == []
    join.add_audio(b"\0" * 1000)
    assert len(list(join.ready())) == 1
    assert join.finish() == b""


def test_missing_subtitles_fail_instead_of_inventing_caption_timing():
    join = SubtitleAudioJoin(1000)
    join.add_audio(b"\0" * 2000)
    with pytest.raises(ValueError, match="alignment"):
        join.finish()


def test_missing_subtitles_have_bounded_memory():
    join = SubtitleAudioJoin(1000, max_pending_seconds=1)
    with pytest.raises(ValueError, match="buffer limit"):
        join.add_audio(b"\0" * 2002)


@pytest.mark.parametrize("start,end", [(0, 0), (-1, 1), (2, 1), (float("nan"), 1)])
def test_bad_timestamps_are_protocol_errors(start, end):
    with pytest.raises(ValueError):
        SubtitleAudioJoin(1000).add_subtitle(subtitle("坏时间戳", start, end))
