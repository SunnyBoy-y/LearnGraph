"""Versioned binary audio and unchanged RTVI messages over WebSocket.

The 16-byte network-order header is magic, version, codec, sequence,
capture timestamp in milliseconds, and playback generation. Opus payloads
are length-prefixed raw packets, not WebM/Ogg files. PCM is little-endian s16.
"""

from __future__ import annotations

import json
import struct
import time
from fractions import Fraction

from pipecat.frames.frames import (
    InputAudioRawFrame,
    InputTransportMessageFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    OutputTransportMessageUrgentFrame,
)
from pipecat.serializers.base_serializer import FrameSerializer

HEADER = struct.Struct("!2sBBIII")
CODECS = {"opus": 1, "pcm": 2}
MAX_MESSAGE_BYTES = 16_384


class VoiceWebSocketSerializer(FrameSerializer):
    def __init__(self, codec: str):
        super().__init__(FrameSerializer.InputParams(ignore_rtvi_messages=False))
        if codec not in CODECS:
            raise ValueError("Unsupported voice codec")
        self.codec = codec
        self.generation = 0
        self.sequence = 0
        self.input_sequence = -1
        self.input_clock_offset: float | None = None
        self.stale_frames = 0
        self.output_samples = 0
        if codec == "opus":
            from aiortc.codecs.opus import OpusDecoder, OpusEncoder
            from av import AudioResampler

            self.decoder = OpusDecoder()
            self.encoder = OpusEncoder()
            self.encoder.codec.bit_rate = 32_000
            self.encoder.codec.layout = "mono"
            self.encoder.resampler = AudioResampler(
                format="s16", layout="mono", rate=48_000, frame_size=960
            )
            self.input_resampler = AudioResampler(format="s16", layout="mono", rate=16_000)

    async def serialize(self, frame):
        if isinstance(frame, InterruptionFrame):
            self.generation += 1
            if self.codec == "opus":
                # Do not let a partial old utterance enter the next generation.
                from aiortc.codecs.opus import OpusEncoder
                from av import AudioResampler

                self.encoder = OpusEncoder()
                self.encoder.codec.bit_rate = 32_000
                self.encoder.codec.layout = "mono"
                self.encoder.resampler = AudioResampler(
                    format="s16", layout="mono", rate=48_000, frame_size=960
                )
                self.output_samples = 0
            return json.dumps({"type": "voice.clear", "generation": self.generation})
        if isinstance(frame, (OutputTransportMessageFrame, OutputTransportMessageUrgentFrame)):
            return json.dumps(frame.message)
        if not isinstance(frame, OutputAudioRawFrame):
            return None
        if frame.num_channels != 1 or frame.sample_rate != 24_000:
            raise ValueError("Voice WSS output must be mono 24 kHz PCM")
        payload = frame.audio
        if self.codec == "opus":
            from av import AudioFrame

            audio = AudioFrame(format="s16", layout="mono", samples=len(payload) // 2)
            audio.planes[0].update(payload)
            audio.sample_rate = frame.sample_rate
            audio.pts = self.output_samples
            audio.time_base = Fraction(1, frame.sample_rate)
            self.output_samples += audio.samples
            packets, _ = self.encoder.encode(audio)
            if not packets:
                return None
            payload = b"".join(struct.pack("!H", len(packet)) + packet for packet in packets)
        header = HEADER.pack(
            b"LG", 1, CODECS[self.codec], self.sequence & 0xFFFFFFFF,
            int(time.monotonic() * 1000) & 0xFFFFFFFF, self.generation,
        )
        self.sequence += 1
        return header + payload

    async def deserialize(self, data):
        if len(data) > MAX_MESSAGE_BYTES:
            raise ValueError("Voice message exceeds limit")
        if isinstance(data, str):
            message = json.loads(data)
            if not isinstance(message, dict):
                raise ValueError("Expected a voice control object")
            if message.get("label") != "rtvi-ai":
                raise ValueError("Expected an RTVI message")
            return InputTransportMessageFrame(message=message)
        if len(data) <= HEADER.size:
            raise ValueError("Missing voice audio payload")
        magic, version, codec, sequence, timestamp, _ = HEADER.unpack_from(data)
        if magic != b"LG" or version != 1 or codec != CODECS[self.codec]:
            raise ValueError("Invalid voice audio header")
        if sequence <= self.input_sequence:
            return None
        self.input_sequence = sequence
        # TCP can deliver stale audio after a stall. Keep only live microphone
        # frames; compare relative clocks, never trust the client's wall clock.
        offset = (int(time.monotonic() * 1000) - timestamp) & 0xFFFFFFFF
        if self.input_clock_offset is None:
            self.input_clock_offset = offset
        self.input_clock_offset = min(self.input_clock_offset, offset)
        if offset - self.input_clock_offset > 400:
            self.stale_frames += 1
            if self.stale_frames >= 25:
                raise ValueError("Voice stream is no longer live; reconnect required")
            return None
        self.stale_frames = 0
        payload = data[HEADER.size:]
        if self.codec == "pcm":
            if len(payload) != 640:
                raise ValueError("Expected 20 ms mono 16 kHz PCM")
            return InputAudioRawFrame(audio=payload, sample_rate=16_000, num_channels=1)
        from aiortc.jitterbuffer import JitterFrame

        if len(payload) < 3:
            raise ValueError("Missing Opus packet")
        size = struct.unpack_from("!H", payload)[0]
        if size > 1275 or size != len(payload) - 2:
            raise ValueError("Expected one bounded Opus packet")
        frames = self.decoder.decode(
            JitterFrame(data=payload[2:], timestamp=sequence * 960)
        )
        if sum(frame.samples for frame in frames) != 960:
            raise ValueError("Expected a 20 ms Opus frame")
        pcm = bytearray()
        for frame in frames:
            for converted in self.input_resampler.resample(frame):
                pcm.extend(bytes(converted.planes[0])[:converted.samples * 2])
        return InputAudioRawFrame(audio=bytes(pcm), sample_rate=16_000, num_channels=1)
