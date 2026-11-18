"""Rebuild the operator byte pins in a repository checkout. No installation/network."""
from pathlib import Path
import hashlib
import json
import re

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'native' / 'yc59_assisted'


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('ascii') + b'\n'


def build():
    names = ('client-adapter.py', 'common.ps1', 'compare-results.py', 'console-bridge.cs',
             'fixture.py', 'flow.ps1', 'group-read.cs', 'owner-seed.ps1', 'parameters.ps1',
             'reject-only.py', 'saved-inputs.ps1')
    pins = {name: hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest() for name in names}
    seed = (ROOT / 'native' / 'yc_owner_release.ps1').read_bytes()
    if (PACKAGE / 'owner-seed.ps1').read_bytes() != seed:
        raise ValueError('accepted owner seed changed')
    raw = canonical(pins)
    (PACKAGE / 'pins.json').write_bytes(raw)
    launch_path = PACKAGE / 'launch.ps1'
    launch = launch_path.read_text(encoding='utf-8-sig')
    launch, count = re.subn(r"-cne '(?:__PINS_SHA256__|[0-9a-f]{64})'", "-cne '" + hashlib.sha256(raw).hexdigest() + "'", launch)
    if count != 1:
        raise ValueError('launcher pin location changed')
    launch_path.write_bytes(launch.encode('utf-8-sig'))
    manifest = {'schema': 'yc59-assisted-package/v1', 'approval_automated': False, 'consume_retry': False,
                'installed_acceptance': False, 'seed_sha256': hashlib.sha256(seed).hexdigest(),
                'files': [{'path': name, 'size': (PACKAGE / name).stat().st_size,
                           'sha256': hashlib.sha256((PACKAGE / name).read_bytes()).hexdigest()}
                          for name in sorted((*names, 'launch.ps1', 'pins.json'))]}
    raw = canonical(manifest)
    (PACKAGE / 'operator-manifest.json').write_bytes(raw)
    module = ROOT / 'setup_yc_owner_operator.py'
    code = module.read_text(encoding='utf-8')
    code, count = re.subn(r"MANIFEST_SHA256 = '[^']+'", "MANIFEST_SHA256 = '" + hashlib.sha256(raw).hexdigest() + "'", code)
    if count != 1:
        raise ValueError('module pin location changed')
    module.write_text(code, encoding='utf-8')
    print('YC59 assisted byte pins rebuilt')


if __name__ == '__main__':
    build()
