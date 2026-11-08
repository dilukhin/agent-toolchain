"""Managed delivery of an exact native owner seed. Never starts an owner or approves."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

import bootstrap_core
import setup_yc_owner_component as library

SEED_SHA256 = '6f8cdfc83efbd5a9835b026a01b42204f356fd8a036ea0a9311f80215d40327c'
MANIFEST = json.loads('{"artifact_id":"sha256:14f643cb899c6bcec43cb2fa417e952d9c9d1a0ba13c5fb6e23a2d1e9e9ea6df","execution_enabled":true,"files":[{"path":"policy/native/rules.v1.json","sha256":"b7af96ecefd9fce341d128073f1bccae8b66c72e544bf0f47ccbb024232c3490","size":10650},{"path":"policy/yc/yc_policy.v1.json","sha256":"c4f8fba27d4c47118be4da3cd15ae06bd30520de6f95d827783b0d293e52cd2d","size":2063},{"path":"runtime/approval_owner/console.py","sha256":"a07d8aaa8a87f650e4f83e19f1aa7a00bb78f22843603bcd2c7373e884b59bd6","size":3252},{"path":"runtime/approval_owner/entry.py","sha256":"faafa5447211861f8f5ba3c8ca0dbf7fce9b2822e3a9b04630befc8d594d3335","size":5822},{"path":"runtime/approval_owner/ledger.py","sha256":"99b9e7fb61f3b4ebb2fe1c2938cf09fa64a4c3e98b46e1f7a4f3068bf40c70ad","size":7175},{"path":"runtime/approval_owner/read_host.py","sha256":"ab652a2a760ffb7773524006f53793b69438cf2d235011c7b529c8d58a75ab19","size":5720},{"path":"runtime/native_policy_matcher.py","sha256":"e70b27f110ba729d8bbc32c0dba0a62caea1a48cb53712cefbe79b497f965477","size":999},{"path":"runtime/render_native_policy.py","sha256":"f519d74ae76eb8a5a32af97230fcc007fa7955a3df9724e0588abfc046e415dd","size":5189},{"path":"runtime/windows_owner_boundary.py","sha256":"093843f9d9f20dfc8cef0be3d0448f734c867cc8c04e76e47dbb92a2075ca86a","size":7702},{"path":"runtime/windows_owner_pipe.py","sha256":"02e0ecf8f442b3207b054bdb47943e6dc7ff88e03596fd293f7c26770703a859","size":17687},{"path":"runtime/yc_owner_native_veto.py","sha256":"dd8187e00fc60ea54800e45f6c2c73edcdc70c598cff2c207d3142246f4bf706","size":4704},{"path":"runtime/yc_owner_windows_context.py","sha256":"a9a98ea2db26ab0d091ed36d9b1ac213b7b92851d9d08c1577272564c7f2349a","size":9314},{"path":"runtime/yc_policy_veto.py","sha256":"6fa913ce712a54fe0f02d7e5cb1314906936ed4f66ca698af5017080d6cda1f5","size":1545},{"path":"runtime/yc_read_capture.py","sha256":"692f8ac80220b2c68cc753e0d61556dbd04d9a89db0a6fa94c3e779f6d51487f","size":3919},{"path":"runtime/yc_read_projection.py","sha256":"a756d5906674b569a6b26c62187ebd4261890eacb3c6543f58cfe3207c9be983","size":8481},{"path":"runtime/yc_snapshot_read.py","sha256":"fcba2888ec9fd40b588945fdbfba33481c00b9b2b89a0acba72c3d3ae17c9def","size":8965}],"human_confirmation":"local_owner_console_exact_request_once","installed_acceptance":false,"owner":"dilukhin/opencode_permissions","platform":"windows","profile":"yc-snapshot-read/v1","required_before_activation":["pinned_native_bootstrap_v1","standard_distinct_owner_and_agent","protected_complete_release_and_journal"],"required_bootstrap_profile":"yc-owner-native/v1","schema":"yc-owner-component/v2","source_ref":"1f7d89d9a8cd2352c6df865e8915f66171475972","sources":{"policy/native/rules.v1.json":"policy/native/rules.v1.json","policy/yc/yc_policy.v1.json":"policy/yc/yc_policy.v1.json","runtime/approval_owner/console.py":"runtime/approval_owner/console.py","runtime/approval_owner/entry.py":"runtime/approval_owner/entry.py","runtime/approval_owner/ledger.py":"runtime/approval_owner/ledger.py","runtime/approval_owner/read_host.py":"runtime/approval_owner/read_host.py","runtime/native_policy_matcher.py":"tools/native_policy_matcher.py","runtime/render_native_policy.py":"tools/render_native_policy.py","runtime/windows_owner_boundary.py":"tools/windows_owner_boundary.py","runtime/windows_owner_pipe.py":"tools/windows_owner_pipe.py","runtime/yc_owner_native_veto.py":"tools/yc_owner_native_veto.py","runtime/yc_owner_windows_context.py":"tools/yc_owner_windows_context.py","runtime/yc_policy_veto.py":"tools/yc_policy_veto.py","runtime/yc_read_capture.py":"tools/yc_read_capture.py","runtime/yc_read_projection.py":"tools/yc_read_projection.py","runtime/yc_snapshot_read.py":"tools/yc_snapshot_read.py"},"status":"managed_owner_candidate"}')
MARKER = b'agent-toolchain:yc-owner-application:v1\n'
DOMAIN = b'opencode_permissions.yc_owner_component.v2\n'


def plan():
    return {'schema': 'yc-owner-application/v1', 'artifact_id': MANIFEST['artifact_id'],
            'source_ref': MANIFEST['source_ref'], 'seed_sha256': SEED_SHA256,
            'status': 'owner_action_required', 'execution_enabled': False,
            'trusted_owner_placement_proven': False, 'installed_acceptance': False}


def _root(data):
    root = Path(data).absolute() / 'tools/yc-owner-application'
    library._ancestors(root)
    return root


def _package(root):
    return root / ('sha256-' + SEED_SHA256)


def _metadata():
    return {**plan(), 'phase': 'complete'}


def _validate(package):
    library._ordinary(package, directory=True)
    library.require({path.name for path in package.iterdir()} == {'owner-start.ps1','manifest.json','journal.json'},
                    'YC_OWNER_APPLICATION_FILESET_CONFLICT')
    for path in package.iterdir():
        library._ordinary(path)
    seed = package / 'owner-start.ps1'
    library.require(100 < seed.stat().st_size <= 131072
                    and hashlib.sha256(seed.read_bytes()).hexdigest() == SEED_SHA256, 'YC_OWNER_SEED_DIGEST_MISMATCH')
    library.require((package / 'manifest.json').read_bytes() == library.canonical(MANIFEST) + b'\n',
                    'YC_OWNER_CANDIDATE_MANIFEST_MISMATCH')
    library.require((package / 'journal.json').read_bytes() == library.canonical(_metadata()) + b'\n',
                    'YC_OWNER_APPLICATION_PENDING_OR_CONFLICT')


def prepare(data, *, source=None):
    source = source or Path(__file__).resolve().parent / 'native/yc_owner_release.ps1'
    library._ordinary(source)
    library.require(source.stat().st_size <= 131072, 'YC_OWNER_SEED_SIZE_LIMIT')
    with source.open('rb') as stream:
        raw = stream.read(131073)
    library.require(100 < len(raw) <= 131072 and hashlib.sha256(raw).hexdigest() == SEED_SHA256,
                    'YC_OWNER_SEED_DIGEST_MISMATCH')
    core = {key: value for key, value in MANIFEST.items() if key != 'artifact_id'}
    library.require('sha256:' + hashlib.sha256(DOMAIN + library.canonical(core)).hexdigest() == MANIFEST['artifact_id'],
                    'YC_OWNER_CANDIDATE_IDENTITY_INVALID')
    root = _root(data)
    if not root.exists():
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir()
        (root / '.owner').write_bytes(MARKER)
    library._ordinary(root, directory=True)
    library._ordinary(root / '.owner')
    library.require((root / '.owner').read_bytes() == MARKER, 'YC_OWNER_APPLICATION_UNOWNED')
    with library._lock(root):
        package = _package(root)
        if package.exists():
            _validate(package)
        else:
            package.mkdir()
            journal = {**_metadata(), 'phase': 'prepared'}
            for name, content in (('journal.json',library.canonical(journal)+b'\n'),
                                  ('owner-start.ps1',raw),('manifest.json',library.canonical(MANIFEST)+b'\n')):
                with (package / name).open('xb') as stream:
                    stream.write(content)
                    stream.flush()
                    __import__('os').fsync(stream.fileno())
            replacement = package / 'journal.new'
            with replacement.open('xb') as stream:
                stream.write(library.canonical(_metadata()) + b'\n')
                stream.flush()
                __import__('os').fsync(stream.fileno())
            replacement.replace(package / 'journal.json')
            _validate(package)
    return {**plan(), 'package': str(package), 'seed_path': str(package / 'owner-start.ps1'),
            'independent_web_pin_required': True,
            'next_action': 'В отдельном сеансе выбранного владельца: проверить внешний хеш, подготовить, проверить и запустить выпуск.'}


def read(data, owner_sid):
    library.require(library.check(data)['status'] == 'staged_verified', 'YC_OWNER_CLIENT_NOT_STAGED')
    runtime = library._root(data) / library.MANIFEST['artifact_id'].replace(':','-',1) / 'runtime'
    import yc_owner_client
    return yc_owner_client.run(runtime, owner_sid)
