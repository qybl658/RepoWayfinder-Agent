"""RepoWayfinder Agent: JSON CLI and MCP entry point."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, default=None)
    sub = parser.add_subparsers(dest='operation', required=True)
    sub.add_parser('serve', help='Run MCP over stdio')
    call = sub.add_parser('call', help='Call an Agent tool; stdout is one JSON object')
    call.add_argument('tool')
    group = call.add_mutually_exclusive_group()
    group.add_argument('--json', default=None)
    group.add_argument('--file', type=Path)
    worker = sub.add_parser('_worker', help=argparse.SUPPRESS)
    worker.add_argument('job_id')
    worker.add_argument('phase', choices=['prepare', 'replan', 'execute', 'resume'])
    worker.add_argument('--token', required=True)
    args = parser.parse_args()
    from agent_service import AgentService
    service = AgentService(args.workspace)
    if args.operation == 'serve':
        from mcp_server import serve
        serve(service)
        return 0
    if args.operation == '_worker':
        return service.worker(args.job_id, args.phase, require_ownership=True, token=args.token)
    try:
        payload = json.loads(args.file.read_text(encoding='utf-8-sig') if args.file else args.json or '{}')
        result = service.dispatch(args.tool, payload)
    except Exception as exc:
        result = {'ok': False, 'error': type(exc).__name__, 'message': str(exc)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok', True) else 2


if __name__ == '__main__':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    raise SystemExit(main())
