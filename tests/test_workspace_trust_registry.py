from __future__ import annotations

import contextlib
import copy
import hashlib
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
import setup_workspace_trust as trust
from setup_manifest import empty_manifest, save_manifest
import toolchain_state
import toolchainctl

ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTrustRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.state = self.root / "state"
        self.workspace = self.root / "project space-é"
        self.workspace.mkdir()
        self.path = str(self.workspace)
        self.registry = self.state / trust.REGISTRY_NAME
        self.env = mock.patch.dict(os.environ, {"AGENT_TOOLCHAIN_STATE_DIR": str(self.state)})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.canonical = mock.patch.object(trust, "canonical_state_dir", return_value=self.state)
        self.canonical.start()
        self.addCleanup(self.canonical.stop)

    def owned(self):
        save_manifest(self.state / "manifest.json", empty_manifest())

    def cli(self, *argv):
        with contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            rc = toolchainctl.main(["workspace-trust", *argv])
        return rc, out.getvalue(), err.getvalue()

    def add(self, scopes=("build", "test")):
        args = ["add", self.path]
        for scope in scopes:
            args += ["--scope", scope]
        return self.cli(*args)

    def snapshot(self):
        return {str(p.relative_to(self.root)): (p.read_bytes(), p.stat().st_mtime_ns)
                for p in self.root.rglob("*") if p.is_file()}

    def test_consumer_contract_is_exact_pinned_upstream_blob(self):
        raw = (ROOT / "workspace_trust_contract.py").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
                         "bd8ce9d1a09bce989a21b8684a412d0d14683555")

    def test_missing_state_list_and_provider_are_read_only_add_requires_owned_state(self):
        provider = trust.WorkspaceTrustProvider()
        before = self.snapshot()
        rc, out, err = self.cli("list")
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"schema": trust.REGISTRY_SCHEMA, "entries": []})
        self.assertEqual(err.count("toolchainctl 0.1.0."), 1)
        self.assertIsNone(provider.lookup(trust.observe_workspace(self.path)))
        self.assertEqual(self.add()[0], 2)
        self.assertEqual(before, self.snapshot())
        self.assertFalse(self.state.exists())

    def test_add_readd_explicit_update_remove_and_provider(self):
        self.owned()
        manifest = (self.state / "manifest.json").read_bytes()
        self.assertEqual(self.add()[0], 0)
        before = self.snapshot()
        self.assertEqual(self.add(("test", "build"))[:2], (0, "up-to-date\n"))
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.add(("static_check",))[0], 2)
        self.assertEqual(before, self.snapshot())
        observed = trust.observe_workspace(self.path)
        provider = trust.WorkspaceTrustProvider()
        fact = provider.lookup(observed)
        self.assertEqual(fact["scopes"], ["build", "test"])
        fact["scopes"].append("delete")
        self.assertEqual(provider.lookup(observed)["scopes"], ["build", "test"])
        self.assertEqual(self.cli("update", self.path, "--scope", "git_read")[0], 0)
        self.assertEqual(provider.lookup(observed)["scopes"], ["git_read"])
        before = self.snapshot()
        self.assertEqual(self.cli("update", self.path, "--scope", "git_read")[:2], (0, "up-to-date\n"))
        self.cli("list")
        provider.lookup(observed)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.cli("remove", self.path)[0], 0)
        self.assertIsNone(provider.lookup(observed))
        self.assertEqual(self.cli("remove", self.path)[:2], (0, "up-to-date\n"))
        self.assertEqual((self.state / "manifest.json").read_bytes(), manifest)

    def test_changed_or_missing_object_needs_explicit_add_and_revokes_by_spelling(self):
        self.owned()
        self.add()
        old = trust.observe_workspace(self.path)
        provider = trust.WorkspaceTrustProvider()
        self.workspace.rename(self.root / "retained-original")
        self.workspace.mkdir()
        new = trust.observe_workspace(self.path)
        self.assertNotEqual(old["object_identity"], new["object_identity"])
        self.assertIsNone(provider.lookup(new))
        self.assertIsNone(provider.lookup(old))
        before = self.snapshot()
        self.assertEqual(self.cli("update", self.path, "--scope", "test")[0], 2)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.add(("test",))[0], 0)
        self.assertEqual(len(json.loads(self.registry.read_bytes())["entries"]), 1)
        self.assertEqual(provider.lookup(new)["scopes"], ["test"])
        self.workspace.rmdir()
        self.assertIsNone(provider.lookup(new))
        self.assertEqual(self.cli("remove", self.path)[0], 0)

    def test_exact_fields_and_child_directory_never_inherit_trust(self):
        self.owned()
        self.add()
        observed = trust.observe_workspace(self.path)
        provider = trust.WorkspaceTrustProvider()
        for field in ("platform", "requested_root", "resolved_root", "object_identity"):
            changed = dict(observed)
            changed[field] += "different"
            self.assertIsNone(provider.lookup(changed))
        child = self.workspace / "child"
        child.mkdir()
        self.assertIsNone(provider.lookup(trust.observe_workspace(str(child))))
        (self.workspace / "source.py").write_text("# ordinary development\n")
        self.assertIsNotNone(provider.lookup(trust.observe_workspace(self.path)))
        self.assertIsNone(provider.lookup({**observed, "trusted": True}))

    def test_corrupt_duplicate_foreign_unknown_registry_preserved(self):
        self.owned()
        self.add()
        valid = json.loads(self.registry.read_bytes())
        invalid = [b"{", b"[]", b'{"schema":"unknown","entries":[]}',
                   b'{"schema":"workspace-trust-registry/v1","entries":[],"entries":[]}']
        duplicate = copy.deepcopy(valid)
        duplicate["entries"] *= 2
        invalid.append(json.dumps(duplicate).encode())
        for field, value in (("scopes", []), ("scopes", ["build", "build"]), ("scopes", ["*"]),
                             ("scopes", ["system"]), ("trusted", True)):
            bad = copy.deepcopy(valid)
            bad["entries"][0][field] = value
            invalid.append(json.dumps(bad).encode())
        bad = copy.deepcopy(valid)
        bad["entries"][0]["workspace"]["platform"] = []  # upstream's unhashable input fails closed here
        invalid.append(json.dumps(bad).encode())
        for raw in invalid:
            with self.subTest(raw=raw):
                self.registry.write_bytes(raw)
                before = self.snapshot()
                self.assertEqual(self.cli("list")[0], 2)
                self.assertEqual(self.add()[0], 2)
                self.assertEqual(self.cli("remove", self.path)[0], 2)
                self.assertIsNone(trust.WorkspaceTrustProvider().lookup(trust.observe_workspace(self.path)))
                self.assertEqual(before, self.snapshot())

    def test_unknown_state_and_nonfile_registry_are_not_adopted(self):
        self.state.mkdir()
        (self.state / "foreign.txt").write_text("keep")
        before = self.snapshot()
        self.assertEqual(self.add()[0], 2)
        self.assertEqual(self.cli("list")[0], 2)
        self.assertEqual(before, self.snapshot())
        self.owned()
        self.registry.mkdir()
        self.assertEqual(self.add()[0], 2)
        self.assertTrue(self.registry.is_dir())

    def test_state_inside_workspace_and_relative_path_rejected(self):
        self.owned()
        self.assertEqual(self.cli("add", str(self.root), "--scope", "build")[0], 2)
        self.assertEqual(self.cli("add", "relative", "--scope", "build")[0], 2)
        self.assertEqual(self.cli("add", self.path, "--scope", "test", "--scope", "test")[0], 2)
        self.assertFalse(self.registry.exists())

    def test_writer_collision_and_failure_preserve_previous_registry(self):
        self.owned()
        self.add()
        before = self.snapshot()
        with trust._writer_lock(self.state):
            # Separate public process must fail, not overwrite a competing writer.
            cp = subprocess.run([sys.executable, "-B", str(ROOT / "toolchainctl.py"),
                                 "workspace-trust", "remove", self.path], capture_output=True)
            self.assertEqual(cp.returncode, 2)
            self.assertIn(b"lock already exists", cp.stderr)
        self.assertEqual(before, self.snapshot())
        for target in ("os.fsync", "os.replace"):
            with mock.patch("setup_workspace_trust." + target, side_effect=OSError("fixture failure")):
                self.assertEqual(self.cli("remove", self.path)[0], 2)
            self.assertEqual(before, self.snapshot())
        self.assertFalse(list(self.state.glob(".workspace-trust-*")))

    def test_atomic_publish_readback_detects_unexpected_result_without_rollback(self):
        self.owned()
        self.add()
        replace = os.replace
        def substituted(source, target):
            replace(source, target)
            Path(target).write_bytes(b"unexpected")
        with mock.patch.object(trust.os, "replace", side_effect=substituted):
            self.assertEqual(self.cli("remove", self.path)[0], 2)
        self.assertEqual(self.registry.read_bytes(), b"unexpected")
        self.assertIsNone(trust.WorkspaceTrustProvider().lookup(trust.observe_workspace(self.path)))

    def test_concurrent_external_registry_change_is_preserved(self):
        self.owned()
        self.add()
        raw = self.registry.read_bytes()
        fsync = os.fsync
        def concurrent(fd):
            fsync(fd)
            self.registry.write_bytes(b"external change")
        with mock.patch.object(trust.os, "fsync", side_effect=concurrent):
            self.assertEqual(self.cli("remove", self.path)[0], 2)
        self.assertEqual(self.registry.read_bytes(), b"external change")
        self.registry.write_bytes(raw)
        self.assertIsNotNone(trust.WorkspaceTrustProvider().lookup(trust.observe_workspace(self.path)))

    def test_workspace_race_before_noop_does_not_report_success(self):
        self.owned()
        self.add()
        observed = trust.observe_workspace(self.path)
        changed = {**observed, "object_identity": "changed"}
        before = self.snapshot()
        with mock.patch.object(trust, "observe_workspace", side_effect=[observed, changed]):
            self.assertEqual(self.add()[0], 2)
        self.assertEqual(before, self.snapshot())

    def test_provider_ignores_cli_override_and_binds_location_at_construction(self):
        self.owned()
        self.add()
        provider = trust.WorkspaceTrustProvider()
        with mock.patch.object(trust, "canonical_state_dir", return_value=self.root / "other"), \
                mock.patch.dict(os.environ, {"AGENT_TOOLCHAIN_STATE_DIR": str(self.root / "forged")}):
            self.assertIsNotNone(provider.lookup(trust.observe_workspace(self.path)))
            self.assertIsNone(trust.WorkspaceTrustProvider().lookup(trust.observe_workspace(self.path)))
        with mock.patch.object(toolchain_state, "state_base", return_value=self.root / "os-state"):
            self.assertNotEqual(toolchain_state.canonical_state_dir(), self.state)
            self.assertEqual(toolchain_state.default_state_dir(), self.state)
        with self.assertRaises(TypeError):
            trust.WorkspaceTrustProvider(registry_path=self.registry)

    def test_windows_and_linux_contract_do_not_fold_case_or_unicode(self):
        for platform, requested in (("linux", "/projects/café"), ("windows", "C:\\Projects\\café")):
            identity = dict(platform=platform, requested_root=requested, resolved_root=requested, object_identity="object:42")
            entry = dict(schema=trust.SCHEMA, trust_class=trust.TRUST_CLASS, workspace=identity, scopes=["test"])
            self.assertTrue(trust.match_workspace_trust_fact(entry, identity)["matched"])
            for alternate in (requested.upper(), requested.replace("é", "e\u0301")):
                self.assertFalse(trust.match_workspace_trust_fact(entry, {**identity, "requested_root": alternate})["matched"])

    def test_installed_public_command_is_independent_of_source_and_check_stays_read_only(self):
        self.owned()
        source = self.root / "source"
        core = self.root / "installed" / "core"
        source.mkdir()
        core.mkdir(parents=True)
        bootstrap._copy_payload(ROOT, source, bootstrap.source_fingerprint(ROOT))
        bootstrap._copy_payload(source, core, bootstrap.source_fingerprint(source))
        source.rename(self.root / "removed-source")
        with mock.patch.dict(os.environ, {"AGENT_TOOLCHAIN_BIN_DIR": str(self.root / "bin")}):
            bootstrap._publish_entrypoint(core)
            public = bootstrap._entrypoint_path()
        cp = subprocess.run([str(public), "workspace-trust", "add", self.path, "--scope", "test"],
                            cwd=self.root, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        before = self.snapshot()
        cp = subprocess.run([str(public), "workspace-trust", "list"], cwd=self.root, capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.assertEqual(json.loads(cp.stdout)["entries"][0]["workspace"], trust.observe_workspace(self.path))
        self.assertEqual(before, self.snapshot())

    @unittest.skipIf(os.name == "nt", "POSIX symlink fixture; Windows reparse fixture tested separately")
    def test_linked_registry_and_state_are_preserved(self):
        self.owned()
        foreign = self.root / "foreign.json"
        foreign.write_text('{"schema":"workspace-trust-registry/v1","entries":[]}')
        self.registry.symlink_to(foreign)
        self.assertEqual(self.add()[0], 2)
        self.assertIsNone(trust.WorkspaceTrustProvider().lookup(trust.observe_workspace(self.path)))
        self.assertTrue(self.registry.is_symlink())
        moved = self.root / "moved-state"
        self.state.rename(moved)
        self.state.symlink_to(moved, target_is_directory=True)
        self.assertEqual(toolchain_state.default_state_dir(), moved)
        self.assertEqual(toolchain_state.default_state_dir(resolve_override=False), self.state)
        self.assertEqual(self.add()[0], 2)
        self.assertTrue(self.state.is_symlink())

    @unittest.skipUnless(os.name == "nt", "Windows directory junction fixture")
    def test_windows_junction_state_is_preserved(self):
        self.owned()
        moved = self.root / "moved-state"
        self.state.rename(moved)
        cp = subprocess.run(["cmd", "/c", "mklink", "/J", str(self.state), str(moved)], capture_output=True)
        self.assertEqual(cp.returncode, 0, cp.stderr)
        self.addCleanup(lambda: self.state.rmdir())
        self.assertEqual(self.add()[0], 2)
        self.assertIsNone(trust.WorkspaceTrustProvider().lookup(trust.observe_workspace(self.path)))
        self.assertTrue((moved / "manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
