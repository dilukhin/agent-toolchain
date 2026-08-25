from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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

            fake_core = script_dir / "setup_core.py"
            fake_core.write_text(
                "from __future__ import annotations\n"
                "import json, os, pathlib, sys\n"
                "pathlib.Path(os.environ['OPENCODE_SETUP_TEST_PROBE']).write_text(\n"
                "    json.dumps({'executable': sys.executable, 'argv': sys.argv}), encoding='utf-8'\n"
                ")\n",
                encoding="utf-8",
            )

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
            self.assertTrue(runtime_python.is_file())
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
            self.assertIn("incomplete", cp.stderr)


if __name__ == "__main__":
    unittest.main()
