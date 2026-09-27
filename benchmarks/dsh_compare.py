"""Matched DSH runs: native PowerShell versus RepoWayfinder MCP.

Requires an existing DSH bundle and DEEPSEEK_API_KEY in the calling environment.
Writes only to the requested fresh evidence/work directories; never loads a key.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import dsh_task

NATIVE_GUIDE = '''
Use the available native DSH PowerShell/file tools to complete this task.
The host is Windows. You may combine steps in one PowerShell call when useful.
Keep source, dependencies, logs and outputs under the given task directory.
Use an existing Python to create a project virtual environment; do not install globally.
Keep full install logs on disk and show short error excerpts only on failure.
Do not read credentials, unrelated private files, or call another model.
Report the artifact path, exact source revision and observed checks briefly.
'''


def native_patch(directory, python, agent, mode='workspace-write', **kwargs):
    q = lambda value: json.dumps(str(value), ensure_ascii=False)
    lines = ['- id: sandbox-policy', '  config:', f'    mode: {mode}',
             f'    workspaceRoot: {q(directory)}', '- id: approval', '  config:',
             '    policy: never', '- id: permission', '  config:',
             '    defaultPreset: benchmark-native', '    presets:',
             '      benchmark-native:', f'        sandbox: {mode}',
             '        approval: never']
    for name in dsh_task.DISABLED_TOOLS:
        lines += [f'- id: {name}', '  disabled: ' + ('false' if name in {'tool-pwsh', 'tool-jobs'} else 'true')]
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode', choices=['native', 'tool'], required=True)
    for name in ('bundle', 'directory', 'prompt-file', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--provider', required=True)
    p.add_argument('--model', required=True)
    p.add_argument('--reasoning-effort')
    p.add_argument('--tool-mode', choices=['companion', 'tool-only'], default='companion')
    p.add_argument('--max-model-calls', type=int, default=40, help='0 disables the model response limit')
    p.add_argument('--permissions', choices=['read-only', 'workspace-write', 'danger-full-access'],
                   help='Explicit permission mode for either group; full access requires authorization')
    p.add_argument('--native-permissions', choices=['workspace-write', 'danger-full-access'],
                   default='workspace-write', help='Full access requires explicit authorization for this trial')
    args = p.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    if any(args.directory.iterdir()):
        p.error('Comparison directory must be empty; do not reuse earlier outputs')
    args.model_settings = None
    args.timeout = 900
    if args.max_model_calls < 0:
        p.error('--max-model-calls must be nonnegative')
    args.permission_mode = args.permissions or args.native_permissions
    if args.mode == 'native':
        dsh_task._profile_patch = native_patch
        dsh_task.GUIDE = NATIVE_GUIDE
        args.tool_mode = 'companion'
    source_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    source_status = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    if source_status.strip():
        p.error('Freeze the candidate in a local commit before model comparisons; working tree is dirty')
    attempt_started_at = datetime.now(timezone.utc).isoformat()
    attempt_started = time.monotonic()
    summary = dsh_task.run(args)
    final_commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    final_status = subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    summary.update(mode=args.mode,
                   max_model_calls=args.max_model_calls,
                   source_commit=source_commit,
                   source_unchanged=final_commit == source_commit and not final_status.strip(),
                   permissions=args.permission_mode + ' / never',
                   attempt_started_at=attempt_started_at,
                   attempt_finished_at=datetime.now(timezone.utc).isoformat(),
                   attempt_seconds=round(time.monotonic() - attempt_started, 3),
                   native_permissions=args.permission_mode + ' / never' if args.mode == 'native' else None)
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    compact = {k: v for k, v in summary.items() if k not in {'answer', 'usage_samples'}}
    compact['answer'] = summary['answer'][:1200]
    print(json.dumps(compact, ensure_ascii=False))
    return 0 if summary['dsh_finished'] else 2


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
