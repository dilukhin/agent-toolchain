from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_managed_tools as managed  # noqa: E402
import setup_manifest  # noqa: E402
import toolchainctl  # noqa: E402
from setup_lib import Reporter, STATE_CONFIGURED, STATE_CONFLICT, STATE_MISSING, STATE_OK  # noqa: E402


class LegacyStateMigrationTests(unittest.TestCase):
    def _write_manifest(self, state: Path) -> bytes:
        state.mkdir(parents=True)
        manifest = setup_manifest.empty_manifest()
        manifest["managed_files"]["fixture"] = {"sha256": "abc", "source": "opencode_setup:test"}
        setup_manifest.save_manifest(state / "manifest.json", manifest)
        nested = state / "backups" / "fixture.txt"
        nested.parent.mkdir(parents=True)
        nested.write_bytes(b"legacy-backup\x00bytes")
        return nested.read_bytes()

    def test_check_uses_legacy_state_without_creating_new_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / "legacy"
            new = root / "new"
            self._write_manifest(legacy)
            with mock.patch.dict(os.environ, {
                "OPENCODE_SETUP_STATE_DIR": str(legacy),
                "AGENT_TOOLCHAIN_STATE_DIR": str(new),
            }, clear=False):
                selected, state, detail = toolchainctl.prepare_state(check=True)
            self.assertEqual(selected, legacy.resolve())
            self.assertEqual(state, "outdated")
            self.assertIn("apply", detail or "")
            self.assertFalse(new.exists(), "check must not create the new state namespace")

    def test_apply_imports_legacy_state_once_and_preserves_original(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / "legacy"
            new = root / "new"
            expected = self._write_manifest(legacy)
            legacy_manifest_before = (legacy / "manifest.json").read_bytes()
            with mock.patch.dict(os.environ, {
                "OPENCODE_SETUP_STATE_DIR": str(legacy),
                "AGENT_TOOLCHAIN_STATE_DIR": str(new),
            }, clear=False):
                selected, state, detail = toolchainctl.prepare_state(check=False)
                selected_again, state_again, detail_again = toolchainctl.prepare_state(check=False)
            self.assertEqual(selected, new.resolve())
            self.assertEqual(state, "configured")
            self.assertIn("original retained unchanged", detail or "")
            self.assertEqual((new / "backups" / "fixture.txt").read_bytes(), expected)
            self.assertEqual((legacy / "manifest.json").read_bytes(), legacy_manifest_before)
            self.assertEqual(selected_again, new.resolve())
            self.assertEqual(state_again, "info")
            self.assertIn("inactive backup", detail_again or "")

    def test_apply_refuses_legacy_directory_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            legacy = root / "legacy"
            new = root / "new"
            legacy.mkdir()
            (legacy / "unknown.txt").write_text("preserve", encoding="utf-8")
            with mock.patch.dict(os.environ, {
                "OPENCODE_SETUP_STATE_DIR": str(legacy),
                "AGENT_TOOLCHAIN_STATE_DIR": str(new),
            }, clear=False):
                with self.assertRaises(toolchainctl.StateMigrationError):
                    toolchainctl.prepare_state(check=False)
            self.assertEqual((legacy / "unknown.txt").read_text(encoding="utf-8"), "preserve")
            self.assertFalse(new.exists())


class ManagedPythonToolRuntimeTests(unittest.TestCase):
    COMMIT = "1" * 40

    def _fake_run(self, spec: managed.PythonToolRuntime):
        def fake_run(cmd: list[str], cwd=None, env=None):
            if cmd[:3] == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, self.COMMIT + "\n", "")
            if len(cmd) >= 4 and cmd[1:4] == ["-B", "-c", "import ensurepip, venv; import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"]:
                return subprocess.CompletedProcess(cmd, 0, "3.12\n", "")
            if "-m" in cmd and "venv" in cmd:
                venv = Path(cmd[-1])
                python = managed._venv_python(venv)
                python.parent.mkdir(parents=True, exist_ok=True)
                python.write_bytes(b"fake-python")
                if os.name != "nt":
                    python.chmod(0o755)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if len(cmd) >= 5 and cmd[1:4] == ["-m", "pip", "install"]:
                venv = Path(cmd[0]).parent.parent
                command = managed._venv_command(venv, spec.public_command)
                command.parent.mkdir(parents=True, exist_ok=True)
                command.write_bytes(b"fake-command")
                if os.name != "nt":
                    command.chmod(0o755)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[-1:] == ["--version"]:
                return subprocess.CompletedProcess(cmd, 0, "fixture 1.0\n", "")
            if cmd[-1:] == ["doctor"]:
                return subprocess.CompletedProcess(cmd, 0, "Runtime: ok\n", "")
            if cmd[-1:] == ["--help"]:
                token = spec.required_help_token or "usage"
                return subprocess.CompletedProcess(cmd, 0, f"usage: fixture {token}\n", "")
            raise AssertionError(f"unexpected command: {cmd}")
        return fake_run

    def _repo(self, root: Path) -> Path:
        repo = root / "repo"
        (repo / ".git").mkdir(parents=True)
        return repo

    def test_check_is_read_only_for_missing_runtime(self) -> None:
        spec = managed.SSH_RELAY_RUNTIME
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            bin_dir = root / "bin"
            repo = self._repo(root)
            with mock.patch.dict(os.environ, {
                "AGENT_TOOLCHAIN_DATA_DIR": str(data),
                "AGENT_TOOLCHAIN_BIN_DIR": str(bin_dir),
            }, clear=False), mock.patch.object(managed, "run", self._fake_run(spec)):
                reporter = Reporter()
                managed.ensure_python_tool_runtime(spec, repo, sys.executable, reporter, check=True, skip_install=False)
            runtime = [r for r in reporter.results if r.component == "ssh_relay runtime"][-1]
            self.assertEqual(runtime.state, STATE_MISSING)
            self.assertIn("non-editable", runtime.detail)
            self.assertFalse(data.exists())
            self.assertFalse(bin_dir.exists())

    def test_apply_publishes_owned_runtime_and_repeat_is_noop(self) -> None:
        spec = managed.SSH_RELAY_RUNTIME
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            bin_dir = root / "bin"
            repo = self._repo(root)
            env = {
                "AGENT_TOOLCHAIN_DATA_DIR": str(data),
                "AGENT_TOOLCHAIN_BIN_DIR": str(bin_dir),
                "PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            }
            fake_run = self._fake_run(spec)
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(managed, "run", fake_run):
                first = Reporter()
                managed.ensure_python_tool_runtime(spec, repo, sys.executable, first, check=False, skip_install=False)
                second = Reporter()
                managed.ensure_python_tool_runtime(spec, repo, sys.executable, second, check=False, skip_install=False)

            release = data / "tools" / spec.name / "releases" / self.COMMIT
            marker = json.loads((release / managed._RUNTIME_MARKER).read_text(encoding="utf-8"))
            self.assertEqual(marker["source_commit"], self.COMMIT)
            self.assertEqual(marker["tool"], spec.name)
            self.assertEqual([r for r in first.results if r.component == "ssh_relay runtime"][-1].state, STATE_CONFIGURED)
            self.assertEqual([r for r in second.results if r.component == "ssh_relay runtime"][-1].state, STATE_OK)
            public = managed._public_entrypoint(spec)
            self.assertTrue(public.exists() or public.is_symlink())

    def test_foreign_public_entrypoint_is_preserved(self) -> None:
        spec = managed.SSH_RELAY_RUNTIME
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            public = bin_dir / ("ssh_relay.cmd" if os.name == "nt" else "ssh_relay")
            public.write_bytes(b"user-owned\n")
            if os.name != "nt":
                public.chmod(public.stat().st_mode | stat.S_IXUSR)
            target = root / ("ssh_relay.exe" if os.name == "nt" else "ssh_relay-target")
            target.write_bytes(b"target")
            if os.name != "nt":
                target.chmod(0o755)
            before = public.read_bytes()
            with mock.patch.dict(os.environ, {"AGENT_TOOLCHAIN_BIN_DIR": str(bin_dir)}, clear=False):
                reporter = Reporter()
                ok = managed._reconcile_entrypoint(spec, target, reporter, check=False)
            self.assertFalse(ok)
            self.assertEqual(public.read_bytes(), before)
            self.assertEqual(reporter.results[-1].state, STATE_CONFLICT)


if __name__ == "__main__":
    unittest.main()
