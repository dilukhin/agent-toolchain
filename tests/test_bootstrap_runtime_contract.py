from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bootstrap_core  # noqa: E402


class WindowsEntrypointEncodingTests(unittest.TestCase):
    def test_managed_cmd_enables_utf8_locally_and_preserves_exit_code(self) -> None:
        core = Path("C:/agent-toolchain/core")
        text = bootstrap_core._entrypoint_bytes(core, windows=True).decode("utf-8-sig")
        self.assertIn("@setlocal\r\n", text)
        self.assertIn('@set "PYTHONUTF8=1"\r\n', text)
        self.assertIn('@set "PYTHONIOENCODING=utf-8"\r\n', text)
        self.assertIn('@set "_AGENT_TOOLCHAIN_RC=%ERRORLEVEL%"\r\n', text)
        self.assertIn("@endlocal & exit /b %_AGENT_TOOLCHAIN_RC%\r\n", text)


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
