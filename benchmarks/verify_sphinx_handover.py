"""Independent acceptance for the fresh Sphinx handover benchmark."""
import argparse
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

from verify_mkdocs_site import Page

REVISION = '847ad0c991e21db9daa02fec09acbd456f353300'


def verify(delivery):
    checkout = delivery.parent
    git = lambda *args: subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()
    checks = {'revision': git('rev-parse', 'HEAD') == REVISION,
              'tracked_source_unchanged': not git('status', '--porcelain', '--untracked-files=no')}
    for stage in ('site-initial', 'site'):
        root = delivery / stage
        pages = {name: (root / name).read_text(encoding='utf-8') for name in ('index.html', 'workflow.html', 'faq.html')}
        parsed = {name: Page(text) for name, text in pages.items()}
        checks[stage + '_content'] = ('HANDOVER-HOME-2026' in pages['index.html']
                                      and '先核对数据，再运行分析。' in pages['workflow.html']
                                      and '中文资料统一保存为 UTF-8。' in pages['faq.html'])
        checks[stage + '_references'] = ('workflow.html#prepare-step' in parsed['index.html'].links
                                         and 'faq.html#encoding-note' in parsed['workflow.html'].links
                                         and 'index.html' in parsed['faq.html'].links)
        broken = []
        for name, page in parsed.items():
            for link in page.links + page.images:
                url = urlsplit(link)
                if url.scheme or url.netloc or not url.path and not url.fragment:
                    continue
                target = (root / name).parent / unquote(url.path) if url.path else root / name
                target = target.resolve()
                if not target.is_relative_to(root.resolve()):
                    broken.append(link)
                    continue
                if target.is_dir():
                    target /= 'index.html'
                if not target.is_file():
                    broken.append(link)
                elif url.fragment and target.suffix == '.html' and unquote(url.fragment) not in Page(target.read_text(encoding='utf-8')).ids:
                    broken.append(link)
        checks[stage + '_local_links_resolve'] = not broken
        image_paths = [root / unquote(urlsplit(src).path) for src in parsed['index.html'].images if src.endswith('handover.svg')]
        checks[stage + '_svg'] = len(image_paths) == 1
        if image_paths:
            svg = ET.parse(image_paths[0]).getroot()
            checks[stage + '_svg'] &= (svg.tag == '{http://www.w3.org/2000/svg}svg'
                                       and svg.find('.//{http://www.w3.org/2000/svg}rect') is not None
                                       and '资料 → 分析 → 复核' in ''.join(svg.itertext()))
        wanted, absent = ('releaseoldmarker', 'releasenewmarker') if stage == 'site-initial' else ('releasenewmarker', 'releaseoldmarker')
        checks[stage + '_version'] = wanted in pages['faq.html'] and absent not in pages['faq.html']
        search = (root / 'searchindex.js').read_text(encoding='utf-8')
        checks[stage + '_search'] = wanted in search and absent not in search and all(page in search for page in ('index', 'workflow', 'faq'))
    checks['clean_removed_obsolete'] = not (delivery / 'site/obsolete.txt').exists()
    for name in ('initial', 'final'):
        log = (delivery / f'build-{name}.log').read_text(encoding='utf-8-sig')
        checks[name + '_build_log'] = 'build succeeded' in log and 'WARNING:' not in log and 'ERROR:' not in log
    site = delivery / 'site'
    files = {path.relative_to(site).as_posix(): path.read_bytes() for path in site.rglob('*') if path.is_file()}
    with zipfile.ZipFile(delivery / 'site.zip') as archive:
        entries = [entry.filename for entry in archive.infolist() if not entry.is_dir()]
        checks['zip_exact_site'] = len(entries) == len(files) and set(entries) == set(files) and all(archive.read(name) == data for name, data in files.items())
    return {'passed': all(checks.values()), 'checks': checks, 'checkout': str(checkout), 'delivery': str(delivery),
            'site_files': len(files), 'site_bytes': sum(map(len, files.values()))}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('delivery', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    try:
        result = verify(args.delivery.resolve())
    except Exception as exc:
        result = {'passed': False, 'error': f'{type(exc).__name__}: {exc}'}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['passed'] else 1)
