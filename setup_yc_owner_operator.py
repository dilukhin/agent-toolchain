"""Stage the pinned assisted operator as data; never run or approve anything."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import setup_yc_owner_component as library

MANIFEST_SHA256 = 'f7360b68153556405bd73d24cf43bfa236d6169c8ff3afd4ce8c2bc2589b1916'
SOURCE = Path(__file__).resolve().parent / 'native' / 'yc59_assisted'
MARKER = b'agent-toolchain:yc59-assisted-staging:v1\n'
DOMAIN = b'agent-toolchain.yc59-assisted/v1\n'
MAX_FILE = 131072


def _read(path):
    library._ordinary(path)
    library.require(0 < path.stat().st_size <= MAX_FILE, 'YC59_OPERATOR_FILE_SIZE')
    with path.open('rb') as stream:
        value = stream.read(MAX_FILE + 1)
    library.require(0 < len(value) <= MAX_FILE, 'YC59_OPERATOR_FILE_SIZE')
    return value


def _source(source):
    source = Path(source)
    library._ancestors(source)
    library._ordinary(source, directory=True)
    raw = _read(source / 'operator-manifest.json')
    library.require(hashlib.sha256(raw).hexdigest() == MANIFEST_SHA256,
                    'YC59_OPERATOR_MANIFEST_CHANGED')
    manifest = json.loads(raw)
    names = {row['path'] for row in manifest['files']}
    library.require({p.name for p in source.iterdir()} == names | {'operator-manifest.json'},
                    'YC59_OPERATOR_SOURCE_FILESET')
    payload = {'operator-manifest.json': raw}
    for row in manifest['files']:
        name = row['path']
        library.require(Path(name).name == name and '/' not in name and '\\' not in name,
                        'YC59_OPERATOR_SOURCE_PATH')
        content = _read(source / name)
        library.require(len(content) == row['size'] and hashlib.sha256(content).hexdigest() == row['sha256'],
                        'YC59_OPERATOR_SOURCE_CHANGED')
        payload[name] = content
    return manifest, payload


def _root(data):
    root = Path(data).absolute() / 'tools' / 'yc59-assisted'
    library._ancestors(root)
    return root


def _write_new(path, raw):
    with path.open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def _validate(package, payload, complete):
    library._ordinary(package, directory=True)
    library.require({p.name for p in package.iterdir()} == set(payload) | {'operator-journal.json'},
                    'YC59_OPERATOR_PACKAGE_FILESET')
    for name, expected in {**payload, 'operator-journal.json': complete}.items():
        library.require(_read(package / name) == expected, 'YC59_OPERATOR_PENDING_OR_CHANGED')


def prepare(data, *, source=None):
    # Validate every source byte before creating any output or importing payload code.
    manifest, payload = _source(source or SOURCE)
    artifact = 'sha256:' + hashlib.sha256(DOMAIN + payload['operator-manifest.json']).hexdigest()
    root = _root(data)
    if not root.exists():
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir()
        _write_new(root / '.owner', MARKER)
    library._ordinary(root, directory=True)
    library.require(_read(root / '.owner') == MARKER, 'YC59_OPERATOR_UNOWNED')
    metadata = {'schema': 'yc59-assisted-stage/v1', 'artifact_id': artifact, 'phase': 'complete',
                'execution_enabled': False, 'installed_acceptance': False,
                'approval_automated': False, 'consume_retry': False}
    complete = library.canonical(metadata) + b'\n'
    package = root / artifact.replace(':', '-', 1)
    with library._lock(root):
        if package.exists():
            _validate(package, payload, complete)
        else:
            package.mkdir()
            _write_new(package / 'operator-journal.json', library.canonical({**metadata, 'phase': 'prepared'}) + b'\n')
            for name, raw in payload.items():
                _write_new(package / name, raw)
            _write_new(package / 'operator-journal.new', complete)
            (package / 'operator-journal.new').replace(package / 'operator-journal.json')
            _validate(package, payload, complete)
    launch = next(row for row in manifest['files'] if row['path'] == 'launch.ps1')
    return {**metadata, 'package': str(package), 'launcher_sha256': launch['sha256'],
            'independent_owner_pin_required': True, 'cloud_run_required_for_staging': False}
