"""Caller-supplied task files must not replace source or fake fresh outputs."""
from pathlib import Path
import tempfile
import unittest

from agent_service import stage_files, validate_files, task_file_evidence, evaluate_checks, file_stamp, file_digest


class TaskInputTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='agent-inputs-')
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_batch_writes_utf8_and_records_changed_inputs(self):
        files = [{'path': 'task/main.py', 'content': 'print("中文")\n'},
                 {'path': 'task/data.txt', 'content': 'initial'}]
        stage_files(self.root, files)
        self.assertEqual((self.root / 'task/main.py').read_text(encoding='utf-8'), files[0]['content'])
        before = task_file_evidence(self.root, files)
        (self.root / 'task/data.txt').write_text('changed', encoding='utf-8')
        self.assertNotEqual(task_file_evidence(self.root, files), before)

    def test_existing_source_rejects_entire_batch_before_writing(self):
        source = self.root / 'README.md'
        source.write_text('preserve', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            stage_files(self.root, [{'path': 'new.txt', 'content': 'new'}, {'path': 'README.md', 'content': 'replace'}])
        self.assertEqual(source.read_text(encoding='utf-8'), 'preserve')
        self.assertFalse((self.root / 'new.txt').exists())

    def test_paths_reject_escape_metadata_windows_aliases_and_overlap(self):
        for name in ('../escape', 'C:/escape', 'C:escape', '/escape', '\\escape',
                     'task/../escape', '.GIT/config', '.venv/x', '.env.secret',
                     'task/file:stream', 'task./x', 'NUL', 'task/CON.txt'):
            with self.subTest(path=name), self.assertRaises(ValueError):
                validate_files([{'path': name, 'content': ''}])
        for names in (('Task/a', 'task/A'), ('task', 'task/a')):
            with self.subTest(paths=names), self.assertRaises(ValueError):
                validate_files([{'path': name, 'content': ''} for name in names])
        with self.assertRaises(ValueError):
            validate_files([{'path': 'large.txt', 'content': 'x' * (256 * 1024 + 1)}])

    def test_staged_input_is_not_fresh_task_output(self):
        stage_files(self.root, [{'path': 'result.txt', 'content': 'DONE'}])
        checks = [{'type': 'file_contains', 'path': 'result.txt', 'expected': 'DONE'}]
        results = evaluate_checks(self.root, checks, [], {'0': file_stamp(self.root / 'result.txt')})
        self.assertFalse(results[0]['passed'])
        self.assertFalse(results[0]['fresh_output'])

    def test_verified_prior_artifact_can_be_reused_but_changed_or_unverified_input_cannot(self):
        path = self.root / 'result.txt'
        path.write_text('valid result', encoding='utf-8')
        check = {'type': 'file_contains', 'path': 'result.txt', 'expected': 'valid result', 'freshness': 'preserved'}
        proof = {'result.txt': {'sha256': file_digest(path), 'attempt_id': 'earlier-attempt'}}
        before = {'0': file_stamp(path)}
        result = evaluate_checks(self.root, [check], [], before, proof)[0]
        self.assertTrue(result['passed'])
        self.assertFalse(result['fresh_output'])
        self.assertEqual(result['verified_attempt'], 'earlier-attempt')
        self.assertFalse(evaluate_checks(self.root, [{**check, 'freshness': 'fresh'}], [], before, proof)[0]['passed'])
        self.assertFalse(evaluate_checks(self.root, [check], [], before)[0]['passed'])
        path.write_text('valid result plus unverified change', encoding='utf-8')
        self.assertFalse(evaluate_checks(self.root, [check], [], {'0': file_stamp(path)}, proof)[0]['passed'])


if __name__ == '__main__':
    unittest.main()
