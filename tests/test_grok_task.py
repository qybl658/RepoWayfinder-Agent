"""Focused checks for unchanged execution and lossless lifecycle-safe capture."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import grok_task


class GrokTaskTests(unittest.TestCase):
    def args(self):
        return argparse.Namespace(grok='grok', execution_mode='companion',
                                  reasoning_effort='high', max_turns=0, resume=None,
                                  model='grok-4.7')

    def test_execution_authority_model_and_native_capabilities_are_preserved(self):
        command = grok_task.command_for(self.args(), Path('work'), Path('prompt'))
        self.assertEqual(command[command.index('--model')+1], 'grok-4.7')
        self.assertEqual(command[command.index('--reasoning-effort')+1], 'high')
        self.assertEqual(command[command.index('--tools')+1], grok_task.NATIVE_TOOLS)
        self.assertIn('--always-approve', command)
        self.assertNotIn('--disallowed-tools', command)
        self.assertNotIn('--max-turns', command)
        self.assertIn('MCPTool(repo_wayfinder__*)', command)

    def test_cli_exit_does_not_wait_for_inherited_stdout_and_preserves_late_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'child.py').write_text('import time\ntime.sleep(1.2)\nprint("child-late",flush=True)\n')
            (root/'parent.py').write_text('import subprocess,sys\nsubprocess.Popen([sys.executable,"child.py"])\nprint("parent-done",flush=True)\n')
            start = time.monotonic()
            code, timeout = grok_task.capture_process([sys.executable, str(root/'parent.py')],
                root, None, root/'trace', root/'stderr', root/'timing', start)
            self.assertEqual(code, 0)
            self.assertFalse(timeout)
            self.assertLess(time.monotonic()-start, .8)
            self.assertIn('parent-done', (root/'trace').read_text())
            deadline = time.monotonic()+4
            while 'child-late' not in (root/'trace').read_text() and time.monotonic()<deadline:
                time.sleep(.05)
            self.assertIn('child-late', (root/'trace').read_text())

    def test_capture_is_lossless_and_marks_receive_time_without_bulk_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw = ('[]\n{"type":"assistant","message":"unknown"}\n'
                   '{"type":"user","message":{"content":"string"}}\n'
                   '{"type":"user","message":{"content":["unknown",null,{}]}}\n'
                   '{"type":"assistant","message":{"content":[{"type":"tool_use",'
                   '"id":"t1","name":"read_file","input":{"target_file":"中文.txt"}}]}}\n'
                   '{"type":"user","message":{"content":[{"type":"tool_result",'
                   '"tool_use_id":"t1","content":"private long result"}]}}\n'
                   '{"type":"result","is_error":false,"result":"完成"}\n')
            script = root/'fake.py'
            script.write_text('import sys,time\nsys.stdout.buffer.write('+repr(raw.encode('utf-8'))+')\nsys.stdout.flush()\n', encoding='utf-8')
            trace, timing, stderr = root/'trace.jsonl', root/'timing.jsonl', root/'stderr'
            code, timeout = grok_task.capture_process([sys.executable, str(script)], root, None,
                                                      trace, stderr, timing, time.monotonic())
            self.assertEqual(code, 0)
            self.assertFalse(timeout)
            self.assertEqual(trace.read_bytes(), raw.encode('utf-8'))
            records = [json.loads(line) for line in timing.read_text().splitlines()]
            self.assertEqual([r['line'] for r in records], list(range(1, 8)))
            self.assertEqual(records[0]['type'], 'unknown')
            self.assertEqual(records[4]['tool_use_ids'], ['t1'])
            self.assertEqual(records[5]['tool_result_ids'], ['t1'])
            self.assertNotIn('private long result', timing.read_text())
            self.assertEqual(sorted(r['received_elapsed_seconds'] for r in records),
                             [r['received_elapsed_seconds'] for r in records])
            result, calls = grok_task.summarize(trace)
            self.assertEqual(result['result'], '完成')
            self.assertEqual(calls, 1)

    def test_nonjson_and_failure_are_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, timeout = grok_task.capture_process(
                [sys.executable, '-c', 'print("not-json",flush=True);raise SystemExit(7)'],
                root, None, root/'trace', root/'stderr', root/'timing', time.monotonic())
            self.assertEqual(code, 7)
            self.assertFalse(timeout)
            self.assertEqual(json.loads((root/'timing').read_text())['type'], 'unparsed')
            self.assertIn('not-json', (root/'trace').read_text())

    def test_explicit_timeout_returns_preserved_partial_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, timeout = grok_task.capture_process(
                [sys.executable, '-c', 'import time;print("partial",flush=True);time.sleep(5)'],
                root, None, root/'trace', root/'stderr', root/'timing', time.monotonic(), timeout=.5)
            self.assertTrue(timeout)
            self.assertNotEqual(code, 0)
            self.assertEqual((root/'trace').read_text().strip(), 'partial')

    def test_interrupt_kills_and_waits_for_owned_cli_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            owned = Mock()
            owned.wait.side_effect = [KeyboardInterrupt, None]
            with patch.object(grok_task.subprocess, 'Popen', return_value=owned):
                with self.assertRaises(KeyboardInterrupt):
                    grok_task.capture_process(['fake-cli'], root, None, root/'trace',
                                              root/'stderr', root/'timing', time.monotonic())
            owned.kill.assert_called_once_with()
            self.assertEqual(owned.wait.call_count, 2)


if __name__ == '__main__':
    unittest.main()
