"""Run one RepoWayfinder MCP task through the bundled DeepSeek Harness SDK."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import uuid


TOOL_ONLY_GUIDE = """\n\nRepoWayfinder tool guide:\nFor this authorized finite repository task, call mcp__repo_wayfinder__rw_run with repository, commands and checks. A revision is optional. Commands run at the acquired repository root; python/pip use the job virtual environment and UTF-8 is set. rw_run waits up to 50 seconds. If it returns a running job, use rw_status with wait_seconds=30. Read logs only if the compact result leaves a real uncertainty. Report the verified revision, output and checks briefly. A tool status or a passing subset of checks does not establish the requested result. Do not call other model tools or create nested agents.\n"""

DISABLED_TOOLS = (
    'tool-bash', 'tool-pwsh', 'tool-jobs', 'tool-skill',
    'tool-subagent-control', 'tool-subagent-list-agents', 'tool-subagent',
    'tool-subagent-fork', 'tool-workflow', 'tool-todo', 'tool-goal',
    'tool-ralph', 'tool-web',
)
USAGE_FIELDS = ('inputTokens', 'outputTokens', 'totalTokens',
                'cacheReadTokens', 'cacheWriteTokens', 'reasoningTokens')

TOOL_ONLY_GUIDE += ('Python/pip steps create/reuse the project venv automatically. Start with '
          'python -m pip install; do not bootstrap or activate another venv. '
          'In Python scripts, launch Python children with sys.executable, not bare python; '
          'use [sys.executable, "-m", "pip", ...] for pip and shutil.which for other child CLIs. '
          'Shell-only plans without python_version do not create a Python venv. '
          'The execution host is Windows. Commands are direct argv, one per step; '
          'do not use shell redirects/pipes or assume GNU commands. Use result checks '
          'for file assertions instead of extra inspection commands. The revision '
          'parameter pins checkout and the result reports that verified commit. '
          'stdout_contains needs expected text only, not a path.\n')
TOOL_ONLY_GUIDE += ('Submit configuration, documents and input directly in files=[{path,content},...] '
          '(new UTF-8 files, 32 files / 256 KiB total). Batch known steps; use short scripts '
          'only for necessary glue, not to embed all file contents and duplicate result checks. '
          'For failure or subsequent stages, reuse the job: rw_replan accepts exact '
          'edits=[{path,old,new}], new files and commands for only the affected steps. '
          'Set execute=true to run in the existing environment. Omit commands to reuse '
          'the saved plan; omit checks to reuse its assertions. Checks default to '
          'current-run output. Use freshness="preserved" for intentionally retained '
          'earlier verified artifacts (such as snapshots); their digest must match. '
          'A request_id makes recovery submission safe to replay. Inspect partial effects '
          'before retrying; do not recreate the checkout or reinstall unnecessarily. '
          'Installed executables are supported; shell builtins require an explicit shell.\n')

GUIDE = '''
RepoWayfinder is available alongside your native PowerShell/file tools.
Use mcp__repo_wayfinder__rw_run to acquire the requested repository/revision and
run its installation or other finite repository commands. Pass python_version
only when a Python version is requested. Python/pip commands automatically use the
checkout's .venv; do not create another environment. A setup-only call needs no
artifact checks. The result reports project_path, revision and result.python_runtime.executable.
For Python tasks, use that exact interpreter for subsequent native commands,
and sys.executable for Python children. For other runtimes, python_runtime may
be absent: use the task's runtime without creating a Python environment.
Keep install output in local job evidence.
Use native tools for task-specific files, transformations and inspection when
they are simpler. You need not encode every task stage into service files/checks
or return native-created artifacts through the service. Batch useful work.
If a job is running, rw_status(wait_seconds=30) waits. After failure, rw_replan
can reuse its checkout/environment with only affected commands. Do not reinstall
or clone again unnecessarily. A setup success proves setup only; verify the full
requested deliverable and report its path, revision and actual checks briefly.
No other models, credentials, unrelated private files or global installation.
'''


def _bundle_model_defaults(bundle: Path) -> dict[str, str]:
    path = bundle / 'app/resources/app/node_modules/@deepseek-ai/dsh-base/cordis.patch.yml'
    lines = path.read_text(encoding='utf-8-sig').splitlines()
    values = {}
    inside = False
    for line in lines:
        if re.match(r'^\s*- id: agent-default-model\s*$', line):
            inside = True
            continue
        if inside and re.match(r'^\s*- id:', line):
            break
        if inside:
            match = re.match(r'^\s+(provider|model|reasoningEffort):\s*[\"\']?([\w.-]+)[\"\']?\s*(?:#.*)?$', line)
            if match:
                values[match.group(1)] = match.group(2)
    return values


def _model_from_settings(path: Path | None) -> dict[str, str]:
    """Read only the agent-default-model scalar section; never expose other settings."""
    if path is None or not path.is_file():
        return {}
    values: dict[str, str] = {}
    in_section = False
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if re.match(r'^agent-default-model:\s*(?:#.*)?$', line):
            in_section = True
            continue
        if in_section and line and not line[0].isspace():
            break
        if in_section:
            match = re.match(r'^\s+(provider|model|reasoningEffort):\s*[\"\']?([\w.-]+)[\"\']?\s*(?:#.*)?$', line)
            if match:
                values[match.group(1)] = match.group(2)
    return values


def _profile_patch(directory: Path, python: Path, agent: Path, mode: str = 'workspace-write',
                   companion: bool = True) -> str:
    # JSON quoted strings and lists are valid YAML. This profile is dedicated to
    # this run; no existing desktop profile, settings, or credentials are edited.
    q = lambda value: json.dumps(str(value), ensure_ascii=False)
    lines = [
        '- id: sandbox-policy',
        '  config:',
        f'    mode: {mode}',
        f'    workspaceRoot: {q(directory)}',
        '- id: approval',
        '  config:',
        '    policy: never',
        '- id: permission',
        '  config:',
        '    defaultPreset: repo-wayfinder',
        '    presets:',
        '      repo-wayfinder:',
        f'        sandbox: {mode}',
        '        approval: never',
    ]
    lines += [f'- id: {name}\n  disabled: ' +
              ('false' if companion and name in {'tool-pwsh', 'tool-jobs'} else 'true')
              for name in DISABLED_TOOLS]
    lines += [
        '- insert:',
        '    - id: repo-wayfinder-mcp',
        "      name: '@deepseek-ai/dsh-mcp-client'",
        '      config:',
        '        transport: stdio',
        '        serverName: repo_wayfinder',
        f'        command: {q(python)}',
        f'        args: {json.dumps([str(agent), "--workspace", str(directory), "serve"], ensure_ascii=False)}',
        '        env: {}',
        f'        cwd: {q(directory)}',
        '        failOnStartupError: true',
    ]
    return '\n'.join(lines) + '\n'


def _pump(stream, kind: str, events: queue.Queue):
    try:
        for line in stream:
            events.put((kind, line))
    finally:
        events.put((kind, None))


def _send(process: subprocess.Popen, method: str, params: dict, request_id: int):
    frame = {'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params}
    process.stdin.write(json.dumps(frame, ensure_ascii=False) + '\n')
    process.stdin.flush()


def run(args: argparse.Namespace) -> dict:
    bundle = args.bundle.resolve()
    directory = args.directory.resolve()
    output = args.output.resolve()
    prompt_file = args.prompt_file.resolve()
    if not directory.is_dir() or not prompt_file.is_file():
        raise ValueError('directory and prompt-file must exist')
    if output.exists() and any(output.iterdir()):
        raise ValueError('output directory must be new or empty')
    node = bundle / 'app/resources/app/node_modules/node/bin/node.exe'
    cli = bundle / 'app/resources/app/node_modules/@deepseek-ai/dsh/lib/bin.js'
    agent = Path(__file__).resolve().with_name('agent.py')
    if not all(path.is_file() for path in (node, cli, agent)):
        raise ValueError('bundle node/DSH CLI or RepoWayfinder agent.py is missing')
    if not os.environ.get('DEEPSEEK_API_KEY'):
        raise ValueError('DEEPSEEK_API_KEY must be set in this process environment')
    saved = _model_from_settings(args.model_settings)
    defaults = _bundle_model_defaults(bundle)
    provider = args.provider or saved.get('provider') or defaults.get('provider')
    model = args.model or saved.get('model') or defaults.get('model')
    if not provider or not model:
        raise ValueError('Cannot resolve bundle model defaults; specify --provider and --model')
    model_source = ('explicit' if args.provider or args.model else
                    'settings' if saved.get('provider') and saved.get('model') else
                    'bundle_default')
    reasoning = args.reasoning_effort or saved.get('reasoningEffort') or defaults.get('reasoningEffort')
    output.mkdir(parents=True, exist_ok=True)
    home = output / 'dsh-home'
    home.mkdir()
    tool_mode = getattr(args, 'tool_mode', 'companion')
    permission_mode = getattr(args, 'permission_mode', 'workspace-write')
    (home / 'cordis.patch.yml').write_text(
        _profile_patch(directory, Path(sys.executable).resolve(), agent,
                       permission_mode, companion=tool_mode == 'companion'), encoding='utf-8')
    prompt = prompt_file.read_text(encoding='utf-8-sig') + (TOOL_ONLY_GUIDE if tool_mode == 'tool-only' else GUIDE)
    (output / 'submitted-prompt.txt').write_text(prompt, encoding='utf-8')
    env = os.environ.copy()
    env['DSH_HOME'] = str(home)
    env['DSH_PERMISSION_MODE'] = permission_mode
    env['DSH_TELEMETRY_DISABLED'] = '1'
    # This runner is for local project work, including native continuation.
    # Apply the same accidental-global-pip guard to both comparison groups.
    env['PIP_REQUIRE_VIRTUALENV'] = '1'
    # The MCP bridge itself calls scrubbedParentEnv() before spawning agent.py;
    # env: {} above adds no DeepSeek credential back to that child.
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    process = subprocess.Popen([str(node), str(cli), '--profile', 'sdk'],
                               cwd=directory, env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding='utf-8', errors='replace', bufsize=1)
    events: queue.Queue = queue.Queue()
    for kind, stream in (('stdout', process.stdout), ('stderr', process.stderr)):
        threading.Thread(target=_pump, args=(stream, kind, events), daemon=True).start()
    trace_path = output / 'trace.jsonl'
    stderr_path = output / 'stderr.log'
    pending = {1: 'initialize'}
    notifications = []
    tool_names = []
    usage_samples = []
    answer = ''
    turn_reason = None
    status = None
    error = None
    stdout_closed = False
    deadline = started + args.timeout if args.timeout > 0 else float('inf')
    _send(process, 'initialize', {'cwd': str(directory), 'provider': provider,
                                  'model': model, **({'reasoningEffort': reasoning} if reasoning else {})}, 1)
    try:
        with trace_path.open('w', encoding='utf-8') as trace, stderr_path.open('w', encoding='utf-8') as stderr:
            while time.monotonic() < deadline:
                try:
                    kind, line = events.get(timeout=min(1, max(.01, deadline-time.monotonic())))
                except queue.Empty:
                    if process.poll() is not None and stdout_closed:
                        break
                    continue
                if line is None:
                    if kind == 'stdout':
                        stdout_closed = True
                    if stdout_closed and process.poll() is not None:
                        break
                    continue
                if kind == 'stderr':
                    stderr.write(line)
                    continue
                trace.write(line)
                trace.flush()
                try:
                    frame = json.loads(line)
                except ValueError:
                    continue
                if frame.get('id') in pending:
                    phase = pending.pop(frame['id'])
                    if 'error' in frame:
                        error = f'{phase}: {frame["error"].get("message", "JSON-RPC error")}'
                        break
                    if phase == 'initialize':
                        pending[2] = 'prompt'
                        _send(process, 'session/prompt', {'sessionId': 'session-' + uuid.uuid4().hex,
                               'contentBlocks': [{'type': 'text', 'text': prompt}]}, 2)
                    continue
                method = frame.get('method')
                params = frame.get('params') or {}
                if method == 'session.status':
                    status = params.get('status')
                elif method == 'session.event':
                    event = params.get('event') or {}
                    notifications.append(event.get('type'))
                    data = event.get('data') or {}
                    if event.get('type') == 'tool/call':
                        tool = data.get('name') or data.get('toolName') or data.get('tool')
                        if isinstance(tool, dict):
                            tool = tool.get('name')
                        tool_names.append(tool)
                    elif event.get('type') == 'assistant/message':
                        usage = data.get('usage')
                        if isinstance(usage, dict):
                            usage_samples.append({key: usage.get(key) for key in USAGE_FIELDS if key in usage})
                        message = data.get('message') or {}
                        text = ''.join(block.get('text', '') for block in message.get('content', [])
                                       if block.get('type') == 'text')
                        if text:
                            answer = text
                        if (args.max_model_calls > 0
                                and notifications.count('assistant/message') >= args.max_model_calls
                                and any(block.get('type') == 'tool-call' for block in message.get('content', []))):
                            error = 'model call limit reached; inspect any existing job before retrying'
                            break
                    elif event.get('type') == 'turn/end':
                        turn_reason = data.get('reason')
                        break
            else:
                error = 'timeout'
            if process.poll() is None:
                try:
                    pending[3] = 'shutdown'
                    _send(process, 'shutdown', {}, 3)
                    process.wait(timeout=8)
                except (OSError, subprocess.TimeoutExpired):
                    process.kill()
                    process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
    reason_kind = turn_reason.get('kind') if isinstance(turn_reason, dict) else None
    usage = {field: sum(sample[field] for sample in usage_samples if isinstance(sample.get(field), int))
             for field in USAGE_FIELDS if any(isinstance(sample.get(field), int) for sample in usage_samples)}
    summary = {'dsh_finished': reason_kind == 'completed' and error is None,
               'task_independently_verified': False, 'exit_code': process.returncode,
               'error': error, 'turn_reason': turn_reason, 'last_status': status,
               'provider': provider, 'model': model, 'model_source': model_source,
               'execution_mode': tool_mode, 'reasoning_effort': reasoning,
               'started_at': started_at, 'finished_at': datetime.now(timezone.utc).isoformat(),
               'timing_scope': 'SDK startup through shutdown; excludes harness preparation, other trials and independent artifact verification',
               'wall_seconds': round(time.monotonic()-started, 3),
               'turns': notifications.count('turn/start'),
               'model_calls': notifications.count('assistant/message'),
               'model_calls_with_usage': len(usage_samples), 'tools': tool_names,
               'usage': usage or None, 'usage_samples': usage_samples,
               'reported_cost_usd': None, 'answer': answer,
               'trace_path': str(trace_path), 'stderr_path': str(stderr_path),
               'note': 'Provider usage is not a bill. Inspect rw_run output and fresh task artifacts for acceptance. Background jobs may outlive the SDK process.'}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--prompt-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--model-settings', type=Path)
    parser.add_argument('--provider')
    parser.add_argument('--model')
    parser.add_argument('--reasoning-effort')
    parser.add_argument('--tool-mode', choices=['companion', 'tool-only'], default='companion')
    parser.add_argument('--permissions', dest='permission_mode',
                        choices=['read-only', 'workspace-write', 'danger-full-access'], default='workspace-write')
    parser.add_argument('--timeout', type=int, default=0, help='0 disables the wall-time limit')
    parser.add_argument('--max-model-calls', type=int, default=0, help='0 disables the model response limit')
    args = parser.parse_args()
    if args.timeout < 0 or 0 < args.timeout < 30:
        parser.error('--timeout must be 0 (unlimited) or at least 30 seconds')
    if args.max_model_calls < 0:
        parser.error('--max-model-calls must be nonnegative')
    try:
        summary = run(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    compact = {k: v for k, v in summary.items() if k not in ('answer', 'usage_samples')}
    compact['answer'] = summary['answer'][:1200]
    print(json.dumps(compact, ensure_ascii=False))
    return 0 if summary['dsh_finished'] else 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
