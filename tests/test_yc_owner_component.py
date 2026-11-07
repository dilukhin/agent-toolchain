"""Inactive component staging: synthetic bytes, no cloud/network/process calls."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import setup_yc_owner_component as component


class OwnerComponentStagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / "data"
        # A tiny, independent reviewed fixture keeps tests self-contained.
        self.payload = {"runtime/library.py": b"raise RuntimeError('must never execute')\n"}
        import hashlib
        self.manifest = {"schema": "yc-owner-component/v1", "owner": "dilukhin/opencode_permissions",
            "profile": "yc-snapshot-read/v1", "platform": "windows", "status": "library_only",
            "execution_enabled": False, "required_before_activation": ["native_sources"],
            "files": [{"path": p, "size": len(b), "sha256": hashlib.sha256(b).hexdigest()} for p,b in self.payload.items()]}
        self.manifest["artifact_id"] = "sha256:" + hashlib.sha256(component.DOMAIN + component.canonical(self.manifest)).hexdigest()
        self.pin = patch.object(component, "MANIFEST", self.manifest)
        self.source = patch.object(component, "SOURCES", {"runtime/library.py": "source/library.py"})
        self.pin.start()
        self.source.start()
        self.calls = []

    def tearDown(self):
        self.source.stop()
        self.pin.stop()
        self.temp.cleanup()

    def fetch(self, source, limit, deadline):
        self.calls.append(source)
        self.assertEqual(source, "source/library.py")
        return self.payload["runtime/library.py"]

    def test_check_and_blocked_apply_do_not_create_state_or_call_executor(self):
        self.assertEqual(component.check(self.data)["status"], "not_staged")
        with patch.object(component.bootstrap_core, "data_root", return_value=self.data), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(component.run_cli(argparse.Namespace(yc_owner_command="apply")), 2)
        self.assertEqual(json.loads(output.getvalue())["code"], "YC_OWNER_TRUSTED_SOURCES_NOT_READY")
        self.assertFalse(self.data.exists())

    def test_fresh_stage_exact_readback_and_idempotent_without_network(self):
        result = component.stage(self.data, fetch=self.fetch)
        self.assertEqual(result["status"], "staged_verified")
        self.assertFalse(result["execution_enabled"])
        self.assertFalse(result["trusted_placement_proven"])
        self.assertEqual(component.stage(self.data, fetch=lambda *_: self.fail("repeat fetch")), result)
        self.assertEqual(self.calls, ["source/library.py"])
        self.assertFalse((self.data / "bin").exists())
        self.assertFalse((self.data / "state").exists())

    def test_failed_or_tampered_download_leaves_pending_and_no_blind_retry(self):
        with self.assertRaisesRegex(component.ComponentConflict, "DOWNLOAD_DIGEST_MISMATCH"):
            component.stage(self.data, fetch=lambda *_: b"changed")
        with self.assertRaisesRegex(component.ComponentConflict, "PENDING_OR_CONFLICT"):
            component.stage(self.data, fetch=lambda *_: self.fail("retry"))
        root = component._root(self.data)
        self.assertEqual(json.loads((root / "stage-journal.json").read_bytes())["phase"], "prepared")

    def test_unknown_staging_directory_is_preserved(self):
        root = component._root(self.data)
        root.mkdir(parents=True)
        unknown = root / "foreign.txt"
        unknown.write_text("keep")
        with self.assertRaises(component.ComponentConflict):
            component.stage(self.data, fetch=self.fetch)
        self.assertEqual(unknown.read_text(), "keep")
        self.assertEqual(self.calls, [])

    def test_changed_payload_manifest_and_extra_file_refused_without_repair(self):
        for name in ("runtime/library.py", "manifest.json", "runtime/foreign.py"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                data = Path(tmp) / "data"
                component.stage(data, fetch=self.fetch)
                package = component._root(data) / self.manifest["artifact_id"].replace(":", "-", 1)
                path = package / name
                path.write_bytes(b"changed")
                with self.assertRaises(component.ComponentConflict):
                    component.check(data)
                with self.assertRaises(component.ComponentConflict):
                    component.stage(data, fetch=lambda *_: self.fail("must not repair"))
                self.assertEqual(path.read_bytes(), b"changed")

    def test_parallel_stage_lock_refuses(self):
        component.stage(self.data, fetch=self.fetch)
        with component._lock(component._root(self.data)):
            with self.assertRaisesRegex(component.ComponentConflict, "LOCKED_OR_IO_FAILED"):
                component.stage(self.data, fetch=self.fetch)

    def test_symlink_parent_cannot_redirect_staging(self):
        target = Path(self.temp.name) / "target"
        target.mkdir()
        try:
            self.data.symlink_to(target, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(component.ComponentConflict):
            component.stage(self.data, fetch=self.fetch)
        self.assertEqual(list(target.iterdir()), [])


class OwnerComponentCliTests(unittest.TestCase):
    def test_read_only_cli_and_apply_refusal_from_arbitrary_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = {**os.environ, "AGENT_TOOLCHAIN_DATA_DIR": str(root / "data"),
                   "AGENT_TOOLCHAIN_STATE_DIR": str(root / "state")}
            for action in ("plan", "check", "apply"):
                result = subprocess.run([sys.executable, str(ROOT / "toolchainctl.py"), "yc-owner", action],
                                        cwd=root, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2 if action == "apply" else 0, result.stderr)
                self.assertFalse(json.loads(result.stdout)["execution_enabled"])
            self.assertFalse((root / "data").exists())
            self.assertFalse((root / "state").exists())


if __name__ == "__main__":
    unittest.main()
