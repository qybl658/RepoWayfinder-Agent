"""Read-only acceptance: usable links, stem-aware search, source install and exact ZIP."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from verify_mkdocs_site import Page

REVISION = '847ad0c991e21db9daa02fec09acbd456f353300'


class ProjectPythonError(ValueError):
    def __init__(self, message, evidence):
        super().__init__(message)
        self.evidence = evidence


def _project_python(checkout, requested, version):
    roots = sorted(checkout.glob('*/pyvenv.cfg'))
    python = (requested.resolve() if requested is not None else
              roots[0].parent / 'Scripts/python.exe' if len(roots) == 1 else None)
    evidence = {'requested_python': str(requested) if requested is not None else None,
                'selected_python': str(python) if python is not None else None,
                'expected_version': version,
                'root_venvs': [str(path.parent) for path in roots]}
    if python is None:
        raise ProjectPythonError('Expected exactly one source-root virtual environment; pass --python to select one', evidence)
    venv = python.parent.parent
    if (venv.parent.resolve() != checkout.resolve() or
            python.name.lower() != 'python.exe' or python.parent.name.lower() != 'scripts' or
            not (venv / 'pyvenv.cfg').is_file() or not python.is_file()):
        raise ProjectPythonError('Selected Python must be in a virtual environment directly under the source checkout', evidence)
    program = ('import json, sys; print(json.dumps({"executable": sys.executable, '
               '"prefix": sys.prefix, "base_prefix": sys.base_prefix, '
               '"version": list(sys.version_info[:3])}))')
    result = subprocess.run([str(python), '-c', program], cwd=checkout,
                            text=True, encoding='utf-8', capture_output=True, timeout=30)
    if result.returncode:
        raise ProjectPythonError(f'Selected Python failed to start ({result.returncode}): {result.stderr[-500:]}', evidence)
    details = json.loads(result.stdout)
    evidence.update(details)
    if (Path(details['prefix']).resolve() != venv.resolve() or
            Path(details['base_prefix']).resolve() == venv.resolve() or
            Path(details['executable']).resolve() != python.resolve()):
        raise ProjectPythonError('Selected Python is not running from its source-root virtual environment', evidence)
    if version is not None:
        if not re.fullmatch(r'\d+\.\d+', version):
            raise ProjectPythonError('--python-version must be MAJOR.MINOR, for example 3.11', evidence)
        actual = '.'.join(map(str, details['version'][:2]))
        if actual != version:
            raise ProjectPythonError(f'Selected Python is {actual}; expected {version}', evidence)
    return python, evidence


def verify(delivery, python=None, python_version=None):
    checkout = delivery.parent
    git = lambda *args: subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()
    checks = {'revision': git('rev-parse', 'HEAD') == REVISION,
              'tracked_source_unchanged': not git('status', '--porcelain', '--untracked-files=no')}
    environment = {'requested_python': str(python) if python is not None else None,
                   'expected_version': python_version}
    try:
        selected_python, environment = _project_python(checkout, python, python_version)
        environment['valid'] = True
    except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        selected_python = None
        if isinstance(exc, ProjectPythonError):
            environment = exc.evidence
        environment['valid'] = False
        environment['error'] = f'{type(exc).__name__}: {exc}'
    # Use the installed Sphinx search language, not a literal substring assumption.
    program = '''
import json, sys
from pathlib import Path
from importlib.metadata import distribution
from sphinx.search import js_index
from sphinx.search.en import SearchEnglish
language = SearchEnglish({})
d = distribution('Sphinx')
out = {'version': d.version, 'direct_url': json.loads(d.read_text('direct_url.json') or '{}'), 'search': {}}
for stage in ('site-initial', 'site'):
    index = js_index.loads((Path(sys.argv[1])/stage/'searchindex.js').read_text(encoding='utf-8'))
    result = {'docnames': index['docnames']}
    for word in ('initialcatalog', 'revisedcatalog'):
        term = language.stem(word)
        found = set()
        for field in ('terms', 'titleterms'):
            value = index.get(field, {}).get(term, [])
            found.update([value] if isinstance(value, int) else value)
        result[word] = [index['docnames'][i] for i in sorted(found)]
    out['search'][stage] = result
print(json.dumps(out))
'''
    info = None
    if selected_python is not None:
        result = subprocess.run([str(selected_python), '-c', program, str(delivery)],
                                cwd=checkout, text=True, encoding='utf-8', capture_output=True, timeout=30)
        if result.returncode:
            environment['search_error'] = f'Sphinx inspection exited {result.returncode}: {result.stderr[-500:]}'
        else:
            try:
                info = json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                environment['search_error'] = f'Invalid Sphinx inspection output: {exc}'
    source_url = urlsplit(info['direct_url'].get('url', '')) if info else None
    source_path = unquote(source_url.path).lstrip('/') if source_url else None
    checks['installed_from_checkout'] = bool(source_url and source_url.scheme == 'file' and
                                             Path(source_path).resolve() == checkout.resolve() and
                                             info['version'] == '8.2.3')
    for stage in ('site-initial', 'site'):
        root = delivery / stage
        pages = {name: (root / name).read_text(encoding='utf-8') for name in ('index.html', 'procedure.html', 'faq.html')}
        parsed = {name: Page(text) for name, text in pages.items()}
        checks[stage + '_content'] = ('COURSE-HOME-2026' in pages['index.html'] and '课程实验手册' in pages['index.html']
                                      and '先检查样本，再记录实验。' in pages['procedure.html']
                                      and 'CSV 文件统一使用 UTF-8。' in pages['faq.html'])
        links = parsed['index.html'].links
        checks[stage + '_toctree'] = ('procedure.html' in links and 'faq.html' in links
                                      and links.index('procedure.html') < links.index('faq.html'))
        checks[stage + '_required_links'] = ('procedure.html#collect-data' in links
                                            and 'faq.html#csv-encoding' in parsed['procedure.html'].links
                                            and 'index.html' in parsed['faq.html'].links)
        broken = []
        for name, page in parsed.items():
            for link in page.links + page.images:
                url = urlsplit(link)
                if url.scheme or url.netloc or not url.path and not url.fragment:
                    continue
                target = ((root / name).parent / unquote(url.path) if url.path else root / name).resolve()
                if not target.is_relative_to(root.resolve()) or not target.is_file():
                    broken.append(link)
                elif url.fragment and target.suffix == '.html' and unquote(url.fragment) not in Page(target.read_text(encoding='utf-8')).ids:
                    broken.append(link)
        checks[stage + '_local_links_resolve'] = not broken
        image_paths = [root / unquote(urlsplit(src).path) for src in parsed['index.html'].images if src.endswith('course.svg')]
        checks[stage + '_svg'] = len(image_paths) == 1
        if image_paths:
            svg = ET.parse(image_paths[0]).getroot()
            checks[stage + '_svg'] &= (svg.tag == '{http://www.w3.org/2000/svg}svg'
                                       and svg.find('.//{http://www.w3.org/2000/svg}rect') is not None
                                       and '采样 → 实验 → 归档' in ''.join(svg.itertext()))
        wanted, absent = ('initialcatalog', 'revisedcatalog') if stage == 'site-initial' else ('revisedcatalog', 'initialcatalog')
        checks[stage + '_version'] = wanted in pages['faq.html'] and absent not in pages['faq.html']
        indexed = info['search'][stage] if info else None
        checks[stage + '_search'] = bool(indexed and 'faq' in indexed[wanted] and 'faq' not in indexed[absent]
                                         and set(indexed['docnames']) == {'index', 'procedure', 'faq'})
    checks['clean_removed_obsolete'] = not (delivery / 'site/obsolete.txt').exists()
    for name in ('initial', 'final'):
        log = (delivery / f'build-{name}.log').read_text(encoding='utf-8-sig')
        checks[name + '_build_log'] = 'build succeeded' in log and 'WARNING:' not in log and 'ERROR:' not in log
    site = delivery / 'site'
    files = {path.relative_to(site).as_posix(): path.read_bytes() for path in site.rglob('*') if path.is_file()}
    with zipfile.ZipFile(delivery / 'site.zip') as archive:
        entries = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        checks['zip_exact_site'] = len(entries) == len(files) and set(entries) == set(files) and all(archive.read(name) == data for name, data in files.items())
    return {'passed': environment['valid'] and all(checks.values()), 'checks': checks,
            'environment': environment, 'checkout': str(checkout), 'delivery': str(delivery),
            'search_evidence': info['search'] if info else None,
            'site_files': len(files), 'site_bytes': sum(map(len, files.values()))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('delivery', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python', type=Path, help='Source-root virtual environment interpreter to verify')
    parser.add_argument('--python-version', help='Required MAJOR.MINOR interpreter version, for example 3.11')
    args = parser.parse_args()
    try:
        result = verify(args.delivery.resolve(), args.python, args.python_version)
    except Exception as exc:
        result = {'passed': False, 'error': f'{type(exc).__name__}: {exc}'}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
