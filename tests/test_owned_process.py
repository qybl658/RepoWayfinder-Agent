"""Real short-lived fixtures for ownership cleanup; no models or services."""
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import owned_process


def alive(pid):
    if os.name == 'nt':
        from ctypes import wintypes
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.WaitForSingleObject.restype = wintypes.DWORD
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return api.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path('/proc') / str(pid) / 'stat'
    if stat.exists() and stat.read_text().split(') ', 1)[1].startswith('Z'):
        return False
    return True


class OwnedProcessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rw-owned-tests-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def wait_file(self, path):
        deadline = time.monotonic() + 5
        while not path.exists():
            if time.monotonic() > deadline:
                self.fail('Fixture failed to start: ' + str(path))
            time.sleep(0.01)
        return int(path.read_text())

    def wait_dead(self, pid):
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(alive(pid), f'Owned PID {pid} survived context cleanup')

    def owned(self, *args, **kwargs):
        return owned_process.spawn_owned([sys.executable, '-c', *args], cwd=self.root,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        **kwargs)

    def test_normal_context_cleans_running_target_and_forwards_environment(self):
        marker = self.root / 'started'
        program = ('import os,time; from pathlib import Path; '
                   f'assert os.environ["RW_OWNED_FIXTURE"] == "fixture-value"; '
                   f'Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)')
        temporary_directory = tempfile.TemporaryDirectory
        with patch.object(owned_process.tempfile, 'TemporaryDirectory', wraps=temporary_directory) as metadata:
            with self.owned(program, env={**os.environ, 'RW_OWNED_FIXTURE': 'fixture-value'},
                            metadata_directory=self.root) as process:
                target = self.wait_file(marker)
                # Windows venv target launchers can create a second interpreter;
                # both belong to the job, and target_pid is the created root.
                self.assertTrue(alive(process.target_pid))
                self.assertIsNone(process.poll())
            if os.name == 'nt':
                self.assertEqual(metadata.call_args.kwargs['dir'], self.root)
        self.assertEqual(list(self.root.glob('rw-owned-start-*')), [])
        self.wait_dead(target)
        self.wait_dead(process.pid)

    def test_early_exited_parent_does_not_leave_its_child(self):
        marker = self.root / 'child'
        child = f'from pathlib import Path; import os,time; Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(30)'
        parent = f'import subprocess,sys; subprocess.Popen([sys.executable,"-c",{child!r}]);'
        with self.owned(parent) as process:
            target = process.target_pid
            child_pid = self.wait_file(marker)
            self.assertEqual(process.wait(timeout=5), 0)
            self.assertFalse(alive(target))
            self.assertTrue(alive(child_pid))
        self.wait_dead(child_pid)

    def test_cleanup_on_exception_preserves_unrelated_process(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            with self.assertRaisesRegex(RuntimeError, 'verification failed'):
                with self.owned('import time; time.sleep(30)') as process:
                    target = process.target_pid
                    raise RuntimeError('verification failed')
            self.wait_dead(target)
            self.assertIsNone(unrelated.poll())
        finally:
            unrelated.terminate()
            unrelated.wait(timeout=5)

    def test_missing_executable_fails_before_yielding(self):
        with self.assertRaises(OSError):
            with owned_process.spawn_owned([str(self.root / 'does-not-exist')], cwd=self.root,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL):
                self.fail('Missing executable must not yield a holder')

    def test_expired_phase_deadline_rejects_before_process_creation(self):
        with patch.object(owned_process.subprocess, 'Popen') as popen:
            with self.assertRaises(TimeoutError):
                with self.owned('raise SystemExit(0)', deadline=time.monotonic() - 1):
                    self.fail('Expired deadline must not yield')
        popen.assert_not_called()

    @unittest.skipUnless(os.name == 'nt', 'Windows supervisor handshake deadline')
    def test_deadline_expiring_during_assignment_never_releases_target(self):
        marker = self.root / 'deadline-should-not-run'
        assign = owned_process._WindowsJob.assign
        def delayed_assign(job, process):
            assign(job, process)
            time.sleep(0.04)
        with patch.object(owned_process._WindowsJob, 'assign', delayed_assign):
            with self.assertRaises(TimeoutError):
                with self.owned(f'from pathlib import Path; Path({str(marker)!r}).write_text("bad")',
                                deadline=time.monotonic() + 0.02):
                    self.fail('Expired handshake must not yield')
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == 'nt', 'Windows ownership establishment boundary')
    def test_failed_job_assignment_never_releases_target(self):
        marker = self.root / 'should-not-run'
        with patch.object(owned_process._WindowsJob, 'assign', side_effect=OSError('assignment blocked')):
            with self.assertRaisesRegex(OSError, 'assignment blocked'):
                with self.owned(f'from pathlib import Path; Path({str(marker)!r}).write_text("bad")'):
                    self.fail('Failed ownership must not yield')
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
