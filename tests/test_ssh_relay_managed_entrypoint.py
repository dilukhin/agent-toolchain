from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_runtime as runtime  # noqa: E402


class SshRelayManagedEntrypointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_run = runtime.run
        self.original_which = runtime.shutil.which
        self.original_bin = os.environ.get("OPENCODE_SETUP_BIN_DIR")

    def tearDown(self) -> None:
        runtime.run = self.original_run
        runtime.shutil.which = self.original_which
        if self.original_bin is None:
            os.environ.pop("OPENCODE_SETUP_BIN_DIR", None)
        else:
            os.environ["OPENCODE_SETUP_BIN_DIR"] = self.original_bin

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        repo = root / "ssh_relay"
        repo.mkdir()
        (repo / "ssh_relay.py").write_text(
            "#!/usr/bin/env python3\n__version__ = '0.9.0'\n",
            encoding="utf-8",
        )

        runtime_root = root / "runtime" / "python"
        scripts = runtime_root / ("Scripts" if os.name == "nt" else "bin")
        scripts.mkdir(parents=True)
        python_exe = scripts / ("python.exe" if os.name == "nt" else "python")
        python_exe.write_bytes(b"")
        (runtime_root / ".opencode-setup-managed-runtime").write_text(
            "opencode_setup-bootstrap-python-v1\n",
            encoding="ascii",
        )

        public_bin = root / "public-bin"
        os.environ["OPENCODE_SETUP_BIN_DIR"] = str(public_bin)
        public = public_bin / ("ssh_relay.cmd" if os.name == "nt" else "ssh_relay")
        return repo, python_exe, public

    def _install_fake_run(self, python_exe: Path, public: Path) -> None:
        system_python = str(Path("C:/system/python.exe") if os.name == "nt" else Path("/usr/bin/python3"))

        def fake_which(name: str):
            if name == "python3" and os.name != "nt":
                return system_python
            if name == "ssh_relay" and public.exists():
                return str(public)
            return None

        def fake_run(cmd: list[str], cwd=None, env=None):
            if cmd[:3] == [str(python_exe), "-c", "import paramiko"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if os.name != "nt" and cmd[:3] == [system_python, "-c", "import paramiko"]:
                return subprocess.CompletedProcess(cmd, 1, "", "ModuleNotFoundError")
            if "--version" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "ssh_relay 0.9.0\n", "")
            if "--help" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "usage: ssh_relay job\n", "")
            raise AssertionError(f"unexpected command: {cmd}")

        runtime.shutil.which = fake_which
        runtime.run = fake_run

    def test_check_apply_repeat_use_managed_python_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, python_exe, public = self._fixture(root)
            self._install_fake_run(python_exe, public)

            check_reporter = runtime.Reporter()
            runtime.ensure_ssh_relay_runtime(repo, str(python_exe), check_reporter, check=True, skip_install=False)
            entry = [x for x in check_reporter.results if x.component == "ssh_relay entrypoint"][-1]
            self.assertEqual(entry.state, runtime.STATE_MISSING)
            self.assertFalse(public.exists())

            apply_reporter = runtime.Reporter()
            runtime.ensure_ssh_relay_runtime(repo, str(python_exe), apply_reporter, check=False, skip_install=False)
            entry = [x for x in apply_reporter.results if x.component == "ssh_relay entrypoint"][-1]
            self.assertEqual(entry.state, runtime.STATE_CONFIGURED)
            self.assertTrue(public.exists())
            health = [x for x in apply_reporter.results if x.component == "ssh_relay health"][-1]
            self.assertEqual(health.state, runtime.STATE_OK)
            resolution = [x for x in apply_reporter.results if x.component == "ssh_relay command resolution"][-1]
            self.assertEqual(resolution.state, runtime.STATE_OK)
            if os.name != "nt":
                self.assertTrue(public.is_symlink())
                self.assertTrue(os.access(public.resolve(), os.X_OK))
                source = [x for x in apply_reporter.results if x.component == "ssh_relay source launcher"][-1]
                self.assertEqual(source.state, runtime.STATE_INFO)
                self.assertIn("paramiko", source.detail)
                self.assertIn(str(public), source.detail)

            repeat_reporter = runtime.Reporter()
            runtime.ensure_ssh_relay_runtime(repo, str(python_exe), repeat_reporter, check=False, skip_install=False)
            entry = [x for x in repeat_reporter.results if x.component == "ssh_relay entrypoint"][-1]
            self.assertEqual(entry.state, runtime.STATE_OK)

    def test_unowned_public_entrypoint_is_preserved_as_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            repo, python_exe, public = self._fixture(root)
            self._install_fake_run(python_exe, public)

            first = runtime.Reporter()
            runtime.ensure_ssh_relay_runtime(repo, str(python_exe), first, check=False, skip_install=False)
            if public.is_symlink():
                public.unlink()
            public.write_text("user-owned entrypoint\n", encoding="utf-8")

            reporter = runtime.Reporter()
            runtime.ensure_ssh_relay_runtime(repo, str(python_exe), reporter, check=False, skip_install=False)
            entry = [x for x in reporter.results if x.component == "ssh_relay entrypoint"][-1]
            self.assertEqual(entry.state, runtime.STATE_CONFLICT)
            self.assertEqual(public.read_text(encoding="utf-8"), "user-owned entrypoint\n")


if __name__ == "__main__":
    unittest.main()
