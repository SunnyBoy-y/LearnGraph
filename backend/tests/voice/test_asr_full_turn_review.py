import asyncio
import base64
import io
import json
import struct
import wave
from unittest.mock import AsyncMock

import httpx
from pipecat.frames.frames import BotStartedSpeakingFrame, TranscriptionFrame, UserStoppedSpeakingFrame, VADUserStartedSpeakingFrame, VADUserStoppedSpeakingFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.stt_service import STTService

from app.voice.embedded_asr_review import TurnAudioBuffer, TurnRecording, WholeTurnASRReviewer, review_url
from app.voice.embedded_dashscope_stt import DashScopeSTTService
from app.voice.embedded_turn_strategy import AdaptiveUserTurnStartStrategy


def test_buffer_preserves_gaps_prefix_and_boundaries_and_rejects_overflow():
    buffer = TurnAudioBuffer(preroll_bytes=4, max_bytes=640)
    buffer.append(b'old1234')
    buffer.begin()
    buffer.append(b'\0\0weak')
    buffer.begin()  # resumed VAD in same Smart Turn must not reset PCM
    buffer.append(b'last')
    assert buffer.finish().pcm == b'1234\0\0weaklast'
    assert buffer.finish() is None
    buffer.begin()
    buffer.append(b'x' * 642)
    assert not buffer.preserve
    turn = buffer.finish()
    assert turn.overflow and turn.pcm == b'' and turn.total_bytes == 642
    buffer.begin()
    buffer.append(b'next')
    assert buffer.finish().pcm == b'next'


def test_actual_stt_keeps_quiet_midturn_and_resets_only_at_smart_turn(monkeypatch):
    async def exercise():
        monkeypatch.setenv('DASHSCOPE_ASR_FULL_TURN_AUDIO', '1')
        service = DashScopeSTTService(api_key='test')
        service._note_vad_missing = AsyncMock()
        service._finish_segment = AsyncMock()
        service._upstream_alive = lambda: True
        messages = []
        service._ws = type('Socket', (), {'send': AsyncMock(side_effect=lambda v: messages.append(json.loads(v)))})()
        # Test the subclass's real lifecycle hooks, without starting transport.
        monkeypatch.setattr(STTService, 'process_frame', AsyncMock())
        voiced = struct.pack('<h', 2000) * 320
        weak = struct.pack('<h', 40) * 320
        for audio in [voiced]*4 + [weak]*40 + [voiced]*4:
            async for _ in service.run_stt(audio): pass
        await service.process_frame(VADUserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        async for _ in service.run_stt(weak): pass
        actual = b''.join(base64.b64decode(x['audio']) for x in messages)
        assert actual == voiced*4 + weak*40 + voiced*4 + weak
        service._preroll.extend(b'previous turn tail')
        await service.process_frame(UserStoppedSpeakingFrame(), FrameDirection.UPSTREAM)
        assert not service._preroll, 'Old turn tail must not be replayed into the next turn'
        size = len(messages)
        for _ in range(40):
            async for _ in service.run_stt(weak): pass
        # Full-turn gate must not leak the next idle period upstream.
        assert len(messages) == size
    asyncio.run(exercise())


def test_review_does_not_block_input_or_emit_control_frames(monkeypatch):
    async def exercise():
        monkeypatch.setenv('DASHSCOPE_ASR_FULL_TURN_AUDIO', '1')
        entered, release = asyncio.Event(), asyncio.Event()
        async def respond(request):
            body = json.loads(request.content)
            assert body['messages'][0]['role'] == 'user'
            assert len(body['messages']) == 1  # no requested-command hint
            encoded = body['messages'][0]['content'][0]['input_audio']['data'].split(',',1)[1]
            with wave.open(io.BytesIO(base64.b64decode(encoded)), 'rb') as wav:
                assert wav.getframerate() == 16000
                assert wav.readframes(wav.getnframes()) == b'\0\0'*320
            entered.set()
            await release.wait()
            return httpx.Response(200, json={'choices':[{'message':{'content':'复核文本'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            reviewer = WholeTurnASRReviewer(api_key='test', url='https://example.test/asr', client=client)
            service = DashScopeSTTService(api_key='test', turn_reviewer=reviewer)
            service._turn_audio.begin()
            service._turn_audio.append(b'\0\0'*320)
            forwarded = []
            async def parent(owner, frame, direction):forwarded.append(frame)
            monkeypatch.setattr(STTService, 'process_frame', parent)
            service.push_frame = AsyncMock()
            await asyncio.wait_for(service.process_frame(UserStoppedSpeakingFrame(), FrameDirection.UPSTREAM), .1)
            await entered.wait()
            # A later onset/control frame proceeds while the HTTP reply is held.
            service._upstream_alive = lambda: True
            await asyncio.wait_for(service.process_frame(VADUserStartedSpeakingFrame(), FrameDirection.UPSTREAM), .1)
            assert len(forwarded) == 2 and not reviewer.results
            interrupt = AsyncMock()
            start = AdaptiveUserTurnStartStrategy(interrupt_callback=interrupt)
            start.trigger_user_turn_started = AsyncMock()
            await start.process_frame(BotStartedSpeakingFrame())
            await asyncio.wait_for(start.process_frame(TranscriptionFrame('停一下', 'u', 'now', finalized=True)), .1)
            interrupt.assert_awaited_once()
            assert not reviewer.results, 'fast interruption must run before review finishes'
            release.set()
            await reviewer.drain()
            assert reviewer.results[0]['text'] == '复核文本'
            service.push_frame.assert_not_awaited()
            await reviewer.close()
    asyncio.run(exercise())


def test_review_timeout_capacity_failure_and_cleanup_are_bounded():
    async def exercise():
        async def slow(request):
            await asyncio.sleep(10)
        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
            reviewer = WholeTurnASRReviewer(api_key='secret', url='https://example.test/asr', client=client, max_pending=1, timeout=.05)
            reviewer.submit(TurnRecording(1, b'\0\0'*320, 640))
            reviewer.submit(TurnRecording(2, b'\0\0'*320, 640))
            await reviewer.drain()
            assert {r['status'] for r in reviewer.results} == {'capacity','timeout'}
            reviewer.submit(TurnRecording(3, b'', 640, True))
            assert reviewer.results[-1]['status'] == 'overflow'
            reviewer.submit(TurnRecording(4, b'\0\0'*320, 640))
            await reviewer.close()
            assert not reviewer._tasks
            reviewer.submit(TurnRecording(5, b'\0\0', 2))
            assert all(r['sequence'] != 5 for r in reviewer.results)
    asyncio.run(exercise())


def test_review_url_preserves_provider_region_and_workspace():
    assert review_url('wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime').startswith('https://dashscope-intl.aliyuncs.com/')
    assert review_url('wss://abc.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime').startswith('https://abc.cn-beijing.maas.aliyuncs.com/')


def test_review_http_failure_is_sanitized_and_does_not_raise():
    async def exercise():
        async def failure(request):return httpx.Response(401, text='secret echoed by upstream')
        async with httpx.AsyncClient(transport=httpx.MockTransport(failure)) as client:
            reviewer = WholeTurnASRReviewer(api_key='secret', url='https://example.test/asr', client=client)
            reviewer.submit(TurnRecording(1, b'\0\0'*320, 640))
            await reviewer.drain()
            assert reviewer.results[0]['httpStatus'] == 401
            assert 'secret' not in json.dumps(list(reviewer.results))
            await reviewer.close()
    asyncio.run(exercise())


def test_features_are_off_by_default(monkeypatch):
    monkeypatch.delenv('DASHSCOPE_ASR_FULL_TURN_AUDIO', raising=False)
    monkeypatch.delenv('DASHSCOPE_ASR_REVIEW', raising=False)
    service = DashScopeSTTService(api_key='test')
    assert service._turn_audio is None and service._turn_reviewer is None


def test_local_review_flag_wires_the_sidecar_and_complete_turn_buffer(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_ASR_REVIEW', '1')
    monkeypatch.setenv('DASHSCOPE_ASR_REVIEW_URL', 'https://example.test/asr')
    service = DashScopeSTTService(api_key='test')
    assert service._turn_audio is not None
    assert service._turn_reviewer is not None
    assert service._turn_reviewer._url == 'https://example.test/asr'


def test_out_of_order_reviews_keep_their_own_turn_identity():
    async def exercise():
        first_entered, release = asyncio.Event(), asyncio.Event()
        async def respond(request):
            body = json.loads(request.content)
            encoded = body['messages'][0]['content'][0]['input_audio']['data'].split(',',1)[1]
            with wave.open(io.BytesIO(base64.b64decode(encoded)), 'rb') as wav:
                sample = wav.readframes(1)
            if sample == b'\1\0':
                first_entered.set()
                await release.wait()
            return httpx.Response(200, json={'choices':[{'message':{'content':sample.hex()}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            reviewer = WholeTurnASRReviewer(api_key='test', url='https://example.test/asr', client=client)
            reviewer.submit(TurnRecording(1, b'\1\0'*320, 640))
            await first_entered.wait()
            reviewer.submit(TurnRecording(2, b'\2\0'*320, 640))
            for _ in range(100):
                if reviewer.results:break
                await asyncio.sleep(.001)
            assert reviewer.results[0]['sequence'] == 2
            release.set()
            await reviewer.drain()
            assert {r['sequence']:r['text'] for r in reviewer.results} == {1:'0100',2:'0200'}
            await reviewer.close()
    asyncio.run(exercise())
