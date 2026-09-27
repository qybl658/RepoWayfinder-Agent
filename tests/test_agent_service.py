"""Focused Agent job contracts; all checkouts are local and target execution is mocked."""

from dataclasses import dataclass
import inspect
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import agent_service


ROOT = Path(__file__).resolve().parents[1]


@dataclass
class Runtime:
    kind: str = 'not_applicable'
    success: bool | None = None
    url: str = ''


@dataclass
class Attempt:
    cmd: str = 'python demo.py'
    returncode: int = 0
    stdout: str = ''
    stderr: str = ''
    timed_out: bool = False
    planned_cmd: str = 'python demo.py'


class PythonEnvironmentError(Exception):
    def __init__(self):
        super().__init__('Python is unavailable before project execution')
        self.evidence = {'tool': 'python', 'status': 'missing'}


@dataclass
class RepoRecord:
    owner: str = 'example'
    name: str = 'repo'
    full_name: str = 'example/repo'


class CoreDouble:
    def __init__(self, *, ready=True, runtime=None, attempts=None, on_execute=None):
        self.ENVIRONMENT_CHANGES = []
        self.SAFE_COMMANDS = {'python'}
        self.adapt_command = lambda cmd, python: shlex.split(cmd)
        self.plan_needs_python = lambda plan: False
        self.build_process_env = lambda target_process=False: {}
        self.command_head = lambda cmd: shlex.split(cmd)[0].lower()
        self.split_command = shlex.split
        self.clean_cli_token = lambda token: token
        self.ready = ready
        self.runtime = runtime or Runtime()
        self.attempts = attempts if attempts is not None else [Attempt()]
        self.on_execute = on_execute
        self.execute_calls = 0

    def normalize_plan(self, plan, source):
        return plan

    def capture_plan_evidence(self, checkout, plan):
        return {'README.md': 'recorded-source'}

    def review_repository_security(self, checkout, plan, mode):
        return {'blocked': False}

    def ensure_plan_prerequisites(self, plan, checkout):
        return (self.ready, [] if self.ready else [{'name': 'docker', 'status_after': 'missing'}],
                '' if self.ready else 'Docker is not ready')

    def execute_plan(self, checkout, plan):
        self.execute_calls += 1
        if self.on_execute:
            self.on_execute()
        return True, self.attempts, [], self.runtime


class AgentServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agent-service-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.service = agent_service.AgentService(self.root / 'state')

    def git(self, checkout, *args):
        return subprocess.run(['git', '-C', str(checkout), *args], check=True, capture_output=True,
                              text=True, encoding='utf-8').stdout.strip()

    def checkout(self):
        checkout = self.root / f'checkout-{len(list(self.root.glob("checkout-*"))) + 1}'
        checkout.mkdir()
        self.git(checkout, 'init', '-q')
        self.git(checkout, 'config', 'user.name', 'Agent Test')
        self.git(checkout, 'config', 'user.email', 'agent@example.invalid')
        (checkout / 'README.md').write_text('local fixture\n', encoding='utf-8')
        self.git(checkout, 'add', 'README.md')
        self.git(checkout, 'commit', '-qm', 'fixture')
        return checkout, self.git(checkout, 'rev-parse', 'HEAD')

    def seeded_job(self, *, checks=None, status='prepared'):
        checkout, revision = self.checkout()
        job_id = f'{len(list(self.service.jobs.iterdir())) + 1:032x}'
        folder = self.service.jobs / job_id
        folder.mkdir()
        plan = {'action': 'DEPLOY', 'steps': [{'type': 'exec', 'cmd': 'python demo.py'}],
                'reason': 'test', 'source': 'agent_explicit'}
        job = {'job_id': job_id, 'status': status, 'phase': 'prepare', 'repository': 'example/repo',
               'project_path': str(checkout), 'revision': revision, 'plan': plan,
               'plan_digest': agent_service.digest(plan)}
        agent_service.write_json(folder / 'job.json', job)
        agent_service.write_json(folder / 'request.json', {'repository': 'example/repo', 'checks': checks or []})
        agent_service.write_json(folder / 'plan-evidence.json', {'README.md': 'recorded-source'})
        return folder, checkout, job

    def test_cli_emits_one_json_object_for_invalid_request_without_prompt(self):
        completed = subprocess.run([sys.executable, str(ROOT / 'agent.py'), '--workspace', str(self.root / 'cli'),
                                    'call', 'rw_prepare', '--json', json.dumps({'repository': 'ambiguous keyword'})],
                                   capture_output=True, text=True, encoding='utf-8', timeout=10)
        lines = completed.stdout.splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(json.loads(lines[0])['ok'], False)
        self.assertFalse(any((self.root / 'cli' / 'jobs').iterdir()))

    def test_prepare_request_id_is_idempotent_and_rejects_changed_inputs(self):
        with patch.object(self.service, 'launch') as launch:
            first = self.service.prepare('example/repo', request_id='same-request', python_version='3.11')
            second = self.service.prepare('https://github.com/example/repo', request_id='same-request', python_version='3.11')
            self.assertEqual(first['job_id'], second['job_id'])
            self.assertEqual(first['python_version'], '3.11')
            self.assertEqual(agent_service.read_json(self.service.folder(first['job_id']) / 'request.json')['python_version'], '3.11')
            self.assertEqual(launch.call_count, 1)
            with self.assertRaisesRegex(ValueError, 'different inputs'):
                self.service.prepare('example/repo', request_id='same-request', python_version='3.12')
        self.assertEqual(len(list(self.service.jobs.iterdir())), 1)

    def test_python_version_rejects_unsupported_and_malformed_values(self):
        for value in ('3.11.9', '03.11', '3.-1', 'python3.11', 3.11, None):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'python_version'):
                self.service.prepare('example/repo', python_version=value)
        self.assertFalse(any(self.service.jobs.iterdir()))

    def test_run_records_auto_execute_and_enters_execution_only_for_prepared_plan(self):
        status = self.service.status
        with patch.object(self.service, 'launch'), \
             patch.object(self.service, 'status', side_effect=lambda job_id, wait_seconds=0: status(job_id, 0)):
            queued = self.service.run('example/repo', request_id='run-once')
        folder = self.service.folder(queued['job_id'])
        self.assertTrue(agent_service.read_json(folder / 'request.json')['auto_execute'])
        core = CoreDouble()
        for plan_status, expected_calls in (('prepared', 1), ('blocked', 0), ('needs_plan', 0)):
            with self.subTest(plan_status=plan_status):
                def finish_preparation(*_args, **_kwargs):
                    self.service.save(folder, {'status': plan_status})
                with patch.object(self.service, 'core', return_value=core), \
                     patch.object(self.service, 'prepare_work', side_effect=finish_preparation), \
                     patch.object(self.service, 'execute_work') as execute:
                    self.service.worker(queued['job_id'], 'prepare')
                self.assertEqual(execute.call_count, expected_calls)

    def test_compact_commands_are_finite_even_when_script_name_looks_like_server(self):
        status = self.service.status
        with patch.object(self.service, 'launch'), \
             patch.object(self.service, 'status', side_effect=lambda job_id, wait_seconds=0: status(job_id, 0)):
            queued = self.service.run('example/repo', commands=['python main.py'], checks=[{'type': 'file_exists', 'path': 'out.txt'}])
        folder = self.service.folder(queued['job_id'])
        core = CoreDouble()
        core.is_runtime_step = lambda step: True
        def prepared(*_args, **_kwargs):
            self.service.save(folder, {'status': 'prepared'})
        def execute(*_args):
            self.assertFalse(core.is_runtime_step(SimpleNamespace(cmd='python main.py')))
        with patch.object(self.service, 'core', return_value=core), \
             patch.object(self.service, 'prepare_work', side_effect=prepared), \
             patch.object(self.service, 'execute_work', side_effect=execute) as run:
            self.service.worker(queued['job_id'], 'prepare')
        run.assert_called_once()
        self.assertEqual(agent_service.read_json(folder / 'request.json')['plan']['steps'][0]['cmd'], 'python main.py')

    def test_compact_commands_reject_ambiguous_or_empty_plan_before_job_creation(self):
        for arguments in ({'commands': []}, {'commands': ['python main.py'], 'plan': {}},
                          {'commands': ['python main.py'], 'timeout': True}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.service.run('example/repo', **arguments)
        self.assertEqual(list(self.service.jobs.iterdir()), [])

    def test_cancel_during_auto_prepare_prevents_execution(self):
        folder, _, job = self.seeded_job(status='queued')
        request = agent_service.read_json(folder / 'request.json')
        request['auto_execute'] = True
        agent_service.write_json(folder / 'request.json', request)
        def cancelled_preparation(*_args, **_kwargs):
            # The caller cancelled while preparation was active. Its worker
            # finished planning just afterward and saved a prepared plan.
            (folder / 'cancel.requested').touch()
            self.service.save(folder, {'status': 'prepared'})
        with patch.object(self.service, 'core', return_value=CoreDouble()), \
             patch.object(self.service, 'prepare_work', side_effect=cancelled_preparation), \
             patch.object(self.service, 'execute_work') as execute:
            self.service.worker(job['job_id'], 'prepare')
        execute.assert_not_called()
        self.assertEqual(agent_service.read_json(folder / 'job.json')['status'], 'cancelled')

    def test_replan_reuses_checkout_preserves_old_evidence_and_stops_prepared(self):
        folder, checkout, job = self.seeded_job(status='completed')
        old_result = {'task_verified': True, 'evidence_path': str(folder / 'deployment_result.json')}
        self.service.save(folder, {'result': old_result})
        old_report = {'task_verified': True, 'attempts': [{'cmd': 'previous command'}]}
        agent_service.write_json(folder / 'deployment_result.json', old_report)
        old_run = folder / 'runs' / 'previous.json'
        agent_service.write_json(old_run, old_report)
        agent_service.write_json(folder / 'repository.json',
                                 {'owner': 'example', 'name': 'repo', 'full_name': 'example/repo'})
        revised = {'action': 'DEPLOY', 'steps': [{'type': 'exec', 'cmd': 'python revised.py', 'timeout': 30}],
                   'reason': 'new task evidence'}
        with patch.object(self.service, 'launch') as launch, \
             patch.object(self.service, 'status', side_effect=lambda jid, wait_seconds=0:
                          agent_service.AgentService.status(self.service, jid, 0)):
            self.service.replan(job['job_id'], revised, checks=[{'type': 'stdout_contains', 'expected': 'DONE'}])
        launch.assert_called_once_with(folder, 'replan', locked=True)
        snapshots = list((folder / 'history').glob('*.json'))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(agent_service.read_json(snapshots[0])['job']['result'], old_result)
        self.assertEqual(agent_service.read_json(folder / 'deployment_result.json'), old_report)
        self.assertEqual(agent_service.read_json(old_run), old_report)
        request = agent_service.read_json(folder / 'request.json')
        self.assertFalse(request['auto_execute'])
        self.assertEqual(request['revision'], job['revision'])
        self.assertEqual(request['checks'][0]['expected'], 'DONE')
        core = CoreDouble()
        core.RepoInfo = lambda **fields: SimpleNamespace(**fields)
        core.normalize_plan = lambda data, source: SimpleNamespace(
            action='DEPLOY', steps=[SimpleNamespace(type='exec', cmd='python revised.py', timeout=30)],
            reason='new task evidence', source=source)
        core.contains_unquoted_shell_operator = lambda command: False
        core.validate_plan = lambda plan, **kwargs: (True, '')
        core.plan_to_dict = lambda plan: {'action': plan.action, 'steps': [vars(step) for step in plan.steps],
                                          'reason': plan.reason, 'source': plan.source}
        core.detect_required_config = lambda path: []
        core.fetch_repo_info = Mock(side_effect=AssertionError('replan must reuse repository metadata'))
        core.clone_repo = Mock(side_effect=AssertionError('replan must reuse checkout'))
        with patch.object(self.service, 'core', return_value=core):
            code = self.service.worker(job['job_id'], 'replan')
        saved = agent_service.read_json(folder / 'job.json')
        self.assertEqual(code, 0)
        self.assertEqual(saved['status'], 'prepared')
        self.assertEqual(saved['project_path'], str(checkout))
        self.assertEqual(saved['revision'], job['revision'])
        self.assertEqual(saved['plan']['steps'][0]['cmd'], 'python revised.py')
        self.assertNotIn('result', saved)
        self.assertEqual(core.execute_calls, 0)
        core.fetch_repo_info.assert_not_called()
        core.clone_repo.assert_not_called()
        self.assertEqual(agent_service.read_json(old_run), old_report)

    def test_rejected_plan_keeps_checkout_identity_for_replan_recovery(self):
        folder, checkout, job = self.seeded_job(status='queued')
        initial = {'action': 'DEPLOY', 'steps': [{'type': 'exec', 'cmd': 'python first.py', 'timeout': 30}],
                   'reason': 'first attempt'}
        request = {'repository': 'example/repo', 'revision': job['revision'], 'plan': initial,
                   'checks': [], 'auto_execute': False}
        agent_service.write_json(folder / 'request.json', request)
        core = CoreDouble()
        core.RepoInfo = RepoRecord
        core.fetch_repo_info = Mock(return_value=RepoRecord())
        core.clone_repo = Mock(return_value=checkout)
        core.normalize_plan = lambda data, source: SimpleNamespace(
            action='DEPLOY', steps=[SimpleNamespace(type='exec', cmd=data['steps'][0]['cmd'], timeout=30)],
            reason=data['reason'], source=source)
        core.contains_unquoted_shell_operator = lambda command: False
        core.validate_plan = Mock(side_effect=[(False, 'unsupported command'), (True, '')])
        core.plan_to_dict = lambda plan: {'action': plan.action, 'steps': [vars(step) for step in plan.steps],
                                          'reason': plan.reason, 'source': plan.source}
        core.detect_required_config = lambda path: []
        with patch.object(self.service, 'core', return_value=core), patch('traceback.print_exc'):
            first_code = self.service.worker(job['job_id'], 'prepare')
        failed = agent_service.read_json(folder / 'job.json')
        self.assertEqual(first_code, 1)
        self.assertEqual(failed['status'], 'failed')
        self.assertEqual(failed['project_path'], str(checkout))
        self.assertEqual(failed['revision'], job['revision'])
        revised = {'action': 'DEPLOY', 'steps': [{'type': 'exec', 'cmd': 'python revised.py', 'timeout': 30}],
                   'reason': 'corrected command'}
        with patch.object(self.service, 'launch'), \
             patch.object(self.service, 'status', side_effect=lambda jid, wait_seconds=0:
                          agent_service.AgentService.status(self.service, jid, 0)):
            self.service.replan(job['job_id'], revised)
        with patch.object(self.service, 'core', return_value=core):
            second_code = self.service.worker(job['job_id'], 'replan')
        recovered = agent_service.read_json(folder / 'job.json')
        self.assertEqual(second_code, 0)
        self.assertEqual(recovered['status'], 'prepared')
        self.assertEqual(recovered['project_path'], str(checkout))
        self.assertEqual(recovered['revision'], job['revision'])
        self.assertEqual(recovered['plan']['steps'][0]['cmd'], 'python revised.py')
        self.assertEqual(core.execute_calls, 0)
        core.fetch_repo_info.assert_called_once()
        core.clone_repo.assert_called_once()
        snapshots = list((folder / 'history').glob('*.json'))
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(agent_service.read_json(snapshots[0])['job']['status'], 'failed')

    def test_core_binds_writable_state_to_its_job_and_disables_internal_ai(self):
        folder = self.service.jobs / ('f' * 32)
        folder.mkdir()
        app = ModuleType('main')
        app.run_process = lambda *args, **kwargs: None
        app.run_runtime_step = lambda *args, **kwargs: None
        with patch.dict(sys.modules, {'main': app}), patch.dict(agent_service.os.environ, {}, clear=False):
            selected = self.service.core(folder)
            self.assertIs(selected, app)
            self.assertEqual(app.BASE_DIR, folder / 'checkouts')
            self.assertEqual(app.REPORTS_DIR, self.service.jobs)
            self.assertEqual(app.ARTIFACT_DIR, folder)
            self.assertEqual(app.REPORT_PATH, folder / 'deployment_result.json')
            self.assertEqual(app.OWNED_TARGETS_PATH, folder / 'owned-projects.json')
            self.assertEqual(app.HISTORY_PATH, self.service.workspace / 'history.json')
            self.assertEqual(app.PREREQUISITE_STATE_PATH, folder / 'prerequisites.json')
            self.assertEqual(app.AI_API_KEY, '')
            self.assertEqual(app.ACTIVE_DEPLOYMENT_MODE, 'protected')
            self.assertEqual(agent_service.os.environ['REPOSCOUT_NONINTERACTIVE'], '1')

    def test_changed_source_or_saved_plan_stops_before_execution(self):
        for changed in ('source', 'plan'):
            with self.subTest(changed=changed):
                folder, checkout, job = self.seeded_job()
                if changed == 'source':
                    (checkout / 'README.md').write_text('changed after preparation\n', encoding='utf-8')
                else:
                    job['plan']['steps'][0]['cmd'] = 'python changed.py'
                    agent_service.write_json(folder / 'job.json', job)
                core = CoreDouble()
                with self.assertRaisesRegex(ValueError, 'changed'):
                    self.service.execute_work(folder, core, 'execute')
                self.assertEqual(core.execute_calls, 0)

    def test_missing_prerequisite_is_wait_not_project_success(self):
        folder, _, _ = self.seeded_job()
        core = CoreDouble(ready=False)
        self.service.execute_work(folder, core, 'execute')
        saved = agent_service.read_json(folder / 'job.json')
        self.assertEqual(saved['status'], 'waiting_environment')
        self.assertEqual(saved['prerequisites'][0]['name'], 'docker')
        self.assertEqual(saved['result'], {'deployment_success': False, 'task_verified': False,
                                           'project_execution_started': False})
        self.assertEqual(core.execute_calls, 0)

    def test_python_environment_exception_is_recoverable_wait(self):
        folder, _, job = self.seeded_job()
        def unavailable():
            raise PythonEnvironmentError()
        core = CoreDouble(on_execute=unavailable)
        with patch.object(self.service, 'core', return_value=core):
            code = self.service.worker(job['job_id'], 'execute')
        saved = agent_service.read_json(folder / 'job.json')
        self.assertEqual(code, 2)
        self.assertEqual(saved['status'], 'waiting_environment')
        self.assertFalse(saved['result']['project_execution_started'])
        self.assertFalse(saved['result']['task_verified'])
        self.assertEqual(saved['prerequisites'][0]['tool'], 'python')

    def test_unchanged_old_output_does_not_verify_task(self):
        folder, checkout, _ = self.seeded_job(checks=[{'type': 'file_exists', 'path': 'result.txt'}])
        (checkout / 'result.txt').write_text('old output', encoding='utf-8')
        core = CoreDouble(runtime=Runtime(kind='http', success=True, url='http://127.0.0.1:1234/'))
        self.service.execute_work(folder, core, 'execute')
        saved = agent_service.read_json(folder / 'job.json')
        self.assertEqual(saved['status'], 'failed')
        self.assertTrue(saved['result']['deployment_success'])
        self.assertFalse(saved['result']['task_verified'])
        self.assertFalse(saved['result']['checks'][0]['fresh_output'])

    def test_command_exit_zero_without_checks_does_not_verify_task(self):
        folder, _, _ = self.seeded_job()
        core = CoreDouble(runtime=Runtime(kind='not_applicable', success=None), attempts=[Attempt(returncode=0)])
        self.service.execute_work(folder, core, 'execute')
        saved = agent_service.read_json(folder / 'job.json')
        self.assertFalse(saved['result']['task_verified'])
        self.assertEqual(saved['result']['outcome'], 'command_verified')

    def test_fresh_cli_json_value_can_verify_without_http_runtime(self):
        check = {'type': 'json_value', 'path': 'out/result.json', 'pointer': '/answer', 'expected': 42}
        folder, checkout, _ = self.seeded_job(checks=[check])
        def produce():
            output = checkout / 'out' / 'result.json'
            output.parent.mkdir()
            output.write_text('{"answer": 42}', encoding='utf-8')
        core = CoreDouble(runtime=Runtime(kind='not_applicable', success=None), on_execute=produce)
        self.service.execute_work(folder, core, 'execute')
        saved = agent_service.read_json(folder / 'job.json')
        self.assertEqual(saved['status'], 'completed')
        self.assertTrue(saved['result']['task_verified'])
        self.assertTrue(saved['result']['checks'][0]['fresh_output'])

    def test_failed_attempt_output_cannot_satisfy_final_stdout_assertion(self):
        folder, _, _ = self.seeded_job(checks=[{'type': 'stdout_contains', 'expected': 'TASK COMPLETE'}])
        core = CoreDouble(attempts=[Attempt(returncode=1, stdout='TASK COMPLETE'),
                                    Attempt(returncode=0, stdout='setup recovered')])
        self.service.execute_work(folder, core, 'execute')
        saved = agent_service.read_json(folder / 'job.json')
        self.assertFalse(saved['result']['task_verified'])

    def test_stdout_assertion_does_not_require_a_file_for_optional_empty_path(self):
        folder, _, _ = self.seeded_job(checks=[{'type': 'stdout_contains', 'path': '', 'expected': 'TASK COMPLETE'}])
        core = CoreDouble(attempts=[Attempt(returncode=0, stdout='TASK COMPLETE')])
        self.service.execute_work(folder, core, 'execute')
        saved = agent_service.read_json(folder / 'job.json')
        self.assertEqual(saved['status'], 'completed')
        self.assertTrue(saved['result']['task_verified'])

    def test_stale_execute_read_cannot_launch_a_completed_job_again(self):
        folder, _, prepared = self.seeded_job()
        fake_process = Mock(pid=12345)
        with patch.object(agent_service.subprocess, 'Popen', return_value=fake_process) as popen, \
             patch.object(agent_service, 'process_alive', return_value=True):
            self.service.execute(prepared['job_id'])
            self.service.save(folder, {'status': 'completed', 'result': {'task_verified': True}})
            original_read = agent_service.read_json
            def stale_initial_read(path):
                if Path(path) == folder / 'job.json' and inspect.currentframe().f_back.f_code.co_name == 'execute':
                    return dict(prepared)
                return original_read(path)
            with patch.object(agent_service, 'read_json', side_effect=stale_initial_read):
                self.service.execute(prepared['job_id'])
        self.assertEqual(popen.call_count, 1, 'A stale concurrent execute request must not start a second worker')

    def test_cancel_and_resume_preserve_prior_result_and_run_record(self):
        folder, _, job = self.seeded_job(status='running')
        prior = {'deployment_success': False, 'task_verified': False, 'attempt_count': 1}
        self.service.save(folder, {'result': prior})
        record = folder / 'runs' / 'old.json'
        agent_service.write_json(record, {'attempts': [{'cmd': 'previous attempt'}]})
        cancelled = self.service.cancel(job['job_id'])
        self.assertEqual(cancelled['status'], 'cancelling')
        self.service.save(folder, {'status': 'cancelled'})
        with patch.object(self.service, 'launch') as launch:
            self.service.resume(job['job_id'])
            launch.assert_called_once()
            self.assertEqual(launch.call_args.args, (folder, 'resume'))
        self.assertEqual(agent_service.read_json(folder / 'job.json')['result'], prior)
        self.assertTrue(record.is_file())


if __name__ == '__main__':
    unittest.main()
