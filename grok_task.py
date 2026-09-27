"""Run an authorized Grok task with this project's MCP tools; retain bulk locally."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid


TOOL_GUIDE = '''
RepoWayfinder interface guide:
For an authorized finite repository task, search for exactly "rw_run" with limit 1 first; call repo_wayfinder__rw_run.
rw_run accepts repository, optional revision, commands (array of command strings), and checks.
Pass python_version (for example "3.11") when the task selects a Python version.
Optional files=[{path,content},...] stages new UTF-8 task files before execution (32 files / 256 KiB total).
Submit configuration/documents directly as files. Batch known steps; use short scripts only for necessary glue, not to embed all content or duplicate result checks.
On failure/subsequent stages, reuse the job with rw_replan: exact edits=[{path,old,new}], optional new files, commands for affected steps, checks, execute=true. Existing environment is preserved. Omit commands/checks to reuse them. Checks default to current-run output; use freshness="preserved" for intentionally retained earlier verified artifacts (such as snapshots), requiring identical digest. A request_id prevents duplicate recovery submissions. Reconcile partial effects before retry; do not recreate the job or reinstall unnecessarily.
Installed executables are supported without a command-name allowlist; shell builtins require an explicit shell.
Commands run from the checkout root; python/pip and declared Python CLIs use the job venv; UTF-8 is set.
Python/pip steps create/reuse that venv automatically. Start with python -m pip install; no venv bootstrap or activation is needed.
In Python scripts, launch Python children with sys.executable, not bare python; use [sys.executable, "-m", "pip", ...] for pip and shutil.which for other child CLIs. Shell-only plans without python_version do not create a Python venv.
The service acquires the source, validates it, executes and checks outputs with per-attempt evidence. It does not call another model.
revision pins the checkout and the result returns the verified commit; no extra command is needed to repeat this check.
Do not generate DEPLOY/action/purpose/reason boilerplate. Discover other tools only if the result requires them.
If running, discover rw_status and wait 30 seconds. Read logs/source only to resolve an actual uncertainty or failure.
Report the artifact path, revision and checks briefly. task_verified proves only the supplied assertions.
'''

NATIVE_TOOLS = 'read_file,list_dir,grep,run_terminal_cmd,write_file,search_replace'
_OTHER_MCP_DENIES = ('MCPTool(playwright__*)', 'MCPTool(playwright-live__*)',
                     'MCPTool(x-api__*)', 'MCPTool(chrome-devtools__*)')


def companion_guide():
    from dsh_task import GUIDE
    return ('Search for exactly "rw_run" with limit 1 before its first use; call '
            'repo_wayfinder__rw_run.\n' +
            GUIDE.replace('mcp__repo_wayfinder__rw_run', 'repo_wayfinder__rw_run')
                 .replace('native PowerShell/file tools', 'native terminal/file tools'))


def command_for(args, directory, submitted_prompt):
    mode = getattr(args, 'execution_mode', 'tool-only')
    command = [args.grok, '--cwd', str(directory), '--prompt-file', str(submitted_prompt),
               '--output-format', 'streaming-messages-json',
               '--reasoning-effort', args.reasoning_effort, '--no-subagents', '--disable-web-search']
    if args.max_turns:
        command += ['--max-turns', str(args.max_turns)]
    if getattr(args, 'resume', None):
        command += ['--resume', args.resume]
    if mode == 'tool-only':
        command += ['--permission-mode', 'dontAsk', '--tools', 'read_file,list_dir,grep',
                    '--allow', 'Read', '--allow', 'Grep', '--allow', 'MCPTool(repo_wayfinder__*)',
                    '--deny', 'Bash', '--deny', 'Edit', '--deny', 'Write']
    else:
        command += ['--always-approve', '--tools', NATIVE_TOOLS]
        if mode == 'companion':
            command += ['--allow', 'MCPTool(repo_wayfinder__*)']
        else:
            command += ['--deny', 'MCPTool(*)']
    for rule in _OTHER_MCP_DENIES:
        command += ['--deny', rule]
    if args.model:
        command += ['--model', args.model]
    return command


def summarize(trace):
    result, calls = None, 0
    for line in trace.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') == 'result':
            result = event
        if event.get('type') == 'assistant':
            calls += sum(item.get('type') == 'tool_use' for item in event.get('message', {}).get('content', []))
    return result, calls


def round_metrics(trace):
    rounds = []
    for line in trace.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get('type') != 'assistant':
            continue
        message = event.get('message', {})
        calls = [block for block in message.get('content', []) if block.get('type') == 'tool_use']
        rounds.append({'round': len(rounds) + 1, 'usage': message.get('usage'),
                       'tools': [block.get('input', {}).get('tool_name', block.get('name')) for block in calls],
                       'argument_characters': sum(len(json.dumps(block.get('input', {}))) for block in calls)})
    return rounds


def run(args, guide=None):
    mode = getattr(args, 'execution_mode', 'tool-only')
    if mode not in {'tool-only', 'companion', 'native'}:
        raise ValueError('execution_mode must be tool-only, companion or native')
    directory = args.directory.resolve()
    prompt = args.prompt_file.resolve()
    if not directory.is_dir() or not prompt.is_file() or args.max_turns < 0:
        raise ValueError('Provide an existing directory, readable prompt file and nonnegative max-turns (0 means no cap)')
    output = (args.output or directory / '.agent-data/grok-runs' / uuid.uuid4().hex).resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError('Output directory must be new or empty')
    output.mkdir(parents=True, exist_ok=True)
    trace = output / 'trace.jsonl'
    submitted_prompt = output / 'submitted-prompt.txt'
    selected_guide = guide if guide is not None else (TOOL_GUIDE if mode == 'tool-only' else
                                                       companion_guide() if mode == 'companion' else '')
    submitted_prompt.write_text(prompt.read_text(encoding='utf-8-sig') + '\n' + selected_guide, encoding='utf-8')
    command = command_for(args, directory, submitted_prompt)
    env = os.environ.copy()
    if mode != 'tool-only':
        # Same accidental-global-pip guard in both matched comparison arms.
        env['PIP_REQUIRE_VIRTUALENV'] = '1'
    started_at = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    timed_out, returncode = False, None
    with trace.open('wb') as stdout, (output / 'stderr.log').open('wb') as stderr:
        try:
            process = subprocess.run(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                     stdout=stdout, stderr=stderr, timeout=getattr(args, 'timeout', 0) or None)
            returncode = process.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        except OSError as exc:
            stderr.write(str(exc).encode('utf-8'))
    result, calls = summarize(trace)
    usage = (result or {}).get('usage')
    if not usage or not any(usage.get(key, 0) for key in ('input_tokens', 'output_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')):
        usage = None
    summary = {'grok_finished': bool(result and not result.get('is_error') and returncode == 0),
               'task_independently_verified': False, 'exit_code': returncode, 'timed_out': timed_out,
               'execution_mode': mode, 'model': args.model or '', 'reasoning_effort': args.reasoning_effort,
               'max_turns': args.max_turns, 'timeout_seconds': getattr(args, 'timeout', 0),
               'resumed_session': getattr(args, 'resume', None),
               'permission_mode': 'always-approve' if mode != 'tool-only' else 'dontAsk',
               'native_tools': NATIVE_TOOLS if mode != 'tool-only' else 'read_file,list_dir,grep',
               'started_at': started_at, 'finished_at': datetime.now(timezone.utc).isoformat(),
               'wall_seconds': round(time.monotonic() - started, 3), 'tool_calls': calls,
               'num_turns': (result or {}).get('num_turns'), 'usage': usage,
               'reported_cost_usd': (result or {}).get('total_cost_usd') or None,
               'reported_api_seconds': (result or {}).get('duration_api_ms', 0) / 1000 or None,
               'rounds': round_metrics(trace),
               'answer': (result or {}).get('result', ''), 'trace_path': str(trace),
               'summary_path': str(output / 'summary.json'),
               'stderr_path': str(output / 'stderr.log'), 'submitted_prompt_path': str(submitted_prompt),
               'note': 'Grok-reported usage is not subscription quota or an invoice. Verify task artifacts. Background jobs can outlive this conversation.'}
    (output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prompt-file', type=Path, required=True)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--grok', default=shutil.which('grok') or str(Path.home() / '.grok/bin/grok.exe'))
    parser.add_argument('--model', default=None, help='Omit to keep the configured model')
    parser.add_argument('--max-turns', type=int, default=0, help='Optional turn cap; 0 leaves the CLI uncapped')
    parser.add_argument('--resume', help='Continue an existing Grok session ID in the same task directory')
    parser.add_argument('--reasoning-effort', default='high')
    parser.add_argument('--execution-mode', choices=['tool-only', 'companion', 'native'], default='tool-only')
    parser.add_argument('--timeout', type=int, default=0, help='Optional process timeout in seconds; 0 waits for completion')
    args = parser.parse_args()
    if args.timeout < 0 or 0 < args.timeout < 30:
        parser.error('--timeout must be 0 (no cap) or at least 30 seconds')
    try:
        summary = run(args)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    compact = dict(summary)
    compact.pop('rounds')
    compact['answer'] = compact['answer'][:1200]
    print(json.dumps(compact, ensure_ascii=False))
    return 0 if summary['grok_finished'] else 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
