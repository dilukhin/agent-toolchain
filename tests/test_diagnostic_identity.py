from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import bootstrap_core as bootstrap
import core_identity as identity
import proxy_tools
import setup_managed_tools as managed
from setup_lib import Reporter
from setup_manifest import empty_manifest
from setup_tools import HealthCheckSpec, ToolSpec
import toolchainctl

ROOT = Path(__file__).resolve().parents[1]
REF = '12345678' + 'a' * 32


def proxy_spec():
    return ToolSpec(name='proxy-tools', source='builtin', runtime='python-builtin',
                    update_policy='bundled-with-setup', module='proxy_tools',
                    entrypoints=('opencode-proxied', 'codex-proxied'), platforms=('windows', 'linux'),
                    health_contract=(HealthCheckSpec(('opencode-proxied', '--health')),))


class DiagnosticIdentityTests(unittest.TestCase):
    def test_metadata_formats_and_invalid_marker_do_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            marker = root / identity.CORE_MARKER
            self.assertEqual(identity.version_text('toolchainctl', identity.read_identity(root)), 'toolchainctl 0.1.0.dev')
            for raw in ('[]', '{', '{"owner":"foreign","source_ref":"' + REF + '"}'):
                marker.write_text(raw)
                self.assertEqual(identity.read_identity(root)['provenance'], 'unknown')
            marker.write_text(json.dumps(dict(schema=1, owner='agent-toolchain', source_ref=REF, fingerprint='b' * 64)))
            info = identity.read_identity(root)
            self.assertEqual(info['source_ref'], REF)
            self.assertEqual(identity.version_text('toolchainctl', info), 'toolchainctl 0.1.0.12345678')
            with mock.patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
                self.assertEqual(identity.read_identity(root)['provenance'], 'unknown')

    def test_ordinary_core_commands_emit_once_on_stderr(self):
        for args in (['check'], ['apply'], ['updates', 'show'], ['updates', 'refresh'], ['update'], ['update', '--apply']):
            with self.subTest(args=args), mock.patch.object(toolchainctl, '_version_text', return_value='toolchainctl 0.1.0.12345678'), \
                    mock.patch.object(toolchainctl, 'prepare_state', return_value=(Path('fixture-state'), None, None)), \
                    mock.patch.object(toolchainctl, '_managed_phase', return_value=0), \
                    mock.patch.object(toolchainctl, '_reconcile_routerai_model_labels', return_value=0), \
                    mock.patch.object(toolchainctl.setup_core, 'main', return_value=0), \
                    mock.patch.object(toolchainctl, '_updates_phase', return_value=0), \
                    mock.patch.object(toolchainctl, '_run_self_update', return_value=0), \
                    contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(toolchainctl.main(args), 0)
                self.assertEqual(out.getvalue(), '')
                self.assertEqual(err.getvalue(), 'toolchainctl 0.1.0.12345678\n')

    def test_core_parser_state_and_update_errors_include_identity(self):
        with mock.patch.object(toolchainctl, '_version_text', return_value='toolchainctl 0.1.0.12345678'):
            with contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as error:
                toolchainctl.main(['not-a-command'])
            self.assertEqual(error.exception.code, 2)
            self.assertEqual(err.getvalue().count('toolchainctl 0.1.0.12345678'), 1)
            for command, target, error in [('apply', 'prepare_state', toolchainctl.StateMigrationError('fixture')),
                                           ('update', '_owned_installed_core', toolchainctl.SelfUpdateError('fixture'))]:
                with mock.patch.object(toolchainctl, target, side_effect=error), \
                        contextlib.redirect_stderr(io.StringIO()) as err, contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(toolchainctl.main([command]), 2)
                    self.assertEqual(err.getvalue().count('toolchainctl 0.1.0.12345678'), 1)
                    self.assertEqual(out.getvalue(), '')

    def test_proxy_reserved_commands_do_not_launch_child_or_network(self):
        for command in ('opencode', 'codex'):
            with mock.patch.object(proxy_tools, 'launch', return_value=37) as launch:
                with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
                    self.assertEqual(proxy_tools.main([command, '--wrapper-version']), 0)
                    self.assertTrue(out.getvalue().startswith(command + '-proxied 0.1.0.'))
                    self.assertEqual(err.getvalue(), '')
                with contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(proxy_tools.main([command, '--health-json']), 0)
                    self.assertFalse(json.loads(out.getvalue())['network_checked'])
                launch.assert_not_called()
                self.assertEqual(proxy_tools.main([command, '--version']), 37)
                launch.assert_called_once_with(command, ['--version'])

    def test_missing_child_error_has_one_wrapper_identity(self):
        with mock.patch.object(proxy_tools, 'external_cli_inventory', return_value=mock.Mock(active=None)), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(proxy_tools.main(['codex', '--version']), 127)
        self.assertEqual(out.getvalue(), '')
        self.assertEqual(err.getvalue().count('codex-proxied 0.1.0.'), 1)
        self.assertIn('no executable found', err.getvalue())

    def test_installed_core_and_proxy_keep_ref_without_source_or_core_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            core = root / 'core'
            core.mkdir()
            source = root / 'source'
            source.mkdir()
            bootstrap._copy_payload(ROOT, source, bootstrap.source_fingerprint(ROOT))
            bootstrap._copy_payload(source, core, bootstrap.source_fingerprint(source), REF)
            source.rename(root / 'removed-source')
            expected = 'toolchainctl 0.1.0.12345678\n'
            cp = subprocess.run([sys.executable, '-B', str(core / 'toolchainctl.py'), '--version'], capture_output=True, check=True, text=True)
            self.assertEqual(cp.stdout, expected)
            self.assertEqual(cp.stderr, '')
            env = {'AGENT_TOOLCHAIN_DATA_DIR': str(root / 'data'), 'AGENT_TOOLCHAIN_BIN_DIR': str(root / 'bin')}
            manifest = empty_manifest()
            with mock.patch.dict(os.environ, env), mock.patch.object(managed, '__file__', str(core / 'setup_managed_tools.py')):
                self.assertTrue(managed.reconcile_builtin_tool(proxy_spec(), Reporter(), check=False, manifest=manifest))
                record = manifest['managed_tools']['proxy-tools']
                self.assertEqual(record['core_identity']['source_ref'], REF)
                release = Path(record['runtime_path'])
                original = {p.name: p.read_bytes() for p in release.iterdir() if p.is_file()}
                marker = json.loads((core / identity.CORE_MARKER).read_text())
                marker['source_ref'] = 'b' * 40
                (core / identity.CORE_MARKER).write_text(json.dumps(marker))
                self.assertNotEqual(managed._release_dir(proxy_spec()), release)
                self.assertEqual(original, {p.name: p.read_bytes() for p in release.iterdir() if p.is_file()})
            # Even the installed core may be moved; proxy metadata belongs to its release.
            core.rename(root / 'removed-core')
            for command in ('opencode-proxied', 'codex-proxied'):
                public = root / 'bin' / (command + ('.cmd' if os.name == 'nt' else ''))
                cp = subprocess.run([str(public), '--health-json'], cwd=root, capture_output=True, check=True)
                info = json.loads(cp.stdout)
                self.assertEqual(info['identity']['source_ref'], REF)
                self.assertEqual(cp.stderr, b'')


class BootstrapProvenanceTests(unittest.TestCase):
    def test_clean_git_dirty_hidden_changes_and_copied_source(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'source'
            source.mkdir()
            # Test only a minimal publication contract in a real local Git repository.
            (source / 'toolchainctl.py').write_bytes(b"print('source')\n")
            def git(*args):
                return subprocess.check_output(['git', '-C', str(source), *args], stderr=subprocess.PIPE).decode().strip()
            git('init')
            git('config', 'user.name', 'fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            git('config', 'core.autocrlf', 'false')
            git('add', 'toolchainctl.py')
            git('commit', '-m', 'fixture')
            ref = git('rev-parse', 'HEAD')
            with mock.patch.object(bootstrap, 'REQUIRED_FILES', ('toolchainctl.py',)), \
                    mock.patch.object(bootstrap, 'REQUIRED_TREES', ()), \
                    mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop('AGENT_TOOLCHAIN_UPDATE_REF', None)
                fingerprint = bootstrap.source_fingerprint(source)
                self.assertEqual(bootstrap._source_ref(source, fingerprint), ref)
                # Git status can hide changes via assume-unchanged; blob comparison cannot.
                git('update-index', '--assume-unchanged', 'toolchainctl.py')
                (source / 'toolchainctl.py').write_bytes(b"print('dirty')\n")
                self.assertEqual(git('status', '--porcelain'), '')
                self.assertIsNone(bootstrap._source_ref(source, bootstrap.source_fingerprint(source)))
                with mock.patch.dict(os.environ, {'AGENT_TOOLCHAIN_UPDATE_REF': ref}):
                    with self.assertRaises(RuntimeError):
                        bootstrap._source_ref(source, bootstrap.source_fingerprint(source))
                # An archive without caller-provided provenance stays local.
                copied = root / 'copy'
                shutil.copytree(source, copied, ignore=shutil.ignore_patterns('.git'))
                self.assertIsNone(bootstrap._source_ref(copied, bootstrap.source_fingerprint(copied)))

    def test_legacy_exact_archive_caller_persists_full_ref_and_noop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / 'archive'
            source.mkdir()
            (source / 'toolchainctl.py').write_bytes(b'# fixture\n')
            with mock.patch.object(bootstrap, 'REQUIRED_FILES', ('toolchainctl.py',)), \
                    mock.patch.object(bootstrap, 'REQUIRED_TREES', ()), \
                    mock.patch.dict(os.environ, {'AGENT_TOOLCHAIN_UPDATE_REF': REF}):
                fingerprint = bootstrap.source_fingerprint(source)
                core = root / 'core'
                self.assertEqual(bootstrap._publish_core(source, core, fingerprint), (True, None))
                self.assertEqual(json.loads((core / identity.CORE_MARKER).read_text())['source_ref'], REF)
                self.assertEqual(bootstrap._publish_core(source, core, fingerprint), (False, None))
                with mock.patch.dict(os.environ, {'AGENT_TOOLCHAIN_UPDATE_REF': 'b' * 40}):
                    changed, backup = bootstrap._publish_core(source, core, fingerprint)
                    self.assertTrue(changed)
                    self.assertTrue(backup.is_dir())
                    self.assertEqual(json.loads((core / identity.CORE_MARKER).read_text())['source_ref'], 'b' * 40)
                    self.assertEqual(bootstrap._publish_core(source, core, fingerprint), (False, None))
                with mock.patch.dict(os.environ, {'AGENT_TOOLCHAIN_UPDATE_REF': 'not-a-sha'}):
                    with self.assertRaises(RuntimeError):
                        bootstrap._publish_core(source, core, fingerprint)


if __name__ == '__main__':
    unittest.main()
