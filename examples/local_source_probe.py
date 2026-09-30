"""Bounded acquisition-adapter experiment; NOT a production local-source API.

Runs only its own synthetic Git fixture. Target commands and recovery use the
real Agent core, in serial in-process workers. No MCP/async acceptance is claimed.
"""
from __future__ import annotations
import argparse
from contextlib import redirect_stdout, redirect_stderr
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--candidate', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.candidate.resolve()))
    import agent_service as api

    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'synthetic-source'
    source.mkdir()
    (source / 'README.md').write_text('Synthetic finite local data task.\n', encoding='utf-8')
    (source / 'transform.py').write_text(
        'import json\nfrom pathlib import Path\n'
        "data = json.loads(Path('payload.json').read_text(encoding='utf-8'))\n"
        "if data['factor'] < 0:\n    raise SystemExit('factor must be nonnegative')\n"
        "Path('result.json').write_text(json.dumps({'total': sum(data['values']) * data['factor']}), encoding='utf-8')\n",
        encoding='utf-8')

    def git(path, *argv):
        return subprocess.run(['git', '-C', str(path), *argv], check=True,
                              capture_output=True, text=True, encoding='utf-8').stdout.strip()

    git(source, 'init', '-q')
    git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'add', 'README.md', 'transform.py')
    git(source, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
        'commit', '-qm', 'synthetic local source')
    revision = git(source, 'rev-parse', 'HEAD')
    acquisition_count = 0

    class LocalProbe(api.AgentService):
        # The worker is deliberately drained by the probe after each call.
        # This bypasses dispatch only; it does not bypass execution checks.
        def launch(self, folder, phase, expected_updated_at=None, *, locked=False):
            job = api.read_json(folder / 'job.json')
            job.update(status='queued', phase=phase, dispatcher_pid=os.getpid(),
                       attempt_id=uuid.uuid4().hex)
            job.pop('result', None)
            api.write_json(folder / 'job.json', job)
            return job

        def status(self, job_id, wait_seconds=0):
            return super().status(job_id, wait_seconds=0)

        def core(self, folder):
            app = super().core(folder)

            def fetch(owner, name):
                if (owner, name) != ('local', 'synthetic-source'):
                    raise ValueError('The experiment only accepts its synthetic fixture')
                return app.RepoInfo(owner=owner, name=name, full_name=f'{owner}/{name}',
                                    html_url=source.as_uri(), clone_url=str(source),
                                    readme='Synthetic finite local data task.')

            def clone(repo):
                nonlocal acquisition_count
                target = folder / 'checkouts' / 'synthetic-source'
                target.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(['git', 'clone', '--no-hardlinks', '--no-checkout', '--',
                                str(source), str(target)], check=True, capture_output=True)
                git(target, 'checkout', '--detach', revision)
                acquisition_count += 1
                return target

            app.fetch_repo_info = fetch
            app.clone_repo = clone
            return app

    service = LocalProbe(root / 'jobs-state')
    checks = [{'type': 'json_value', 'path': 'result.json', 'pointer': '/total', 'expected': 20}]
    with (root / 'worker.log').open('w', encoding='utf-8') as log, redirect_stdout(log), redirect_stderr(log):
        first = service.prepare('local/synthetic-source', revision=revision,
                                plan=api.command_plan(['python transform.py']), checks=checks,
                                files=[{'path': 'payload.json', 'content': '{"values":[2,3,5],"factor":-1}'}],
                                python_version='3.11', bounded_commands=True, auto_execute=True)
        job_id = first['job_id']
        service.worker(job_id, 'prepare')
        failed = service.status(job_id)
        folder = service.folder(job_id)
        checkout = Path(failed.get('project_path', '.'))
        if failed['status'] != 'failed' or not failed.get('result', {}).get('project_execution_started'):
            raise AssertionError(('Expected target-command failure', failed))
        runtime_before = failed['result']['python_runtime']['executable']
        service.replan(job_id, edits=[{'path': 'payload.json', 'old': '"factor":-1', 'new': '"factor":2'}],
                       commands=['python transform.py'], checks=checks, execute=True,
                       request_id='repair-factor')
        service.worker(job_id, 'replan')
        success = service.status(job_id)

    assertions = {
        'first_target_failure_retained': failed['status'] == 'failed',
        'recovered_output_verified': success['status'] == 'completed' and success['result']['task_verified'],
        'independent_output_value': json.loads((checkout / 'result.json').read_text(encoding='utf-8')) == {'total': 20},
        'source_revision_preserved': git(checkout, 'rev-parse', 'HEAD') == revision,
        'one_acquisition': acquisition_count == 1,
        'same_job_and_checkout': success['job_id'] == job_id and Path(success['project_path']) == checkout,
        'same_python_environment': success['result']['python_runtime']['executable'] == runtime_before,
        'both_attempts_retained': len(list((folder / 'runs').glob('*.json'))) == 2,
        'original_source_unchanged': not git(source, 'status', '--porcelain'),
        'no_artifacts_written_to_original': not (source / 'result.json').exists() and not (source / '.venv').exists(),
    }
    result = {'kind': 'local-source-acquisition-prototype', 'assertions': assertions,
              'passed': all(assertions.values()), 'acquisitions': acquisition_count,
              'model_calls': 0, 'network_acquisition': False,
              'boundary': 'Synthetic repository; serial in-process worker and acquisition adapter only. Production API remains GitHub-only. No MCP, async dispatch, arbitrary URL, private repository or nested Agent acceptance.',
              'candidate_commit': git(args.candidate, 'rev-parse', 'HEAD'), 'job_id': job_id,
              'revision': revision}
    (root / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
