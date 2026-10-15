"""Isolated strategy/control-path regression; no Pipecat/cloud required."""
from __future__ import annotations
import ast
import asyncio
import contextlib
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

VOICE = Path(__file__).resolve().parents[1] / 'app' / 'voice'
spec = importlib.util.spec_from_file_location('noise_intent', VOICE / 'embedded_turn_intent.py')
intent = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = intent
spec.loader.exec_module(intent)

class Frame:
    def __init__(self, text=''):
        self.text = text

class Base:
    def __init__(self, enable_interruptions=True, **kwargs):
        self._enable_interruptions = enable_interruptions
        self.trigger_user_turn_started = AsyncMock()
    def create_task(self, coro):
        return asyncio.create_task(coro)
    async def cancel_task(self, task):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    async def handle_user_turn_started(self):
        pass
    async def cleanup(self):
        pass

ns = dict(asyncio=asyncio, logger=Mock(), BaseUserTurnStartStrategy=Base, _DECISION_WINDOW=0.5, Frame=Frame, ProcessFrameResult=SimpleNamespace(CONTINUE='continue', STOP='stop'))
for name in ['BotStartedSpeakingFrame', 'BotStoppedSpeakingFrame', 'InterimTranscriptionFrame', 'TranscriptionFrame', 'VADUserStartedSpeakingFrame', 'VADUserStoppedSpeakingFrame']:
    ns[name] = type(name, (Frame,), {})
for name in ['DEFAULT_TURN_INTENT_CLASSIFIER', 'TurnIntent', 'is_asr_filler', 'normalize_transcript']:
    ns[name] = getattr(intent, name)
tree = ast.parse((VOICE / 'embedded_turn_strategy.py').read_text(encoding='utf-8'))
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AdaptiveUserTurnStartStrategy')
module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), cls], type_ignores=[])
exec(compile(ast.fix_missing_locations(module), str(VOICE / 'embedded_turn_strategy.py'), 'exec'), ns)
Strategy = ns['AdaptiveUserTurnStartStrategy']

class NoiseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.interrupt = AsyncMock()
        self.s = Strategy(decision_window=0, interrupt_callback=self.interrupt)
    async def asyncTearDown(self):
        await self.s.cleanup()
    async def send(self, name, text=''):
        await self.s.process_frame(ns[name](text))
    def untouched(self):
        self.interrupt.assert_not_awaited()
        self.s.trigger_user_turn_started.assert_not_awaited()
    async def test_noise_during_playback(self):
        await self.send('BotStartedSpeakingFrame')
        await self.send('VADUserStartedSpeakingFrame')
        await self.send('VADUserStoppedSpeakingFrame')
        self.untouched()
    async def test_noise_before_audio(self):
        await self.send('VADUserStartedSpeakingFrame')
        await self.send('VADUserStoppedSpeakingFrame')
        self.untouched()
    async def test_playback_gap_with_empty_candidate(self):
        await self.send('BotStartedSpeakingFrame')
        await self.send('VADUserStartedSpeakingFrame')
        await self.send('BotStoppedSpeakingFrame')
        self.untouched()
    async def test_timeout_and_late_final(self):
        await self.send('BotStartedSpeakingFrame')
        await self.send('VADUserStartedSpeakingFrame')
        await self.s._decision_task
        self.untouched()
        await self.send('TranscriptionFrame', '我想问一个问题')
        self.interrupt.assert_awaited_once()
        self.s.trigger_user_turn_started.assert_awaited_once_with(enable_interruptions=True)
    async def test_retracted_interim(self):
        await self.send('BotStartedSpeakingFrame')
        await self.send('VADUserStartedSpeakingFrame')
        await self.send('InterimTranscriptionFrame', '我想问一下')
        await self.s._decision_task
        await self.send('TranscriptionFrame', '嗯。')
        self.untouched()
    async def test_empty_punctuation_and_fillers(self):
        for speaking in (False, True):
            self.s._bot_speaking = speaking
            for text in ['', ' ', '……', '嗯。', '嗯，啊。']:
                await self.send('TranscriptionFrame', text)
        self.untouched()
    async def test_real_final_without_vad(self):
        await self.send('TranscriptionFrame', '请解释一下')
        self.s.trigger_user_turn_started.assert_awaited_once()
    async def test_interim_stop_waits_for_final(self):
        await self.send('BotStartedSpeakingFrame')
        await self.send('InterimTranscriptionFrame', '停一下')
        self.untouched()
        await self.send('TranscriptionFrame', '停一下')
        self.interrupt.assert_awaited_once()
    async def test_button_bypasses_asr(self):
        tree = ast.parse((VOICE / 'embedded_bot.py').read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'handle_learngraph_client_message')
        scope = dict(logger=Mock(), VOICE_TYPED_TURN_MESSAGE='learngraph-typed-turn', VOICE_PLAYBACK_MESSAGE='learngraph-playback', VOICE_INTERRUPT_MESSAGE='learngraph-interrupt')
        module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), fn], type_ignores=[])
        exec(compile(ast.fix_missing_locations(module), 'button_handler', 'exec'), scope)
        gate, interrupts = Mock(), Mock()
        interrupts.accept.return_value = True
        stop = AsyncMock()
        await self.send('VADUserStartedSpeakingFrame')
        await scope['handle_learngraph_client_message'](message=SimpleNamespace(type='learngraph-interrupt', data=None), journal=None, generation_gate=gate, interrupt_bot=stop, interrupts=interrupts)
        await self.send('VADUserStoppedSpeakingFrame')
        stop.assert_awaited_once()
        gate.invalidate.assert_called_once()
        self.untouched()

if __name__ == '__main__':
    unittest.main(verbosity=2)
