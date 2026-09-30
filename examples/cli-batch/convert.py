"""Small batch glue; Markdown parsing is performed by the upstream CLI."""
from __future__ import annotations
import argparse
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit, urlunsplit

EXTENSIONS = ['markdown.extensions.tables', 'markdown.extensions.fenced_code',
              'markdown.extensions.attr_list']
STYLE = '''body{max-width:52rem;margin:2rem auto;padding:0 1rem;font:1rem/1.7 system-ui,sans-serif;color:#202124}nav{padding-bottom:1rem;border-bottom:1px solid #ddd}nav a{margin-right:1rem}a{color:#1456a0}table{border-collapse:collapse;width:100%}th,td{border:1px solid #ccc;padding:.4rem .7rem;text-align:left}pre{padding:1rem;background:#f4f5f6;overflow:auto}code{font-family:Consolas,monospace}'''


def rewrite_links(fragment, outputs):
    """Edit only actual anchor href attributes targeting this batch's inputs."""
    line_starts, position = [], 0
    for line in fragment.splitlines(keepends=True):
        line_starts.append(position)
        position += len(line)
    edits = []
    class Links(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag != 'a':
                return
            raw = self.get_starttag_text()
            match = re.search(r'''(?<![\w:-])href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))''', raw, re.I)
            if not match:
                return
            group = next(i for i in (1, 2, 3) if match.group(i) is not None)
            value = html.unescape(match.group(group))
            parsed = urlsplit(value)
            name = unquote(parsed.path)
            if name.startswith('./'):
                name = name[2:]
            if parsed.scheme or parsed.netloc or name not in outputs:
                return
            target = urlunsplit(('', '', quote(outputs[name], safe=''), parsed.query, parsed.fragment))
            start = line_starts[self.getpos()[0] - 1] + self.getpos()[1]
            edits.append((start + match.start(group), start + match.end(group), html.escape(target, quote=True)))
        handle_startendtag = handle_starttag
    parser = Links(convert_charrefs=False)
    parser.feed(fragment)
    parser.close()
    for start, end, value in reversed(edits):
        fragment = fragment[:start] + value + fragment[end:]
    return fragment


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('filenames', nargs='*', help='Optional affected input filenames for explicit recovery')
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    inputs, outputs, work = root / 'input', root / 'output', root / 'work'
    available = sorted((p for p in inputs.iterdir() if p.suffix.lower() == '.md'), key=lambda p: p.name.casefold())
    mapping = {p.name: p.stem + '.html' for p in available}
    names = args.filenames or list(mapping)
    if not names:
        raise ValueError('No Markdown inputs')
    # Preflight all selected inputs before generating any output.
    for name in names:
        path = inputs / name
        if name not in mapping or path.is_symlink() or not path.is_file() or path.resolve().parent != inputs.resolve():
            raise ValueError('Missing or unsafe input: ' + name)
    outputs.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    manifest_path = outputs / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'tool': 'Python-Markdown', 'source_tag': '3.9.0', 'documents': {}}
    navigation = '<nav>' + ' '.join('<a href="' + quote(target, safe='') + '">' + html.escape(Path(source).stem) + '</a>' for source, target in mapping.items()) + '</nav>'
    for name in names:
        source = inputs / name
        fragment_path = work / mapping[name]
        argv = [sys.executable, '-m', 'markdown', '-e', 'utf-8', '-o', 'html']
        for extension in EXTENSIONS:
            argv.extend(['-x', extension])
        argv.extend(['-f', str(fragment_path), str(source)])
        completed = subprocess.run(argv, capture_output=True)
        (work / (name + '.stderr.log')).write_bytes(completed.stderr)
        if completed.returncode:
            raise RuntimeError('Markdown CLI failed for ' + name + '; inspect its local stderr log')
        fragment = rewrite_links(fragment_path.read_text(encoding='utf-8'), mapping)
        title = html.escape(Path(name).stem)
        page = '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>' + title + '</title><style>' + STYLE + '</style></head><body>' + navigation + '<main>\n' + fragment + '\n</main></body></html>\n'
        (outputs / mapping[name]).write_text(page, encoding='utf-8', newline='\n')
        manifest['documents'][name] = {'output': mapping[name], 'CLI_module': 'markdown',
                                      'CLI_returncode': 0, 'extensions': EXTENSIONS}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'converted': len(names), 'outputs': [mapping[n] for n in names], 'CLI_module': 'markdown'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
