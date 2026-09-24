"""Ownership and read-only regressions for the pinned tunnelctl binary."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import setup_managed_tools as managed  # noqa: E402
from setup_lib import Reporter, STATE_CONFLICT, STATE_MISSING  # noqa: E402
from setup_manifest import empty_manifest  # noqa: E402
from setup_tools import parse_tool_spec  # noqa: E402


class GoToolDeploymentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.env = mock.patch.dict(os.environ, {
            "AGENT_TOOLCHAIN_DATA_DIR": str(self.root / "data"),
            "AGENT_TOOLCHAIN_BIN_DIR": str(self.root / "bin"),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.spec, error = parse_tool_spec("tunnelctl", {
            "source": "git", "repo": "https://github.com/dilukhin/tunnelctl.git",
            "ref": "a" * 40, "project_directory": "tunnelctl", "runtime": "go-binary",
            "update_policy": "pinned-tested", "entrypoints": ["tunnelctl"],
            "health_contract": [{"argv": ["tunnelctl", "--version"]}],
        })
        self.assertIsNone(error)

    def _release(self) -> tuple[Path, Path]:
        release = managed._release_dir(self.spec)
        release.mkdir(parents=True)
        binary = managed._go_command(release, "tunnelctl")
        binary.write_bytes(b"an installed Go binary fixture")
        if os.name != "nt":
            binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
        marker = {**managed._marker_payload(self.spec), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        managed._marker_path(release).write_text(json.dumps(marker), encoding="utf-8")
        return release, binary

    def test_missing_check_creates_nothing(self) -> None:
        report = Reporter()
        changed = managed.reconcile_go_tool(self.spec, report, check=True, skip_install=False, manifest=empty_manifest())
        self.assertFalse(changed)
        self.assertFalse((self.root / "data").exists())
        self.assertFalse((self.root / "bin").exists())
        self.assertTrue(any(item.state == STATE_MISSING for item in report.results))

    def test_missing_go_fails_before_creating_release(self) -> None:
        report = Reporter()
        with mock.patch.object(managed.shutil, "which", side_effect=lambda name: None if name == "go" else "/usr/bin/git"):
            self.assertIsNone(managed._install_go_release(self.spec, report))
        self.assertFalse((self.root / "data").exists())
        self.assertTrue(any("Go 1.22+" in item.detail for item in report.results))

    def test_modified_binary_is_preserved_and_blocks_entrypoint(self) -> None:
        _, binary = self._release()
        binary.write_bytes(b"modified binary")
        report = Reporter()
        with mock.patch.object(managed, "_health_go", side_effect=AssertionError("must not execute unknown binary")):
            changed = managed.reconcile_go_tool(self.spec, report, check=False, skip_install=False, manifest=empty_manifest())
        self.assertFalse(changed)
        self.assertEqual(binary.read_bytes(), b"modified binary")
        self.assertFalse((self.root / "bin").exists())
        self.assertTrue(any(item.state == STATE_CONFLICT for item in report.results))

    def test_foreign_public_command_is_preserved(self) -> None:
        self._release()
        public = managed._public_entrypoint(self.spec, "tunnelctl")
        public.parent.mkdir(parents=True)
        public.write_bytes(b"foreign command")
        report = Reporter()
        with mock.patch.object(managed, "_health_go", return_value=(True, "healthy")):
            changed = managed.reconcile_go_tool(self.spec, report, check=False, skip_install=False, manifest=empty_manifest())
        self.assertFalse(changed)
        self.assertEqual(public.read_bytes(), b"foreign command")
        self.assertTrue(any(item.state == STATE_CONFLICT for item in report.results))

    def test_forged_marker_cannot_adopt_modified_binary_from_same_release(self) -> None:
        release, binary = self._release()
        record = managed._manifest_record(self.spec, release)
        manifest = empty_manifest()
        manifest["managed_tools"]["tunnelctl"] = record
        binary.write_bytes(b"replacement")
        marker = {**managed._marker_payload(self.spec), "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest()}
        managed._marker_path(release).write_text(json.dumps(marker), encoding="utf-8")
        report = Reporter()
        with mock.patch.object(managed, "_health_go", side_effect=AssertionError("must not execute changed binary")):
            changed = managed.reconcile_go_tool(self.spec, report, check=False, skip_install=False, manifest=manifest)
        self.assertFalse(changed)
        self.assertEqual(manifest["managed_tools"]["tunnelctl"], record)
        self.assertTrue(any(item.state == STATE_CONFLICT for item in report.results))

    def test_unsupported_go_contract_fails_closed(self) -> None:
        wrong = self.spec.__class__(**{**self.spec.__dict__, "health_contract": ()})
        self.assertIsNotNone(managed._validate_supported_spec(wrong))


if __name__ == "__main__":
    unittest.main()
