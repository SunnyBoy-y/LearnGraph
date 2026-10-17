"""Keep ASR control signals responsive while continuous PCM is arriving."""
from dataclasses import dataclass

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import InterimTranscriptionFrame, SystemFrame, TranscriptionFrame


@dataclass
class RealtimeTranscriptionFrame(TranscriptionFrame, SystemFrame):
    """Same STT contract, scheduled alongside input audio rather than behind it."""


@dataclass
class RealtimeInterimTranscriptionFrame(InterimTranscriptionFrame, SystemFrame):
    """Interims retain arrival order with finals and incoming PCM."""


class BatchedSileroVADAnalyzer(SileroVADAnalyzer):
    """Amortize executor handoffs over 80 ms, preserving every PCM sample.

    Silero still evaluates its original 32 ms windows and thresholds. Only the
    number of thread submissions changes; Smart Turn receives the original PCM.
    """
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._pending_pcm = bytearray()

    async def analyze_audio(self, buffer):
        self._pending_pcm.extend(buffer)
        if len(self._pending_pcm) < int(self.sample_rate * self.num_channels * 2 * 0.08):
            return self._vad_state
        batch = bytes(self._pending_pcm)
        self._pending_pcm.clear()
        return await super().analyze_audio(batch)

    def _run_analyzer(self, buffer):
        # The base analyzer applies STARTING/SPEAKING/STOPPING transitions
        # after its loop. Passing several model windows to that loop can
        # erase an onset when a quiet window occurs later in the same batch.
        # Keep one executor handoff, but advance the state per model window.
        for offset in range(0,len(buffer),self._vad_frames_num_bytes):
            super()._run_analyzer(buffer[offset:offset+self._vad_frames_num_bytes])
        return self._vad_state
