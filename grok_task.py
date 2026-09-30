"""Run an authorized Grok task with this project's MCP tools; retain bulk locally."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import threading
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


def message_blocks(event):
    message = event.get('message')
    if not isinstance(message, dict):
        return []
    content = message.get('content')
    return [block for block in content if isinstance(block, dict)] if isinstance(content, list) else []


def summarize(trace):
    result, calls = None, 0
    for line in trace.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'result':
            result = event
        if event.get('type') == 'assistant':
            calls += sum(item.get('type') == 'tool_use' for item in message_blocks(event))
    return result, calls


def round_metrics(trace):
    rounds = []
    for line in trace.read_text(encoding='utf-8', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') != 'assistant':
            continue
        message = event.get('message')
        message = message if isinstance(message, dict) else {}
        calls = [block for block in message_blocks(event) if block.get('type') == 'tool_use']
        arguments = [block.get('input') if isinstance(block.get('input'), dict) else {} for block in calls]
        rounds.append({'round': len(rounds) + 1, 'usage': message.get('usage'),
                       'tools': [arg.get('tool_name', block.get('name')) for block, arg in zip(calls, arguments)],
                       'argument_characters': sum(len(json.dumps(arg)) for arg in arguments)})
    return rounds


def capture_process(command, directory, env, trace, stderr, timing, started, timeout=0):
    """Observe a raw file while the CLI lives; inherited handles cannot delay exit."""
    errors = []
    stopped = threading.Event()
    final_offset = [None]
    with trace.open('wb') as stdout, stderr.open('wb') as error_stream, timing.open('w', encoding='utf-8') as clock_stream:
        process = subprocess.Popen(command, cwd=directory, env=env, stdin=subprocess.DEVNULL,
                                   stdout=stdout, stderr=error_stream)

        def receive():
            try:
                source = trace.open('rb')
                number, pending = 0, b''
                def record_line(raw):
                    nonlocal number
                    number += 1
                    record = {'line': number, 'received_elapsed_seconds': round(received, 6),
                              'received_at': datetime.now(timezone.utc).isoformat()}
                    try:
                        event = json.loads(raw)
                        record['type'] = event.get('type', 'unknown') if isinstance(event, dict) else 'unknown'
                        blocks = message_blocks(event) if isinstance(event, dict) else []
                        record['tool_use_ids'] = [b['id'] for b in blocks if b.get('type') == 'tool_use' and 'id' in b]
                        record['tool_result_ids'] = [b['tool_use_id'] for b in blocks if b.get('type') == 'tool_result' and 'tool_use_id' in b]
                    except (ValueError, KeyError, TypeError):
                        record['type'] = 'unparsed'
                    clock_stream.write(json.dumps(record) + '\n')
                    clock_stream.flush()
                with source:
                    while True:
                        limit = final_offset[0]
                        count = 65536 if limit is None else max(0, min(65536, limit-source.tell()))
                        chunk = source.read(count)
                        received = time.monotonic() - started
                        pending += chunk
                        while b'\n' in pending:
                            raw, pending = pending.split(b'\n', 1)
                            record_line(raw + b'\n')
                        if chunk:
                            continue
                        if stopped.is_set():
                            if pending:
                                record_line(pending)
                            break
                        stopped.wait(.05)
            except Exception as exc:
                errors.append(exc)
                # A sidecar I/O error does not stop the CLI or its background
                # jobs: the OS still writes the authoritative raw trace.

        reader = threading.Thread(target=receive, name='grok-trace-receiver')
        reader.start()
        timed_out = False
        try:
            process.wait(timeout=timeout or None)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            process.wait()
        except BaseException:
            # Match subprocess.run's interruption behavior for this owned CLI,
            # without killing the separately owned background process tree.
            process.kill()
            process.wait()
            raise
        finally:
            exited = time.monotonic() - started
            final_offset[0] = trace.stat().st_size
            stopped.set()
            reader.join()
        timing.with_name('process-lifecycle.json').write_text(json.dumps({
            'cli_wait_returned_elapsed_seconds': round(exited, 6),
            'observer_finished_elapsed_seconds': round(time.monotonic() - started, 6),
            'scope': 'CLI wait returned, independent of descendant stdout handle lifetime; raw file may receive later descendant output.'}), encoding='utf-8')
        if errors:
            raise OSError(f'Trace capture failed: {errors[0]}') from errors[0]
        return process.returncode, timed_out


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
    timing = output / 'event-timing.jsonl'
    try:
        returncode, timed_out = capture_process(command, directory, env, trace, output / 'stderr.log',
                                               timing, started, getattr(args, 'timeout', 0))
    except OSError as exc:
        with (output / 'stderr.log').open('ab') as stderr:
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
               'event_timing_path': str(timing),
               'event_timing_scope': 'Local observation of written stdout lines while the CLI runs (50 ms sampling). Includes CLI buffering, orchestration, network and generation; not direct reasoning/tool durations. Late descendant raw output is preserved without delaying CLI exit.',
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
