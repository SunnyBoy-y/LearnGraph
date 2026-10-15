"""Regression tests for bounded results, resumable reads, and atomic JSON edits."""
import json
import unittest

from app.core.errors import AppError
from app.domain.schemas.sandbox import SandboxAgentFileReadRequest
from app.services.harness_contracts import bounded_json, text_page, apply_json_patch


class HarnessContractsTests(unittest.TestCase):
    def test_bounded_result_counts_final_utf8_bytes(self):
        value = {"text": ('中文\"\\\n' * 10000)}
        encoded, truncated = bounded_json(value, 1024)
        self.assertTrue(truncated)
        self.assertLessEqual(len(encoded.encode('utf-8')), 1024)
        self.assertTrue(json.loads(encoded)['truncated'])

    def test_small_result_is_preserved(self):
        value = {"ok": True, "text": "中文"}
        encoded, truncated = bounded_json(value, 1024)
        self.assertFalse(truncated)
        self.assertEqual(json.loads(encoded), value)

    def test_cursor_reassembles_unicode_long_lines(self):
        original = '第一行\n' + '中abc' * 30 + '\n最后一行'
        cursor = None
        pieces = []
        for _ in range(100):
            page = text_page(original, key='file:owner:chat:a', max_chars=7, cursor=cursor)
            pieces.append(page['content'])
            cursor = page['next_cursor']
            if cursor is None:
                break
        self.assertIsNone(cursor)
        self.assertEqual(''.join(pieces), original)

    def test_cursor_is_bound_to_content_and_identity(self):
        page = text_page('abcdefghijkl', key='owner:a', max_chars=3)
        for text, key in [('changed', 'owner:a'), ('abcdefghijkl', 'owner:b')]:
            with self.assertRaises(AppError) as caught:
                text_page(text, key=key, max_chars=3, cursor=page['next_cursor'])
            self.assertEqual(caught.exception.status_code, 409)

    def test_selected_lines_resume_without_loss(self):
        original = 'one\ntwo\nthree\nfour\n'
        first = text_page(original, key='x', max_chars=3, start_line=2, end_line=3)
        second = text_page(original, key='x', max_chars=100, cursor=first['next_cursor'])
        self.assertEqual(first['content'] + second['content'], 'two\nthree\n')

    def test_empty_file(self):
        page = text_page('', key='empty', max_chars=10)
        self.assertEqual(page['content'], '')
        self.assertIsNone(page['next_cursor'])

    def test_json_patch_is_atomic_on_failure(self):
        original = {'a': [1, 2], 'b': True}
        with self.assertRaises(AppError):
            apply_json_patch(original, [{'op': 'replace', 'path': '/a/0', 'value': 8}, {'op': 'test', 'path': '/b', 'value': 1}])
        self.assertEqual(original, {'a': [1, 2], 'b': True})

    def test_json_pointer_and_array_operations(self):
        original = {'a/b': {'~key': [1, 2]}}
        result = apply_json_patch(original, [
            {'op': 'add', 'path': '/a~1b/~0key/-', 'value': 3},
            {'op': 'replace', 'path': '/a~1b/~0key/0', 'value': 9},
            {'op': 'remove', 'path': '/a~1b/~0key/1'},
        ])
        self.assertEqual(result, {'a/b': {'~key': [9, 3]}})
        self.assertEqual(original, {'a/b': {'~key': [1, 2]}})

    def test_paths_only_request(self):
        request = SandboxAgentFileReadRequest(chat_session_id='chat', paths=['a.txt', 'b.txt'])
        self.assertIsNone(request.path)
        self.assertEqual(request.max_chars, 16384)

    def test_invalid_read_selections(self):
        cases = [{}, {'path': 'a', 'paths': ['a']}, {'paths': []}, {'paths': ['a', 'a']}, {'paths': [' ']}, {'path': 'a', 'start_line': 3, 'end_line': 2}, {'paths': ['a'], 'cursor': 'x'}]
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ValueError):
                SandboxAgentFileReadRequest(chat_session_id='chat', **case)


if __name__ == '__main__':
    unittest.main()
