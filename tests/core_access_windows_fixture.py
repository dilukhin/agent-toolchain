"""Worker for the disposable Windows CI fixture; not an operational repair tool."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock

root = Path(sys.argv[1]).resolve()
mode = sys.argv[2]
source = root / 'source'
sys.path.insert(0, str(source))
import bootstrap_core as bootstrap
import toolchainctl

assert os.name == 'nt' and os.environ.get('GITHUB_ACTIONS') == 'true'
case = 'legacy' if mode in ('legacy', 'legacy-denied') else 'current'
base = root / case
os.environ.update({
    'AGENT_TOOLCHAIN_DATA_DIR': str(base / 'data'),
    'AGENT_TOOLCHAIN_BIN_DIR': str(base / 'bin'),
    'AGENT_TOOLCHAIN_STATE_DIR': str(base / 'state'),
    'OPENCODE_SETUP_STATE_DIR': str(base / 'legacy-state'),
    'OPENCODE_CONFIG_DIR': str(base / 'config'),
    'OPENCODE_CREDENTIAL_DIR': str(base / 'credentials'),
    'OPENCODE_SKILLS_DIR': str(base / 'skills'),
    'OPENCODE_PROJECTS_DIR': str(base / 'projects'),
    'OPENCODE_STASH_DIR': str(base / 'stash'),
})
core = base / 'data/core'
entry = base / 'bin/toolchainctl.cmd'

def launch(*args):
    return subprocess.run([str(entry), *args], cwd=root, capture_output=True, timeout=30)

if mode == 'legacy':
    assert not toolchainctl._non_elevated(), 'legacy publication must run as administrator'
    # Exact previous staging algorithm. Parent explicitly permits the fixture user.
    with mock.patch.object(bootstrap, '_new_staging', side_effect=lambda parent:
            Path(tempfile.mkdtemp(prefix='.core.tmp-', dir=parent))):
        bootstrap._publish_core(source, core, bootstrap.source_fingerprint(source))
        bootstrap._publish_entrypoint(core)
    assert bootstrap._owned_core(core) is not None
elif mode == 'publish':
    assert not toolchainctl._non_elevated(), 'publication must run as administrator'
    with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
        assert bootstrap.main() == 2
    assert output.getvalue() == '', output.getvalue()
    assert 'Ordinary-user access is not verified' in errors.getvalue(), errors.getvalue()
    assert bootstrap._owned_core(core) is not None
elif mode in ('legacy-denied', 'denied'):
    assert toolchainctl._non_elevated(), 'negative probe must run without elevation'
    try:
        cp = launch('--bootstrap-access-check')
    except OSError:
        pass  # Access denied can happen before the child starts.
    else:
        assert cp.returncode != 0, cp.stdout
    if mode == 'denied':
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()):
            assert bootstrap.main() == 2
        assert output.getvalue() == '', output.getvalue()
elif mode == 'verify':
    assert toolchainctl._non_elevated(), 'positive probe must run without elevation'
    cp = launch('--bootstrap-access-check')
    assert cp.returncode == 0, cp.stderr
    evidence = json.loads(cp.stdout)
    assert evidence['non_elevated'] is True
    assert evidence['core'] == str(core)
    assert evidence['fingerprint'] == bootstrap.source_fingerprint(source)
    before = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in base.rglob('*') if p.is_file()}
    assert bootstrap.main() == 0
    assert bootstrap.main() == 0
    # Preserve the existing installed CLI check contract, with all writable targets isolated.
    cp = launch('check', '--skip-package-install', '--skip-dependency-install')
    assert cp.returncode == 0, cp.stdout + cp.stderr
    for name in ('state', 'legacy-state', 'config', 'credentials', 'skills', 'projects', 'stash'):
        assert not (base / name).exists(), name
    after = {str(p): (p.read_bytes(), p.stat().st_mtime_ns) for p in base.rglob('*') if p.is_file()}
    assert before == after, 'repeat bootstrap/probe/check must not write'
    assert not list((base / 'data').glob('core.previous.*'))
    wrapper_base = root / 'wrapper'
    wrapper_env = {**os.environ, 'AGENT_TOOLCHAIN_DATA_DIR': str(wrapper_base / 'data'),
                   'AGENT_TOOLCHAIN_BIN_DIR': str(wrapper_base / 'bin')}
    wrapper_command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                       '-File', str(source / 'bootstrap_windows.ps1')]
    for _ in range(2):
        cp = subprocess.run(wrapper_command, env=wrapper_env, capture_output=True, timeout=30)
        assert cp.returncode == 0, cp.stdout + cp.stderr
    assert not list((wrapper_base / 'data').glob('core.previous.*'))
    print(json.dumps(evidence, sort_keys=True))
else:
    raise AssertionError(mode)
print('PASS ' + mode)
