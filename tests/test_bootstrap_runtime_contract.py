from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "opencode_setup-bootstrap-python-v1"


def _fake_core_text() -> str:
    return (
        "from __future__ import annotations\n"
        "import json, os, pathlib, sys\n"
        "pathlib.Path(os.environ['OPENCODE_SETUP_TEST_PROBE']).write_text(\n"
        "    json.dumps({'executable': sys.executable, 'argv': sys.argv}), encoding='utf-8'\n"
        ")\n"
    )


@unittest.skipIf(os.name == "nt", "Linux wrapper contract")
class LinuxBootstrapPythonRuntimeTests(unittest.TestCase):
    def test_check_is_read_only_and_apply_uses_managed_venv_idempotently(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")

        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            script_dir = temp / "repo"
            script_dir.mkdir()
            wrapper = script_dir / "setup_linux.sh"
            shutil.copy2(ROOT / "setup_linux.sh", wrapper)
            wrapper.chmod(0o755)
            (script_dir / "setup_core.py").write_text(_fake_core_text(), encoding="utf-8")

            home = temp / "home"
            runtime_dir = temp / "managed-runtime"
            probe = temp / "probe.json"
            env = {
                **os.environ,
                "HOME": str(home),
                "OPENCODE_SETUP_RUNTIME_DIR": str(runtime_dir),
                "OPENCODE_SETUP_TEST_PROBE": str(probe),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
            common = ["--skip-package-install", "--skip-dependency-install"]

            check = subprocess.run(
                [bash, str(wrapper), "--check", *common],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertFalse(runtime_dir.exists(), "--check must not create the managed runtime")
            check_probe = json.loads(probe.read_text(encoding="utf-8"))
            self.assertNotIn(str(runtime_dir), check_probe["executable"])

            apply = subprocess.run(
                [bash, str(wrapper), *common],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(apply.returncode, 0, apply.stdout + apply.stderr)
            runtime_python = runtime_dir / "bin" / "python"
            marker = runtime_dir / ".opencode-setup-managed-runtime"
            self.assertTrue(runtime_python.is_file())
            self.assertEqual(marker.read_text(encoding="utf-8").strip(), MARKER)
            apply_probe = json.loads(probe.read_text(encoding="utf-8"))
            self.assertEqual(Path(apply_probe["executable"]).resolve(), runtime_python.resolve())
            pip = subprocess.run(
                [str(runtime_python), "-m", "pip", "--version"],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            self.assertEqual(pip.returncode, 0, pip.stdout + pip.stderr)

            cfg = runtime_dir / "pyvenv.cfg"
            before_bytes = cfg.read_bytes()
            before_mtime = cfg.stat().st_mtime_ns
            second = subprocess.run(
                [bash, str(wrapper), *common],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(cfg.read_bytes(), before_bytes)
            self.assertEqual(cfg.stat().st_mtime_ns, before_mtime)
            second_probe = json.loads(probe.read_text(encoding="utf-8"))
            self.assertEqual(Path(second_probe["executable"]).resolve(), runtime_python.resolve())

    def test_apply_refuses_incomplete_existing_runtime(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")

        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            script_dir = temp / "repo"
            script_dir.mkdir()
            wrapper = script_dir / "setup_linux.sh"
            shutil.copy2(ROOT / "setup_linux.sh", wrapper)
            wrapper.chmod(0o755)
            (script_dir / "setup_core.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

            runtime_dir = temp / "managed-runtime"
            runtime_dir.mkdir()
            sentinel = runtime_dir / "user-file.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            env = {
                **os.environ,
                "HOME": str(temp / "home"),
                "OPENCODE_SETUP_RUNTIME_DIR": str(runtime_dir),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
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
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertIn("ownership/health is not proven", cp.stderr)

    def test_check_refuses_external_venv_without_managed_marker(self) -> None:
        bash = shutil.which("bash")
        if not bash:
            self.skipTest("bash is unavailable")

        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            script_dir = temp / "repo"
            script_dir.mkdir()
            wrapper = script_dir / "setup_linux.sh"
            shutil.copy2(ROOT / "setup_linux.sh", wrapper)
            wrapper.chmod(0o755)
            (script_dir / "setup_core.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

            runtime_dir = temp / "external-venv"
            runtime_bin = runtime_dir / "bin"
            runtime_bin.mkdir(parents=True)
            fake_python = runtime_bin / "python"
            fake_python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_python.chmod(0o755)
            env = {
                **os.environ,
                "HOME": str(temp / "home"),
                "OPENCODE_SETUP_RUNTIME_DIR": str(runtime_dir),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
            cp = subprocess.run(
                [bash, str(wrapper), "--check", "--skip-package-install", "--skip-dependency-install"],
                text=True,
                encoding="utf-8",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
            self.assertIn("refusing to adopt or repair", cp.stderr.lower())


@unittest.skipUnless(os.name == "nt", "Windows wrapper contract")
class WindowsBootstrapPythonRuntimeTests(unittest.TestCase):
    def _powershell(self) -> str:
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if not shell:
            self.skipTest("PowerShell is unavailable")
        return shell

    def _run(self, shell: str, wrapper: Path, runtime_dir: Path, env: dict[str, str], *, check: bool) -> subprocess.CompletedProcess[str]:
        cmd = [
            shell,
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", str(wrapper),
            "-RuntimeDir", str(runtime_dir),
            "-SkipPackageInstall",
            "-SkipDependencyInstall",
        ]
        if check:
            cmd.append("-Check")
        return subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

    def test_check_is_read_only_apply_creates_owned_runtime_and_repeat_reuses_it(self) -> None:
        shell = self._powershell()
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            script_dir = temp / "repo"
            script_dir.mkdir()
            wrapper = script_dir / "setup_windows.ps1"
            shutil.copy2(ROOT / "setup_windows.ps1", wrapper)
            (script_dir / "setup_core.py").write_text(_fake_core_text(), encoding="utf-8")

            runtime_dir = temp / "managed-runtime"
            probe = temp / "probe.json"
            env = {
                **os.environ,
                "USERPROFILE": str(temp / "home"),
                "LOCALAPPDATA": str(temp / "localappdata"),
                "OPENCODE_SETUP_TEST_PROBE": str(probe),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }

            check = self._run(shell, wrapper, runtime_dir, env, check=True)
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertFalse(runtime_dir.exists(), "-Check must not create the managed runtime")
            check_probe = json.loads(probe.read_text(encoding="utf-8"))
            self.assertNotIn(str(runtime_dir).lower(), check_probe["executable"].lower())

            apply = self._run(shell, wrapper, runtime_dir, env, check=False)
            self.assertEqual(apply.returncode, 0, apply.stdout + apply.stderr)
            runtime_python = runtime_dir / "Scripts" / "python.exe"
            marker = runtime_dir / ".opencode-setup-managed-runtime"
            self.assertTrue(runtime_python.is_file())
            self.assertEqual(marker.read_text(encoding="ascii").strip(), MARKER)
            apply_probe = json.loads(probe.read_text(encoding="utf-8"))
            self.assertEqual(Path(apply_probe["executable"]).resolve(), runtime_python.resolve())

            cfg = runtime_dir / "pyvenv.cfg"
            before_bytes = cfg.read_bytes()
            before_mtime = cfg.stat().st_mtime_ns
            second = self._run(shell, wrapper, runtime_dir, env, check=False)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(cfg.read_bytes(), before_bytes)
            self.assertEqual(cfg.stat().st_mtime_ns, before_mtime)

    def test_check_refuses_external_runtime_without_marker(self) -> None:
        shell = self._powershell()
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            script_dir = temp / "repo"
            script_dir.mkdir()
            wrapper = script_dir / "setup_windows.ps1"
            shutil.copy2(ROOT / "setup_windows.ps1", wrapper)
            (script_dir / "setup_core.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

            runtime_python = temp / "external-runtime" / "Scripts" / "python.exe"
            runtime_python.parent.mkdir(parents=True)
            runtime_python.write_bytes(b"not an opencode_setup runtime")
            runtime_dir = runtime_python.parents[1]
            sentinel = runtime_dir / "user-file.txt"
            sentinel.write_text("preserve", encoding="utf-8")
            env = {
                **os.environ,
                "USERPROFILE": str(temp / "home"),
                "LOCALAPPDATA": str(temp / "localappdata"),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
            }
            cp = self._run(shell, wrapper, runtime_dir, env, check=True)
            self.assertNotEqual(cp.returncode, 0, cp.stdout + cp.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
            combined = (cp.stdout + cp.stderr).lower()
            self.assertIn("ownership/health is not proven", combined)
            self.assertIn("refusing to adopt or repair", combined)


if __name__ == "__main__":
    unittest.main()
