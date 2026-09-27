"""Recovery invariants and a real failed subprocess repaired in its original venv."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import agent_service as service_module
import test_agent_service as fixtures


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.AgentServiceTests.setUp
    git = fixtures.AgentServiceTests.git
    checkout = fixtures.AgentServiceTests.checkout
    seeded_job = fixtures.AgentServiceTests.seeded_job

    def wire_replan(self, job_id, **args):
        """Real UTF-8 MCP -> service -> owned worker; never a mocked dispatch."""
        request = {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                   'params': {'name': 'rw_replan', 'arguments': {'job_id': job_id, **args}}}
        process = subprocess.run(
            [sys.executable, str(fixtures.ROOT / 'agent.py'), '--workspace',
             str(self.root / 'state'), 'serve'], cwd=fixtures.ROOT,
            env={**os.environ, 'PYTHONIOENCODING': 'gbk:surrogateescape', 'PYTHONUTF8': '0'},
            input=(json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8'),
            capture_output=True, timeout=65)
        self.assertEqual(process.returncode, 0, process.stderr.decode('utf-8', errors='replace'))
        reply = json.loads(process.stdout.decode('utf-8'))
        self.assertIn('result', reply, reply)
        self.assertFalse(reply['result']['isError'], reply)
        result = json.loads(reply['result']['content'][0]['text'])
        if result['status'] in {'running', 'preparing'}:
            result = self.service.status(job_id, wait_seconds=50)
        return result

    def ready(self, python_version=''):
        folder, checkout, job = self.seeded_job(status='failed')
        request = {
            'repository': job['repository'], 'revision': job['revision'],
            'plan': service_module.command_plan(['python task/build.py']),
            'checks': [{'type': 'json_value', 'path': 'result.json', 'pointer': '/answer', 'expected': 42}],
            'bounded_commands': True, 'auto_execute': False,
        }
        if python_version:
            request['python_version'] = python_version
            job['python_version'] = python_version
            service_module.write_json(folder / 'job.json', job)
        service_module.write_json(folder / 'request.json', request)
        service_module.write_json(folder / 'repository.json', {
            'owner': 'example', 'name': 'repo', 'full_name': 'example/repo',
            'html_url': 'https://github.com/example/repo', 'clone_url': 'https://github.com/example/repo.git',
        })
        return folder, checkout, job

    def test_real_failure_exact_repair_same_environment_and_immutable_evidence(self):
        requested = f'{sys.version_info.major}.{sys.version_info.minor}'
        folder, checkout, job = self.ready(python_version=requested)
        script = ("from pathlib import Path\nimport json, sys, subprocess\n"
                  "Path('environment.txt').write_text(sys.prefix, encoding='utf-8')\n"
                  "child = subprocess.check_output([sys.executable, '-c', 'import rw_local_dependency; print(rw_local_dependency.TEXT)'], text=True, encoding='utf-8')\n"
                  "assert child.strip() == '课程实验 😀', repr(child)\n"
                  "answer = 0\nassert answer == 42, 'correct answer input'\n"
                  "Path('result.json').write_text(json.dumps({'answer': answer}), encoding='utf-8')\n")
        setup = ("from pathlib import Path\nimport sys, subprocess, sysconfig, json\n"
                 "expected = Path('.venv').resolve()\n"
                 "assert Path(sys.prefix).resolve() == expected\n"
                 # Inspect child destinations BEFORE creating any fixture module.
                 "probe = subprocess.check_output([sys.executable, '-c', 'import sys; print(sys.prefix)'], text=True).strip()\n"
                 "assert Path(probe).resolve() == expected, 'nested Python escaped job environment: ' + probe\n"
                 "pip = subprocess.check_output(['pip', '--version'], text=True).strip()\n"
                 "assert str(expected).lower() in pip.lower(), 'nested pip escaped job environment: ' + pip\n"
                 "message = Path('task/说明.txt').read_text(encoding='utf-8')\n"
                 "assert message == '课程实验 😀'\n"
                 "module = Path(sysconfig.get_path('purelib')) / 'rw_local_dependency.py'\n"
                 "assert module.resolve().is_relative_to(expected)\n"
                 "module.write_text('TEXT = ' + repr(message), encoding='utf-8')\n"
                 "p=Path('setup-count.txt')\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')\n")
        first = self.wire_replan(job['job_id'], files=[
            {'path': 'task/build.py', 'content': script},
            {'path': 'task/setup.py', 'content': setup},
            {'path': 'task/说明.txt', 'content': '课程实验 😀'},
        ], commands=['python task/setup.py', 'python task/build.py'], execute=True, request_id='initial')
        self.assertEqual(first['status'], 'failed', first)
        self.assertEqual(first['python_version'], requested)
        self.assertTrue(first['result']['python_runtime']['version'].startswith(requested + '.'), first)
        self.assertTrue(Path(first['result']['python_runtime']['executable']).is_file())
        self.assertTrue(first['result']['python_runtime']['base'])
        first_path = Path(first['result']['evidence_path'])
        first_bytes = first_path.read_bytes()
        # Failure must be the intentional task defect, never a broken transport,
        # missing dependency or a child using a different environment.
        self.assertIn('correct answer input', first_bytes.decode('utf-8'))
        first_environment = (checkout / 'environment.txt').read_text(encoding='utf-8')
        args = dict(edits=[{'path': 'task/build.py', 'old': 'answer = 0', 'new': 'answer = 42'}],
                    commands=['python task/build.py'], execute=True, request_id='fix-1')
        second = self.wire_replan(job['job_id'], **args)
        self.assertEqual(second['status'], 'completed', second)
        self.assertTrue(second['result']['task_verified'])
        self.assertEqual(second['result']['python_runtime'], first['result']['python_runtime'])
        self.assertEqual(service_module.read_json(folder / 'request.json')['python_version'], requested)
        self.assertEqual(second['project_path'], first['project_path'])
        self.assertEqual((checkout / 'setup-count.txt').read_text(), '1')
        self.assertEqual((checkout / 'environment.txt').read_text(encoding='utf-8'), first_environment)
        self.assertIn('.venv', first_environment)
        self.assertEqual(first_path.read_bytes(), first_bytes)
        self.assertNotEqual(first['result']['evidence_path'], second['result']['evidence_path'])
        snapshots = [service_module.read_json(p) for p in (folder / 'history').glob('*.json')]
        self.assertTrue(any(s['files_before'].get('task/build.py') == script for s in snapshots))
        with patch.object(self.service, 'launch') as launch:
            replay = self.service.replan(job['job_id'], **args)
            self.assertEqual(replay['result'], second['result'])
            launch.assert_not_called()
            with self.assertRaisesRegex(ValueError, 'different inputs'):
                self.service.replan(job['job_id'], **{**args, 'commands': ['python task/setup.py']})
        self.assertEqual(len(list((folder / 'runs').glob('*.json'))), 2)
        self.assertEqual(self.git(checkout, 'status', '--porcelain', '--untracked-files=no'), '')
        # A later stage keeps the valid earlier artifact. It must not force
        # regeneration merely because the old file has not changed this time.
        follow_on = self.service.replan(job['job_id'], commands=['python -c "print(42)"'],
                                       checks=[{'type': 'json_value', 'path': 'result.json', 'pointer': '/answer',
                                                'expected': 42, 'freshness': 'preserved'}],
                                       execute=True, request_id='inspect-previous')
        self.assertTrue(follow_on['result']['task_verified'], follow_on)
        self.assertEqual(follow_on['result']['checks'][0]['verified_attempt'], second['attempt_id'])
        self.assertFalse(follow_on['result']['checks'][0]['fresh_output'])
        proofs = service_module.read_json(folder / 'verified-artifacts.json')
        self.assertEqual(proofs['result.json']['attempt_id'], second['attempt_id'])
        # Editing an input must not make an old answer look freshly recomputed.
        stale = self.service.replan(job['job_id'],
            edits=[{'path': 'task/build.py', 'old': 'answer = 42', 'new': 'answer = 99'}],
            commands=['python -c "print(42)"'],
            checks=[{'type': 'json_value', 'path': 'result.json', 'pointer': '/answer', 'expected': 42}],
            execute=True, request_id='changed-input')
        self.assertEqual(stale['status'], 'failed')
        self.assertFalse(stale['result']['task_verified'])

    def test_requested_python_conflicting_with_repository_waits_before_execution(self):
        folder, checkout, job = self.ready(python_version='3.12')
        (checkout / '.python-version').write_text('3.11\n', encoding='utf-8')
        result = self.wire_replan(job['job_id'], commands=['python -V'], execute=True,
                                  request_id='version-conflict')
        self.assertEqual(result['status'], 'waiting_environment', result)
        self.assertIn('requires 3.11', result['reason'])
        self.assertFalse((checkout / '.venv').exists())
        self.assertFalse((folder / 'runs').exists())

    def test_invalid_revision_batches_leave_all_original_state_intact(self):
        folder, checkout, job = self.ready()
        (checkout / 'input.txt').write_text('old old', encoding='utf-8')
        before = {n: (folder / n).read_bytes() for n in ('job.json', 'request.json')}
        bad = [
            {'edits': [{'path': 'input.txt', 'old': 'old', 'new': 'new'}]},
            {'edits': [{'path': 'README.md', 'old': 'local', 'new': 'new'}]},
            {'files': [{'path': 'input.txt', 'content': 'new'}]},
            {'files': [{'path': '.venv/x', 'content': 'new'}]},
            {'files': [{'path': 'node_modules/x', 'content': 'new'}]},
            {'files': [{'path': 'x', 'content': '\udcac'}]},
            {'commands': ['python x'], 'plan': service_module.command_plan(['python y'])},
        ]
        for payload in bad:
            with self.subTest(payload=repr(payload)), self.assertRaises(ValueError):
                self.service.replan(job['job_id'], **payload)
            self.assertEqual((checkout / 'input.txt').read_text(), 'old old')
            self.assertEqual(before, {n: (folder / n).read_bytes() for n in before})
        self.assertFalse((folder / 'history').exists())

    def test_interrupted_input_transaction_restores_before_any_resume(self):
        folder, checkout, job = self.ready()
        (checkout / 'input.txt').write_text('before', encoding='utf-8')
        with patch.object(service_module, 'stage_files', side_effect=SystemExit('simulated process death')):
            with self.assertRaises(SystemExit):
                self.service.replan(job['job_id'], edits=[{'path': 'input.txt', 'old': 'before', 'new': 'after'}])
        # Model a crash between file replacement and request publication.
        (checkout / 'input.txt').write_text('external edit', encoding='utf-8')
        with patch.object(service_module, 'process_alive', return_value=False):
            conflict = self.service.status(job['job_id'])
        self.assertIn('external change', conflict['reason'])
        self.assertEqual((checkout / 'input.txt').read_text(), 'external edit')
        with self.assertRaisesRegex(ValueError, 'Reconcile'):
            self.service.resume(job['job_id'])
        (checkout / 'input.txt').write_text('after', encoding='utf-8')
        with patch.object(service_module, 'process_alive', return_value=False):
            result = self.service.status(job['job_id'])
        self.assertEqual(result['status'], 'interrupted')
        self.assertIn('rolled back', result['reason'])
        self.assertEqual((checkout / 'input.txt').read_text(), 'before')

    def test_duplicate_and_unowned_workers_cannot_execute(self):
        folder, _, job = self.ready()
        self.service.save(folder, {'status': 'running', 'worker_pid': os.getpid()})
        with patch.object(service_module.subprocess, 'Popen') as popen:
            self.service.execute(job['job_id'])
            self.service.launch(folder, 'replan')
            popen.assert_not_called()
        with patch.object(self.service, 'core') as core:
            self.assertEqual(self.service.worker(job['job_id'], 'execute', require_ownership=True), 2)
            core.assert_not_called()

    def test_new_attempt_discards_old_summary_even_if_launch_fails(self):
        folder, _, job = self.ready()
        self.service.save(folder, {'status': 'prepared', 'result': {'task_verified': True}})
        with patch.object(service_module.subprocess, 'Popen', side_effect=OSError('fixture launch failure')):
            with self.assertRaises(OSError):
                self.service.execute(job['job_id'])
        result = self.service.status(job['job_id'])
        self.assertEqual(result['status'], 'interrupted')
        self.assertNotIn('result', result)
        self.assertTrue(result['attempt_id'])

    @unittest.skipUnless(os.name == 'nt', 'Windows junction boundary')
    def test_input_paths_reject_in_tree_junction_to_runtime(self):
        folder, checkout, job = self.ready()
        (checkout / '.venv').mkdir()
        junction = checkout / 'shortcut'
        subprocess.run(['powershell', '-NoProfile', '-Command',
                        "New-Item -ItemType Junction -Path '" + str(junction) + "' -Target '" + str(checkout / '.venv') + "' | Out-Null"],
                       check=True, capture_output=True)
        try:
            with self.assertRaisesRegex(ValueError, 'link or junction'):
                self.service.replan(job['job_id'], files=[{'path': 'shortcut/x', 'content': 'new'}])
            self.assertFalse((checkout / '.venv/x').exists())
        finally:
            junction.rmdir()


if __name__ == '__main__':
    unittest.main()
