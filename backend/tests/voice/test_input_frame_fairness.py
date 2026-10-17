import asyncio
import struct
from unittest.mock import AsyncMock, patch

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADState
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import InputAudioRawFrame, InterimTranscriptionFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorQueue
from app.voice.embedded_input_frames import (
    BatchedSileroVADAnalyzer, RealtimeTranscriptionFrame, RealtimeInterimTranscriptionFrame,
)


def test_asr_final_is_not_starved_by_later_continuous_audio():
    async def exercise():
        queue=FrameProcessorQueue()
        interim=RealtimeInterimTranscriptionFrame('甲组','user','now')
        final=RealtimeTranscriptionFrame('甲组十四','user','now',finalized=True)
        assert isinstance(interim,InterimTranscriptionFrame)
        assert isinstance(final,TranscriptionFrame) and final.finalized
        for frame in [interim,final,*[InputAudioRawFrame(b'\0'*640,16000,1) for _ in range(1200)]]:
            await queue.put((frame,FrameDirection.DOWNSTREAM,None))
        assert (await queue.get())[0] is interim
        assert (await queue.get())[0] is final
    asyncio.run(exercise())


def test_vad_batching_preserves_pcm_and_reduces_thread_handoffs():
    async def exercise():
        analyzer=BatchedSileroVADAnalyzer(sample_rate=16000)
        analyzer.set_sample_rate(16000)
        chunks=[bytes([value])*640 for value in range(8)]
        with patch.object(SileroVADAnalyzer,'analyze_audio',new=AsyncMock(return_value=VADState.QUIET)) as analyze:
            for chunk in chunks:
                await analyzer.analyze_audio(chunk)
            assert analyze.await_count==2
            assert b''.join(call.args[0] for call in analyze.await_args_list)==b''.join(chunks)
        await analyzer.cleanup()
    asyncio.run(exercise())


def test_batched_vad_keeps_the_same_window_state_transitions_as_unbatched_vad():
    async def exercise():
        params=VADParams(start_secs=.064,stop_secs=.064,min_volume=0)
        reference=SileroVADAnalyzer(sample_rate=16000,params=params)
        batched=BatchedSileroVADAnalyzer(sample_rate=16000,params=params)
        for analyzer in [reference,batched]:
            analyzer.set_sample_rate(16000)
            analyzer.voice_confidence=lambda pcm: 1.0 if struct.unpack_from('<h',pcm)[0] else 0.0
            analyzer._get_smoothed_volume=lambda pcm: 1.0
        # Two voiced windows should enter SPEAKING before the following quiet
        # window enters STOPPING. Processing transitions only after the batch
        # instead loses the entire speech episode.
        voiced=struct.pack('<h',3000)*512
        quiet=b'\0'*1024
        expected=VADState.QUIET
        for chunk in [voiced,voiced,quiet]:
            expected=reference._run_analyzer(chunk)
        actual=batched._run_analyzer(voiced+voiced+quiet)
        assert expected==VADState.STOPPING
        assert actual==expected
        await reference.cleanup();await batched.cleanup()
    asyncio.run(exercise())
