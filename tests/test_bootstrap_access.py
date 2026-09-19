from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import bootstrap_core as bootstrap
import toolchainctl

ROOT = Path(__file__).resolve().parents[1]


class PublishedAccessTests(unittest.TestCase):
    def test_probe_reuses_owned_payload_validation_without_reconciliation(self):
        with mock.patch.object(toolchainctl, '_owned_installed_core', return_value={'fingerprint': 'a' * 64}) as owned, \
                mock.patch.object(toolchainctl, '_non_elevated', return_value=True), \
                mock.patch.object(toolchainctl, 'prepare_state') as state, \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(toolchainctl.main(['--bootstrap-access-check']), 0)
        owned.assert_called_once_with()
        state.assert_not_called()
        self.assertTrue(json.loads(output.getvalue())['non_elevated'])

    def test_probe_fails_closed_when_marker_or_token_cannot_be_read(self):
        for failure in (PermissionError('denied'), toolchainctl.SelfUpdateError('invalid')):
            with self.subTest(failure=failure), \
                    mock.patch.object(toolchainctl, '_owned_installed_core', side_effect=failure), \
                    contextlib.redirect_stdout(io.StringIO()) as output, \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(toolchainctl.main(['--bootstrap-access-check']), 2)
                self.assertEqual(output.getvalue(), '')
        with mock.patch.object(toolchainctl, '_owned_installed_core', return_value={'fingerprint': 'a' * 64}), \
                mock.patch.object(toolchainctl, '_non_elevated', side_effect=OSError('token denied')), \
                contextlib.redirect_stdout(io.StringIO()) as output, \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(toolchainctl.main(['--bootstrap-access-check']), 2)
            self.assertEqual(output.getvalue(), '')

    def test_validates_actual_entrypoint_path_fingerprint_and_privilege(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td).resolve()
            core, entry = root / 'core', root / 'toolchainctl'
            fingerprint = 'a' * 64
            entry.write_bytes(bootstrap._entrypoint_bytes(core))
            good = dict(core=str(core), fingerprint=fingerprint, non_elevated=True)
            results = [
                (good, 0, True),
                ({**good, 'core': str(root / 'other')}, 0, False),
                ({**good, 'fingerprint': 'b' * 64}, 0, False),
                ({**good, 'non_elevated': False}, 0, False),
                ({**good, 'non_elevated': 'true'}, 0, False),
                (good, 2, False),
                ([], 0, False),
            ]
            with mock.patch.object(bootstrap, '_owned_core', return_value={'fingerprint': fingerprint}), \
                    mock.patch.object(bootstrap, '_entrypoint_path', return_value=entry):
                for evidence, rc, ok in results:
                    with self.subTest(evidence=evidence, rc=rc), mock.patch.object(bootstrap.subprocess, 'run', return_value=
                            subprocess.CompletedProcess([], rc, json.dumps(evidence).encode(), b'')) as run:
                        if ok:
                            bootstrap._validate_published(core, fingerprint)
                        else:
                            with self.assertRaises(RuntimeError):
                                bootstrap._validate_published(core, fingerprint)
                        self.assertEqual(run.call_args.args[0], [str(entry), '--bootstrap-access-check'])
                        self.assertEqual(run.call_args.kwargs['timeout'], 30)
                entry.write_bytes(b'foreign')
                with mock.patch.object(bootstrap.subprocess, 'run') as run, self.assertRaises(RuntimeError):
                    bootstrap._validate_published(core, fingerprint)
                run.assert_not_called()

    def test_failed_postcheck_reports_failure_retains_publication_and_backup(self):
        for failure in (PermissionError('denied'), RuntimeError('mismatch'), subprocess.TimeoutExpired('probe', 30)):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as td:
                root = Path(td).resolve()
                env = {'AGENT_TOOLCHAIN_DATA_DIR': str(root / 'data'), 'AGENT_TOOLCHAIN_BIN_DIR': str(root / 'bin')}
                with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()) as output, \
                        contextlib.redirect_stderr(io.StringIO()) as errors, \
                        mock.patch.object(bootstrap, '_validate_published', side_effect=failure):
                    self.assertEqual(bootstrap.main(), 2)
                self.assertEqual(output.getvalue(), '')
                self.assertIn('failed', errors.getvalue())
                self.assertTrue((root / 'data/core' / bootstrap.CORE_MARKER).exists())
                # An existing owned core is retained after failed verification, too.
                with mock.patch.dict(os.environ, env), mock.patch.object(bootstrap, '_validate_published', side_effect=failure), \
                        mock.patch.object(bootstrap, 'source_fingerprint', return_value='b' * 64), \
                        mock.patch.object(bootstrap, '_publish_core', return_value=(True, root / 'previous')), \
                        contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as errors:
                    self.assertEqual(bootstrap.main(), 2)
                self.assertEqual(output.getvalue(), '')
                self.assertIn(str(root / 'previous'), errors.getvalue())

    @unittest.skipIf(os.name == 'nt', 'POSIX permissions and real unprivileged process')
    def test_linux_real_user_install_noop_denials_and_readonly_probe(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            kwargs = {}
            if os.geteuid() == 0:
                # Only this disposable fixture is reassigned; never the workspace.
                try:
                    os.chown(root, 65534, 65534)
                except OSError as exc:
                    self.skipTest(f"Container cannot create an unprivileged fixture: {exc}")
                kwargs = {'user': 65534, 'group': 65534, 'extra_groups': []}
            env = {**os.environ, 'AGENT_TOOLCHAIN_DATA_DIR': str(root / 'data'),
                   'AGENT_TOOLCHAIN_BIN_DIR': str(root / 'bin'), 'HOME': str(root),
                   'AGENT_TOOLCHAIN_STATE_DIR': str(root / 'state'), 'OPENCODE_CONFIG_DIR': str(root / 'config')}
            def run(argv):
                return subprocess.run(argv, cwd=root, env=env, capture_output=True, timeout=30, **kwargs)
            command = [sys.executable, '-B', str(ROOT / 'bootstrap_core.py')]
            first = run(command)
            self.assertEqual(first.returncode, 0, first.stderr.decode())
            core = root / 'data/core'
            snapshot = {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
                        for p in root.rglob('*') if p.is_file()}
            self.assertEqual(run(command).returncode, 0)
            probe = run([str(root / 'bin/toolchainctl'), '--bootstrap-access-check'])
            self.assertEqual(probe.returncode, 0, probe.stderr)
            self.assertTrue(json.loads(probe.stdout)['non_elevated'])
            self.assertEqual(snapshot, {p.relative_to(root): (p.read_bytes(), p.stat().st_mtime_ns)
                                       for p in root.rglob('*') if p.is_file()})
            self.assertFalse((root / 'state').exists())
            self.assertFalse((root / 'config').exists())
            for target in (core, core / bootstrap.CORE_MARKER, core / 'config_data.json', root / 'bin/toolchainctl'):
                mode = target.stat().st_mode
                target.chmod(0)
                try:
                    denied = run(command)
                    self.assertEqual(denied.returncode, 2, denied.stdout)
                    self.assertNotIn(b'configured', denied.stdout)
                    self.assertEqual(target.stat().st_mode & 0o777, 0)
                finally:
                    target.chmod(mode)
            self.assertFalse(list((root / 'data').glob('core.previous.*')))


if __name__ == '__main__':
    unittest.main()
