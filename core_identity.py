"""Offline build identity shared by the core and its immutable bundled tools.

Metadata describes the declared build; this is not an ownership/integrity validator.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import sys

CORE_SEMVER = '0.1.0'
CORE_MARKER = '.agent-toolchain-managed-core.json'
BUNDLED_IDENTITY = 'core_identity.json'


def _hex(value: object, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def read_identity(root: Path, *, bundled: bool = False) -> dict[str, object]:
    result = dict(schema=1, owner='agent-toolchain', version=CORE_SEMVER,
                  source_ref=None, fingerprint=None, provenance='unknown')
    try:
        marker = json.loads((root / (BUNDLED_IDENTITY if bundled else CORE_MARKER)).read_text(encoding='utf-8'))
    except FileNotFoundError:
        result['provenance'] = 'dev'
        return result
    except (OSError, ValueError):
        return result
    if not isinstance(marker, dict) or marker.get('schema') != 1 or marker.get('owner') != 'agent-toolchain':
        return result
    if _hex(marker.get('fingerprint'), 64):
        result['fingerprint'] = marker['fingerprint']
        result['provenance'] = 'local'
    if _hex(marker.get('source_ref'), 40):
        result['source_ref'] = marker['source_ref']
        result['provenance'] = 'git'
    elif bundled and marker.get('provenance') == 'dev' and marker.get('fingerprint') is None:
        result['provenance'] = 'dev'
    return result


def version_text(label: str, identity: dict[str, object]) -> str:
    provenance = identity['provenance']
    if provenance == 'git':
        build = str(identity['source_ref'])[:8]
    elif provenance == 'local':
        build = 'local.' + str(identity['fingerprint'])[:8]
    else:
        build = str(provenance)
    return f'{label} {CORE_SEMVER}.{build}'


def emit_identity(label: str, identity: dict[str, object]) -> None:
    # ASCII metadata; stdout remains available for data and child protocols.
    print(version_text(label, identity), file=sys.stderr)
