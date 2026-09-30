"""Task-scoped process lifetime ownership, not an OS sandbox.

Windows assigns a trusted supervisor to a kill-on-close Job Object before
letting it start target code. Normal CreateProcess descendants stay in that
job even when their parent exits. No breakaway flags or elevated rights are
enabled. WMI/external services can create processes outside this lifetime.
POSIX uses a new session/process group; deliberate setsid/setpgid escape is
outside this cleanup contract.

Job semantics: https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


class _WindowsJob:
    def __init__(self):
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
                        ('max_working_set', ctypes.c_size_t), ('active_limit', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]

        class IoCounters(ctypes.Structure):
            _fields_ = [(name, ctypes.c_ulonglong) for name in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [('basic', BasicLimits), ('io', IoCounters),
                        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]

        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        # Popen retains this exact process handle, so no PID lookup/reuse race.
        if not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle is not None:
            handle, self.handle = self.handle, None
            if not self.api.CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())


class OwnedProcess:
    """Popen-like holder whose termination covers its owned job/process group.

    pid identifies the ownership root (Windows supervisor); target_pid is the
    actual target program. poll/wait/returncode reflect the target exit status.
    The supervisor forwards inherited stdout/stderr unchanged; stdin is not
    exposed because these finite verification processes are noninteractive.
    """
    def __init__(self, process, *, target_pid=None, job=None):
        self._process = process
        self._job = job
        self.pid = process.pid
        self.target_pid = target_pid or process.pid
        self.stdout = process.stdout
        self.stderr = process.stderr
        self._closed = False

    @property
    def returncode(self):
        return self._process.returncode

    def poll(self):
        return self._process.poll()

    def wait(self, timeout=None):
        return self._process.wait(timeout=timeout)

    def terminate(self):
        self.close()

    def kill(self):
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._job is not None:
            # Close even after the supervisor/target has exited: surviving
            # descendants remain attached to the Job Object.
            self._job.close()
        else:
            try:
                os.killpg(self.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=5)
        for pipe in (self._process.stdin, self.stdout, self.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()


def _publish_ready(path, payload):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(payload), encoding='utf-8')
    os.replace(temporary, path)


def _supervisor(ready_path):
    """Internal trusted bootstrap; target input arrives only after job assign."""
    ready = Path(ready_path)
    try:
        config = json.loads(sys.stdin.buffer.readline().decode('utf-8'))
        if config.get('deadline') is not None and time.monotonic() >= config['deadline']:
            raise TimeoutError('Owned target startup deadline reached before launch')
        process = subprocess.Popen(config['argv'], cwd=config['cwd'], env=config['env'],
                                   stdin=subprocess.DEVNULL)
    except Exception as exc:
        _publish_ready(ready, {'error': str(exc), 'error_type': type(exc).__name__})
        return 127
    _publish_ready(ready, {'pid': process.pid})
    return process.wait()


def _start_windows(argv, cwd, stdout, stderr, env, deadline, metadata_directory):
    startup_deadline = min(time.monotonic() + 10, deadline) if deadline is not None else time.monotonic() + 10
    job = _WindowsJob()
    process = None
    try:
        # Boot/handshake has a bounded internal timeout; target runtime does not.
        # The metadata directory is private to this invocation and removed
        # once launch is confirmed. Scoped callers put it in their evidence
        # directory; the general helper otherwise uses the platform temp dir.
        with tempfile.TemporaryDirectory(prefix='rw-owned-start-', dir=metadata_directory) as directory:
            ready = Path(directory) / 'ready.json'
            # A Windows venv python.exe can be a launcher that creates the
            # actual interpreter before we assign it. Start the base binary
            # directly so the only bootstrap process waits for our handshake.
            # -S also avoids executing site hooks before ownership is set.
            bootstrap = getattr(sys, '_base_executable', sys.executable)
            process = subprocess.Popen(
                [bootstrap, '-I', '-S', str(Path(__file__).resolve()), '--_owned-supervisor', str(ready)],
                stdin=subprocess.PIPE, stdout=stdout, stderr=stderr)
            job.assign(process)
            if time.monotonic() >= startup_deadline:
                raise TimeoutError('Owned process startup deadline reached before target release')
            payload = {'argv': argv, 'cwd': str(cwd) if cwd is not None else None,
                       'env': dict(env) if env is not None else None, 'deadline': startup_deadline}
            process.stdin.write((json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8'))
            process.stdin.close()
            while not ready.is_file():
                if process.poll() is not None:
                    raise OSError('Owned supervisor exited before target startup confirmation')
                if time.monotonic() >= startup_deadline:
                    raise TimeoutError('Owned supervisor did not confirm target startup')
                time.sleep(min(0.01, max(0, startup_deadline - time.monotonic())))
            result = json.loads(ready.read_text(encoding='utf-8'))
            if 'error' in result:
                if result.get('error_type') == 'TimeoutError':
                    raise TimeoutError(result['error'])
                raise OSError('Owned target could not start: ' + result['error'])
            if time.monotonic() >= startup_deadline:
                raise TimeoutError('Owned process startup deadline reached')
            return OwnedProcess(process, target_pid=result['pid'], job=job)
    except BaseException:
        # If assignment failed the supervisor has not received target input.
        # If later startup failed, closing the job cleans its partial effects.
        job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None and not pipe.closed:
                    pipe.close()
        raise


@contextmanager
def spawn_owned(argv, *, cwd, stdout, stderr, env=None, deadline=None, metadata_directory=None):
    """Start one noninteractive task and always reap its owned descendants.

    argv must be a nonempty argument sequence; shell strings are never guessed.
    stdout/stderr and env follow subprocess.Popen. Failure to establish Windows
    ownership raises before target code is released. No parent/MCP process is
    assigned to the target job. Optional deadline is an absolute monotonic
    phase deadline and constrains startup too. Cleanup always runs even after
    that deadline; it may use its short bounded reap wait. The scope supplies
    cleanup, not isolation. metadata_directory optionally selects the parent
    for Windows startup handshake files so scoped task writes stay in-scope.
    """
    if isinstance(argv, (str, bytes)) or not argv:
        raise ValueError('argv must be a nonempty argument sequence')
    argv = [os.fspath(item) for item in argv]
    if deadline is not None:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise ValueError('deadline must be a finite absolute monotonic timestamp')
        if time.monotonic() >= deadline:
            raise TimeoutError('Owned process startup deadline reached')
    if os.name == 'nt':
        holder = _start_windows(argv, cwd, stdout, stderr, env, deadline, metadata_directory)
    else:
        holder = OwnedProcess(subprocess.Popen(argv, cwd=cwd, stdout=stdout, stderr=stderr,
                                             env=env, stdin=subprocess.DEVNULL,
                                             start_new_session=True))
        if deadline is not None and time.monotonic() >= deadline:
            holder.close()
            raise TimeoutError('Owned process startup deadline reached')
    try:
        yield holder
    finally:
        holder.close()


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--_owned-supervisor':
        raise SystemExit(_supervisor(sys.argv[2]))
    raise SystemExit('This module is an internal process-lifetime helper.')
