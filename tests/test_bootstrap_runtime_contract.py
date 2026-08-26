from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class LegacyInterfaceRetirementTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "Linux tombstone contract")
    def test_linux_setup_name_is_a_hard_tombstone(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            env = {**os.environ, "HOME": str(home)}
            cp = subprocess.run(
                [bash, str(ROOT / "setup_linux.sh")],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
            self.assertIn("removed as a supported interface", cp.stderr)
            self.assertIn("bootstrap_linux.sh", cp.stderr)
            self.assertIn("toolchainctl apply", cp.stderr)
            self.assertFalse(home.exists(), "legacy tombstone must not create state/runtime directories")

    @unittest.skipUnless(os.name == "nt", "Windows tombstone contract")
    def test_windows_setup_name_is_a_hard_tombstone(self) -> None:
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if not shell:
            self.skipTest("PowerShell is unavailable")
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = {
                **os.environ,
                "USERPROFILE": str(root / "home"),
                "LOCALAPPDATA": str(root / "localappdata"),
            }
            cp = subprocess.run(
                [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "setup_windows.ps1")],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
            combined = cp.stdout + cp.stderr
            self.assertIn("removed as a supported interface", combined)
            self.assertIn("bootstrap_windows.ps1", combined)
            self.assertIn("toolchainctl apply", combined)
            self.assertFalse((root / "localappdata" / "opencode_setup").exists())
            self.assertFalse((root / "localappdata" / "agent-toolchain").exists())


if __name__ == "__main__":
    unittest.main()
