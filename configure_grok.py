"""Register this checkout as a project-local Grok MCP server; no credentials."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sys
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--workspace', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    directory = args.directory.resolve()
    path = directory / '.grok' / 'config.toml'
    existing = path.read_text(encoding='utf-8') if path.exists() else ''
    config = tomllib.loads(existing)
    if 'repo_wayfinder' in config.get('mcp_servers', {}):
        print(json.dumps({'configured': False, 'reason': 'repo_wayfinder already exists; review it rather than overwriting', 'path': str(path)}))
        return 1
    command = [str(root / 'agent.py'), '--workspace', str((args.workspace or root / '.agent-data').resolve()), 'serve']
    entry = '\n[mcp_servers.repo_wayfinder]\ncommand = ' + json.dumps(sys.executable) + '\n'
    entry += 'args = ' + json.dumps(['-u', *command]) + '\n'
    entry += 'cwd = ' + json.dumps(str(root)) + '\nenabled = true\nstartup_timeout_sec = 30\ntool_timeout_sec = 75\n'
    tomllib.loads(existing + entry)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + '.backup-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S-%f')))
    path.write_text(existing + entry, encoding='utf-8')
    print(json.dumps({'configured': True, 'scope': 'project', 'path': str(path), 'server': 'repo_wayfinder'}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
