"""Focused wire-contract tests for the stdlib MCP transport."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import mcp_server


def request(method, params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


class MCPTransportTests(unittest.TestCase):
    def test_real_verify_wire_preserves_raw_http_bytes(self):
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='rw-raw-wire-') as folder:
            root = Path(folder).resolve()
            task = root / 'task'
            task.mkdir()
            (task / 'server.py').write_text(
                "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
                "import json,sys\n"
                "class Handler(BaseHTTPRequestHandler):\n"
                " def do_GET(self):\n"
                "  self.send_response(200);self.end_headers();self.wfile.write(b'ready')\n"
                " def do_POST(self):\n"
                "  data=self.rfile.read(int(self.headers.get('Content-Length',0)))\n"
                "  body=json.dumps({'hex':data.hex(),'type':self.headers.get('Content-Type')}).encode()\n"
                "  self.send_response(200);self.end_headers();self.wfile.write(body)\n"
                "HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()\n",
                encoding='utf-8')
            arguments = {'directory': str(task), 'service': {
                'argv': ['python', 'server.py', '{port}'],
                'ready': {'path': '/', 'status': 200, 'contains': 'ready'},
                'requests': [
                    {'method': 'POST', 'path': '/', 'status': 200,
                     'body_base64': 'ew==', 'content_type': 'application/json',
                     'json_pointer': '', 'expected': {'hex': '7b', 'type': 'application/json'}},
                    {'method': 'POST', 'path': '/', 'status': 200,
                     'body_bytes': [255], 'content_type': 'application/json',
                     'json_pointer': '', 'expected': {'hex': 'ff', 'type': 'application/json'}},
                    {'method': 'POST', 'path': '/', 'status': 200,
                     'body_text': '{', 'content_type': 'application/json',
                     'json_pointer': '', 'expected': {'hex': '7b', 'type': 'application/json'}},
                    {'method': 'POST', 'path': '/', 'status': 200,
                     'body_text': '汉', 'content_type': 'text/plain',
                     'json_pointer': '', 'expected': {'hex': 'e6b189', 'type': 'text/plain'}},
                    {'path': '/', 'status': 200, 'contains': 'ready'},
                ]}}
            wire = request('tools/call', {'name': 'rw_verify', 'arguments': arguments})
            result = subprocess.run(
                [sys.executable, str(project / 'agent.py'), '--workspace', str(root / 'jobs'), 'serve'],
                cwd=project, input=(json.dumps(wire) + '\n').encode('utf-8'),
                capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            reply = json.loads(result.stdout.decode('utf-8'))
            self.assertNotIn('error', reply)
            report = json.loads(reply['result']['content'][0]['text'])
            self.assertTrue(report['passed'], report)
            self.assertTrue(Path(report['evidence_path']).is_file())

    def test_real_verify_backend_batches_run_repeat_and_cli_failure(self):
        # Exercise the shipped entry points, not a mocked dispatch: command
        # prints must stay out of JSON and Unicode must survive Windows locale.
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='rw-wire-') as folder:
            root = Path(folder).resolve()
            task = root / 'existing-task'
            task.mkdir()
            script = task / 'task.py'
            script.write_text(
                "from pathlib import Path\n"
                "import csv,json\n"
                "p=Path('counter.txt')\n"
                "p.write_text(str(int(p.read_text())+1) if p.exists() else '1')\n"
                "with open('数据.csv','w',encoding='utf-8',newline='') as f:\n"
                " w=csv.DictWriter(f,fieldnames=['name','amount']);w.writeheader();"
                "w.writerows([{'name':'甲','amount':'0.10'},{'name':'乙','amount':'0.20'}])\n"
                "Path('结果.json').write_text(json.dumps({'说明':'中文😀','rows':2},ensure_ascii=False),encoding='utf-8')\n"
                "print('command output 中文😀')\n", encoding='utf-8')
            original = script.read_bytes()
            arguments = {'directory': str(task), 'run': {'argv': ['python', 'task.py']},
                'unchanged': ['数据.csv', '结果.json'], 'checks': [
                    {'type': 'csv_row_count', 'path': '数据.csv', 'expected': 2},
                    {'type': 'csv_value_counts', 'path': '数据.csv', 'column': 'name', 'expected': {'甲': 1, '乙': 1}},
                    {'type': 'csv_sum', 'path': '数据.csv', 'column': 'amount', 'expected': '0.30'},
                    {'type': 'json_value', 'path': '结果.json', 'pointer': '/说明', 'expected': '中文😀'}]}
            command = [sys.executable, str(project / 'agent.py'), '--workspace', str(root / 'workspace')]
            env = {**os.environ, 'PYTHONIOENCODING': 'gbk:surrogateescape', 'PYTHONUTF8': '0'}
            wire = request('tools/call', {'name': 'rw_verify', 'arguments': arguments})
            result = subprocess.run(command + ['serve'], cwd=project, env=env,
                input=(json.dumps(wire, ensure_ascii=False)+'\n').encode('utf-8'),
                capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
            reply = json.loads(result.stdout.decode('utf-8'))
            report = json.loads(reply['result']['content'][0]['text'])
            self.assertTrue(report['passed'], report)
            self.assertTrue(report['unchanged']['passed'])
            self.assertEqual((task / 'counter.txt').read_text(), '2')
            self.assertEqual(script.read_bytes(), original)
            self.assertFalse((task / '.venv').exists())
            self.assertTrue(Path(report['evidence_path']).is_file())
            self.assertIn('command output', Path(report['evidence_path']).with_name('run-1.log').read_text(encoding='utf-8'))
            # A false assertion is a nonzero JSON CLI result, with no re-run.
            bad = {'directory': str(task), 'checks': [
                {'type': 'csv_count', 'path': '数据.csv', 'expected': 99}]}
            config = root / 'bad.json'
            config.write_text(json.dumps(bad, ensure_ascii=False), encoding='utf-8')
            result = subprocess.run(command + ['call', 'rw_verify', '--file', str(config)],
                cwd=project, env=env, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertFalse(json.loads(result.stdout.decode('utf-8'))['passed'])
            self.assertEqual((task / 'counter.txt').read_text(), '2')

    def test_real_stdio_is_utf8_even_under_legacy_windows_locale(self):
        content = '课程实验：采样 → 实验 → 归档；参考 :ref:`步骤`。 😀'
        payload = request('tools/call', {'name': 'rw_run', 'arguments': {
            'repository': 'owner/repo', 'commands': ['python task.py'],
            'files': [{'path': 'task/说明.txt', 'content': content}]}})
        child = "from mcp_server import serve\nclass Echo:\n def dispatch(self,name,args): return args\nserve(Echo())\n"
        result = subprocess.run([sys.executable, '-c', child],
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'PYTHONIOENCODING': 'gbk:surrogateescape', 'PYTHONUTF8': '0'},
            input=(json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'),
            capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        reply = json.loads(result.stdout.decode('utf-8'))
        self.assertIn('result', reply, reply)
        echoed = json.loads(reply['result']['content'][0]['text'])
        self.assertEqual(echoed['files'][0], payload['params']['arguments']['files'][0])

    def run_messages(self, service, *messages):
        source = io.StringIO("".join(json.dumps(message) + "\n" if not isinstance(message, str) else message + "\n" for message in messages))
        output = io.StringIO()
        with patch("sys.stdout", output):
            mcp_server.serve(service, source)
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_initialize_versions_and_discovery(self):
        service = Mock()
        messages = [request("initialize", {"protocolVersion": version}, index)
                    for index, version in enumerate(("2024-11-05", "2025-03-26", "2025-06-18", "future"), 1)]
        messages.append(request("tools/list", request_id=5))
        replies = self.run_messages(service, *messages)
        self.assertEqual([reply["result"]["protocolVersion"] for reply in replies[:4]],
                         ["2024-11-05", "2025-03-26", "2025-06-18", "2025-06-18"])
        self.assertEqual(replies[0]["result"]["capabilities"], {"tools": {}})
        tools = {tool["name"]: tool for tool in replies[4]["result"]["tools"]}
        self.assertEqual(set(tools), {"rw_verify", "rw_search", "rw_prepare", "rw_run", "rw_execute", "rw_replan", "rw_status", "rw_resume", "rw_logs", "rw_cancel"})
        self.assertTrue(all(tool["inputSchema"]["additionalProperties"] is False for tool in tools.values()))
        self.assertTrue(tools["rw_search"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["rw_execute"]["annotations"]["destructiveHint"])
        run_schema = tools["rw_run"]["inputSchema"]
        self.assertEqual(run_schema["required"], ["repository", "commands"])
        self.assertEqual(set(run_schema["properties"]),
                         {"repository", "commands", "revision", "checks", "timeout", "request_id", "files", "python_version"})
        self.assertEqual(run_schema["properties"]["commands"]["minItems"], 1)
        self.assertEqual(run_schema["properties"]["commands"]["maxItems"], 20)
        self.assertEqual(run_schema["properties"]["timeout"]["default"], 300)
        self.assertIn("plan", tools["rw_prepare"]["inputSchema"]["properties"])
        self.assertIn("python_version", tools["rw_prepare"]["inputSchema"]["properties"])
        self.assertEqual(tools["rw_run"]["annotations"], tools["rw_execute"]["annotations"])
        for term in ("repository root", "virtual environment", "project-declared CLIs", "UTF-8",
                     "relative to the repository", "this run's output", "long-running servers",
                     "50 seconds", "rw_status"):
            self.assertIn(term, tools["rw_run"]["description"])
        self.assertEqual(tools["rw_status"]["inputSchema"]["properties"]["wait_seconds"]["maximum"], 50)
        replan = tools["rw_replan"]
        self.assertEqual(replan["inputSchema"]["required"], ["job_id"])
        self.assertFalse(replan["annotations"]["readOnlyHint"])
        self.assertTrue(replan["annotations"]["destructiveHint"])
        self.assertTrue(replan["annotations"]["openWorldHint"])
        self.assertIn("reusing its checkout", replan["description"])
        self.assertIn("wait_seconds", tools["rw_status"]["description"])
        service.dispatch.assert_not_called()

    def test_call_returns_machine_readable_json_and_no_stdout_pollution(self):
        def dispatch(name, args):
            print("backend diagnostic")
            return {"job_id": "job-1", "status": "waiting_environment", "task_verified": False,
                    "echo": [name, args]}

        service = Mock()
        service.dispatch.side_effect = dispatch
        stderr = io.StringIO()
        with patch("sys.stderr", stderr):
            replies = self.run_messages(service,
                                        request("tools/call", {"name": "rw_status", "arguments": {"job_id": "job-1", "wait_seconds": 10}}))
        result = replies[0]["result"]
        self.assertFalse(result["isError"])
        self.assertNotIn("structuredContent", result)
        self.assertFalse(json.loads(result["content"][0]["text"])["task_verified"])
        self.assertEqual(service.dispatch.call_count, 1)
        self.assertIn("backend diagnostic", stderr.getvalue())

    def test_invalid_arguments_never_reach_backend(self):
        service = Mock()
        bad_calls = [
            ("rw_search", {"query": "x", "limit": True}),
            ("rw_search", {"query": "x", "limit": 11}),
            ("rw_search", {"query": "x", "other": 1}),
            ("rw_prepare", {"repository": "owner/repo", "revision": "main"}),
            ("rw_prepare", {"repository": "owner/repo", "plan": {"action": "DEPLOY", "steps": [{"type": "shell", "cmd": "run", "purpose": "demo", "timeout": 601}], "reason": "test"}}),
            ("rw_prepare", {"repository": "owner/repo", "checks": [{"type": "file_exists", "unexpected": True}]}),
            ("rw_status", {"job_id": "j", "wait_seconds": 51}),
            ("rw_run", {"repository": "owner/repo"}),
            ("rw_run", {"repository": "owner/repo", "commands": []}),
            ("rw_run", {"repository": "owner/repo", "commands": ["x"] * 21}),
            ("rw_run", {"repository": "owner/repo", "commands": [""]}),
            ("rw_run", {"repository": "owner/repo", "commands": ["x", 3]}),
            ("rw_run", {"repository": "owner/repo", "commands": ["x"], "timeout": 601}),
            ("rw_run", {"repository": "owner/repo", "commands": ["x"], "plan": {}}),
            ("rw_logs", {"job_id": "j", "max_chars": 0}),
            ("rw_cancel", {"job_id": 3}),
            ("rw_verify", {"directory": ".", "run": {"argv": []}}),
            ("rw_verify", {"directory": ".", "checks": [{"type": "csv_count", "path": "data.csv", "where": {"name": 2}}]}),
            ("rw_replan", {"job_id": "j", "edits": [{"path": "x", "old": "", "new": "x"}]}),
            ("rw_replan", {"job_id": "j", "execute": "true"}),
            ("rw_run", {"repository": "owner/repo", "commands": ["x"], "files": [{"path": "x", "content": "\udcac"}]}),
            ("rw_run", {"repository": "owner/repo", "commands": ["x"], "checks": [{"type": "file_exists", "path": "x"}] * 129}),
            ("rw_replan", {"job_id": "j", "plan": {"action": "EXECUTE", "steps": [], "reason": "try"}}),
            ("rw_replan", {"job_id": "j", "plan": {"action": "DEPLOY", "steps": [{"type": "shell", "cmd": "x", "purpose": "p", "timeout": 0}], "reason": "try"}}),
            ("rw_replan", {"job_id": "j", "plan": {"action": "DEPLOY", "steps": [], "reason": "try"}, "checks": [{"type": "unknown"}]}),
        ]
        replies = self.run_messages(service, *(request("tools/call", {"name": name, "arguments": args}, index)
                                               for index, (name, args) in enumerate(bad_calls, 1)))
        self.assertEqual(len(replies), len(bad_calls))
        self.assertTrue(all(reply["error"]["code"] == -32602 for reply in replies))
        service.dispatch.assert_not_called()

    def test_verify_structured_expected_values_reach_backend(self):
        arguments = {"directory": "D:/authorized-task", "checks": [
            {"type": "json_value", "path": "结果.json",
             "expected": {"中文": [{"count": 2, "enabled": False}, None]}},
            {"type": "csv_rows", "path": "数据.csv",
             "expected": [{"name": "甲", "value": "2"}]}]}
        service = Mock()
        service.dispatch.return_value = {"ok": True, "passed": True}
        replies = self.run_messages(service, request("tools/call", {
            "name": "rw_verify", "arguments": arguments}))
        self.assertNotIn("error", replies[0])
        service.dispatch.assert_called_once_with("rw_verify", arguments)

    def test_verify_rejects_nested_nonfinite_values_before_dispatch(self):
        service = Mock()
        reply = mcp_server._handle(request("tools/call", {"name": "rw_verify", "arguments": {
            "directory": "D:/authorized-task", "checks": [{"type": "json_value",
                "path": "data.json", "expected": {"bad": [float('nan')]}}]}}), service)
        self.assertEqual(reply["error"]["code"], -32602)
        service.dispatch.assert_not_called()

    def test_replan_dispatches_valid_plan_and_checks(self):
        service = Mock()
        service.dispatch.return_value = {"ok": True, "job_id": "j", "status": "prepared", "task_verified": False}
        args = {"job_id": "j", "plan": {"action": "DEPLOY", "steps": [
            {"type": "exec", "cmd": "python app.py", "purpose": "Run app", "timeout": 30}],
            "reason": "Use the repository entry point"},
            "checks": [{"type": "file_exists", "path": "result.json"}]}
        replies = self.run_messages(service, request("tools/call", {"name": "rw_replan", "arguments": args}))
        service.dispatch.assert_called_once_with("rw_replan", args)
        self.assertEqual(json.loads(replies[0]["result"]["content"][0]["text"])["status"], "prepared")
        self.assertFalse(replies[0]["result"]["isError"])

    def test_run_dispatches_compact_commands(self):
        service = Mock()
        service.dispatch.return_value = {"job_id": "j", "status": "running", "task_verified": False}
        args = {"repository": "owner/repo", "commands": ["python -m pytest"],
                 "checks": [{"type": "file_exists", "path": "results.json"}], "timeout": 45,
                 "files": [{"path": "task/input.txt", "content": "hello"}], "python_version": "3.11"}
        reply = self.run_messages(service, request("tools/call", {"name": "rw_run", "arguments": args}))[0]
        service.dispatch.assert_called_once_with("rw_run", args)
        self.assertEqual(json.loads(reply["result"]["content"][0]["text"])["job_id"], "j")
        self.assertNotIn("structuredContent", reply["result"])

    def test_recovery_and_documented_check_capacity_reach_backend(self):
        import agent_service
        service = Mock()
        service.dispatch.return_value = {'status': 'completed'}
        checks = [{'type': 'file_exists', 'path': f'file-{i}.txt'} for i in range(128)]
        agent_service.validate_checks(checks)
        args = {'job_id': 'j', 'commands': ['python task.py'], 'checks': checks,
                'edits': [{'path': 'task/input.txt', 'old': 'before', 'new': 'after'}],
                'execute': True, 'request_id': 'fix-1'}
        reply = self.run_messages(service, request('tools/call', {'name': 'rw_replan', 'arguments': args}))[0]
        self.assertFalse(reply['result']['isError'])
        service.dispatch.assert_called_once_with('rw_replan', args)
        with self.assertRaises(ValueError):
            agent_service.validate_checks(checks + checks[:1])

    def test_nonstandard_numbers_are_rejected(self):
        service = Mock()
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                raw = '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"rw_status","arguments":{"job_id":"j","wait_seconds":' + token + '}}}'
                replies = self.run_messages(service, raw)
                self.assertEqual(replies[0]["error"]["code"], -32700)
        # Direct handler calls are also bounded if a caller supplied a float.
        reply = mcp_server._handle(request("tools/call", {"name": "rw_status", "arguments": {"job_id": "j", "wait_seconds": float("nan")}}), service)
        self.assertEqual(reply["error"]["code"], -32602)
        service.dispatch.assert_not_called()

    def test_notifications_errors_and_backend_exception(self):
        service = Mock()
        service.dispatch.side_effect = RuntimeError("sensitive backend detail")
        replies = self.run_messages(
            service,
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            "{broken",
            [],
            request("unknown"),
            request("tools/call", {"name": "unknown", "arguments": {}}, 2),
            request("tools/call", {"name": "rw_execute", "arguments": {"job_id": "job-1"}}, 3),
            request("ping", request_id=4),
        )
        self.assertEqual([reply["id"] for reply in replies], [None, None, 1, 2, 3, 4])
        self.assertEqual([reply["error"]["code"] for reply in replies[:3]], [-32700, -32600, -32601])
        self.assertTrue(replies[3]["result"]["isError"])
        self.assertTrue(replies[4]["result"]["isError"])
        self.assertEqual(json.loads(replies[4]["result"]["content"][0]["text"])["message"], "sensitive backend detail")
        self.assertEqual(replies[5]["result"], {})
        service.dispatch.assert_called_once_with("rw_execute", {"job_id": "job-1"})


if __name__ == "__main__":
    unittest.main()
