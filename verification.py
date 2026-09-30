"""Finite verification of an existing task directory; no acquisition or setup.

Checks inspect current artifacts, not job freshness. Explicit commands and HTTP
requests can have effects: this module is not an operating-system sandbox.
"""
from __future__ import annotations

from contextlib import redirect_stdout
from http.client import HTTPConnection, HTTPException
from http.cookiejar import CookieJar
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPCookieProcessor, ProxyHandler, Request, build_opener
import uuid

from agent_service import scoped_file, task_file_path, write_json
import artifact_checks

MAX_BODY = 20 * 1024 * 1024
_RUN_LOCK = threading.Lock()


class VerificationFailure(Exception):
    def __init__(self, message, **detail):
        super().__init__(message)
        self.detail = {'reason': message, **detail}


def _fields(value, allowed, required, where):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ValueError(f'{where}: invalid or missing fields')


def _timeout(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0.1 <= value <= 600:
        raise ValueError('timeout_seconds must be between 0.1 and 600')
    return float(value)


def _argv(value, root):
    if not isinstance(value, list) or not 1 <= len(value) <= 128 or any(
        not isinstance(item, str) or not item or len(item) > 8000 or '\0' in item for item in value
    ):
        raise ValueError('argv must be a nonempty array of nonempty strings')
    for item in value:
        try:
            item.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('argv contains invalid Unicode') from None
    result = list(value)
    interpreter = None
    if result[0].lower() in {'python', 'python.exe', 'python3', 'py'}:
        candidate = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if candidate.is_file():
            if root not in candidate.resolve().parents:
                raise ValueError('Task virtual environment resolves outside the task directory')
            interpreter = str(candidate)
        else:
            interpreter = sys.executable
        result[0] = interpreter
    elif Path(result[0]).name.lower().startswith('python'):
        interpreter = result[0]
    return result, interpreter


def _http_path(value):
    if not isinstance(value, str) or not value.startswith('/') or value.startswith('//'):
        raise ValueError('HTTP path must start with one / and stay on the loopback origin')
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.fragment or '\\' in value or any(ord(c) < 32 for c in value):
        raise ValueError('HTTP path must be relative to the loopback origin without fragment/control characters')
    # Validate the URL encoding before creating evidence or starting a process.
    try:
        value.encode('utf-8')
    except UnicodeEncodeError:
        raise ValueError('HTTP path contains invalid Unicode') from None
    return value


def _expect(value, where):
    _http_path(value['path'])
    if type(value['status']) is not int or not 100 <= value['status'] <= 599:
        raise ValueError(f'{where}.status must be an HTTP status integer')
    if 'contains' in value and (not isinstance(value['contains'], str) or not value['contains']):
        raise ValueError(f'{where}.contains must be nonempty text')
    if ('json_pointer' in value) != ('expected' in value):
        raise ValueError(f'{where}: json_pointer and expected must be supplied together')
    if 'json_pointer' in value:
        pointer = value['json_pointer']
        if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
            raise ValueError(f'{where}.json_pointer must be a JSON pointer')
        artifact_checks.validate_checks([{'type': 'json_value', 'path': 'http-body.json',
                                         'pointer': pointer, 'expected': value['expected']}])


def _check_port(port):
    with socket.socket() as probe:
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(('127.0.0.1', port))
        except OSError as exc:
            raise ValueError('Requested loopback port is unavailable') from exc
        return probe.getsockname()[1]


def _validate(directory, checks, run, unchanged, service, evidence_directory):
    if not isinstance(directory, (str, os.PathLike)):
        raise ValueError('directory must be an absolute existing task directory')
    original = Path(directory)
    if not original.is_absolute():
        raise ValueError('directory must be absolute')
    root = original.resolve(strict=True)
    if not root.is_dir() or root == Path(root.anchor):
        raise ValueError('directory must be a specific existing directory, not a drive root')
    if run is not None and service is not None:
        raise ValueError('run and service are mutually exclusive')
    normalized = artifact_checks.validate_checks(checks) if checks else []
    if checks is not None and not isinstance(checks, list):
        raise ValueError('checks must be an array')
    for check in normalized:
        scoped_file(root, check['path'])
    if not normalized and service is None:
        raise ValueError('Artifact and command verification require nonempty checks')
    selected_run = None
    selected_service = None
    budget = 60.0
    if run is not None:
        _fields(run, {'argv', 'timeout_seconds'}, {'argv'}, 'run')
        argv, interpreter = _argv(run['argv'], root)
        budget = _timeout(run.get('timeout_seconds', 60))
        selected_run = {'argv': argv, 'interpreter': interpreter}
    paths = None
    if unchanged is not None:
        if run is None:
            raise ValueError('unchanged requires an explicit run')
        paths = artifact_checks.validate_snapshot_paths(root, unchanged)
    if service is not None:
        _fields(service, {'argv', 'port', 'ready', 'requests', 'timeout_seconds'},
                {'argv', 'ready', 'requests'}, 'service')
        argv, interpreter = _argv(service['argv'], root)
        lower = ' '.join(argv).lower()
        if any(word in lower for word in ('--reload', '--reloader', '--daemon', '--detach',
                                          'start-process', 'nohup', 'start /b')):
            raise ValueError('Verification services must stay attached without reloader/detach')
        if Path(argv[0]).name.lower() in {'cmd', 'cmd.exe', 'pwsh', 'powershell', 'powershell.exe', 'sh', 'bash'}:
            raise ValueError('Service argv must launch the attached executable directly, not a shell')
        port = service.get('port', 0)
        if 'port' in service and (type(port) is not int or not 1 <= port <= 65535):
            raise ValueError('service.port must be 1-65535')
        port = _check_port(port)
        argv = [item.replace('{port}', str(port)) for item in argv]
        ready = service['ready']
        _fields(ready, {'path', 'status', 'contains'}, {'path', 'status'}, 'service.ready')
        _expect(ready, 'ready')
        requests = service['requests']
        if not isinstance(requests, list) or not 1 <= len(requests) <= 64:
            raise ValueError('service.requests must contain 1-64 steps')
        count = 0
        for item in requests:
            if isinstance(item, dict) and item.get('restart') is True:
                _fields(item, {'restart'}, {'restart'}, 'restart')
                continue
            _fields(item, {'method', 'path', 'status', 'contains', 'json_pointer', 'expected',
                           'json', 'form', 'actor'}, {'path', 'status'}, 'request')
            if item.get('method', 'GET') not in {'GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'}:
                raise ValueError('Unsupported HTTP method')
            _expect(item, 'request')
            if 'json' in item and 'form' in item:
                raise ValueError('HTTP json and form bodies are mutually exclusive')
            if item.get('method', 'GET') in {'GET', 'HEAD'} and ('json' in item or 'form' in item):
                raise ValueError('GET/HEAD requests cannot have a body')
            if 'form' in item and (not isinstance(item['form'], dict) or any(
                not isinstance(k, str) or not isinstance(v, str)
                for k, v in item['form'].items()
            )):
                raise ValueError('form must be an object with text keys and scalar values')
            if not isinstance(item.get('actor', 'default'), str) or not 1 <= len(item.get('actor', 'default')) <= 120:
                raise ValueError('actor must be nonempty text up to 120 characters')
            count += 1
        if not normalized and not count:
            raise ValueError('Pure service verification needs at least one real request assertion')
        # All body/expectation values must be serializable before effects occur.
        if len(json.dumps(service, ensure_ascii=False, allow_nan=False).encode('utf-8')) > MAX_BODY:
            raise ValueError('Service request declaration exceeds 20 MiB')
        budget = _timeout(service.get('timeout_seconds', 60))
        selected_service = {'argv': argv, 'interpreter': interpreter, 'port': port,
                            'ready': ready, 'requests': requests}
    # Do not follow an existing redirected evidence directory.
    task_file_path(root, evidence_directory + '/' + uuid.uuid4().hex + '/result.json')
    return root, normalized, selected_run, paths, selected_service, budget


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise VerificationFailure('Verification phase deadline reached', timed_out=True)
    return remaining


def _command(run, root, evidence, deadline, number, execution):
    import main as core
    with _RUN_LOCK, (evidence / f'run-{number}.log').open('w', encoding='utf-8') as log, redirect_stdout(log):
        timeout = _remaining(deadline)
        entry = {'kind': 'run', 'number': number, 'argv': run['argv'], 'interpreter': run['interpreter']}
        execution.append(entry)
        # No adapt_command, execute_plan, preparation or inferred retries.
        result = core.run_process(run['argv'], root, timeout, shell=False, target_process=True,
                                  env_overrides={'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'})
    entry.update(returncode=result.returncode, timed_out=result.timed_out,
                 seconds=result.duration_seconds, log=f'run-{number}.log')
    if result.returncode != 0 or result.timed_out:
        raise VerificationFailure('Explicit command failed; no automatic repeat', **entry,
                                  output_tail=(result.stderr or result.stdout)[-500:])
    _remaining(deadline)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _DeadlineConnection(HTTPConnection):
    """Interrupt slow headers/body by shutting down this request's socket."""
    def __init__(self, host, deadline, **kwargs):
        super().__init__(host, **kwargs)
        self.deadline = deadline
        self.watchdog = None
        self.request_socket = None
        self.expired = False

    def connect(self):
        self.timeout = min(self.timeout, _remaining(self.deadline))
        super().connect()
        self.request_socket = self.sock
        self.watchdog = threading.Timer(_remaining(self.deadline), self.abort)
        self.watchdog.daemon = True
        self.watchdog.start()

    def abort(self):
        self.expired = True
        if self.request_socket is not None:
            try:
                self.request_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def cancel_watchdog(self):
        if self.watchdog is not None:
            self.watchdog.cancel()


class _DeadlineHTTPHandler(HTTPHandler):
    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def http_open(self, request):
        connections = []

        def connect(host, **kw):
            connection = _DeadlineConnection(host, self.deadline, **kw)
            connections.append(connection)
            return connection

        try:
            response = self.do_open(connect, request)
            response._verification_connection = connections[0]
            return response
        except BaseException:
            for connection in connections:
                connection.cancel_watchdog()
            raise


def _response(opener, origin, step, deadline, evidence, label, execution):
    headers = {}
    data = None
    if 'json' in step:
        data = json.dumps(step['json'], ensure_ascii=False, allow_nan=False).encode('utf-8')
        headers['Content-Type'] = 'application/json; charset=utf-8'
    if 'form' in step:
        data = urlencode(step['form']).encode('utf-8')
        headers['Content-Type'] = 'application/x-www-form-urlencoded; charset=utf-8'
    path = quote(step['path'], safe="/%?&=:+,@;!$'()*-._~")
    req = Request(origin + path, data=data, headers=headers, method=step.get('method', 'GET'))
    started = time.monotonic()
    connection = None
    try:
        try:
            remaining = _remaining(deadline)
            response = opener.open(req, timeout=min(1.0, remaining) if label.startswith('ready-') else remaining)
        except HTTPError as error:
            response = error
        transport = response.fp if isinstance(response, HTTPError) else response
        connection = getattr(transport, '_verification_connection', None)
        with response:
            status = response.code
            location = response.headers.get('Location', '')
            if location:
                target = urlsplit(location)
                base = urlsplit(origin)
                if (target.scheme and target.scheme != base.scheme) or (target.netloc and target.netloc != base.netloc) or location.startswith('//'):
                    raise VerificationFailure('Response redirects outside the owned loopback origin', step=label)
            body = bytearray()
            while True:
                remaining = _remaining(deadline)
                stream_socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
                if stream_socket is not None:
                    stream_socket.settimeout(remaining)
                chunk = response.read1(min(64 * 1024, MAX_BODY + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > MAX_BODY:
                    (evidence / (label + '.body')).write_bytes(body[:MAX_BODY])
                    raise VerificationFailure('HTTP body exceeds 20 MiB; local evidence is truncated',
                                              step=label, body_truncated=True)
            if connection is not None and connection.expired:
                raise VerificationFailure('HTTP request deadline reached', step=label, timed_out=True)
    except (URLError, OSError, HTTPException) as exc:
        raise VerificationFailure('HTTP request failed', step=label, error=str(exc)[:300],
                                  timed_out=time.monotonic() >= deadline) from exc
    finally:
        if connection is not None:
            connection.cancel_watchdog()
    (evidence / (label + '.body')).write_bytes(body)
    record = {'kind': 'http', 'step': label, 'method': step.get('method', 'GET'),
              'path': step['path'], 'status': status, 'bytes': len(body),
              'seconds': round(time.monotonic() - started, 3)}
    if execution is not None:
        execution.append(record)
    if status != step['status']:
        raise VerificationFailure('HTTP status assertion failed', step=label,
                                  expected=step['status'], actual=status)
    if 'contains' in step:
        try:
            text = body.decode('utf-8-sig')
        except UnicodeError as exc:
            raise VerificationFailure('HTTP body is not UTF-8', step=label) from exc
        if step['contains'] not in text:
            raise VerificationFailure('HTTP text assertion failed', step=label, expected=step['contains'][:200])
    if 'json_pointer' in step:
        try:
            actual = artifact_checks.json_pointer(artifact_checks.load_json(body.decode('utf-8-sig')), step['json_pointer'])
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError, RecursionError) as exc:
            raise VerificationFailure('HTTP JSON assertion cannot be evaluated', step=label, error=str(exc)[:200]) from exc
        # bool and numeric values must not compare equal accidentally.
        if not artifact_checks.equal_values(actual, step['expected']):
            raise VerificationFailure('HTTP JSON assertion failed', step=label, pointer=step['json_pointer'])
    return record


def _service(spec, root, evidence, deadline, execution, effects):
    import main as core
    from owned_process import spawn_owned
    process = None
    owner = None
    log = None
    actors = {}
    starts = 0
    origin = f'http://127.0.0.1:{spec["port"]}'

    def stop():
        nonlocal process, log, owner
        if process is not None:
            pid = process.pid
            owner.__exit__(None, None, None)
            effects.append({'kind': 'service_cleanup', 'pid': pid, 'stopped': process.poll() is not None})
            if process.poll() is None:
                raise VerificationFailure('Owned service cleanup did not complete', pid=pid)
            process = None
            owner = None
        if log is not None:
            log.close()
            log = None

    def start():
        nonlocal process, log, starts, owner
        _remaining(deadline)
        # Recheck immediately before starting; never kill an existing listener.
        if starts:
            with socket.socket() as probe:
                probe.settimeout(min(0.25, _remaining(deadline)))
                if probe.connect_ex(('127.0.0.1', spec['port'])) == 0:
                    raise VerificationFailure('Requested loopback port became occupied before restart')
        else:
            _check_port(spec['port'])
        starts += 1
        log = (evidence / f'service-{starts}.log').open('wb')
        env = core.build_process_env(target_process=True)
        env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
        owner = spawn_owned(spec['argv'], cwd=root, stdout=log, stderr=subprocess.STDOUT, env=env,
                            deadline=deadline, metadata_directory=evidence)
        process = owner.__enter__()
        _remaining(deadline)
        effects.append({'kind': 'service_start', 'pid': process.pid, 'port': spec['port']})
        execution.append({'kind': 'service_start', 'number': starts, 'pid': process.pid,
                          'argv': spec['argv'], 'interpreter': spec['interpreter']})
        ready_opener = build_opener(ProxyHandler({}), _DeadlineHTTPHandler(deadline), _NoRedirect())
        last = None
        while True:
            if process.poll() is not None:
                raise VerificationFailure('Service launcher exited before readiness', returncode=process.returncode)
            _remaining(deadline)
            try:
                _response(ready_opener, origin, spec['ready'], deadline, evidence, f'ready-{starts}', None)
                execution.append({'kind': 'ready', 'number': starts, 'passed': True})
                return
            except VerificationFailure as exc:
                if exc.detail.get('body_truncated') or 'redirects outside' in str(exc):
                    raise
                last = exc.detail
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise VerificationFailure('Service readiness deadline reached', timed_out=True, last_failure=last)
                time.sleep(min(0.05, remaining))

    try:
        start()
        for index, step in enumerate(spec['requests'], 1):
            _remaining(deadline)
            if step.get('restart') is True:
                stop()
                start()
                execution.append({'kind': 'restart', 'step': index, 'passed': True})
                continue
            actor = step.get('actor', 'default')
            opener = actors.setdefault(actor, build_opener(ProxyHandler({}), _DeadlineHTTPHandler(deadline), HTTPCookieProcessor(CookieJar()), _NoRedirect()))
            _response(opener, origin, step, deadline, evidence, f'request-{index}', execution)
    finally:
        stop()


def verify(directory, checks=None, run=None, unchanged=None, service=None,
           evidence_directory='.repowayfinder-checks'):
    """Inspect artifacts and optionally execute one explicit finite task phase.

    timeout_seconds is a shared tool-phase budget, including explicit repeats
    and requests; it is not a limit on the model's overall task.
    """
    if not isinstance(evidence_directory, str) or not evidence_directory:
        raise ValueError('evidence_directory must be a relative task directory')
    root, checks, run, unchanged, service, budget = _validate(directory, checks, run, unchanged, service, evidence_directory)
    started = time.monotonic()
    deadline = started + budget
    attempt = uuid.uuid4().hex
    relative_evidence = f'{evidence_directory}/{attempt}/result.json'
    evidence = task_file_path(root, relative_evidence).parent
    evidence.mkdir(parents=True, exist_ok=False)
    execution = []
    effects = [{'kind': 'evidence_directory', 'path': str(evidence)}]
    check_results = []
    failure = None
    unchanged_result = None
    try:
        if run is not None:
            effects.append({'kind': 'explicit_command_attempt', 'number': 1, 'directory': str(root),
                            'writes': 'possible command-defined partial effects; not sandboxed'})
            _command(run, root, evidence, deadline, 1, execution)
            check_results = artifact_checks.evaluate_checks(root, checks)
            failed = next((item for item in check_results if not item['passed']), None)
            if failed:
                raise VerificationFailure('Artifact assertion failed after first run; repeat not executed', check=failed)
            if unchanged is not None:
                # Parse only after successful execution and checks. Invalid data
                # must stop before repeating a potentially mutating command.
                baseline = artifact_checks.snapshot_artifacts(root, unchanged)
                effects.append({'kind': 'explicit_command_attempt', 'number': 2, 'directory': str(root),
                                'writes': 'explicit repeat; possible command-defined partial effects'})
                _command(run, root, evidence, deadline, 2, execution)
                current = artifact_checks.snapshot_artifacts(root, unchanged)
                differences = [path for path in unchanged if not artifact_checks.equal_values(baseline[path], current[path])]
                unchanged_result = {'passed': not differences, 'paths_checked': len(unchanged), 'changed_paths': differences[:10]}
                check_results = artifact_checks.evaluate_checks(root, checks)
                if differences:
                    raise VerificationFailure('Explicit repeat changed requested parsed artifacts', changed_paths=differences[:10])
        elif service is not None:
            _service(service, root, evidence, deadline, execution, effects)
        if checks and not check_results:
            check_results = artifact_checks.evaluate_checks(root, checks)
        failed = next((item for item in check_results if not item['passed']), None)
        if failed:
            raise VerificationFailure('Artifact assertion failed', check=failed)
        _remaining(deadline)
    except VerificationFailure as exc:
        failure = exc.detail
    except Exception as exc:
        failure = {'reason': str(exc)[:500], 'error': type(exc).__name__}
    except BaseException:
        failure = {'reason': 'Verification interrupted; inspect recorded partial effects', 'interrupted': True}
        raise
    finally:
        # No input artifacts are restored or removed. Record incomplete effects.
        report = {'attempt_id': attempt, 'ok': failure is None, 'passed': failure is None,
                  'verification_scope': 'caller-supplied current artifact and HTTP assertions; not job freshness or full task acceptance',
                  'directory': str(root), 'timeout_seconds': budget,
                  'seconds': round(time.monotonic() - started, 3),
                  'timed_out': bool(failure and failure.get('timed_out')),
                  'first_failure': failure, 'checks': check_results,
                  'unchanged': unchanged_result, 'execution': execution, 'side_effects': effects}
        write_json(task_file_path(root, relative_evidence), report)
    compact = {'ok': report['passed'], 'passed': report['passed'], 'attempt_id': attempt,
               'verification_scope': report['verification_scope'], 'seconds': report['seconds'],
               'timeout_seconds': budget, 'timed_out': report['timed_out'],
               'checks_passed': sum(item['passed'] for item in check_results), 'checks_total': len(checks),
               'first_failure': failure, 'execution_count': len(execution),
               'side_effects': effects, 'evidence_path': str(evidence / 'result.json')}
    if run is not None or service is not None:
        compact['interpreter'] = (run or service)['interpreter']
        compact['note'] = 'Explicit execution may have partial effects; no automatic retries or rollback. Only owned service processes are stopped.'
    if budget > 60:
        compact['host_timeout_note'] = 'This phase exceeds the default host tool timeout; configure the host timeout before invoking it.'
    if unchanged_result is not None:
        compact['unchanged'] = unchanged_result
    return compact
