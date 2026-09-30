"""Durable, non-interactive tool jobs around RepoWayfinder's execution core.

No model is invoked here. Repository text and process output remain untrusted
data. Protected execution is a set of checks, not an operating-system sandbox.
"""
from __future__ import annotations
from contextlib import contextmanager, nullcontext, redirect_stdout
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parent
ACTIVE = {'queued', 'preparing', 'running', 'cancelling'}
MAX_COMMANDS = 20
MAX_COMMAND_CHARS = 8000
MAX_CHECKS = 128
MAX_EXPECTED_CHARS = 4000
MAX_FILES = 32
MAX_FILE_PATH_CHARS = 240
MAX_INPUT_BYTES = 256 * 1024
MAX_REQUEST_ID_CHARS = 120


def utf8_bytes(value, location):
    try:
        return value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise ValueError(f'{location}: invalid Unicode at character {exc.start}; correct the text, no files were changed') from None


def command_plan(commands, timeout=300):
    if (not isinstance(commands, list) or not 1 <= len(commands) <= MAX_COMMANDS
            or any(not isinstance(cmd, str) or not cmd.strip() or len(cmd) > MAX_COMMAND_CHARS for cmd in commands)):
        raise ValueError(f'commands requires 1-{MAX_COMMANDS} nonempty strings, each at most {MAX_COMMAND_CHARS} characters')
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise ValueError('timeout must be 1-600 seconds')
    for index, cmd in enumerate(commands):
        utf8_bytes(cmd, f'commands[{index}]')
    return {'action': 'DEPLOY', 'reason': 'Execute caller commands and verify requested outputs',
            'steps': [{'type': 'exec', 'cmd': cmd, 'purpose': '', 'timeout': timeout} for cmd in commands]}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@contextmanager
def file_lock(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as handle:
        if path.stat().st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = wintypes.DWORD()
        try:
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def canonical_repo(value):
    if not isinstance(value, str):
        raise ValueError('repository must be owner/repo or a public GitHub HTTPS URL')
    value = value.strip().removesuffix('.git').rstrip('/')
    if value.startswith('https://github.com/'):
        value = value[len('https://github.com/'):]
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value):
        raise ValueError('Use one explicit GitHub owner/repo; search is a separate tool')
    return value


def scoped_file(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('check path must be relative to the target checkout')
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if root not in path.parents or any(part in {'.git', '..'} for part in Path(relative).parts):
        raise ValueError('check path escapes the project')
    if path.name.lower().startswith('.env'):
        raise ValueError('Do not use credential files as result checks')
    return path


def task_file_path(root, relative):
    """Input mutations never follow directory junctions or file aliases."""
    validate_files([{'path': relative, 'content': ''}])
    root = Path(root).resolve()
    candidate = root
    for part in relative.replace('\\', '/').split('/'):
        candidate = candidate / part
        if candidate.is_symlink() or (candidate.exists() and
                getattr(candidate.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Task input path contains a link or junction: ' + relative)
    return scoped_file(root, relative)


def validate_checks(checks):
    if not isinstance(checks, list) or len(checks) > MAX_CHECKS:
        raise ValueError(f'checks must contain at most {MAX_CHECKS} assertions')
    for check in checks:
        if not isinstance(check, dict) or check.get('type') not in {'file_exists', 'file_contains', 'stdout_contains', 'json_value'}:
            raise ValueError('Unknown result check type')
        if set(check) - {'type', 'path', 'expected', 'pointer', 'freshness'}:
            raise ValueError('Unknown result check field')
        if check.get('freshness', 'fresh') not in {'preserved', 'fresh'}:
            raise ValueError('freshness must be preserved or fresh')
        kind = check['type']
        if kind != 'stdout_contains':
            scoped_file(ROOT / 'validation-root', check.get('path'))
        if kind in {'file_contains', 'stdout_contains'} and (not isinstance(check.get('expected'), str) or not check['expected'] or len(check['expected']) > MAX_EXPECTED_CHARS):
            raise ValueError(f'contains checks need nonempty expected text up to {MAX_EXPECTED_CHARS} characters')
        expected = check.get('expected')
        if expected is not None and type(expected) not in (str, bool, int, float):
            raise ValueError('check.expected must be a JSON scalar')
        if isinstance(expected, str) and len(expected) > MAX_EXPECTED_CHARS:
            raise ValueError(f'check.expected exceeds {MAX_EXPECTED_CHARS} characters')
        if isinstance(check.get('expected'), str):
            utf8_bytes(check['expected'], 'check.expected')
        if kind == 'json_value' and ('expected' not in check or not isinstance(check.get('pointer', ''), str)):
            raise ValueError('json_value requires expected and an optional JSON pointer')
        if kind == 'json_value' and check.get('pointer') and not check['pointer'].startswith('/'):
            raise ValueError('JSON pointer must be empty or begin with /')
    return checks


def validate_files(files):
    if files is None:
        return []
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise ValueError(f'files must contain at most {MAX_FILES} UTF-8 task files')
    seen, total = set(), 0
    for item in files:
        if not isinstance(item, dict) or set(item) != {'path', 'content'} or not isinstance(item['content'], str):
            raise ValueError('Each task file requires path and text content only')
        name = item['path']
        if not isinstance(name, str) or not name or len(name) > MAX_FILE_PATH_CHARS:
            raise ValueError(f'Task file path must be a relative path up to {MAX_FILE_PATH_CHARS} characters')
        utf8_bytes(name, 'file.path')
        path = PureWindowsPath(name)
        parts = name.replace('\\', '/').split('/')
        if (path.drive or path.root or any(part in {'', '.', '..'} or part.endswith((' ', '.'))
                or ':' in part or part.lower() in {'.git', '.venv', 'venv', '__pycache__', 'node_modules'}
                or part.lower().startswith('.env') or PureWindowsPath(part).is_reserved() for part in parts)):
            raise ValueError('Task file path must stay inside the checkout and outside credential/runtime metadata')
        key = '/'.join(parts).casefold()
        if any(key == old or key.startswith(old + '/') or old.startswith(key + '/') for old in seen):
            raise ValueError('Task file paths overlap or duplicate one another')
        seen.add(key)
        total += len(utf8_bytes(item['content'], f'files[{name}].content'))
    if total > MAX_INPUT_BYTES:
        raise ValueError('Task files exceed 256 KiB; supply only task inputs, not bulk source')
    return files


def validate_edits(edits):
    if edits is None:
        return []
    if not isinstance(edits, list) or len(edits) > MAX_FILES:
        raise ValueError(f'edits must contain at most {MAX_FILES} exact replacements')
    total = 0
    for edit in edits:
        if (not isinstance(edit, dict) or set(edit) != {'path', 'old', 'new'}
                or not isinstance(edit['old'], str) or not edit['old'] or not isinstance(edit['new'], str)):
            raise ValueError('Each edit requires path, nonempty old text, and new text')
        validate_files([{'path': edit['path'], 'content': edit['new']}])
        total += len(utf8_bytes(edit['old'], f'edits[{edit["path"]}].old'))
        total += len(utf8_bytes(edit['new'], f'edits[{edit["path"]}].new'))
    if total > MAX_INPUT_BYTES:
        raise ValueError('Edit text exceeds 256 KiB; use a focused replacement')
    return edits


def stage_files(checkout, files):
    """Create caller inputs only; never overwrite repository or runtime files."""
    pending = []
    for item in validate_files(files):
        path = task_file_path(checkout, item['path'])
        if path.exists() or path.is_symlink():
            raise ValueError('Task file already exists; choose a new path: ' + item['path'])
        pending.append((path, item['content']))
    created = []
    try:
        for path, content in pending:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Re-resolve after directory creation to reject symlink/junction escapes.
            task_file_path(checkout, path.relative_to(checkout).as_posix())
            with path.open('x', encoding='utf-8', newline='\n') as output:
                created.append(path)
                output.write(content)
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def task_file_evidence(checkout, files):
    evidence = {}
    for item in files or []:
        path = scoped_file(checkout, item['path'].replace('\\', '/'))
        evidence[item['path']] = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else 'missing'
    return evidence


def file_stamp(path):
    if not path.is_file():
        return None
    st = path.stat()
    # copy2 and build tools preserve mtime. Include identity/creation-change
    # time to observe replacement without requiring bytes to differ.
    return [st.st_size, st.st_mtime_ns, st.st_ino, st.st_ctime_ns]


def file_digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def evaluate_checks(root, checks, attempts, before, verified_artifacts=None):
    stdout = '\n'.join(str(item.get('stdout', '')) for item in attempts
                       if item.get('command_success', item.get('returncode') == 0) and not item.get('timed_out'))
    results = []
    for index, check in enumerate(checks):
        item = {'type': check['type'], 'passed': False}
        if 'path' in check:
            item['path'] = check['path']
        try:
            if check['type'] == 'stdout_contains':
                item['passed'] = check['expected'] in stdout
            else:
                path = scoped_file(root, check['path'])
                current = file_stamp(path)
                item['fresh_output'] = current is not None and current != before.get(str(index))
                proof = (verified_artifacts or {}).get(check['path'].replace('\\', '/').casefold())
                preserved = check.get('freshness', 'fresh') == 'preserved'
                reused = (preserved and current is not None and proof is not None
                          and file_digest(path) == proof['sha256'])
                if reused:
                    item['verified_attempt'] = proof['attempt_id']
                if (preserved and not reused) or (not preserved and not item['fresh_output']):
                    item['reason'] = ('Preserved artifact is absent, changed, or has no prior verification in this job' if preserved else
                        'No current-run output. Use freshness=preserved only to intentionally retain an earlier verified artifact; unverified stale files cannot pass')
                elif check['type'] == 'file_exists':
                    item['passed'] = True
                elif path.stat().st_size > 20 * 1024 * 1024:
                    item['reason'] = 'Content assertion limit is 20 MiB; output preserved for explicit inspection'
                elif check['type'] == 'file_contains':
                    item['passed'] = check['expected'] in path.read_text(encoding='utf-8-sig')
                else:
                    value = read_json(path)
                    pointer = check.get('pointer', '')
                    if pointer and not pointer.startswith('/'):
                        raise ValueError('JSON pointer must be empty or begin with /')
                    for part in pointer.split('/')[1:]:
                        part = part.replace('~1', '/').replace('~0', '~')
                        value = value[int(part)] if isinstance(value, list) else value[part]
                    item['passed'] = type(value) is type(check['expected']) and value == check['expected']
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            item['reason'] = str(exc)[:500]
        results.append(item)
    return results


class Cancelled(Exception):
    pass


class AgentService:
    def __init__(self, workspace=None):
        self.workspace = Path(workspace or os.environ.get('REPOWAYFINDER_AGENT_HOME', ROOT / '.agent-data')).resolve()
        self.jobs = self.workspace / 'jobs'
        self.jobs.mkdir(parents=True, exist_ok=True)

    def folder(self, job_id):
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('Invalid job_id')
        path = self.jobs / job_id
        if not (path / 'job.json').is_file():
            raise ValueError('Unknown job_id')
        return path

    def dispatch(self, name, arguments):
        if not isinstance(arguments, dict):
            raise ValueError('arguments must be a JSON object')
        handlers = {'rw_verify': self.verify, 'rw_search': self.search, 'rw_prepare': self.prepare, 'rw_run': self.run,
                    'rw_replan': self.replan, 'rw_execute': self.execute,
                    'rw_status': self.status, 'rw_resume': self.resume, 'rw_logs': self.logs,
                    'rw_cancel': self.cancel}
        if name not in handlers:
            raise ValueError('Unknown tool: ' + str(name))
        return handlers[name](**arguments)

    def verify(self, directory, checks=None, run=None, unchanged=None, service=None,
               evidence_directory='.repowayfinder-checks'):
        """Finite local verification; does not create or resume a deployment job."""
        from verification import verify
        return verify(directory, checks=checks, run=run, unchanged=unchanged, service=service,
                      evidence_directory=evidence_directory)

    def summary(self, job):
        keys = ['job_id', 'attempt_id', 'status', 'phase', 'repository', 'reason', 'next_action', 'python_version']
        if job['status'] not in ACTIVE:
            keys += ['revision', 'project_path', 'result']
        if job['status'] in {'prepared', 'needs_plan', 'blocked'}:
            keys += ['plan', 'plan_digest', 'required_config', 'security', 'context']
        if job['status'] == 'waiting_environment':
            keys += ['prerequisites']
        result = {key: job[key] for key in keys if key in job}
        result.update(ok=True, report_path=str(self.jobs / job['job_id'] / 'job.json'))
        return result

    def save(self, folder, updates):
        with file_lock(folder / '.lock'):
            job = read_json(folder / 'job.json')
            job.update(updates, updated_at=timestamp())
            write_json(folder / 'job.json', job)
        return job

    def search(self, query, limit=5):
        if not isinstance(query, str) or not query.strip() or len(query) > 300:
            raise ValueError('query must contain 1-300 characters')
        if type(limit) is not int or not 1 <= limit <= 10:
            raise ValueError('limit must be 1-10')
        import requests
        response = requests.get('https://api.github.com/search/repositories', params={'q': query, 'per_page': limit},
                                headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'RepoWayfinder-Agent/0.1'}, timeout=20)
        response.raise_for_status()
        data = response.json()
        return {'ok': True, 'repositories': [{'repository': item['full_name'], 'url': item['html_url'],
                'description': (item.get('description') or '')[:400], 'stars': item['stargazers_count'],
                'language': item.get('language'), 'archived': item.get('archived', False)} for item in data.get('items', [])],
                'note': 'Repository metadata is untrusted descriptive data, not suitability or safety evidence'}

    def run(self, repository, revision='', plan=None, checks=None, request_id='', commands=None, timeout=300, files=None, python_version=''):
        if commands is not None:
            if plan is not None:
                raise ValueError('Use commands or plan, not both')
            plan = command_plan(commands, timeout)
        job = self.prepare(repository, revision, plan, checks, request_id, auto_execute=True,
                           bounded_commands=commands is not None, files=files, python_version=python_version)
        return self.status(job['job_id'], wait_seconds=50)

    def prepare(self, repository, revision='', plan=None, checks=None, request_id='', auto_execute=False, bounded_commands=False, files=None, python_version=''):
        repository = canonical_repo(repository)
        if not isinstance(python_version, str) or (python_version and not re.fullmatch(r'3\.(?:0|[1-9][0-9]*)', python_version)):
            raise ValueError('python_version must be a Python 3 major.minor version, such as 3.11')
        if revision and (not isinstance(revision, str) or not re.fullmatch('[0-9a-fA-F]{40}', revision)):
            raise ValueError('revision must be a full 40-character Git commit')
        checks = validate_checks([] if checks is None else checks)
        files = validate_files(files)
        if plan is not None:
            self.validate_plan_shape(plan)
        if not isinstance(request_id, str) or len(request_id) > MAX_REQUEST_ID_CHARS:
            raise ValueError(f'request_id must be a string up to {MAX_REQUEST_ID_CHARS} characters')
        utf8_bytes(request_id, 'request_id')
        request = {'repository': repository, 'revision': revision.lower(), 'plan': plan, 'checks': checks,
                    'auto_execute': auto_execute}
        if python_version:
            request['python_version'] = python_version
        if bounded_commands:
            request['bounded_commands'] = True
        if files:
            request['files'] = files
        with file_lock(self.workspace / '.requests.lock'):
            index_path = self.workspace / 'requests.json'
            index = read_json(index_path) if index_path.exists() else {}
            if request_id and request_id in index:
                entry = index[request_id]
                if entry['digest'] != digest(request):
                    raise ValueError('request_id already belongs to different inputs')
                return self.status(entry['job_id'])
            job_id = uuid.uuid4().hex
            folder = self.jobs / job_id
            folder.mkdir()
            write_json(folder / 'request.json', request)
            write_json(folder / 'job.json', {'job_id': job_id, 'status': 'queued', 'phase': 'prepare',
                       'dispatcher_pid': os.getpid(),
                       'repository': repository, 'python_version': python_version, 'created_at': timestamp(), 'updated_at': timestamp(),
                       'next_action': 'Use rw_status with wait_seconds=10; preparing does not execute target code'})
            if request_id:
                index[request_id] = {'job_id': job_id, 'digest': digest(request)}
                write_json(index_path, index)
            self.launch(folder, 'prepare')
        return self.status(job_id)

    def replan(self, job_id, plan=None, checks=None, commands=None, timeout=300,
               files=None, edits=None, execute=False, request_id=''):
        if type(execute) is not bool:
            raise ValueError('execute must be a boolean')
        if commands is not None:
            if plan is not None:
                raise ValueError('Use commands or plan, not both')
            plan = command_plan(commands, timeout)
        if plan is not None:
            self.validate_plan_shape(plan)
        if checks is not None:
            validate_checks(checks)
        files, edits = validate_files(files), validate_edits(edits)
        if not isinstance(request_id, str) or len(request_id) > MAX_REQUEST_ID_CHARS:
            raise ValueError(f'request_id must be a string up to {MAX_REQUEST_ID_CHARS} characters')
        utf8_bytes(request_id, 'request_id')
        operation_digest = digest({'plan': plan, 'commands': commands, 'checks': checks,
                                   'files': files, 'edits': edits, 'execute': execute})
        folder = self.folder(job_id)
        with file_lock(folder / '.lock'):
            job = read_json(folder / 'job.json')
            index_path = folder / 'recovery-requests.json'
            index = read_json(index_path) if index_path.exists() else {}
            if request_id and request_id in index:
                if index[request_id]['digest'] != operation_digest:
                    raise ValueError('Recovery request_id already belongs to different inputs')
                # Replays observe the existing attempt instead of reapplying a
                # patch or starting another process, including while it runs.
                return self.summary(job)
            if job['status'] in ACTIVE or not job.get('project_path'):
                raise ValueError('Replan requires a stopped job with an existing checkout')
            if job.get('phase') == 'replan-inputs':
                raise ValueError('Interrupted input changes need reconciliation through rw_status before replanning')
            request = read_json(folder / 'request.json')
            if plan is None and request.get('plan') is None:
                raise ValueError('This job has no saved plan; supply commands')
            checkout = Path(job['project_path']).resolve()
            if self.git(checkout, 'rev-parse', 'HEAD').lower() != job['revision']:
                raise ValueError('Source revision changed; preserve this job and prepare a new one')
            originals, revised = {}, {}
            for edit in edits:
                path = task_file_path(checkout, edit['path'])
                relative = path.relative_to(checkout).as_posix()
                validate_files([{'path': relative, 'content': ''}])
                if not path.is_file() or path.is_symlink() or path.stat().st_nlink > 1:
                    raise ValueError('Edit requires one existing, unshared task file: ' + edit['path'])
                if self.git(checkout, 'ls-files', '--', relative):
                    raise ValueError('Tracked source cannot be edited through task recovery: ' + relative)
                if path not in originals:
                    if path.stat().st_size > MAX_INPUT_BYTES:
                        raise ValueError('Edit target exceeds 256 KiB: ' + relative)
                    originals[path] = path.read_bytes()
                    revised[path] = originals[path].decode('utf-8')
                if revised[path].count(edit['old']) != 1:
                    raise ValueError(f'Edit old text must match exactly once in {relative}; no files were changed')
                revised[path] = revised[path].replace(edit['old'], edit['new'], 1)
            changed = [{'path': path.relative_to(checkout).as_posix(), 'content': content}
                       for path, content in revised.items()]
            validate_files([*files, *changed])
            for item in files:
                if task_file_path(checkout, item['path']).exists():
                    raise ValueError('Task file already exists; use an exact edit: ' + item['path'])
            updated = dict(request)
            if plan is not None:
                updated['plan'] = plan
            updated['auto_execute'] = execute
            if commands is not None:
                updated['bounded_commands'] = True
            elif plan is not None:
                updated.pop('bounded_commands', None)
            if checks is not None:
                updated['checks'] = checks
            inputs = {item['path'].replace('\\', '/').casefold(): item for item in request.get('files', [])}
            inputs.update({item['path'].replace('\\', '/').casefold(): item for item in [*files, *changed]})
            if inputs:
                updated['files'] = list(inputs.values())
            updated['revision'] = job['revision']
            history_path = folder / 'history' / (uuid.uuid4().hex + '.json')
            snapshot = {'job': job, 'request': request, 'edits': edits,
                        'files_before': {path.relative_to(checkout).as_posix(): data.decode('utf-8')
                                         for path, data in originals.items()},
                        'files_after': {item['path']: item['content'] for item in changed},
                        'new_contents': {item['path']: item['content'] for item in files},
                        'new_files': [item['path'] for item in files]}
            for name in ('plan-evidence.json', 'task-file-evidence.json'):
                if (folder / name).is_file():
                    snapshot[name] = read_json(folder / name)
            write_json(history_path, snapshot)
            # Publish an active state before input changes. A process crash is
            # detectable, and the snapshot retains the exact pre-edit contents.
            write_json(folder / 'job.json', {**job, 'status': 'preparing', 'phase': 'replan-inputs',
                       'worker_pid': os.getpid(), 'updated_at': timestamp(), 'recovery_snapshot': str(history_path)})
            staged = False
            try:
                stage_files(checkout, files)
                staged = True
                for path, content in revised.items():
                    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
                    try:
                        temp.write_bytes(utf8_bytes(content, f'edits[{path.name}]'))
                        os.replace(temp, path)
                    finally:
                        temp.unlink(missing_ok=True)
                write_json(folder / 'request.json', updated)
                next_job = {**job, 'status': 'queued', 'phase': 'replan', 'updated_at': timestamp(),
                            'dispatcher_pid': os.getpid(),
                            'next_action': 'Revalidating the revised inputs in this checkout; use rw_status'}
                for key in ('result', 'worker_pid', 'auto_execute_ready'):
                    next_job.pop(key, None)
                write_json(folder / 'job.json', next_job)
                if request_id:
                    index[request_id] = {'digest': operation_digest, 'snapshot': str(history_path)}
                    write_json(index_path, index)
            except Exception:
                for path, data in originals.items():
                    path.write_bytes(data)
                if staged:
                    for item in files:
                        scoped_file(checkout, item['path']).unlink(missing_ok=True)
                write_json(folder / 'request.json', request)
                write_json(folder / 'job.json', job)
                raise
            # Dispatch while still holding the mutation lock. status/execute
            # cannot observe a queued operation without its owner.
            self.launch(folder, 'replan', locked=True)
        return self.status(job_id, wait_seconds=50 if execute else 15)

    @staticmethod
    def validate_plan_shape(plan):
        if not isinstance(plan, dict) or plan.get('action') != 'DEPLOY':
            raise ValueError('External plan requires action DEPLOY')
        if set(plan) - {'action', 'steps', 'reason'}:
            raise ValueError('Unknown external plan field')
        if not isinstance(plan.get('steps'), list) or not 1 <= len(plan['steps']) <= MAX_COMMANDS:
            raise ValueError(f'Plan requires 1-{MAX_COMMANDS} steps')
        for step in plan['steps']:
            if not isinstance(step, dict) or set(step) - {'type', 'cmd', 'purpose', 'timeout'}:
                raise ValueError('Invalid plan step')
            if not isinstance(step.get('cmd'), str) or not step['cmd'].strip() or len(step['cmd']) > MAX_COMMAND_CHARS:
                raise ValueError(f'Each step requires a command up to {MAX_COMMAND_CHARS} characters')
            utf8_bytes(step['cmd'], 'plan.steps.cmd')
            if step.get('type', 'exec') not in {'exec', 'shell'}:
                raise ValueError('Invalid step type')
            if type(step.get('timeout', 120)) is not int or not 1 <= step.get('timeout', 120) <= 600:
                raise ValueError('Step timeout must be 1-600 seconds')

    def launch(self, folder, phase, expected_updated_at=None, *, locked=False):
        with nullcontext() if locked else file_lock(folder / '.lock'):
            job = read_json(folder / 'job.json')
            if expected_updated_at is not None and job.get('updated_at') != expected_updated_at:
                return job
            if phase == 'execute' and job['status'] != 'prepared':
                return job
            if phase == 'resume' and job['status'] not in {'waiting_environment', 'failed', 'interrupted', 'cancelled'}:
                return job
            if job.get('worker_pid') and process_alive(job['worker_pid']) and job['status'] in ACTIVE:
                return job
            (folder / 'cancel.requested').unlink(missing_ok=True)
            job.update(status='queued', phase=phase, updated_at=timestamp(),
                       dispatcher_pid=os.getpid(),
                       next_action='Use rw_status with wait_seconds=10; do not resubmit this job')
            token = uuid.uuid4().hex
            for key in ('worker_pid', 'dispatch_token', 'result', 'reason'):
                job.pop(key, None)
            job['attempt_id'] = token
            write_json(folder / 'job.json', job)
            try:
                with (folder / 'worker.log').open('ab') as log:
                    process = subprocess.Popen([sys.executable, '-u', str(ROOT / 'agent.py'), '--workspace', str(self.workspace),
                                '_worker', job['job_id'], phase, '--token', token], cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            except Exception as exc:
                job.update(status='interrupted', reason='Worker could not start: ' + str(exc),
                           next_action='No target process started; retry the saved plan after resolving the launch error')
                write_json(folder / 'job.json', job)
                raise
            job['worker_pid'] = process.pid
            job['dispatch_token'] = token
            write_json(folder / 'job.json', job)
            return job

    def execute(self, job_id):
        folder = self.folder(job_id)
        job = read_json(folder / 'job.json')
        if job['status'] in {'queued', 'preparing', 'running', 'cancelling', 'completed'}:
            return self.status(job_id)
        if job['status'] != 'prepared':
            raise ValueError('Execute requires a prepared plan; use status.next_action')
        self.launch(folder, 'execute', expected_updated_at=job.get('updated_at'))
        return self.status(job_id)

    def resume(self, job_id):
        folder = self.folder(job_id)
        job = read_json(folder / 'job.json')
        if job.get('phase') == 'replan-inputs':
            raise ValueError('Reconcile interrupted input changes through rw_status before resuming')
        if job['status'] in ACTIVE or job['status'] == 'completed':
            return self.status(job_id)
        if job['status'] not in {'waiting_environment', 'failed', 'interrupted', 'cancelled'} or not job.get('plan'):
            raise ValueError('This job cannot be resumed; prepare a new plan')
        self.launch(folder, 'resume', expected_updated_at=job.get('updated_at'))
        return self.status(job_id)

    def status(self, job_id, wait_seconds=0):
        if type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 50:
            raise ValueError('wait_seconds must be 0-50')
        folder = self.folder(job_id)
        deadline = time.monotonic() + wait_seconds
        while True:
            with file_lock(folder / '.lock'):
                job = read_json(folder / 'job.json')
                owner = job.get('worker_pid') or job.get('dispatcher_pid')
                if (job['status'] in ACTIVE or job['phase'] == 'replan-inputs') and owner and not process_alive(owner):
                    if job['phase'] == 'replan-inputs':
                        snapshot = read_json(job['recovery_snapshot'])
                        checkout = Path(job['project_path'])
                        conflict = None
                        # Preflight the entire rollback. Never overwrite edits
                        # made by another actor after the worker died.
                        for relative, content in snapshot['files_before'].items():
                            current = task_file_path(checkout, relative)
                            allowed = {content.encode('utf-8'), snapshot['files_after'][relative].encode('utf-8')}
                            if not current.is_file() or current.read_bytes() not in allowed:
                                conflict = relative
                        for relative, content in snapshot['new_contents'].items():
                            current = task_file_path(checkout, relative)
                            if current.exists() and (not current.is_file() or current.read_bytes() != content.encode('utf-8')):
                                conflict = relative
                        if conflict:
                            job.update(status='interrupted', reason='Input rollback found an external change: ' + conflict,
                                       next_action='Reconcile with recovery_snapshot before retrying rw_status; no files were rolled back',
                                       updated_at=timestamp())
                            write_json(folder / 'job.json', job)
                            return self.summary(job)
                        for relative, content in snapshot['files_before'].items():
                            task_file_path(checkout, relative).write_bytes(content.encode('utf-8'))
                        for relative in snapshot['new_files']:
                            task_file_path(checkout, relative).unlink(missing_ok=True)
                        write_json(folder / 'request.json', snapshot['request'])
                        job = snapshot['job']
                        reason = 'Interrupted input revision rolled back; no target execution was launched'
                    else:
                        reason = 'Worker exited before a terminal result was saved; command effects may be partial'
                    job.update(status='interrupted', reason=reason, updated_at=timestamp(),
                               next_action='Inspect evidence and reconcile effects, then explicitly replan or resume')
                    write_json(folder / 'job.json', job)
            if job['status'] not in ACTIVE or time.monotonic() >= deadline:
                return self.summary(job)
            time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))

    def logs(self, job_id, max_chars=2000):
        if type(max_chars) is not int or not 1 <= max_chars <= 6000:
            raise ValueError('max_chars must be 1-6000')
        folder = self.folder(job_id)
        path = folder / 'worker.log'
        with path.open('rb') as handle:
            handle.seek(max(0, path.stat().st_size - max_chars * 4))
            tail = handle.read().decode('utf-8', errors='replace')[-max_chars:]
        return {'ok': True, 'job_id': job_id, 'untrusted_log_tail': tail, 'log_path': str(path)}

    def cancel(self, job_id):
        folder = self.folder(job_id)
        with file_lock(folder / '.lock'):
            job = read_json(folder / 'job.json')
            if job['status'] in ACTIVE:
                (folder / 'cancel.requested').touch()
                job.update(status='cancelling', next_action='Cancellation is cooperative; the current bounded command may finish before the next step is stopped')
            elif job['status'] == 'prepared':
                job.update(status='cancelled', reason='Prepared job cancelled before target execution',
                           next_action='rw_resume explicitly starts the saved plan if this work is wanted again')
            job['updated_at'] = timestamp()
            write_json(folder / 'job.json', job)
        return self.summary(job)

    def core(self, folder):
        os.environ['REPOSCOUT_NONINTERACTIVE'] = '1'
        os.environ['REPOSCOUT_SKIP_TARGET_CONFIG'] = '1'
        os.environ['REPOSCOUT_UI_LANGUAGE'] = 'en'
        import main as app
        app.PROJECT_DIR = ROOT
        app.BASE_DIR = folder / 'checkouts'
        app.REPORTS_DIR = self.jobs
        app.ARTIFACT_DIR = folder
        app.REPORT_PATH = folder / 'deployment_result.json'
        app.SETTINGS_PATH = self.workspace / 'settings.json'
        app.HISTORY_PATH = self.workspace / 'history.json'
        app.OWNED_TARGETS_PATH = folder / 'owned-projects.json'
        app.PREREQUISITE_STATE_PATH = folder / 'prerequisites.json'
        app.AI_API_KEY = app.OPENROUTER_API_KEY = ''
        app.AI_MODEL = app.AI_BASE_URL_OVERRIDE = ''
        app.ACTIVE_DEPLOYMENT_MODE = 'protected'
        app.UI_LANGUAGE = 'en'
        # Execute only already available prerequisites. The noninteractive core
        # reports missing prerequisites; it never elevates or starts an installer.
        original_run = app.run_process
        original_runtime = app.run_runtime_step
        def cancellation():
            if (folder / 'cancel.requested').exists():
                raise Cancelled('Cancellation requested; no further plan steps were started')
        def run(*args, **kwargs):
            cancellation()
            value = original_run(*args, **kwargs)
            cancellation()
            return value
        def runtime(*args, **kwargs):
            cancellation()
            value = original_runtime(*args, **kwargs)
            cancellation()
            return value
        app.run_process = run
        app.run_runtime_step = runtime
        return app

    def git(self, root, *args):
        result = subprocess.run(['git', '-C', str(root), *args], capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=45)
        if result.returncode:
            raise ValueError('Git operation failed: ' + result.stderr[-1200:])
        return result.stdout.strip()

    def worker(self, job_id, phase, *, require_ownership=False, token=None):
        folder = self.folder(job_id)
        if require_ownership:
            # Parent publishes our operation token under this lock before
            # releasing us. An orphan from an uncommitted spawn does no work.
            with file_lock(folder / '.lock'):
                job = read_json(folder / 'job.json')
                if not token or job.get('dispatch_token') != token or job.get('phase') != phase:
                    return 2
                # Windows venv launchers may spawn a second Python process;
                # authenticate the operation token, then publish the actual PID.
                job['worker_pid'] = os.getpid()
                job.pop('dispatch_token', None)
                write_json(folder / 'job.json', job)
        try:
            self.save(folder, {'status': 'preparing' if phase in {'prepare', 'replan'} else 'running', 'phase': phase,
                               'next_action': 'Use rw_status with wait_seconds=10; logs are available separately'})
            app = self.core(folder)
            if read_json(folder / 'request.json').get('bounded_commands'):
                # An explicit finite command must not be reclassified as a
                # server because its name contains "main.py", "demo" or "start".
                app.is_runtime_step = lambda step: False
            with redirect_stdout(sys.stderr):
                if phase in {'prepare', 'replan'}:
                    self.prepare_work(folder, app, reuse=phase == 'replan')
                    if (folder / 'cancel.requested').exists():
                        raise Cancelled('Cancellation requested during preparation; target execution was not started')
                    current = read_json(folder / 'job.json')
                    if (current['status'] == 'prepared' or current.get('auto_execute_ready')) and read_json(folder / 'request.json').get('auto_execute'):
                        self.save(folder, {'status': 'running', 'phase': 'execute', 'auto_execute_ready': False,
                                           'next_action': 'Use rw_status with wait_seconds=30'})
                        self.execute_work(folder, app, 'execute')
                else:
                    self.execute_work(folder, app, phase)
            return 0
        except Cancelled as exc:
            self.save(folder, {'status': 'cancelled', 'reason': str(exc), 'next_action': 'Inspect effects; rw_resume explicitly reruns the saved plan'})
            return 2
        except Exception as exc:
            if type(exc).__name__ == 'PythonEnvironmentError':
                self.save(folder, {'status': 'waiting_environment', 'reason': str(exc),
                                   'prerequisites': [getattr(exc, 'evidence', {})],
                                   'result': {'deployment_success': False, 'task_verified': False,
                                              'project_execution_started': False},
                                   'next_action': 'Resolve the Python environment issue, then explicitly call rw_resume'})
                return 2
            self.save(folder, {'status': 'failed', 'reason': str(exc)[:2000],
                               'next_action': 'Inspect rw_logs; repair the cause or prepare a revised plan'})
            import traceback
            traceback.print_exc()
            return 1

    def prepare_work(self, folder, app, reuse=False):
        request = read_json(folder / 'request.json')
        owner, name = request['repository'].split('/')
        if reuse:
            repo = app.RepoInfo(**read_json(folder / 'repository.json'))
            checkout = Path(read_json(folder / 'job.json')['project_path'])
        else:
            repo = app.fetch_repo_info(owner, name)
            write_json(folder / 'repository.json', asdict(repo))
            checkout = app.clone_repo(repo)
        if not (checkout / '.git').is_dir():
            raise ValueError('AI edition requires a Git checkout for exact revision validation; ZIP fallback is not executable here')
        revision = request['revision']
        if revision and self.git(checkout, 'rev-parse', 'HEAD').lower() != revision:
            if reuse:
                raise ValueError('Source revision changed; prepare a new checkout')
            self.git(checkout, 'fetch', '--depth', '1', 'origin', revision)
            self.git(checkout, 'checkout', '--detach', revision)
        revision = self.git(checkout, 'rev-parse', 'HEAD').lower()
        if self.git(checkout, 'status', '--porcelain', '--untracked-files=no'):
            raise ValueError('Prepared source has tracked modifications')
        # Preserve acquisition even when plan validation fails, so an Agent can
        # correct its plan without another clone or rediscovering the checkout.
        self.save(folder, {'project_path': str(checkout), 'revision': revision})
        if not reuse:
            stage_files(checkout, request.get('files'))
        from agent_runtime import configure_project_runtime
        configure_project_runtime(app, checkout, request.get('python_version', ''))
        plan = (app.normalize_plan(request['plan'], source='agent_explicit') if request['plan'] is not None
                else app.local_heuristic_plan(repo, checkout))
        # External commands use argv execution; shell pipelines/chaining are not
        # part of this interface even if the legacy UI accepted a shell step.
        for step in plan.steps:
            if app.contains_unquoted_shell_operator(step.cmd):
                raise ValueError('Use separate commands without shell operators')
            step.type = 'exec'
            step.timeout = min(600, max(1, step.timeout))
        valid, reason = app.validate_plan(plan)
        if not valid:
            raise ValueError('Plan rejected: ' + reason)
        security = app.review_repository_security(checkout, plan, 'protected')
        planned = app.plan_to_dict(plan)
        evidence = app.capture_plan_evidence(checkout, plan)
        write_json(folder / 'plan-evidence.json', evidence)
        write_json(folder / 'task-file-evidence.json', task_file_evidence(checkout, request.get('files')))
        status = 'blocked' if security.get('blocked') else 'prepared' if plan.action == 'DEPLOY' else 'needs_plan'
        updates = {'status': status, 'project_path': str(checkout), 'revision': revision, 'plan': planned,
                   'plan_digest': digest(planned), 'required_config': {'declared_names': app.detect_required_config(checkout),
                   'note': 'Repository declarations; not all are required by the selected route. Do not request credentials without route evidence.'},
                   'security': {'blocked': bool(security.get('blocked')), 'findings': security.get('findings', [])[:12]},
                   'reason': plan.reason, 'next_action': 'Review this plan and call rw_execute to run it' if status == 'prepared' else
                   'Review repository evidence and call rw_prepare with an explicit plan; blocked plans must be corrected, not bypassed'}
        if status == 'prepared' and request.get('auto_execute'):
            updates.update(status='preparing', auto_execute_ready=True,
                           next_action='This job will execute automatically; use rw_status, not rw_execute')
        if status == 'needs_plan':
            updates['context'] = {'untrusted_readme_excerpt': repo.readme[:3500], 'structure': app.scan_repo(checkout)[:2000]}
        self.save(folder, updates)

    def execute_work(self, folder, app, phase):
        job = read_json(folder / 'job.json')
        request = read_json(folder / 'request.json')
        checkout = Path(job['project_path'])
        if self.git(checkout, 'rev-parse', 'HEAD').lower() != job['revision']:
            raise ValueError('Source revision changed; prepare a new job')
        if self.git(checkout, 'status', '--porcelain', '--untracked-files=no'):
            raise ValueError('Tracked source changed after preparation; prepare a new job')
        if digest(job['plan']) != job['plan_digest']:
            raise ValueError('Saved plan changed; prepare a new job')
        plan = app.normalize_plan(job['plan'], source=job['plan'].get('source', 'agent'))
        from agent_runtime import configure_project_runtime
        configure_project_runtime(app, checkout, request.get('python_version', ''))
        if app.capture_plan_evidence(checkout, plan) != read_json(folder / 'plan-evidence.json'):
            raise ValueError('Planning inputs changed after preparation; prepare a new job')
        if request.get('files') and task_file_evidence(checkout, request['files']) != read_json(folder / 'task-file-evidence.json'):
            raise ValueError('Staged task inputs changed after preparation; replan before execution')
        security = app.review_repository_security(checkout, plan, 'protected')
        if security.get('blocked'):
            self.save(folder, {'status': 'blocked', 'reason': 'Security review blocked execution',
                              'security': security, 'next_action': 'Prepare a corrected plan; no bypass is exposed'})
            return
        ready, evidence, reason = app.ensure_plan_prerequisites(plan, checkout)
        if not ready:
            self.save(folder, {'status': 'waiting_environment', 'reason': reason, 'prerequisites': evidence,
                              'result': {'deployment_success': False, 'task_verified': False, 'project_execution_started': False},
                              'next_action': 'Prepare the named environment through the user/host, then call rw_resume'})
            return
        before = {str(i): file_stamp(scoped_file(checkout, check['path']))
                  for i, check in enumerate(request['checks']) if check['type'] != 'stdout_contains'}
        started = time.monotonic()
        success, attempts, repairs, runtime = app.execute_plan(checkout, plan)
        python_runtime = None
        python = checkout / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if python.is_file() and (checkout / '.venv' / 'pyvenv.cfg').is_file():
            if hasattr(app, 'python_version_tuple'):
                version = app.python_version_tuple(python)
                config = checkout / '.venv' / 'pyvenv.cfg'
                values = {}
                if config.is_file():
                    values = {key.strip(): value.strip() for key, value in
                              (line.split('=', 1) for line in config.read_text(encoding='utf-8').splitlines() if '=' in line)}
                python_runtime = {'executable': str(python),
                                  'version': '.'.join(map(str, version)) if version else '',
                                  'base': values.get('executable', values.get('home', '')).strip()}
        recorded = []
        for attempt in attempts:
            item = asdict(attempt)
            if hasattr(app, 'command_succeeded'):
                from types import SimpleNamespace
                item['command_success'] = app.command_succeeded(
                    attempt, SimpleNamespace(cmd=attempt.planned_cmd, purpose='', timeout=600))
            recorded.append(item)
        attempts = recorded
        artifacts_path = folder / 'verified-artifacts.json'
        verified_artifacts = read_json(artifacts_path) if artifacts_path.exists() else {}
        checks = evaluate_checks(checkout, request['checks'], attempts, before, verified_artifacts)
        deployed = bool(success and runtime.success is not False)
        task_verified = bool(deployed and checks and all(check['passed'] for check in checks))
        report = {'repository': job['repository'], 'revision': job['revision'], 'plan': job['plan'], 'phase': phase,
                  'attempt_id': job.get('attempt_id'),
                  'python_version': request.get('python_version', ''), 'python_runtime': python_runtime,
                  'deployment_success': deployed, 'task_verified': task_verified, 'project_execution_started': True,
                  'runtime_check': asdict(runtime), 'checks': checks, 'attempts': attempts, 'repairs': repairs,
                  'environment_changes': list(app.ENVIRONMENT_CHANGES),
                  'duration_seconds': round(time.monotonic() - started, 3), 'finished_at': timestamp()}
        history = folder / 'runs'
        run_id = uuid.uuid4().hex
        artifacts = {}
        if deployed:
            for check in checks:
                if check['passed'] and check.get('path') and check['type'] != 'stdout_contains':
                    relative = check['path'].replace('\\', '/').casefold()
                    if relative not in artifacts:
                        artifacts[relative] = (verified_artifacts[relative] if check.get('verified_attempt') else
                            {'sha256': file_digest(scoped_file(checkout, check['path'])),
                             'attempt_id': job.get('attempt_id') or run_id, 'run_id': run_id})
        report['verified_artifacts'] = artifacts
        write_json(history / (run_id + '.json'), report)
        write_json(folder / 'deployment_result.json', report)
        if artifacts:
            write_json(artifacts_path, {**verified_artifacts, **artifacts})
        outcome = 'task_verified' if task_verified else 'runtime_verified' if deployed and runtime.success else 'command_verified' if deployed else 'failed'
        failed_checks = bool(checks and not all(check['passed'] for check in checks))
        result = {key: report[key] for key in ('deployment_success', 'task_verified', 'project_execution_started', 'runtime_check', 'checks', 'duration_seconds')}
        if python_runtime is not None:
            result['python_runtime'] = python_runtime
        result.update(outcome=outcome, attempt_count=len(attempts), repair_count=len(repairs),
                      attempt_id=job.get('attempt_id'),
                      evidence_path=str(history / (run_id + '.json')),
                      note='task_verified covers only the caller-supplied assertions. attempt_count counts command executions, not retries. Validation servers are stopped after probing.')
        failures = [item for item in attempts if not item.get('command_success', item.get('returncode') in (0, None))]
        if failures and not deployed:
            last = failures[-1]
            result['failure'] = {'command': last.get('planned_cmd'), 'returncode': last.get('returncode'),
                                 'stderr_tail': str(last.get('stderr', ''))[-1500:], 'stdout_tail': str(last.get('stdout', ''))[-1000:]}
        if not deployed:
            result['completed_commands'] = [item.get('planned_cmd') for item in attempts
                                             if item.get('command_success', item.get('returncode') == 0)]
        self.save(folder, {'status': 'completed' if deployed and not failed_checks else 'failed', 'result': result,
                          'reason': 'Execution and requested checks finished' if deployed and not failed_checks else 'Execution or requested result checks failed',
                          'next_action': 'Continue in project_path using the existing runtime; for Python use result.python_runtime.executable when present. Keep native tools for business code and compact checks. rw_verify can batch local HTTP start/request/restart/cleanup without a lifecycle script; keep its evidence inside the task output boundary. Complete remaining user requirements; result.outcome covers only submitted commands/checks.' if deployed and not failed_checks else
                          'Reuse this job with rw_replan: exact edits, only affected commands, execute=true; do not clone/install again unless necessary'})
