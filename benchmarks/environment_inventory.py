"""Read-only package/version inventories for named base Python interpreters.

Snapshots detect persistent package changes, not temporary writes or arbitrary
filesystem access. Do not use a clean diff as an OS confinement claim.
"""
import argparse
import json
from pathlib import Path
import subprocess


PROBE = r'''
import importlib.metadata as metadata
import json, site, sys
from pathlib import Path
paths = list(dict.fromkeys([*site.getsitepackages(), site.getusersitepackages()]))
packages = []
for path in paths:
    for dist in metadata.distributions(path=[path]):
        packages.append({'name': dist.metadata['Name'], 'version': dist.version, 'site': path})
print(json.dumps({'executable': sys.executable, 'version': sys.version,
                  'prefix': sys.prefix, 'base_prefix': sys.base_prefix,
                  'packages': sorted(packages, key=lambda p: (p['name'].lower(), p['version'], p['site']))}))
'''


def inventory(interpreters):
    result = []
    for python in interpreters:
        probe = subprocess.run([str(python), '-I', '-c', PROBE], capture_output=True,
                               text=True, encoding='utf-8', timeout=30)
        if probe.returncode:
            raise RuntimeError(f'Inventory failed for {python}: {probe.stderr[-500:]}')
        result.append(json.loads(probe.stdout))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', type=Path, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Preserve existing snapshots; choose a new output path')
    result = inventory(args.python)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'snapshot': str(args.output),
                      'interpreters': [{'version': p['version'].split()[0],
                                        'packages': len(p['packages'])} for p in result]}))
