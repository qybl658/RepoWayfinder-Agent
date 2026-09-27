"""A finite Agent command executes through the real worker and core."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import agent_service


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_PYTHON = ROOT / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')


class FiniteCommandExecutionTests(unittest.TestCase):
    def test_main_py_finishes_as_command_and_verifies_fresh_json(self):
        with tempfile.TemporaryDirectory(prefix='agent-finite-') as temporary:
            root = Path(temporary)
            checkout = root / 'checkout'
            checkout.mkdir()
            script = checkout / 'main.py'
            script.write_text(
                'from pathlib import Path\n'
                'import json\n'
                "Path('result.json').write_text(json.dumps({'answer': 42}), encoding='utf-8')\n",
                encoding='utf-8',
            )
            def git(*args):
                return subprocess.run(
                    ['git', '-C', str(checkout), *args], check=True, capture_output=True,
                    text=True, encoding='utf-8', timeout=15,
                ).stdout.strip()

            git('init', '-q')
            git('config', 'user.name', 'Agent Test')
            git('config', 'user.email', 'agent@example.invalid')
            git('add', 'main.py')
            git('commit', '-qm', 'finite command fixture')
            revision = git('rev-parse', 'HEAD').lower()

            service = agent_service.AgentService(root / 'state')
            job_id = 'a' * 32
            folder = service.jobs / job_id
            folder.mkdir()
            plan = {
                'action': 'DEPLOY',
                'steps': [{'type': 'exec', 'cmd': 'python main.py', 'timeout': 30}],
                'reason': 'Write a finite JSON result',
                'source': 'agent_explicit',
            }
            check = {'type': 'json_value', 'path': 'result.json', 'pointer': '/answer', 'expected': 42}
            agent_service.write_json(folder / 'job.json', {
                'job_id': job_id, 'status': 'prepared', 'phase': 'prepare',
                'repository': 'example/finite-command', 'project_path': str(checkout),
                'revision': revision, 'plan': plan, 'plan_digest': agent_service.digest(plan),
            })
            agent_service.write_json(folder / 'request.json', {
                'repository': 'example/finite-command', 'revision': revision,
                'checks': [check], 'bounded_commands': True,
            })
            import main as core
            evidence = core.capture_plan_evidence(checkout, core.normalize_plan(plan, source='agent_explicit'))
            agent_service.write_json(folder / 'plan-evidence.json', evidence)

            service.launch(folder, 'execute')
            service.status(job_id, wait_seconds=50)
            saved = agent_service.read_json(folder / 'job.json')
            report = agent_service.read_json(folder / 'deployment_result.json') if (folder / 'deployment_result.json').exists() else {}
            self.assertEqual(saved['status'], 'completed', (folder / 'worker.log').read_text(encoding='utf-8')[-3000:])
            self.assertTrue(saved['result']['task_verified'], report)
            self.assertEqual(report['checks'][0]['type'], 'json_value')
            self.assertTrue(report['checks'][0]['passed'])
            self.assertTrue(report['checks'][0]['fresh_output'])
            self.assertEqual(report['runtime_check']['kind'], 'not_applicable')
            self.assertEqual(report['attempts'][0]['returncode'], 0)
            self.assertEqual(json.loads((checkout / 'result.json').read_text(encoding='utf-8')), {'answer': 42})


if __name__ == '__main__':
    unittest.main()
