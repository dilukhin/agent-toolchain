from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_yc_transitional_guard as guard  # noqa: E402
import bootstrap_core  # noqa: E402
from test_yc_transitional_guard_deployment import make_artifact  # noqa: E402
REAL_TRUSTED_PYTHON = guard._trusted_python


class GuardUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for item in ("old", "new"):
            (self.root / item).mkdir()
        self.old_artifact = make_artifact(self.root / "old")
        self.new_artifact = make_artifact(self.root / "new", runtime_adapter=b"# newer synthetic adapter\n")
        self.old_id = "sha256:" + self.old_artifact.name[7:]
        self.new_id = "sha256:" + self.new_artifact.name[7:]
        self.patches = (
            mock.patch.object(guard, "OLD_ARTIFACT_ID", self.old_id),
            mock.patch.object(guard, "EXPECTED_ARTIFACT_ID", self.new_id),
        )
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        trusted = mock.patch.object(guard, "_trusted_python", return_value=Path(sys.executable).resolve())
        trusted.start()
        self.addCleanup(trusted.stop)
        self.legacy = self.root / "legacy"
        self.legacy.mkdir()
        downstream = self.root / "vendor" / "yc.exe"
        downstream.parent.mkdir()
        downstream.write_bytes(b"synthetic downstream, never executed")
        (self.legacy / "state.json").write_text(json.dumps({"underlying_cli": str(downstream)}), encoding="utf-8")
        self.data = self.root / "data"
        self.bin = self.root / "managed-bin"
        self.state = self.root / "state"
        with mock.patch.object(guard, "SOURCE_REF", guard.OLD_SOURCE_REF):
            guard.apply_guard(
                artifact_dir=self.old_artifact, legacy_guard_root=self.legacy,
                entry_script_source=ROOT / "yc_transitional_entry.py",
                data_dir=self.data, bin_dir=self.bin, state_dir=self.state,
                require_effective_path=False,
            )

    def options(self):
        return dict(legacy_guard_root=self.legacy, data_dir=self.data, bin_dir=self.bin,
                    state_dir=self.state, require_effective_path=False)

    def upgrade(self, **kwargs):
        return guard.upgrade_guard(artifact_dir=self.new_artifact,
                                   entry_script_source=ROOT / "yc_transitional_entry.py",
                                   **self.options(), **kwargs)

    def active(self):
        return guard._checked_active(state_dir=self.state, data_dir=self.data,
                                     bin_dir=self.bin, legacy_guard_root=self.legacy,
                                     require_effective_path=False)[0]["artifact_id"]

    def snapshot(self):
        return ((self.state / "yc-transitional-guard.json").read_bytes(),
                (self.bin / "yc.cmd").read_bytes())

    def test_upgrade_repeat_apply_and_explicit_rollback(self):
        old = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            guard.apply_guard(artifact_dir=self.new_artifact, legacy_guard_root=self.legacy,
                              entry_script_source=ROOT / "yc_transitional_entry.py",
                              data_dir=self.data, bin_dir=self.bin, state_dir=self.state,
                              require_effective_path=False)
        self.assertEqual(ctx.exception.code, "YC_UPGRADE_REQUIRED")
        self.assertEqual(self.snapshot(), old)
        self.assertFalse((self.data / "tools" / "yc-guard" / self.new_artifact.name).exists())
        result = self.upgrade()
        self.assertTrue(result["changed"])
        self.assertEqual(self.active(), self.new_id)
        self.assertFalse(self.upgrade()["changed"])
        self.assertEqual(self.active(), self.new_id)
        self.assertFalse(guard.apply_guard(artifact_dir=self.new_artifact, legacy_guard_root=self.legacy,
                                           entry_script_source=ROOT / "yc_transitional_entry.py",
                                           data_dir=self.data, bin_dir=self.bin, state_dir=self.state,
                                           require_effective_path=False)["changed"])
        self.assertTrue(guard.rollback_guard(**self.options())["changed"])
        self.assertEqual(self.snapshot(), old)
        self.assertTrue((self.data / "tools" / "yc-guard" / self.new_artifact.name).is_dir())
        self.assertEqual(guard._load_upgrade_journal(self.state)["phase"], "rolled_back")

    def verify_failpoint(self, stage):
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            self.upgrade(failpoint=stage)
        self.assertEqual(ctx.exception.code, "YC_UPGRADE_INJECTED_FAILURE")
        journal = guard._load_upgrade_journal(self.state)
        if stage in {"after_release", "after_checkpoint"}:
            self.assertIsNone(journal)
            self.assertEqual(self.active(), self.old_id)
            return
        if stage == "after_finalization":
            self.assertEqual(journal["phase"], "complete")
            self.assertEqual(self.active(), self.new_id)
            return
        self.assertEqual(journal["phase"], "pending")
        with self.assertRaises(guard.YcGuardDeploymentError) as check:
            guard.inspect_guard(legacy_guard_root=self.legacy, data_dir=self.data,
                                bin_dir=self.bin, state_dir=self.state)
        self.assertEqual(check.exception.code, "YC_UPGRADE_PENDING")
        restored = guard.recover_guard(to="old", **self.options())
        self.assertEqual(restored["artifact_id"], self.old_id)
        self.assertEqual(guard._load_upgrade_journal(self.state)["phase"], "rolled_back")

    def test_partial_new_recovery_completes_pinned_new(self):
        with self.assertRaises(guard.YcGuardDeploymentError):
            self.upgrade(failpoint="after_shim")
        self.assertEqual(guard.recover_guard(to="new", **self.options())["artifact_id"], self.new_id)
        self.assertEqual(self.active(), self.new_id)
        self.assertEqual(guard._load_upgrade_journal(self.state)["phase"], "complete")

    def test_unknown_current_bytes_or_checkpoint_block_recovery(self):
        with self.assertRaises(guard.YcGuardDeploymentError):
            self.upgrade(failpoint="after_shim")
        (self.bin / "yc.cmd").write_bytes(b"foreign shim")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            guard.recover_guard(to="old", **self.options())
        self.assertEqual(ctx.exception.code, "YC_RECOVERY_STATE_UNKNOWN")
        self.assertEqual(self.snapshot(), before)

    def test_foreign_lock_or_corrupted_old_release_blocks_upgrade(self):
        (self.state / guard.UPGRADE_LOCK).write_bytes(b"foreign lock")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            self.upgrade()
        self.assertEqual(ctx.exception.code, "YC_UPGRADE_LOCK_UNKNOWN")
        self.assertEqual(self.snapshot(), before)

    def test_corrupted_old_release_and_downstream_fail_before_publish(self):
        old_release = self.data / "tools" / "yc-guard" / self.old_artifact.name
        entry_script = old_release / guard.ENTRY_SCRIPT
        entry_script.write_bytes(b"tampered")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError):
            self.upgrade()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.data / "tools" / "yc-guard" / self.new_artifact.name).exists())

    def test_corrupted_new_release_or_checkpoint_blocks_recovery(self):
        with self.assertRaises(guard.YcGuardDeploymentError):
            self.upgrade(failpoint="after_shim")
        manifest = self.data / "tools" / "yc-guard" / self.new_artifact.name / "artifact" / "manifest.json"
        manifest.write_bytes(b"foreign manifest")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError):
            guard.recover_guard(to="old", **self.options())
        self.assertEqual(self.snapshot(), before)

    def test_checkpoint_tamper_blocks_recovery_without_writes(self):
        with self.assertRaises(guard.YcGuardDeploymentError):
            self.upgrade(failpoint="after_shim")
        checkpoint = self.state / guard._load_upgrade_journal(self.state)["checkpoint"]
        (checkpoint / "old-yc.cmd").write_bytes(b"tampered checkpoint")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            guard.recover_guard(to="old", **self.options())
        self.assertEqual(ctx.exception.code, "YC_UPGRADE_CHECKPOINT_MODIFIED")
        self.assertEqual(self.snapshot(), before)

    def test_unsupported_state_and_foreign_journal_fail_before_release(self):
        state_path = self.state / "yc-transitional-guard.json"
        record = json.loads(state_path.read_text(encoding="utf-8"))
        record["source_ref"] = "foreign-ref"
        state_path.write_text(json.dumps(record), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError):
            self.upgrade()
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.data / "tools" / "yc-guard" / self.new_artifact.name).exists())

    def test_state_hash_does_not_authorize_noncanonical_shim(self):
        entrypoint = self.bin / "yc.cmd"
        foreign = entrypoint.read_bytes() + b"@echo unauthorized extra command\r\n"
        entrypoint.write_bytes(foreign)
        state_path = self.state / "yc-transitional-guard.json"
        record = json.loads(state_path.read_text(encoding="utf-8"))
        record["entrypoint_sha256"] = guard._sha256_bytes(foreign)
        state_path.write_text(json.dumps(record), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            self.upgrade()
        self.assertEqual(ctx.exception.code, "YC_ENTRYPOINT_INTERPRETER_OR_FORMAT_MISMATCH")
        self.assertEqual(self.snapshot(), before)

    def test_changed_python_in_active_shim_and_state_is_rejected(self):
        release = self.data / "tools" / "yc-guard" / self.old_artifact.name
        foreign_python = self.root / "foreign-python.exe"
        foreign_python.write_bytes(b"synthetic foreign executable; never run")
        entrypoint = self.bin / "yc.cmd"
        tampered = guard._entrypoint_bytes(foreign_python, release / guard.ENTRY_SCRIPT, self.old_id)
        entrypoint.write_bytes(tampered)
        state_path = self.state / "yc-transitional-guard.json"
        record = json.loads(state_path.read_text(encoding="utf-8"))
        record["entrypoint_sha256"] = guard._sha256_bytes(tampered)
        state_path.write_text(json.dumps(record), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            self.upgrade()
        self.assertEqual(ctx.exception.code, "YC_ENTRYPOINT_INTERPRETER_OR_FORMAT_MISMATCH")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.data / "tools" / "yc-guard" / self.new_artifact.name).exists())

    def test_checkpoint_and_journal_hashes_cannot_authorize_other_python(self):
        with self.assertRaises(guard.YcGuardDeploymentError):
            self.upgrade(failpoint="after_shim")
        journal_path = self.state / guard.UPGRADE_JOURNAL
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
        checkpoint = self.state / journal["checkpoint"]
        foreign_python = self.root / "foreign-python.exe"
        foreign_python.write_bytes(b"synthetic foreign executable; never run")
        release = self.data / "tools" / "yc-guard" / self.old_artifact.name
        old_shim = guard._entrypoint_bytes(foreign_python, release / guard.ENTRY_SCRIPT, self.old_id)
        old_record = json.loads((checkpoint / "old-state.json").read_text(encoding="utf-8"))
        old_record["entrypoint_sha256"] = guard._sha256_bytes(old_shim)
        old_state = (json.dumps(old_record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        (checkpoint / "old-state.json").write_bytes(old_state)
        (checkpoint / "old-yc.cmd").write_bytes(old_shim)
        journal["hashes"]["old-state.json"] = guard._sha256_bytes(old_state)
        journal["hashes"]["old-yc.cmd"] = guard._sha256_bytes(old_shim)
        journal_path.write_text(json.dumps(journal), encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            guard.recover_guard(to="old", **self.options())
        self.assertEqual(ctx.exception.code, "YC_ENTRYPOINT_INTERPRETER_OR_FORMAT_MISMATCH")
        self.assertEqual(self.snapshot(), before)

    def test_trusted_python_requires_owned_core_and_independent_launcher(self):
        self.data.mkdir(exist_ok=True)
        # Match the canonical core path used by the deployed verifier.
        # Windows CI may expose temp directories through an 8.3 alias.
        core = self.data.resolve() / "core"
        core.mkdir()
        launcher = self.bin / ("toolchainctl.cmd" if os.name == "nt" else "toolchainctl")
        launcher.write_bytes(bootstrap_core._entrypoint_bytes(core))
        # Runtime injects its integrity-checked bootstrap module explicitly.
        # Do the same in the fixture so unrelated test module state cannot
        # change the independently verified launcher comparison.
        with mock.patch.object(guard, "bootstrap_core", bootstrap_core, create=True), \
                mock.patch.object(bootstrap_core, "_owned_core", return_value={"owner": "agent-toolchain"}):
            self.assertEqual(REAL_TRUSTED_PYTHON(self.data, self.bin), Path(sys.executable).resolve())
            launcher.write_bytes(b"foreign toolchainctl launcher")
            with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
                REAL_TRUSTED_PYTHON(self.data, self.bin)
            self.assertEqual(ctx.exception.code, "YC_TRUSTED_LAUNCHER_UNVERIFIED")

    def test_completed_state_drift_refuses_check_apply_and_upgrade(self):
        self.upgrade()
        state_path = self.state / "yc-transitional-guard.json"
        record = json.loads(state_path.read_text(encoding="utf-8"))
        record["untrusted_note"] = "foreign drift"
        state_path.write_text(json.dumps(record), encoding="utf-8")
        before = self.snapshot()
        with mock.patch.object(guard, "preflight_effective_path", return_value={"current_yc": str(self.bin / "yc.cmd")}):
            with self.assertRaises(guard.YcGuardDeploymentError) as check:
                guard.inspect_guard(legacy_guard_root=self.legacy, data_dir=self.data,
                                    bin_dir=self.bin, state_dir=self.state)
        self.assertEqual(check.exception.code, "YC_UPGRADE_COMPLETED_STATE_MISMATCH")
        with self.assertRaises(guard.YcGuardDeploymentError) as apply:
            guard.apply_guard(artifact_dir=self.new_artifact, legacy_guard_root=self.legacy,
                              entry_script_source=ROOT / "yc_transitional_entry.py",
                              data_dir=self.data, bin_dir=self.bin, state_dir=self.state,
                              require_effective_path=False)
        self.assertEqual(apply.exception.code, "YC_UPGRADE_COMPLETED_STATE_MISMATCH")
        with self.assertRaises(guard.YcGuardDeploymentError) as upgrade:
            self.upgrade()
        self.assertEqual(upgrade.exception.code, "YC_UPGRADE_COMPLETED_STATE_MISMATCH")
        self.assertEqual(self.snapshot(), before)

    def test_rollback_keeps_lock_while_competing_recover_fails(self):
        self.upgrade()
        contender_errors = []
        def competing_recover(_failpoint, stage):
            if stage == "after_journal":
                try:
                    guard.recover_guard(to="new", **self.options())
                except guard.YcGuardDeploymentError as exc:
                    contender_errors.append(exc.code)
        with mock.patch.object(guard, "_inject_failure", side_effect=competing_recover):
            result = guard.rollback_guard(**self.options())
        self.assertEqual(contender_errors, ["YC_UPGRADE_LOCK_BUSY"])
        self.assertEqual(result["artifact_id"], self.old_id)
        self.assertEqual(guard._load_upgrade_journal(self.state)["phase"], "rolled_back")

    def test_journal_of_unknown_owner_refused(self):
        (self.state / guard.UPGRADE_JOURNAL).write_text('{"schema":1,"owner":"foreign"}', encoding="utf-8")
        before = self.snapshot()
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            self.upgrade()
        self.assertEqual(ctx.exception.code, "YC_UPGRADE_JOURNAL_UNKNOWN")
        self.assertEqual(self.snapshot(), before)

    def test_unknown_path_shadow_blocks_upgrade_without_publish(self):
        other = self.root / "foreign-bin"
        other.mkdir()
        before = self.snapshot()
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join((str(other), str(self.bin)))}), \
                mock.patch.object(guard.shutil, "which", return_value=str(other / "yc.cmd")):
            with self.assertRaises(guard.YcGuardDeploymentError):
                guard.upgrade_guard(artifact_dir=self.new_artifact,
                                    entry_script_source=ROOT / "yc_transitional_entry.py",
                                    legacy_guard_root=self.legacy, data_dir=self.data,
                                    bin_dir=self.bin, state_dir=self.state, require_effective_path=True)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.data / "tools" / "yc-guard" / self.new_artifact.name).exists())

    def test_unknown_symlink_is_not_replaced(self):
        entrypoint = self.bin / "yc.cmd"
        owned = self.bin / "owned-original.txt"
        entrypoint.rename(owned)
        try:
            entrypoint.symlink_to(owned)
        except OSError:
            self.skipTest("Symlink unavailable in fixture environment")
        with self.assertRaises(guard.YcGuardDeploymentError):
            self.upgrade()
        self.assertTrue(entrypoint.is_symlink())

    def test_held_owned_lock_fails_closed(self):
        with guard._upgrade_lock(self.state):
            before = self.snapshot()
            with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
                self.upgrade()
            self.assertEqual(ctx.exception.code, "YC_UPGRADE_LOCK_BUSY")
            self.assertEqual(self.snapshot(), before)

    def test_interrupted_rollback_is_recoverable(self):
        self.upgrade()
        with self.assertRaises(guard.YcGuardDeploymentError) as ctx:
            guard.rollback_guard(**self.options(), failpoint="after_journal")
        self.assertEqual(ctx.exception.code, "YC_UPGRADE_INJECTED_FAILURE")
        self.assertEqual(guard._load_upgrade_journal(self.state)["phase"], "rollback_pending")
        with self.assertRaises(guard.YcGuardDeploymentError):
            guard.inspect_guard(legacy_guard_root=self.legacy, data_dir=self.data,
                                bin_dir=self.bin, state_dir=self.state)
        self.assertEqual(guard.recover_guard(to="old", **self.options())["artifact_id"], self.old_id)


if __name__ == "__main__":
    unittest.main()


def _make_failpoint_test(stage):
    def test(self):
        self.verify_failpoint(stage)
    return test


for _stage in ("after_release", "after_checkpoint", "after_journal", "after_shim", "after_state", "after_verify", "after_finalization"):
    setattr(GuardUpgradeTests, "test_failpoint_" + _stage, _make_failpoint_test(_stage))
