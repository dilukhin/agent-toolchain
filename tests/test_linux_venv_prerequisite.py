from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(os.name == "nt", "Linux venv prerequisite contract")
class LinuxVenvPrerequisiteTests(unittest.TestCase):
    def _fake_python(self, directory: Path) -> Path:
        path = directory / "python-no-ensurepip"
        path.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"-B\" ] && [ \"$2\" = \"-c\" ]; then\n"
            "  case \"$3\" in\n"
            "    *\"import ensurepip, venv\"*) exit 1 ;;\n"
            "    *\"sys.version_info\"*) echo 3.12; exit 0 ;;\n"
            "  esac\n"
            "fi\n"
            "exec \"$REAL_PYTHON\" \"$@\"\n",
            encoding="utf-8",
        )
        path.chmod(0o755)
        return path

    def test_check_reports_missing_venv_prerequisite_without_creating_runtime(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")

        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            repo = temp / "repo"
            repo.mkdir()
            wrapper = repo / "setup_linux.sh"
            shutil.copy2(ROOT / "setup_linux.sh", wrapper)
            wrapper.chmod(0o755)
            (repo / "setup_core.py").write_text("raise SystemExit(0)\n", encoding="utf-8")
            fake_python = self._fake_python(temp)
            runtime_dir = temp / "runtime"
            env = {
                **os.environ,
                "HOME": str(temp / "home"),
                "REAL_PYTHON": sys.executable,
                "OPENCODE_SETUP_PYTHON": str(fake_python),
                "OPENCODE_SETUP_RUNTIME_DIR": str(runtime_dir),
            }

            cp = subprocess.run(
                [bash, str(wrapper), "--check", "--skip-package-install", "--skip-dependency-install"],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            self.assertEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            self.assertFalse(runtime_dir.exists())
            self.assertIn("PREREQUISITE", cp.stderr)
            self.assertIn("MANUAL ACTION REQUIRED", cp.stderr)
            self.assertIn("sudo apt install python3.12-venv", cp.stderr)

    def test_apply_stops_before_runtime_mutation_when_venv_prerequisite_is_missing(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")

        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            repo = temp / "repo"
            repo.mkdir()
            wrapper = repo / "setup_linux.sh"
            shutil.copy2(ROOT / "setup_linux.sh", wrapper)
            wrapper.chmod(0o755)
            (repo / "setup_core.py").write_text("raise AssertionError('core must not run')\n", encoding="utf-8")
            fake_python = self._fake_python(temp)
            runtime_parent = temp / "runtime-parent"
            runtime_dir = runtime_parent / "python"
            env = {
                **os.environ,
                "HOME": str(temp / "home"),
                "REAL_PYTHON": sys.executable,
                "OPENCODE_SETUP_PYTHON": str(fake_python),
                "OPENCODE_SETUP_RUNTIME_DIR": str(runtime_dir),
            }

            cp = subprocess.run(
                [bash, str(wrapper), "--skip-package-install", "--skip-dependency-install"],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )

            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
            self.assertFalse(runtime_dir.exists())
            self.assertFalse(runtime_parent.exists(), "prerequisite failure must happen before mkdir/temp runtime creation")
            self.assertIn("MANUAL ACTION REQUIRED", cp.stderr)
            self.assertIn("sudo apt install python3.12-venv", cp.stderr)


if __name__ == "__main__":
    unittest.main()
