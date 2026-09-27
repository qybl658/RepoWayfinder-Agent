"""Run one frozen Grok repository-task comparison arm with local evidence.

Run native and tool arms separately in fresh, task-local directories. The caller
prepares each directory's .grok/config.toml: native has no RepoWayfinder MCP;
tool has the task-local repo_wayfinder service. This runner never edits login or
global Grok settings and does not judge deliverable quality from CLI success.
"""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import grok_task


NATIVE_GUIDE = '''
Use Grok's native terminal and file tools to complete the requested task.
The host is Windows. Keep source, dependencies, logs and outputs in this task
directory. For Python tasks, use the version named in the task and create a
virtual environment in the source checkout; install from the fixed source
commit into that environment only. For other runtimes, follow the task's local
installation requirements without adding an unnecessary Python environment.
In Python scripts, use sys.executable for Python child
processes and [sys.executable, '-m', 'pip', ...] for pip. Keep full install and
build logs on disk; show short excerpts only when needed to diagnose failure.
Use native tools for the actual file creation, builds and checks. Do not call
another model, read credentials or unrelated private files, or publish results.
Report the deliverable path, source revision and observed checks briefly.
'''


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root, text=True, encoding='utf-8').strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['native', 'tool'], required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--prompt-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--grok', default=shutil.which('grok') or str(Path.home() / '.grok/bin/grok.exe'))
    parser.add_argument('--model', required=True)
    parser.add_argument('--reasoning-effort', required=True)
    parser.add_argument('--max-turns', type=int, default=0, help='Optional turn cap; 0 leaves the CLI uncapped')
    parser.add_argument('--timeout', type=int, default=0, help='Optional seconds limit; 0 waits for completion')
    parser.add_argument('--expected-commit', help='Optional full source commit expected for both arms')
    args = parser.parse_args()
    directory = args.directory.resolve()
    output = args.output.resolve()
    prompt = args.prompt_file.resolve()
    if not directory.is_dir() or not (directory / '.grok' / 'config.toml').is_file():
        parser.error('Prepare a fresh task directory with .grok/config.toml first')
    if {path.name for path in directory.iterdir()} != {'.grok'}:
        parser.error('Task directory must contain only its prepared .grok configuration')
    if not prompt.is_file() or args.max_turns < 0 or args.timeout < 0 or 0 < args.timeout < 30:
        parser.error('Provide a prompt, nonnegative max-turns and timeout of 0 or at least 30 seconds')
    if output.exists() and any(output.iterdir()):
        parser.error('Output directory must be new or empty')
    if directory == output or directory in output.parents or output in directory.parents:
        parser.error('Keep evidence output separate from the task directory')
    commit = git(ROOT, 'rev-parse', 'HEAD')
    if args.expected_commit and commit.lower() != args.expected_commit.lower():
        parser.error('Source HEAD differs from --expected-commit')
    if git(ROOT, 'status', '--porcelain'):
        parser.error('Freeze the candidate in a local commit before model comparisons; source tree is dirty')
    args.execution_mode = 'companion' if args.mode == 'tool' else 'native'
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    summary = grok_task.run(args, grok_task.companion_guide() if args.mode == 'tool' else NATIVE_GUIDE)
    final_commit = git(ROOT, 'rev-parse', 'HEAD')
    final_dirty = bool(git(ROOT, 'status', '--porcelain'))
    summary.update(mode=args.mode, source_commit=commit,
                   source_unchanged=(final_commit == commit and not final_dirty),
                   attempt_started_at=started_at,
                   attempt_finished_at=datetime.now(timezone.utc).isoformat(),
                   attempt_seconds=round(time.monotonic() - started, 3),
                   timing_scope='Grok process startup through exit; attempt also includes local prompt and summary handling. Independent artifact verification is separate.',
                   task_directory=str(directory),
                   evidence_directory=str(output))
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    compact = {key: value for key, value in summary.items() if key not in {'rounds', 'answer'}}
    compact['answer'] = summary['answer'][:1200]
    print(json.dumps(compact, ensure_ascii=False))
    return 0 if summary['grok_finished'] and summary['source_unchanged'] else 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
