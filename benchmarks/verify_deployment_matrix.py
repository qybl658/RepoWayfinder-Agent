"""Independent HTTP acceptance for the Bottle, http-server and Flaskr tasks.

Usage: python benchmarks/verify_deployment_matrix.py TASK CHECKOUT/bench-delivery
       [--output PATH]
The verifier starts only the supplied launcher and terminates only its own PID.
"""

import argparse
from contextlib import contextmanager
from html.parser import HTMLParser
from http.cookiejar import CookieJar
import json
from pathlib import Path
import re
import socket
import sqlite3
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, HTTPRedirectHandler, Request, build_opener


REVISIONS = {
    'bottle_quote_zh': 'b0a0a1014f7a7e6f6b7c193886a0913f46ee3ee6',
    'http_server_static_zh': 'af0ac3e4b9bd5fff55337aee32bf37f6116c7b4f',
    'flaskr_state_zh': '2c1b30d0503cfb064f1cb252e6614a06915a362a',
}
PYTHON_BASE = Path.home() / 'AppData/Local/Programs/Python/Python311/python.exe'
CSV_BYTES = 'name,count\n甲,2\n乙,5\n'.encode('utf-8')


class VerificationError(Exception):
    pass


class Results:
    def __init__(self, task, delivery):
        self.task = task
        self.delivery = delivery
        self.checkout = delivery.parent
        self.checks = {}
        self.provenance = {'task': task, 'checkout': str(self.checkout), 'delivery': str(delivery)}
        self.errors = []

    def check(self, name, condition, detail=''):
        self.checks[name] = bool(condition)
        if not condition:
            self.errors.append(f'{name}: {detail or "check failed"}')
        return bool(condition)

    def need(self, name, condition, detail=''):
        if not self.check(name, condition, detail):
            raise VerificationError(name)

    def finish(self):
        return {'passed': bool(self.checks) and all(self.checks.values()) and not self.errors,
                'checks': self.checks, 'provenance': self.provenance, 'errors': self.errors}


def command(*args, cwd=None, timeout=15):
    return subprocess.check_output(args, cwd=cwd, text=True, encoding='utf-8',
                                   errors='replace', stderr=subprocess.STDOUT,
                                   timeout=timeout).strip()


def source_checks(r):
    r.need('delivery_inside_checkout', r.delivery.name == 'bench-delivery' and
           r.delivery.is_dir() and r.delivery.parent.is_dir(), 'expected CHECKOUT/bench-delivery')
    revision = command('git', '-C', str(r.checkout), 'rev-parse', 'HEAD')
    r.provenance['revision'] = revision
    r.need('fixed_revision', revision == REVISIONS[r.task], revision)
    tracked = command('git', '-C', str(r.checkout), 'status', '--porcelain', '--untracked-files=no')
    r.check('tracked_source_unchanged', not tracked, tracked[:350])
    for name in ('README.md', 'smoke.log'):
        path = r.delivery / name
        r.check(name + '_present', path.is_file() and bool(path.read_text(encoding='utf-8-sig').strip()))


def python_checks(r, distribution):
    executable = r.checkout / '.venv' / 'Scripts' / 'python.exe'
    r.need('venv_python_present', executable.is_file(), str(executable))
    script = (
        'import importlib.metadata as m,json,sys; '
        f'd=m.distribution({distribution!r}); '
        'print(json.dumps({"version":list(sys.version_info[:3]),'
        '"executable":sys.executable,"base":sys._base_executable,'
        '"prefix":sys.prefix,"base_prefix":sys.base_prefix,'
        '"direct_url":d.read_text("direct_url.json")},ensure_ascii=False))'
    )
    info = json.loads(command(str(executable), '-I', '-c', script, timeout=15))
    r.provenance['python'] = {key: info[key] for key in ('version', 'executable', 'base')}
    r.check('python_3_11_9', info['version'] == [3, 11, 9], str(info['version']))
    r.check('job_venv', info['prefix'] != info['base_prefix'])
    r.check('selected_base_python', Path(info['base']).resolve() == PYTHON_BASE.resolve(), info['base'])
    direct = json.loads(info['direct_url'] or '{}')
    r.check('installed_from_checkout', r.checkout.as_posix().lower() in
            str(direct.get('url', '')).replace('\\', '/').lower(), str(direct)[:250])
    return executable


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, port):
        self.port = port
        self.opener = build_opener(HTTPCookieProcessor(CookieJar()), NoRedirect())

    def request(self, path, fields=None):
        data = urlencode(fields).encode('utf-8') if fields is not None else None
        req = Request(f'http://127.0.0.1:{self.port}{path}', data=data)
        try:
            with self.opener.open(req, timeout=4) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            with error:
                return error.code, error.headers, error.read()


@contextmanager
def server(argv, port, cwd):
    with tempfile.TemporaryDirectory(prefix='repowayfinder-verify-') as scratch:
        log_path = Path(scratch) / 'server.log'
        with log_path.open('wb') as log:
            process = subprocess.Popen([*map(str, argv), '--port', str(port)], cwd=cwd,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 12
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise VerificationError(f'launcher exited {process.returncode}: '
                                                f'{log_path.read_text(encoding="utf-8", errors="replace")[-800:]}')
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.25):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise VerificationError('launcher did not listen on the requested loopback port')
                yield Client(port)
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=4)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=4)


def json_response(client, path):
    status, headers, raw = client.request(path)
    try:
        value = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f'{path}: invalid UTF-8 JSON: {exc}') from exc
    return status, headers, value


def bottle_checks(r):
    python = python_checks(r, 'bottle')
    app = r.delivery / 'app.py'
    r.need('app_present', app.is_file())
    for attempt in range(2):
        with server([python, app], free_port(), r.checkout) as client:
            status, _, health = json_response(client, '/health')
            r.check(f'health_{attempt}', status == 200 and health.get('status') == 'ok' and
                    health.get('project') == 'bottle-quote')
            status, _, quote = json_response(client, '/quote?sku=notebook&count=3')
            r.check(f'notebook_quote_{attempt}', status == 200 and all(
                quote.get(key) == value for key, value in {
                    'sku': 'notebook', 'name': '实验笔记本', 'count': 3,
                    'unit_cents': 1250, 'total_cents': 3750}.items()))
            status, _, quote = json_response(client, '/quote?sku=pen&count=2')
            r.check(f'pen_quote_{attempt}', status == 200 and all(
                quote.get(key) == value for key, value in {
                    'sku': 'pen', 'name': '中性笔', 'count': 2,
                    'unit_cents': 250, 'total_cents': 500}.items()))
            for label, path in {
                'missing_count': '/quote?sku=pen',
                'noninteger_count': '/quote?sku=pen&count=abc',
                'zero_count': '/quote?sku=pen&count=0',
                'high_count': '/quote?sku=pen&count=101',
            }.items():
                status, _, body = json_response(client, path)
                r.check(f'{label}_{attempt}', status == 400 and isinstance(body.get('error'), str)
                        and bool(body['error']))
            status, _, body = json_response(client, '/quote?sku=unknown&count=1')
            r.check(f'unknown_sku_{attempt}', status == 404 and isinstance(body.get('error'), str)
                    and bool(body['error']))


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.styles = []
        self.title = ''
        self.in_title = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        data = dict(attrs)
        if tag == 'a':
            self.links.append(data.get('href', ''))
        if tag == 'link' and data.get('rel') == 'stylesheet':
            self.styles.append(data.get('href', ''))
        if tag == 'title':
            self.in_title = True

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


def static_checks(r):
    site = r.delivery / 'site'
    launcher = r.delivery / 'serve.cjs'
    r.need('launcher_present', launcher.is_file())
    r.need('site_present', site.is_dir())
    r.check('node_modules_present', (r.checkout / 'node_modules').is_dir())
    source = launcher.read_text(encoding='utf-8')
    direct_require = re.search(r'require\s*\(\s*[\'"]\.\./lib/http-server(?:\.js)?[\'"]\s*\)', source)
    joined_require = re.search(r'require\s*\([^)]*[\'"]lib[\'"][^)]*[\'"]http-server(?:\.js)?[\'"]',
                               source, re.S)
    r.check('source_createServer_used', bool(direct_require or joined_require) and
        bool(re.search(r'\bcreateServer\s*\(', source)))
    node_version = command('node', '--version')
    r.provenance['node'] = node_version
    r.check('node_22_23_1', node_version == 'v22.23.1', node_version)
    r.check('csv_exact_bytes', (site / 'downloads/data.csv').read_bytes() == CSV_BYTES)
    for attempt in range(2):
        with server(['node', launcher], free_port(), r.checkout) as client:
            status, headers, raw = client.request('/index.html')
            html = raw.decode('utf-8')
            page = Page(html)
            r.check(f'index_{attempt}', status == 200 and page.title.strip() == '实验资料站'
                    and 'STATIC-LAB-2026' in html)
            r.check(f'local_links_{attempt}', 'guide.html' in page.links and
                    'downloads/data.csv' in page.links and 'style.css' in page.styles)
            status, _, raw = client.request('/guide.html')
            guide = raw.decode('utf-8')
            r.check(f'guide_{attempt}', status == 200 and '下载' in guide and 'CSV' in guide)
            status, _, raw = client.request('/style.css')
            r.check(f'stylesheet_{attempt}', status == 200 and bool(raw.strip()))
            status, _, raw = client.request('/downloads/data.csv')
            r.check(f'csv_http_{attempt}', status == 200 and raw == CSV_BYTES)
            status, _, _ = client.request('/not-found-acceptance.txt')
            r.check(f'missing_path_404_{attempt}', status == 404)
            r.check(f'cache_disabled_{attempt}', 'no-cache' in headers.get('Cache-Control', ''))


def database_snapshot(path):
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=3) as db:
        users = db.execute('SELECT id, username, password FROM user ORDER BY id').fetchall()
        posts = db.execute('SELECT id, author_id, title, body FROM post ORDER BY id').fetchall()
    return users, posts


def flask_checks(r):
    python = python_checks(r, 'Flask')
    app = r.delivery / 'app.py'
    r.need('app_present', app.is_file())
    demo = r.delivery / 'demo.sqlite'
    r.need('demo_database_present', demo.is_file())
    users, posts = database_snapshot(demo)
    names = {row[1] for row in users}
    r.check('demo_two_users', {'alice', 'bob'}.issubset(names))
    alice_ids = {row[0] for row in users if row[1] == 'alice'}
    r.check('demo_final_post', any(row[1] in alice_ids and row[2] == '实验记录（修订）'
                                   and row[3] == 'CSV 使用 UTF-8。' for row in posts))
    r.check('demo_passwords_hashed', all(row[2] not in ('alice', 'bob', '') for row in users))

    with tempfile.TemporaryDirectory(prefix='repowayfinder-flask-check-') as scratch:
        db_path = Path(scratch) / 'fresh.sqlite'
        launcher = [python, app, '--database', db_path]
        with server(launcher, free_port(), r.checkout) as alice:
            anonymous = Client(alice.port)
            status, headers, _ = anonymous.request('/create')
            r.check('anonymous_create_redirect', status in (301, 302, 303) and
                    urlsplit(headers.get('Location', '')).path == '/auth/login')

            for name in ('alice', 'bob'):
                client = alice if name == 'alice' else Client(alice.port)
                status, headers, _ = client.request('/auth/register',
                                                    {'username': name, 'password': f'{name}-local-2026'})
                r.check(f'{name}_register', status in (301, 302, 303) and
                        urlsplit(headers.get('Location', '')).path == '/auth/login')

            status, headers, _ = alice.request('/auth/login',
                                               {'username': 'alice', 'password': 'alice-local-2026'})
            r.need('alice_login', status in (301, 302, 303) and
                   urlsplit(headers.get('Location', '')).path == '/')
            status, _, _ = alice.request('/create', {'title': '实验记录', 'body': '初版。'})
            r.need('alice_create', status in (301, 302, 303))
            users, posts = database_snapshot(db_path)
            alice_id = next((row[0] for row in users if row[1] == 'alice'), None)
            post = next((row for row in posts if row[1] == alice_id and row[2] == '实验记录'), None)
            r.need('created_post_in_sqlite', post is not None)
            post_id = post[0]
            status, _, _ = alice.request(f'/{post_id}/update',
                                         {'title': '实验记录（修订）', 'body': 'CSV 使用 UTF-8。'})
            r.check('alice_update', status in (301, 302, 303))
            bob = Client(alice.port)
            status, _, _ = bob.request('/auth/login',
                                       {'username': 'bob', 'password': 'bob-local-2026'})
            r.need('bob_login', status in (301, 302, 303))
            status, _, _ = bob.request(f'/{post_id}/update',
                                       {'title': '越权修改', 'body': '不应保存'})
            r.check('bob_update_forbidden', status == 403)
            _, posts = database_snapshot(db_path)
            r.check('post_intact_after_forbidden', any(row[0] == post_id and
                    row[2:] == ('实验记录（修订）', 'CSV 使用 UTF-8。') for row in posts))
            status, _, _ = alice.request('/auth/logout')
            r.check('alice_logout', status in (301, 302, 303))

        before = database_snapshot(db_path)
        with server(launcher, free_port(), r.checkout) as restarted:
            status, _, raw = restarted.request('/')
            r.check('restart_index', status == 200 and '实验记录（修订）' in raw.decode('utf-8'))
            status, _, _ = restarted.request('/auth/login',
                                              {'username': 'alice', 'password': 'alice-local-2026'})
            r.check('alice_login_after_restart', status in (301, 302, 303))
            status, _, raw = restarted.request('/')
            r.check('revised_body_after_restart', status == 200 and
                    'CSV 使用 UTF-8。' in raw.decode('utf-8'))
        r.check('sqlite_unchanged_after_restart', database_snapshot(db_path) == before)


def verify(task, delivery):
    r = Results(task, delivery)
    try:
        source_checks(r)
        {'bottle_quote_zh': bottle_checks,
         'http_server_static_zh': static_checks,
         'flaskr_state_zh': flask_checks}[task](r)
    except Exception as exc:
        if not isinstance(exc, VerificationError) or str(exc) not in r.checks:
            r.errors.append(f'{type(exc).__name__}: {exc}')
    return r.finish()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task', choices=REVISIONS)
    parser.add_argument('delivery', type=Path, help='CHECKOUT/bench-delivery')
    parser.add_argument('--output', type=Path, help='optional JSON result file')
    args = parser.parse_args()
    result = verify(args.task, args.delivery.resolve())
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + '\n', encoding='utf-8')
    print(rendered)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
