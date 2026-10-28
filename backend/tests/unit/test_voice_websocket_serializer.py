import asyncio
import json
import math
import struct

import pytest

pytest.importorskip("pipecat.transports.websocket.fastapi")
pytest.importorskip("aiortc.codecs.opus")

from pipecat.frames.frames import (
    InputAudioRawFrame,
    InputTransportMessageFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
)
from app.voice.websocket_serializer import HEADER, VoiceWebSocketSerializer


def run(awaitable):
    return asyncio.run(awaitable)


def packet(codec=2, sequence=0, audio=b"\0" * 640):
    return HEADER.pack(b"LG", 1, codec, sequence, 0, 0) + audio


def test_pcm_binary_and_rtvi_share_transport():
    serializer = VoiceWebSocketSerializer("pcm")
    audio = run(serializer.deserialize(packet()))
    assert isinstance(audio, InputAudioRawFrame)
    assert (len(audio.audio), audio.sample_rate, audio.num_channels) == (640, 16000, 1)
    message = {"label": "rtvi-ai", "type": "client-ready", "id": "one"}
    control = run(serializer.deserialize(json.dumps(message)))
    assert isinstance(control, InputTransportMessageFrame)
    assert control.message == message
    assert json.loads(run(serializer.serialize(OutputTransportMessageFrame(message=message)))) == message


@pytest.mark.parametrize("data", [
    b"", b"x" * 17000, packet(codec=1), packet(audio=b"\0"),
    json.dumps([]), json.dumps({"type": "untrusted"}),
])
def test_malformed_or_oversized_frames_are_rejected(data):
    with pytest.raises(ValueError):
        run(VoiceWebSocketSerializer("pcm").deserialize(data))


def test_duplicate_and_stale_microphone_frames_are_dropped(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("app.voice.websocket_serializer.time.monotonic", lambda: now[0])
    serializer = VoiceWebSocketSerializer("pcm")
    assert run(serializer.deserialize(packet())) is not None
    assert run(serializer.deserialize(packet())) is None
    now[0] = 11.0
    assert run(serializer.deserialize(packet(sequence=1))) is None
    fresh = HEADER.pack(b"LG", 1, 2, 2, 1000, 0) + b"\0" * 640
    assert run(serializer.deserialize(fresh)) is not None


def test_interruption_changes_playback_generation():
    serializer = VoiceWebSocketSerializer("pcm")
    frame = OutputAudioRawFrame(audio=b"\0" * 960, sample_rate=24000, num_channels=1)
    assert HEADER.unpack_from(run(serializer.serialize(frame)))[-1] == 0
    assert json.loads(run(serializer.serialize(InterruptionFrame()))) == {"type": "voice.clear", "generation": 1}
    assert HEADER.unpack_from(run(serializer.serialize(frame)))[-1] == 1


def test_real_opus_encode_decode_resample_and_interrupt():
    output = VoiceWebSocketSerializer("opus")
    input_ = VoiceWebSocketSerializer("opus")
    packets = []
    for index in range(12):
        samples = [
            int(8000 * math.sin(2 * math.pi * 440 * (index * 480 + i) / 24000))
            for i in range(480)
        ]
        frame = OutputAudioRawFrame(
            audio=struct.pack("<480h", *samples), sample_rate=24000, num_channels=1,
        )
        encoded = run(output.serialize(frame))
        if encoded:
            packets.append(encoded)
    assert len(packets) >= 10
    decoded = [run(input_.deserialize(item)) for item in packets]
    assert all(frame is not None and frame.sample_rate == 16000 and frame.num_channels == 1 for frame in decoded)
    pcm = b"".join(frame.audio for frame in decoded)
    assert len(pcm) > 5000
    assert max(abs(value[0]) for value in struct.iter_unpack("<h", pcm)) > 1000
    run(output.serialize(InterruptionFrame()))
    assert output.generation == 1


def test_opus_must_be_one_20ms_packet():
    with pytest.raises(ValueError):
        run(VoiceWebSocketSerializer("opus").deserialize(packet(codec=1, audio=b"\0\2x")))
