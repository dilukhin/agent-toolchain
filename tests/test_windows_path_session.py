from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import setup_manifest
import setup_path
from setup_lib import Reporter, STATE_OUTDATED


class WindowsPathSessionTests(unittest.TestCase):
    def _owned_manifest(self, desired: Path) -> dict:
        manifest = setup_manifest.empty_manifest()
        manifest["managed_path_entries"]["agent-toolchain-bin"] = {
            "owner": "agent-toolchain",
            "scope": "user",
            "path": str(desired),
        }
        return manifest

    def test_check_reports_registry_path_but_stale_current_session_without_patching_environment(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            desired = Path(td) / "bin"
            manifest = self._owned_manifest(desired)
            inherited = r"C:\Windows\System32;C:\Tools"
            with mock.patch.object(setup_path, "platform_name", return_value="windows"), \
                    mock.patch.object(setup_path, "public_bin_dir", return_value=desired), \
                    mock.patch.object(setup_path, "_read_user_path", return_value=(str(desired), 2)), \
                    mock.patch.object(setup_path, "_write_user_path") as write, \
                    mock.patch.dict(os.environ, {"PATH": inherited}, clear=False):
                reporter = Reporter()
                changed = setup_path.reconcile_public_bin_path(manifest, reporter, check=True)
                self.assertEqual(os.environ["PATH"], inherited)
            self.assertFalse(changed)
            write.assert_not_called()
            self.assertEqual(reporter.results[-1].state, STATE_OUTDATED)
            self.assertIn("current process PATH does not include it", reporter.results[-1].details)
            self.assertIn("Far Manager/ConEmu", reporter.results[-1].details)

    def test_apply_uses_process_local_activation_but_still_reports_parent_session_as_stale(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            desired = Path(td) / "bin"
            manifest = self._owned_manifest(desired)
            inherited = r"C:\Windows\System32;C:\Tools"
            with mock.patch.object(setup_path, "platform_name", return_value="windows"), \
                    mock.patch.object(setup_path, "public_bin_dir", return_value=desired), \
                    mock.patch.object(setup_path, "_read_user_path", return_value=(str(desired), 2)), \
                    mock.patch.object(setup_path, "_write_user_path") as write, \
                    mock.patch.object(setup_path.os, "name", "nt"), \
                    mock.patch.dict(os.environ, {"PATH": inherited}, clear=False):
                reporter = Reporter()
                changed = setup_path.reconcile_public_bin_path(manifest, reporter, check=False)
                self.assertTrue(any(setup_path._normalized(item) == setup_path._normalized(str(desired)) for item in setup_path._split(os.environ["PATH"])))
            self.assertFalse(changed)
            write.assert_not_called()
            self.assertEqual(reporter.results[-1].state, STATE_OUTDATED)
            self.assertIn("only for its own child processes", reporter.results[-1].details)


if __name__ == "__main__":
    unittest.main()
