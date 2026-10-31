from __future__ import annotations

import json
import hashlib
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_yc_transitional_guard as setup  # noqa: E402
import yc_transitional_entry as entry  # noqa: E402


class RuntimeActivationTests(unittest.TestCase):
    def setUp(self):
        from test_yc_guard_upgrade import GuardUpgradeTests
        self.fixture_type = GuardUpgradeTests
        fixture = GuardUpgradeTests("test_upgrade_repeat_apply_and_explicit_rollback")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture
        self.release = fixture.data / "tools" / "yc-guard" / fixture.new_artifact.name

    def invoke(self, argv=None, *, data=None, public_bin=None, state_dir=None):
        f = self.fixture
        data, public_bin, state_dir = data or f.data, public_bin or f.bin, state_dir or f.state
        calls = []

        def dispatch(args, *, artifact, deployment, executor):
            allowed = (
                ["vpc", "security-group", "get", "--id", "synthetic-sg"],
                ["vpc", "subnet", "get", "--id", "synthetic-subnet"],
                ["vpc", "security-group", "list"],
                ["compute", "instance", "start", "--id", "epd42hrnss08t2440g90"],
                ["compute", "instance", "stop", "--id", "epd42hrnss08t2440g90"],
            )
            if args in allowed:
                calls.append(list(args))
                return 0
            if "--token" in args:
                return entry.DENY_EXIT
            return entry.ASK_EXIT

        with mock.patch.object(entry, "NEW_ARTIFACT_ID", f.new_id), \
                mock.patch.object(entry, "_owned_setup", return_value=(setup, data, public_bin, state_dir)), \
                mock.patch.object(entry, "_dispatch_guard", side_effect=dispatch):
            result = entry.run_guard(argv or ["vpc", "security-group", "get", "--id", "synthetic-sg"],
                                     release=self.release, executor=lambda path, tail: calls.append(list(tail)) or 0)
        return result, calls

    def test_upgrade_pending_matrix_never_dispatches(self):
        for stage in ("after_journal", "after_shim", "after_state", "after_verify"):
            with self.subTest(stage=stage):
                # Each fixture is used for only one interrupted transaction.
                f = self.fixture_type("test_upgrade_repeat_apply_and_explicit_rollback")
                f.setUp()
                try:
                    with self.assertRaises(setup.YcGuardDeploymentError):
                        f.upgrade(failpoint=stage)
                    self.release = f.data / "tools" / "yc-guard" / f.new_artifact.name
                    self.fixture = f
                    result, calls = self.invoke()
                    self.assertEqual(result, entry.FAIL_CLOSED_EXIT)
                    self.assertEqual(calls, [])
                finally:
                    f.doCleanups()

    def test_complete_then_rollback_blocks_saved_new_runtime(self):
        f = self.fixture
        f.upgrade()
        for allowed in (
            ["vpc", "security-group", "get", "--id", "synthetic-sg"],
            ["vpc", "subnet", "get", "--id", "synthetic-subnet"],
            ["vpc", "security-group", "list"],
            ["compute", "instance", "start", "--id", "epd42hrnss08t2440g90"],
            ["compute", "instance", "stop", "--id", "epd42hrnss08t2440g90"],
        ):
            with self.subTest(allowed=allowed):
                rc, calls = self.invoke(allowed)
                self.assertEqual(rc, 0)
                self.assertEqual(calls, [allowed])
        for tail, expected in ((["vpc", "security-group", "get", "--id", "synthetic-sg", "--quiet"], entry.ASK_EXIT),
                               (["vpc", "security-group", "get", "--id", "synthetic-sg", "--token", "synthetic"], entry.DENY_EXIT)):
            rc, calls = self.invoke(tail)
            self.assertEqual(rc, expected)
            self.assertEqual(calls, [])
        f = self.fixture
        setup.rollback_guard(**f.options())
        rc, calls = self.invoke()
        self.assertEqual(rc, entry.FAIL_CLOSED_EXIT)
        self.assertEqual(calls, [])

    def test_competing_rollback_cannot_cross_runtime_execution_lock(self):
        f = self.fixture
        f.upgrade()
        busy = []
        def dispatch(args, *, artifact, deployment, executor):
            try:
                setup.rollback_guard(**f.options())
            except setup.YcGuardDeploymentError as exc:
                busy.append(exc.code)
            return 0
        with mock.patch.object(entry, "NEW_ARTIFACT_ID", f.new_id), \
                mock.patch.object(entry, "_owned_setup", return_value=(setup, f.data, f.bin, f.state)), \
                mock.patch.object(entry, "_dispatch_guard", side_effect=dispatch):
            self.assertEqual(entry.run_guard(["vpc", "security-group", "list"], release=self.release), 0)
        self.assertEqual(busy, ["YC_UPGRADE_LOCK_BUSY"])
        self.assertEqual(setup.rollback_guard(**f.options())["artifact_id"], f.old_id)

    def test_initial_pending_complete_and_explicit_recover_to_new(self):
        f = self.fixture
        data = f.root / "fresh-data"
        public_bin, state_dir = data / "bin", data / "state"
        opts = dict(artifact_dir=f.new_artifact, legacy_guard_root=f.legacy,
                    entry_script_source=ROOT / "yc_transitional_entry.py",
                    data_dir=data, bin_dir=public_bin, state_dir=state_dir, require_effective_path=False)
        result = setup.apply_guard(**opts)
        self.assertTrue(result["changed"])
        self.release = data / "tools" / "yc-guard" / f.new_artifact.name
        journal_path = state_dir / setup.UPGRADE_JOURNAL
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(journal["mode"], "initial")
        self.assertEqual(journal["phase"], "complete")
        self.assertNotIn("old_id", journal)
        rc, calls = self.invoke(data=data, public_bin=public_bin, state_dir=state_dir)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(setup.apply_guard(**opts)["changed"])
        journal["phase"] = "pending"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        rc, calls = self.invoke(data=data, public_bin=public_bin, state_dir=state_dir)
        self.assertEqual(rc, entry.FAIL_CLOSED_EXIT)
        self.assertEqual(calls, [])
        with self.assertRaises(setup.YcGuardDeploymentError):
            setup.recover_guard(to="old", legacy_guard_root=f.legacy, data_dir=data,
                                bin_dir=public_bin, state_dir=state_dir, require_effective_path=False)
        setup.recover_guard(to="new", legacy_guard_root=f.legacy, data_dir=data,
                            bin_dir=public_bin, state_dir=state_dir, require_effective_path=False)
        rc, calls = self.invoke(data=data, public_bin=public_bin, state_dir=state_dir)
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 1)

    def test_initial_interruption_before_shim_requires_explicit_recovery(self):
        f = self.fixture
        data = f.root / "initial-interrupted-data"
        public_bin, state_dir = data / "bin", data / "state"
        setup.apply_guard(artifact_dir=f.new_artifact, legacy_guard_root=f.legacy,
                          entry_script_source=ROOT / "yc_transitional_entry.py",
                          data_dir=data, bin_dir=public_bin, state_dir=state_dir,
                          require_effective_path=False)
        self.release = data / "tools" / "yc-guard" / f.new_artifact.name
        journal_path = state_dir / setup.UPGRADE_JOURNAL
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        journal["phase"] = "pending"
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        state_path = state_dir / "yc-transitional-guard.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["phase"] = "prepared"
        state_path.write_bytes((json.dumps(state, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        (public_bin / "yc.cmd").unlink()  # synthetic temp fixture only
        result, calls = self.invoke(data=data, public_bin=public_bin, state_dir=state_dir)
        self.assertEqual(result, entry.FAIL_CLOSED_EXIT)
        self.assertEqual(calls, [])
        setup.recover_guard(to="new", legacy_guard_root=f.legacy, data_dir=data,
                            bin_dir=public_bin, state_dir=state_dir, require_effective_path=False)
        result, calls = self.invoke(data=data, public_bin=public_bin, state_dir=state_dir)
        self.assertEqual(result, 0)
        self.assertEqual(len(calls), 1)

    def test_initial_downstream_symlink_rejected_before_recovery_write(self):
        f = self.fixture
        data = f.root / "initial-symlink-data"
        public_bin, state_dir = data / "bin", data / "state"
        setup.apply_guard(artifact_dir=f.new_artifact, legacy_guard_root=f.legacy,
                          entry_script_source=ROOT / "yc_transitional_entry.py",
                          data_dir=data, bin_dir=public_bin, state_dir=state_dir,
                          require_effective_path=False)
        original = f.root / "vendor" / "yc.exe"
        other = f.root / "other" / "yc.exe"
        other.parent.mkdir()
        other.write_bytes(original.read_bytes())
        original.unlink()  # synthetic temp fixture only
        try:
            original.symlink_to(other)
        except OSError:
            self.skipTest("Symlink unavailable in synthetic fixture environment")
        before = ((state_dir / "yc-transitional-guard.json").read_bytes(),
                  (public_bin / "yc.cmd").read_bytes())
        with self.assertRaises(setup.YcGuardDeploymentError) as ctx:
            setup._initial_view(state_dir=state_dir, data_dir=data, bin_dir=public_bin)
        self.assertEqual(ctx.exception.code, "YC_INITIAL_DOWNSTREAM_INVALID")
        self.assertEqual(before, ((state_dir / "yc-transitional-guard.json").read_bytes(),
                                  (public_bin / "yc.cmd").read_bytes()))

    def test_complete_state_drift_or_missing_journal_blocks_runtime(self):
        f = self.fixture
        f.upgrade()
        state_path = f.state / "yc-transitional-guard.json"
        exact = state_path.read_bytes()
        state = json.loads(exact)
        state["foreign_note"] = "drift"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        result, calls = self.invoke()
        self.assertEqual(result, entry.FAIL_CLOSED_EXIT)
        self.assertEqual(calls, [])
        state_path.write_bytes(exact)
        (f.state / setup.UPGRADE_JOURNAL).unlink()  # synthetic temp fixture only
        result, calls = self.invoke()
        self.assertEqual(result, entry.FAIL_CLOSED_EXIT)
        self.assertEqual(calls, [])

    def test_corrupt_checkpoint_or_incomplete_journal_denies_downstream(self):
        f = self.fixture
        f.upgrade()
        journal_path = f.state / setup.UPGRADE_JOURNAL
        checkpoint = f.state / json.loads(journal_path.read_text(encoding="utf-8"))["checkpoint"]
        (checkpoint / "new-state.json").write_bytes(b"foreign checkpoint")
        rc, calls = self.invoke()
        self.assertEqual(rc, entry.FAIL_CLOSED_EXIT)
        self.assertEqual(calls, [])
        # A separate synthetic transaction models a torn journal write.
        other = self.fixture_type("test_upgrade_repeat_apply_and_explicit_rollback")
        other.setUp()
        try:
            with self.assertRaises(setup.YcGuardDeploymentError):
                other.upgrade(failpoint="after_journal")
            (other.state / setup.UPGRADE_JOURNAL).write_bytes(b"{")
            self.fixture = other
            self.release = other.data / "tools" / "yc-guard" / other.new_artifact.name
            rc, calls = self.invoke()
            self.assertEqual(rc, entry.FAIL_CLOSED_EXIT)
            self.assertEqual(calls, [])
        finally:
            other.doCleanups()

    def test_direct_main_cannot_bypass_pending_activation(self):
        f = self.fixture
        with self.assertRaises(setup.YcGuardDeploymentError):
            f.upgrade(failpoint="after_shim")
        release = f.data / "tools" / "yc-guard" / f.new_artifact.name
        with mock.patch.object(entry, "__file__", str(release / "yc_transitional_entry.py")), \
                mock.patch.object(entry, "NEW_ARTIFACT_ID", f.new_id), \
                mock.patch.object(entry, "_owned_setup", return_value=(setup, f.data, f.bin, f.state)), \
                mock.patch.object(entry, "_dispatch_guard") as dispatch:
            self.assertEqual(entry.main(["vpc", "security-group", "get", "--id", "synthetic-sg"]), entry.FAIL_CLOSED_EXIT)
            dispatch.assert_not_called()

    def test_unknown_journal_or_held_lock_fails_before_dispatch(self):
        f = self.fixture
        f.upgrade()
        with setup._upgrade_lock(f.state):
            rc, calls = self.invoke()
            self.assertEqual(rc, entry.FAIL_CLOSED_EXIT)
            self.assertEqual(calls, [])
        journal_path = f.state / setup.UPGRADE_JOURNAL
        journal_path.write_bytes(b"foreign journal")
        rc, calls = self.invoke()
        self.assertEqual(rc, entry.FAIL_CLOSED_EXIT)
        self.assertEqual(calls, [])

    def test_owned_release_context_rejects_direct_origin_and_core_tamper(self):
        f = self.fixture
        data = f.root / "owned-data"
        public_bin, state_dir = data / "bin", data / "state"
        setup.apply_guard(artifact_dir=f.new_artifact, legacy_guard_root=f.legacy,
                          entry_script_source=ROOT / "yc_transitional_entry.py",
                          data_dir=data, bin_dir=public_bin, state_dir=state_dir,
                          require_effective_path=False)
        release = data / "tools" / "yc-guard" / f.new_artifact.name
        artifact, deployment, manifest = entry.validate_runtime(release)
        core = data / "core"
        core.mkdir()
        source = b"synthetic owned setup source"
        bootstrap = b"synthetic owned bootstrap source"
        (core / "setup_yc_transitional_guard.py").write_bytes(source)
        (core / "bootstrap_core.py").write_bytes(bootstrap)
        fingerprint = hashlib.sha256(b"setup_yc_transitional_guard.py\0" + source + b"\0" +
                                     b"bootstrap_core.py\0" + bootstrap + b"\0").hexdigest()
        (core / ".agent-toolchain-managed-core.json").write_text(json.dumps({
            "schema": 1, "owner": "agent-toolchain", "fingerprint": fingerprint,
            "payload": [{"path": "setup_yc_transitional_guard.py", "sha256": hashlib.sha256(source).hexdigest()},
                        {"path": "bootstrap_core.py", "sha256": hashlib.sha256(bootstrap).hexdigest()}],
        }), encoding="utf-8")
        with mock.patch.object(entry, "NEW_ARTIFACT_ID", f.new_id):
            with self.assertRaises(entry.RuntimeGuardError) as origin:
                entry._owned_setup(release, deployment, manifest)
            self.assertEqual(str(origin.exception), "ACTIVATION_RUNTIME_ORIGIN_MISMATCH")
            fake_setup = types.SimpleNamespace(SOURCE_REF=entry.NEW_SOURCE_REF, EXPECTED_ARTIFACT_ID=f.new_id)
            spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=lambda module: None))
            with mock.patch.object(entry, "__file__", str(release / "yc_transitional_entry.py")), \
                    mock.patch.object(entry.importlib.util, "spec_from_file_location", return_value=spec), \
                    mock.patch.object(entry.importlib.util, "module_from_spec", return_value=fake_setup):
                self.assertEqual(entry._owned_setup(release, deployment, manifest)[1:], (data, public_bin, state_dir))
                foreign = dict(deployment, managed_state_dir=str(f.root / "foreign-state"))
                with self.assertRaises(entry.RuntimeGuardError) as context:
                    entry._owned_setup(release, foreign, manifest)
                self.assertEqual(str(context.exception), "ACTIVATION_MANAGED_PATH_MISMATCH")
                (core / "setup_yc_transitional_guard.py").write_bytes(b"tampered")
                with self.assertRaises(entry.RuntimeGuardError) as damaged:
                    entry._owned_setup(release, deployment, manifest)
                self.assertEqual(str(damaged.exception), "CORE_PAYLOAD_MODIFIED")


if __name__ == "__main__":
    unittest.main()
