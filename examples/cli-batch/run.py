"""Convert local UTF-8 Markdown files through the production Agent JSON CLI."""
from __future__ import annotations
import argparse
import html
import json
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

REPOSITORY = 'Python-Markdown/markdown'
REVISION = 'f39cf84a24124526c1a0efbe52219fa9950774f6'  # official 3.9.0 tag
LIMIT = 256 * 1024
ACTIVE = {'queued', 'preparing', 'running'}


def request_for(input_directory):
    source = Path(input_directory).resolve(strict=True)
    if not source.is_dir() or source == Path(source.anchor):
        raise ValueError('input-directory must be a concrete directory, not a drive root')
    helper = Path(__file__).with_name('convert.py').read_text(encoding='utf-8')
    files = [{'path': 'cli-batch/convert.py', 'content': helper}]
    names, seen = [], set()
    for path in sorted(source.iterdir(), key=lambda p: p.name.casefold()):
        if path.suffix.lower() != '.md':
            continue
        if path.is_symlink() or path.resolve().parent != source or not path.is_file():
            raise ValueError('Markdown inputs must be ordinary files inside input-directory')
        output = path.stem + '.html'
        if output.casefold() in seen:
            raise ValueError('Markdown filenames would produce duplicate HTML names')
        seen.add(output.casefold())
        content = path.read_bytes().decode('utf-8-sig')
        files.append({'path': 'cli-batch/input/' + path.name, 'content': content})
        names.append(output)
    if not names or len(files) > 32:
        raise ValueError('Supply 1-31 Markdown files; convert.py also consumes one files slot')
    if sum(len(f['content'].encode('utf-8')) for f in files) > LIMIT:
        raise ValueError('Input texts plus convert.py exceed the 256 KiB files-content limit')
    checks = []
    for name in names:
        relative = 'cli-batch/output/' + name
        checks.extend([
            {'type': 'file_exists', 'path': relative},
            {'type': 'file_contains', 'path': relative, 'expected': '<meta charset="utf-8">'},
            {'type': 'file_contains', 'path': relative,
             'expected': '<title>' + html.escape(Path(name).stem) + '</title>'},
        ])
    payload = {'repository': REPOSITORY, 'revision': REVISION, 'python_version': '3.11',
               'commands': ['python -m markdown --version', 'python cli-batch/convert.py'],
               'files': files, 'checks': checks, 'request_id': 'cli-batch-' + uuid.uuid4().hex}
    if len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) > LIMIT:
        raise ValueError('The complete JSON request exceeds 256 KiB; split this batch')
    return payload, names


def call(agent_python, agent, workspace, control, tool, payload, number):
    request = control / f'{number:03d}-{tool}-request.json'
    request.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    process = subprocess.run([str(agent_python), str(agent), '--workspace', str(workspace),
                              'call', tool, '--file', str(request)], capture_output=True)
    (control / f'{number:03d}-{tool}-stderr.log').write_bytes(process.stderr)
    try:
        response = json.loads(process.stdout.decode('utf-8'))
    except (ValueError, UnicodeError) as exc:
        (control / f'{number:03d}-{tool}-stdout.log').write_bytes(process.stdout)
        raise RuntimeError('Agent did not return JSON; inspect local control logs') from exc
    (control / f'{number:03d}-{tool}-response.json').write_text(
        json.dumps(response, ensure_ascii=False, indent=2), encoding='utf-8')
    if process.returncode or response.get('ok') is False:
        raise RuntimeError(response.get('message', 'Agent call failed; inspect local control evidence'))
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-directory', type=Path, default=Path(__file__).with_name('input'))
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--output-directory', type=Path)
    args = parser.parse_args()
    repository_root = Path(__file__).resolve().parents[2]
    agent = repository_root / 'agent.py'
    if not agent.is_file():
        raise ValueError('Place this example under a RepoWayfinder Agent checkout')
    payload, names = request_for(args.input_directory)  # before any output/control writes
    # Reuse the product's installed-runtime selector; the Python launching this
    # stdlib-only entry need not have the Agent's own dependencies installed.
    sys.path.insert(0, str(repository_root))
    from configure_clients import choose_python
    agent_python = choose_python(repository_root)
    workspace = (args.workspace or repository_root / '.agent-data').resolve()
    output_root = (args.output_directory or workspace / 'cli-batch-deliveries').resolve()
    if output_root.exists() and not output_root.is_dir():
        raise ValueError('output-directory must be a directory')
    control = workspace / 'cli-batch-controls' / uuid.uuid4().hex
    control.mkdir(parents=True, exist_ok=False)
    response = call(agent_python, agent, workspace, control, 'rw_run', payload, 1)
    job_id = response.get('job_id')
    number = 1
    while response.get('status') in ACTIVE:
        print(json.dumps({'job_id': job_id, 'status': response['status']}, ensure_ascii=False), flush=True)
        number += 1
        response = call(agent_python, agent, workspace, control, 'rw_status',
                        {'job_id': job_id, 'wait_seconds': 10}, number)
    result = response.get('result', {})
    if response.get('status') != 'completed' or not result.get('task_verified'):
        print(json.dumps({'ok': False, 'job_id': job_id, 'status': response.get('status'),
                          'reason': response.get('reason'), 'control_directory': str(control),
                          'next_action': 'Inspect rw_logs, then rw_replan this job with affected commands/checks.'}, ensure_ascii=False))
        return 2
    checkout = Path(response['project_path']).resolve()
    generated = checkout / 'cli-batch/output'
    sources = []
    for name in names:
        path = generated / name
        if not path.is_file() or path.is_symlink() or path.resolve().parent != generated.resolve():
            raise ValueError('Expected current-job output missing or linked: ' + name)
        sources.append(path)
    output_root.mkdir(parents=True, exist_ok=True)
    delivery = output_root / job_id
    delivery.mkdir(exist_ok=False)  # never overwrite an earlier delivery
    for path in sources:
        shutil.copyfile(path, delivery / path.name)
    manifest = generated / 'manifest.json'
    shutil.copyfile(manifest, delivery / 'manifest.json')
    print(json.dumps({'ok': True, 'job_id': job_id, 'revision': response['revision'],
                      'project_path': str(checkout), 'delivery_directory': str(delivery),
                      'artifacts': [str(delivery / n) for n in names],
                      'control_directory': str(control),
                      'verification_scope': 'file presence, UTF-8 metadata and titles; review task-specific business content separately'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    try:
        raise SystemExit(main())
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({'ok': False, 'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False))
        raise SystemExit(2)
