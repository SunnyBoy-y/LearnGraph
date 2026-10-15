"""Run with: python backend/scripts/verify_asr_filler.py (no cloud credentials)."""
from __future__ import annotations

import ast
import asyncio
from collections import OrderedDict, deque
import importlib.util
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

VOICE = Path(__file__).resolve().parents[1] / 'app' / 'voice'
spec = importlib.util.spec_from_file_location('filler_intent_under_test', VOICE / 'embedded_turn_intent.py')
intent = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = intent
spec.loader.exec_module(intent)
# Execute actual STT method bodies without importing optional Pipecat/cloud dependencies.
source = (VOICE / 'embedded_dashscope_stt.py').read_text(encoding='utf-8')
tree = ast.parse(source)
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'DashScopeSTTService')
methods = [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in {'_is_backchannel_filler', '_handle_final'}]
ns = dict(is_asr_filler=intent.is_asr_filler, is_backchannel=intent.is_backchannel, logger=Mock(), timeline_mark=Mock(), time=time, time_now_iso8601=lambda: 'test', TranscriptionFrame=lambda *a, **kw: (a, kw))
exec(compile(ast.Module(body=methods, type_ignores=[]), str(VOICE / 'embedded_dashscope_stt.py'), 'exec'), ns)

class FillerTests(unittest.TestCase):
    def service(self, replying=False):
        obj = SimpleNamespace(_suppressed_backchannels=0, _assistant_reply_in_flight=lambda: replying)
        obj._is_backchannel_filler = lambda text: ns['_is_backchannel_filler'](obj, text)
        return obj

    def test_regex_full_sentence(self):
        for text in ['嗯', '嗯。', ' 嗯！\n', '嗯嗯嗯嗯。', '嗯，啊。', '哦……', '噢！', '喔', '呃', '额。', '唔', '嗯哼。', '啊哈！']:
            with self.subTest(text=text):
                self.assertTrue(intent.is_asr_filler(text))
        for text in ['', '。', '  ', '好的', '是的', '对', '嗯，我想问一下', '哦，继续讲', '啊，不对', '嗯停一下', '嗯1', '嗯abc', '余额', '啊Q', '嗯🙂']:
            with self.subTest(text=text):
                self.assertFalse(intent.is_asr_filler(text))

    def test_gate_idle_and_replying(self):
        for replying in (False, True):
            obj = self.service(replying)
            for text in ['嗯。', '嗯嗯嗯嗯', '哦……', '嗯，啊。']:
                self.assertTrue(obj._is_backchannel_filler(text))
            # A growing partial must recover after its initial filler is dropped.
            self.assertFalse(obj._is_backchannel_filler('嗯，我想问一下'))
            self.assertFalse(obj._is_backchannel_filler('停一下'))
            self.assertEqual(obj._is_backchannel_filler('好的'), replying)

    def test_final_paths(self):
        async def check():
            for cloud in (False, True):
                for replying in (False, True):
                    for text, expected in [('嗯。', 0), ('嗯，啊。', 0), ('嗯，我想问一下', 1), ('停一下', 1)]:
                        obj = self.service(replying)
                        obj._seen_final_items = OrderedDict()
                        obj._prune_pending_commits = Mock()
                        obj._segments_padded = 1
                        obj._segments_resolved = 0
                        obj._connection_generation = obj._utterance_generation = 1
                        commit = SimpleNamespace(connection_generation=1, utterance_generation=1, superseded=False, sent_at=time.time(), event_id='commit', trigger='test')
                        obj._pending_commits = deque([] if cloud else [commit])
                        obj._cloud_vad_enabled = cloud
                        obj._user_id = 'test'
                        obj.emit_stt_usage_metrics = AsyncMock()
                        obj.push_frame = AsyncMock()
                        await ns['_handle_final'](obj, {'transcript': text, 'item_id': 'one'})
                        self.assertEqual(obj.push_frame.await_count, expected, (cloud, replying, text))
                        self.assertEqual(obj._segments_resolved, 1)
                        self.assertFalse(obj._pending_commits)
                        obj.emit_stt_usage_metrics.assert_awaited_once()
        asyncio.run(check())

    def test_partial_guard_precedes_publish(self):
        reader = next(n for n in cls.body if isinstance(n, ast.AsyncFunctionDef) and any(isinstance(x, ast.Name) and x.id == 'InterimTranscriptionFrame' for x in ast.walk(n)))
        guards = [n for n in ast.walk(reader) if isinstance(n, ast.If) and '_is_backchannel_filler(display)' in ast.unparse(n.test)]
        self.assertEqual(len(guards), 1)
        self.assertTrue(any(isinstance(n, ast.Name) and n.id == 'InterimTranscriptionFrame' for n in ast.walk(guards[0])))

if __name__ == '__main__':
    unittest.main(verbosity=2)
