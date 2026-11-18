"""No credentials, cloud calls, real consent or installed state in these tests."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import setup_yc_owner_component as library
import setup_yc_owner_operator as operator
import yc_owner_client as client

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / 'native/yc59_assisted'
OWNER = 'S-1-5-21-1-2-3-1001'
AGENT = 'S-1-5-21-1-2-3-1002'


def load(name):
    spec = importlib.util.spec_from_file_location('assisted_' + name, PACKAGE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    with patch.object(sys, 'dont_write_bytecode', True):
        spec.loader.exec_module(result)
    return result


class OperatorDeliveryTests(unittest.TestCase):
    def test_exact_data_only_package_idempotence_and_preserved_previous(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'data'
            first = operator.prepare(data)
            self.assertFalse(first['execution_enabled'])
            self.assertFalse(first['installed_acceptance'])
            self.assertFalse(first['consume_retry'])
            self.assertFalse(first['approval_automated'])
            self.assertEqual(first, operator.prepare(data))
            package = Path(first['package'])
            previous = package.parent / 'sha256-previous'
            previous.mkdir()
            (previous / 'retain').write_bytes(b'old bytes and unknown outcome')
            operator.prepare(data)
            self.assertEqual((previous / 'retain').read_bytes(), b'old bytes and unknown outcome')
            self.assertEqual(hashlib.sha256((package / 'launch.ps1').read_bytes()).hexdigest(), first['launcher_sha256'])

    def test_bad_source_fails_before_creating_output(self):
        for defect in ('manifest', 'payload', 'extra', 'symlink'):
            with self.subTest(defect=defect), tempfile.TemporaryDirectory() as tmp:
                source = Path(tmp) / 'source'
                shutil.copytree(PACKAGE, source)
                data = Path(tmp) / 'data'
                if defect == 'extra':
                    (source / 'foreign').write_bytes(b'keep')
                elif defect == 'symlink':
                    path = source / 'fixture.py'
                    path.unlink()
                    try:
                        path.symlink_to(PACKAGE / 'fixture.py')
                    except OSError:
                        continue  # Windows without symbolic-link privilege; others still run.
                else:
                    (source / ('operator-manifest.json' if defect == 'manifest' else 'console-bridge.cs')).write_bytes(b'changed')
                with self.assertRaises(library.ComponentConflict):
                    operator.prepare(data, source=source)
                self.assertFalse(data.exists())

    def test_pending_corrupt_and_extra_destination_are_preserved_and_refused(self):
        for name in ('operator-journal.json', 'launch.ps1', 'foreign'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                data = Path(tmp) / 'data'
                package = Path(operator.prepare(data)['package'])
                path = package / name
                path.write_bytes(b'pending or foreign bytes')
                with self.assertRaises(library.ComponentConflict):
                    operator.prepare(data)
                self.assertEqual(path.read_bytes(), b'pending or foreign bytes')

    def test_unknown_root_and_parallel_apply_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'data'
            root = operator._root(data)
            root.mkdir(parents=True)
            with self.assertRaises((library.ComponentConflict, FileNotFoundError)):
                operator.prepare(data)
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'data'
            operator.prepare(data)
            with library._lock(operator._root(data)):
                with self.assertRaises(library.ComponentConflict):
                    operator.prepare(data)

    def test_core_publication_includes_exact_operator(self):
        import bootstrap_core
        import toolchainctl
        self.assertEqual(bootstrap_core.REQUIRED_FILES, toolchainctl._CORE_REQUIRED_FILES)
        payload = dict(bootstrap_core._iter_source_files(ROOT))
        self.assertIn('setup_yc_owner_operator.py', payload)
        self.assertIn('native/yc59_assisted/launch.ps1', payload)
        import setup_yc_owner_release as release
        self.assertEqual((PACKAGE / 'owner-seed.ps1').read_bytes(), (ROOT / 'native/yc_owner_release.ps1').read_bytes())
        self.assertEqual(hashlib.sha256((PACKAGE / 'console-bridge.cs').read_bytes()).hexdigest(),
                         'a2d4ffe80d4fbd35c09b9dcf2b333377779c128e48a64c8aa344e063c7ffa375')
        self.assertFalse(release.MANIFEST['installed_acceptance'])


class AssistedConsumeTests(unittest.TestCase):
    def invoke(self, tmp, *, lost=False, approved=True, wrong_actor=False):
        adapter = load('client-adapter')
        shared = Path(tmp) / 'shared'
        shared.mkdir(exist_ok=True)
        latch = Path(tmp) / 'read_attempted.json'
        actor = SimpleNamespace(sid=OWNER, session_id=2)
        pending = {'ok': True, 'state': 'pending', 'request_id': 'request',
                   'operation_sha256': 'digest', 'owner_instance_id': 'instance'}
        self.actions = []
        def call(address, raw, **kwargs):
            request = json.loads(raw)
            self.actions.append(request)
            action = request['action']
            if action == 'describe':
                value = {'ok': True, 'approval_required': True, 'operation': {'schema': 'yc-snapshot-read/v1'}}
            elif action == 'propose':
                value = pending
            elif action == 'inspect':
                value = {**pending, 'state': 'approved' if approved else 'rejected'}
            elif action == 'consume':
                # The durable intent must precede the first transport call.
                self.assertTrue(json.loads(latch.read_bytes())['consume_attempted'])
                if lost:
                    raise OSError('private diagnostic must not escape')
                value = {**pending, 'state': 'consumed', 'logical_reads_completed': 3,
                         'profile': 'yc-snapshot-read/v1', 'restore_verified': False, 'projection': {}}
            else:
                raise AssertionError('unexpected permission action')
            return json.dumps(value).encode()
        modules = {
            'setup_yc_owner_component': SimpleNamespace(check=lambda _: {'status': 'staged_verified'},
                _root=lambda _: Path(tmp), MANIFEST={'artifact_id': 'sha256:test'}),
            'windows_owner_boundary': SimpleNamespace(current=lambda: SimpleNamespace(sid=OWNER if wrong_actor else AGENT)),
            'windows_owner_pipe': SimpleNamespace(call=call),
            'approval_owner.ledger': SimpleNamespace(strict_json=json.loads),
            'yc_snapshot_read': SimpleNamespace(canonical=load('compare-results').canonical),
        }
        old_path = list(sys.path)
        try:
            with patch.dict(sys.modules, modules), patch.object(client, 'discover', return_value=(actor, 23)):
                return adapter.run(Path(tmp), Path(tmp), shared, latch, OWNER, AGENT)
        finally:
            sys.path[:] = old_path

    def test_durable_consume_once_and_restart_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.invoke(tmp)['status'], 'CONSUMED_PENDING_INDEPENDENT_REVIEW')
            self.assertEqual([a['action'] for a in self.actions], ['describe', 'propose', 'inspect', 'consume'])
            with self.assertRaises(ValueError):
                self.invoke(tmp)
            self.assertEqual(self.actions, [])

    def test_lost_response_never_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.invoke(tmp, lost=True)
            self.assertEqual(result, {'status': 'STOPPED_AFTER_CONSUME_OR_UNKNOWN', 'consume_attempted': True})
            self.assertEqual(sum(a['action'] == 'consume' for a in self.actions), 1)
            with self.assertRaises(ValueError):
                self.invoke(tmp)

    def test_reject_never_consumes_and_wrong_actor_never_connects(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(self.invoke(tmp, approved=False)['consume_attempted'])
            self.assertFalse((Path(tmp) / 'read_attempted.json').exists())
            self.assertNotIn('consume', [a['action'] for a in self.actions])
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(client.ClientRefusal, 'AGENT_LOGIN_REQUIRED'):
                self.invoke(tmp, wrong_actor=True)
            self.assertEqual(self.actions, [])


class ReceiptAndCancellationTests(unittest.TestCase):
    def test_receipts_require_full_content_identity_and_independent_digest(self):
        compare = load('compare-results')
        result = {'state': 'consumed', 'logical_reads_completed': 3, 'profile': 'yc-snapshot-read/v1',
                  'restore_verified': False, 'projection': {'size_bytes': 10}}
        digest = hashlib.sha256(compare.canonical(result)).hexdigest()
        owner = {'schema': 'yc-owner-result/v1', 'component_id': compare.ARTIFACT, 'result': result, 'result_sha256': digest}
        agent = {'schema': 'yc-owner-agent-result/v1', 'result': result, 'result_sha256': digest,
                 'owner_sid': OWNER, 'server_pid': 23, 'owner_session_id': 2, 'component_independently_confirmed': False}
        self.assertEqual(compare.reconcile(owner, agent, 23, 2, OWNER), agent)
        for changed in ({'owner_sid': AGENT}, {'server_pid': 24}, {'owner_session_id': 3},
                        {'result_sha256': '0' * 64}, {'result': {**result, 'restore_verified': True}}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                compare.reconcile(owner, {**agent, **changed}, 23, 2, OWNER)
        for raw in ('{"x":{"a":1,"a":2}}', '{"x":NaN}'):
            with self.assertRaises(ValueError):
                compare.strict_json(raw)

    def test_cancellation_only_rejects_authenticated_pending_request(self):
        helper = load('reject-only')
        request = {'request_id': 'request', 'operation_sha256': 'digest', 'owner_instance_id': 'instance'}
        actor = SimpleNamespace(sid=OWNER)
        calls = []
        def call(address, raw, **kwargs):
            value = json.loads(raw)
            calls.append(value)
            return json.dumps({'ok': True, **request, 'state': 'pending' if value['action'] == 'inspect' else 'rejected'}).encode()
        modules = {'windows_owner_boundary': SimpleNamespace(current=lambda: actor),
                   'windows_owner_pipe': SimpleNamespace(call=call),
                   'approval_owner.ledger': SimpleNamespace(strict_json=json.loads)}
        old_path = list(sys.path)
        try:
            with patch.dict(sys.modules, modules):
                self.assertEqual(helper.cancel(Path('/synthetic'), 23, request, OWNER)['state'], 'rejected')
                self.assertEqual([r['action'] for r in calls], ['inspect', 'decide'])
                self.assertEqual(calls[-1]['decision'], 'reject')
                calls.clear()
                with self.assertRaises(ValueError):
                    helper.cancel(Path('/synthetic'), 23, request, AGENT)
                self.assertEqual(calls, [])
        finally:
            sys.path[:] = old_path
