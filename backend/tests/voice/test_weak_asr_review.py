import asyncio
import base64
import io
import json
import struct
import wave

import httpx

from app.voice.embedded_asr_review import TurnRecording, WholeTurnASRReviewer, review_url, canonical_zero_padding
from app.voice.embedded_dashscope_stt import DashScopeSTTService


def test_weak_route_preserves_every_sample_and_never_uses_text_hints():
    async def run():
        requests = []
        weak = struct.pack('<h', 40)*320 + b'\1\0'*320
        normal = struct.pack('<h', 4000)*320
        async def respond(request):
            body = json.loads(request.content)
            flash = request.url.path.endswith('/generation')
            messages = body['input']['messages'] if flash else body['messages']
            assert len(messages) == 1 and messages[0]['role'] == 'user'
            assert len(messages[0]['content']) == 1
            content = messages[0]['content'][0]
            assert content['type'] == 'input_audio'
            with wave.open(io.BytesIO(base64.b64decode(content['input_audio']['data'].split(',',1)[1])), 'rb') as wav:
                pcm = wav.readframes(wav.getnframes())
                assert wav.getframerate() == 16000 and wav.getnchannels() == 1
            requests.append((flash, pcm))
            if flash:
                assert body['parameters'] == {'format':'wav','sample_rate':'16000','language_hints':['zh']}
                return httpx.Response(200, json={'output':{'text':'弱句复核'}})
            return httpx.Response(200, json={'choices':[{'message':{'content':'原模型复核'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            reviewer = WholeTurnASRReviewer(api_key='test', url='https://example.test/chat',
                weak_model='qwen-audio-3.1-asr-flash', weak_url='https://example.test/generation', client=client)
            for seq, pcm in enumerate([weak, weak+b'\0'*32000, normal, b'\0'*640],1):
                result = await reviewer.submit(TurnRecording(seq,pcm,len(pcm)))
                assert result['sequence'] == seq
            assert requests == [(True,weak),(True,weak+b'\0'*32000),(False,normal),(False,b'\0'*640)]
            assert [r['model'] for r in reviewer.results] == ['qwen-audio-3.1-asr-flash']*2+['qwen3-asr-flash']*2
            await reviewer.close()
    asyncio.run(run())


def test_weak_route_is_opt_in_and_preserves_region(monkeypatch):
    monkeypatch.setenv('DASHSCOPE_ASR_REVIEW','1')
    monkeypatch.delenv('DASHSCOPE_ASR_REVIEW_WEAK_MODEL',raising=False)
    stt=DashScopeSTTService(api_key='test')
    assert stt._turn_reviewer.weak_model is None
    monkeypatch.setenv('DASHSCOPE_ASR_REVIEW_WEAK_MODEL','qwen-audio-3.1-asr-flash')
    stt=DashScopeSTTService(api_key='test')
    assert stt._turn_reviewer.weak_model == 'qwen-audio-3.1-asr-flash'
    assert stt._turn_reviewer._weak_url == review_url(stt._settings.ws_url,api_format='dashscope_flash')
    assert review_url('wss://abc.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime',api_format='dashscope_flash') == 'https://abc.cn-beijing.maas.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation'


def test_weak_route_bad_response_is_bounded_failure_not_a_second_request():
    async def run():
        calls = []
        async def respond(request):
            calls.append(request)
            return httpx.Response(200,json={'output':{'text':42}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            reviewer = WholeTurnASRReviewer(api_key='secret',url='https://example.test/chat',
                weak_model='qwen-audio-3.1-asr-flash',weak_url='https://example.test/generation',client=client)
            result=await reviewer.submit(TurnRecording(1,b'\1\0'*320,640))
            assert result['status']=='error' and len(calls)==1
            assert 'secret' not in json.dumps(result)
            await reviewer.close()
    asyncio.run(run())


def test_canonical_padding_preserves_pcm16_boundaries_and_internal_weak_gaps():
    samples = [256, 1, 0, 0, -1, -256, 2]
    voice = struct.pack('<'+'h'*len(samples),*samples)
    expected = b'\0'*7680+voice+b'\0'*12800
    assert canonical_zero_padding(b'\0'*320+voice+b'\0'*96000)==expected
    assert canonical_zero_padding(expected)==expected
    assert canonical_zero_padding(voice)==expected


def test_weak_canonicalization_matches_full_turn_without_touching_normal_speech():
    reviewer=WholeTurnASRReviewer(api_key='test',url='https://example.test',canonical_weak_audio=True)
    weak=b'\0'*12800+struct.pack('<h',40)*320+b'\1\0'*320
    normal=struct.pack('<h',4000)*320
    def unpack(pcm):
        body,*_,metadata=reviewer._prepare_request(TurnRecording(1,pcm,len(pcm)))
        value=json.loads(body)
        encoded=value['messages'][0]['content'][0]['input_audio']['data'].split(',',1)[1]
        with wave.open(io.BytesIO(base64.b64decode(encoded)),'rb') as wav:
            return wav.readframes(wav.getnframes()),metadata
    a,ameta=unpack(weak)
    b,bmeta=unpack(weak+b'\0'*96000)
    assert a==b and ameta['requestPcmSha256']==bmeta['requestPcmSha256']
    assert ameta['canonicalWeakAudio'] is True
    assert unpack(normal)[0]==normal and unpack(normal)[1]['canonicalWeakAudio'] is False


def test_skipped_canonical_review_cannot_certify_provisional_text_as_normal():
    async def run():
        reviewer=WholeTurnASRReviewer(api_key='test',url='https://example.test',canonical_weak_audio=True)
        reviewer._closed=True
        future=reviewer.submit(TurnRecording(1,b'\1\0'*320,640))
        assert (await future)['status']=='closed'
        assert await future.review_quality == {'status':'unavailable'}
        await reviewer.close()
    asyncio.run(run())
