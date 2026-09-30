"""Independent standard-library business acceptance; not candidate verification core."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, ProxyHandler, build_opener


def same(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, (list, tuple)):
        return len(actual) == len(expected) and all(same(a, e) for a, e in zip(actual, expected))
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(same(actual[k], expected[k]) for k in actual)
    return actual == expected


class Client:
    def __init__(self, port):
        self.base = f'http://127.0.0.1:{port}'
        self.opener = build_opener(ProxyHandler({}))

    def request(self, path, body=None, raw=None):
        payload = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else raw
        req = Request(self.base + path, data=payload,
                      headers={'Content-Type': 'application/json'} if payload is not None else {})
        try:
            response = self.opener.open(req, timeout=2)
        except HTTPError as exc:
            response = exc
        with response:
            data = response.read(65537)
            if len(data) > 65536:
                raise ValueError('Independent response limit exceeded')
            return response.status, json.loads(data.decode('utf-8'))


@contextmanager
def server(root, db, evidence, index):
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    with (evidence / f'server-{index}.log').open('wb') as log:
        process = subprocess.Popen([sys.executable, str(root / 'server.py'), '--host', '127.0.0.1',
                                    '--port', str(port), '--db', str(db)], cwd=root,
                                   stdout=log, stderr=subprocess.STDOUT)
        client = Client(port)
        try:
            # A finite independent readiness operation, not a model/task timeout.
            deadline = time.monotonic() + 15
            while True:
                try:
                    client.request('/health')
                    break
                except (URLError, OSError):
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RuntimeError('Owned acceptance service did not become ready')
                    time.sleep(0.05)
            yield client
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def verify(root, scratch):
    scratch.mkdir(exist_ok=False)
    db = scratch / 'acceptance-state.sqlite3'
    checks = {'script_and_readme': (root / 'server.py').is_file() and (root / 'README.md').is_file()}
    texts = ['中文记录：甲', '第二条记录']
    expected = [{'id': 1, 'text': texts[0]}, {'id': 2, 'text': texts[1]}]
    with server(root, db, scratch, 1) as client:
        checks['health'] = same(client.request('/health'), (200, {'ok': True}))
        checks['initial_empty'] = same(client.request('/items'), (200, {'items': []}))
        checks['create_unicode'] = same(client.request('/items', {'text': texts[0]}), (201, expected[0]))
        checks['create_increment'] = same(client.request('/items', {'text': texts[1]}), (201, expected[1]))
        checks['ordered_list'] = same(client.request('/items'), (200, {'items': expected}))
        checks['get_existing'] = same(client.request('/items/1'), (200, expected[0]))
        for key, data in [('malformed', b'{'), ('nonobject', b'[]'), ('missing_text', b'{}'),
                          ('wrong_type', b'{"text":7}'), ('blank_text', b'{"text":"  "}')]:
            status, value = client.request('/items', raw=data)
            checks[key + '_400'] = status == 400 and isinstance(value, dict)
        checks['unknown_404'] = client.request('/unknown')[0] == 404
        checks['missing_id_404'] = client.request('/items/999')[0] == 404
        checks['invalid_no_insert'] = same(client.request('/items'), (200, {'items': expected}))
    with server(root, db, scratch, 2) as client:
        checks['restart_health'] = same(client.request('/health'), (200, {'ok': True}))
        checks['restart_persistent'] = same(client.request('/items'), (200, {'items': expected}))
        checks['restart_increment'] = same(client.request('/items', {'text': '重启后'}), (201, {'id': 3, 'text': '重启后'}))
    return {'passed': all(checks.values()), 'checks': checks}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.root, args.scratch)
    except Exception as exc:
        result = {'passed': False, 'error': f'{type(exc).__name__}: {exc}'}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
