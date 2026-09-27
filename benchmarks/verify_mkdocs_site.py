"""Independent, read-only acceptance of the MkDocs benchmark's real artifacts."""
import argparse
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile

REVISION = 'bb7e8b62185b11d9f59bb7f50b13c15134f62f8a'


class Page(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.ids, self.links, self.images = set(), [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.add(attrs['id'])
        if tag == 'a' and 'href' in attrs:
            self.links.append(attrs['href'])
        if tag == 'img' and 'src' in attrs:
            self.images.append(attrs['src'])


def verify(delivery):
    checkout = delivery.parent
    git = lambda *args: subprocess.check_output(['git', '-C', str(checkout), *args], text=True).strip()
    checks = {'revision': git('rev-parse', 'HEAD') == REVISION,
              'tracked_source_unchanged': not git('status', '--porcelain', '--untracked-files=no')}
    texts = {}
    for stage in ('site-initial', 'site'):
        root = delivery / stage
        pages = {name: (root / name).read_text(encoding='utf-8')
                 for name in ('index.html', 'guide/index.html', 'faq/index.html')}
        texts[stage] = pages
        checks[stage + '_content'] = ('LAB-HOME-2026' in pages['index.html']
                                      and '先备份数据，再开始实验。' in pages['guide/index.html']
                                      and '中文文件统一使用 UTF-8。' in pages['faq/index.html'])
        checks[stage + '_navigation'] = all(all(label in html for label in ('首页', '实验指南', '常见问题')) for html in pages.values())
        parsed = {name: Page(html) for name, html in pages.items()}
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
        checks[stage + '_required_links'] = (
            'guide/#prepare' in parsed['index.html'].links
            and '../faq/#encoding' in parsed['guide/index.html'].links
            and '../' in parsed['faq/index.html'].links
            and 'assets/workflow.svg' in parsed['index.html'].images)
        svg = ET.parse(root / 'assets/workflow.svg').getroot()
        checks[stage + '_svg'] = (svg.tag == '{http://www.w3.org/2000/svg}svg'
                                  and svg.find('.//{http://www.w3.org/2000/svg}rect') is not None
                                  and '准备 → 执行 → 检查' in ''.join(svg.itertext()))
        search = json.loads((root / 'search/search_index.json').read_text(encoding='utf-8'))
        search_text = json.dumps(search, ensure_ascii=False)
        wanted, absent = ('LAB-FAQ-V1', 'LAB-FAQ-V2') if stage == 'site-initial' else ('LAB-FAQ-V2', 'LAB-FAQ-V1')
        checks[stage + '_version'] = wanted in pages['faq/index.html'] and absent not in pages['faq/index.html']
        checks[stage + '_search'] = (wanted in search_text and absent not in search_text
                                   and 'LAB-HOME-2026' in search_text and '先备份数据' in search_text)
    checks['clean_removed_obsolete'] = not (delivery / 'site/obsolete.txt').exists()
    for name in ('initial', 'final'):
        log = (delivery / f'build-{name}.log').read_text(encoding='utf-8-sig')
        checks[f'{name}_build_log'] = 'Documentation built in' in log and 'ERROR' not in log and 'WARNING' not in log
    site = delivery / 'site'
    files = {path.relative_to(site).as_posix(): path.read_bytes() for path in site.rglob('*') if path.is_file()}
    with zipfile.ZipFile(delivery / 'site.zip') as archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        checks['zip_exact_site'] = (len(entries) == len(files) and set(e.filename for e in entries) == set(files)
                                    and all(archive.read(name) == data for name, data in files.items()))
    return {'passed': all(checks.values()), 'checks': checks, 'checkout': str(checkout),
            'delivery': str(delivery), 'site_files': len(files), 'site_bytes': sum(map(len, files.values()))}


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
