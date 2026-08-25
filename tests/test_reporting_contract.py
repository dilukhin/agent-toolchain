from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_lib as lib  # noqa: E402
import setup_runtime as runtime  # noqa: E402


class ReporterTransitionTests(unittest.TestCase):
    def test_successful_migration_replaces_observed_pre_state(self) -> None:
        reporter = lib.Reporter()
        reporter.add("OpenCode config", lib.STATE_OUTDATED, "before")
        reporter.add("OpenCode config migration", lib.STATE_OK, "backup: /tmp/example")

        self.assertEqual(len(reporter.results), 1)
        self.assertEqual(reporter.results[0].component, "OpenCode config")
        self.assertEqual(reporter.results[0].state, lib.STATE_OK)
        self.assertIn("backup", reporter.results[0].detail)

    def test_successful_runtime_validation_replaces_missing_pre_state(self) -> None:
        reporter = lib.Reporter()
        reporter.add("ssh_relay runtime", lib.STATE_MISSING, "before")
        reporter.add("ssh_relay runtime validation", lib.STATE_OK, "ssh_relay 0.9.0")

        self.assertEqual(len(reporter.results), 1)
        self.assertEqual(reporter.results[0].component, "ssh_relay runtime")
        self.assertEqual(reporter.results[0].state, lib.STATE_OK)


class FileReconciliationReportingTests(unittest.TestCase):
    def test_check_explains_automatic_fix_and_apply_reports_final_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "managed" / "file.txt"
            state_dir = root / "state"
            manifest = {"managed_files": {}}

            check_reporter = lib.Reporter()
            changed = lib.reconcile_file(
                component="fixture",
                destination=destination,
                source_data=b"desired\n",
                source_label="test",
                manifest=manifest,
                reporter=check_reporter,
                check=True,
                force=False,
                state_dir=state_dir,
            )
            self.assertFalse(changed)
            self.assertFalse(destination.exists())
            self.assertEqual(check_reporter.results[-1].state, lib.STATE_MISSING)
            self.assertIn("обычный apply", check_reporter.results[-1].detail)

            apply_reporter = lib.Reporter()
            changed = lib.reconcile_file(
                component="fixture",
                destination=destination,
                source_data=b"desired\n",
                source_label="test",
                manifest=manifest,
                reporter=apply_reporter,
                check=False,
                force=False,
                state_dir=state_dir,
            )
            self.assertTrue(changed)
            self.assertEqual(destination.read_bytes(), b"desired\n")
            self.assertEqual(len(apply_reporter.results), 1)
            self.assertEqual(apply_reporter.results[0].state, lib.STATE_OK)
            self.assertNotIn(lib.STATE_MISSING, [item.state for item in apply_reporter.results])


class RuntimeReportingTests(unittest.TestCase):
    def test_ssh_relay_check_explains_auto_install_and_apply_reports_up_to_date(self) -> None:
        original_run = runtime.run
        try:
            def fake_run(cmd: list[str], cwd=None, env=None):
                if cmd[1:] == ["-c", "import paramiko"]:
                    return subprocess.CompletedProcess(cmd, 1, "", "missing")
                if cmd[1:4] == ["-m", "pip", "install"] and cmd[-1] == "paramiko":
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                if cmd[-1] == "--version":
                    return subprocess.CompletedProcess(cmd, 0, "ssh_relay 0.9.0\n", "")
                if cmd[-1] == "--help":
                    return subprocess.CompletedProcess(cmd, 0, "usage: ssh_relay job\n", "")
                raise AssertionError(f"unexpected command: {cmd}")

            runtime.run = fake_run
            repo = Path("/tmp/ssh-relay-fixture")

            check_reporter = runtime.Reporter()
            runtime.ensure_ssh_relay_runtime(repo, sys.executable, check_reporter, check=True, skip_install=False)
            self.assertEqual(check_reporter.results[-1].state, runtime.STATE_MISSING)
            self.assertIn("обычный apply", check_reporter.results[-1].detail)

            apply_reporter = runtime.Reporter()
            runtime.ensure_ssh_relay_runtime(repo, sys.executable, apply_reporter, check=False, skip_install=False)
            self.assertEqual(len(apply_reporter.results), 1)
            self.assertEqual(apply_reporter.results[0].component, "ssh_relay runtime")
            self.assertEqual(apply_reporter.results[0].state, runtime.STATE_OK)
            self.assertIn("paramiko установлен автоматически", apply_reporter.results[0].detail)
        finally:
            runtime.run = original_run

    def test_agent_safe_check_explains_auto_install_and_apply_reports_up_to_date(self) -> None:
        original_run = runtime.run
        original_origin = runtime._module_origin
        try:
            with tempfile.TemporaryDirectory() as td:
                repo = Path(td) / "agent-safe"
                repo.mkdir()
                repo_resolved = repo.resolve()

                runtime._module_origin = lambda python_exe, module: None
                check_reporter = runtime.Reporter()
                runtime.ensure_agent_safe_runtime(repo, sys.executable, check_reporter, check=True, skip_install=False)
                self.assertEqual(check_reporter.results[-1].state, runtime.STATE_MISSING)
                self.assertIn("обычный apply", check_reporter.results[-1].detail)

                origins = iter([None, repo_resolved / "agent_safe" / "__init__.py"])
                runtime._module_origin = lambda python_exe, module: next(origins)

                def fake_run(cmd: list[str], cwd=None, env=None):
                    if cmd[1:4] == ["-m", "pip", "install"] and "-e" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, "", "")
                    if cmd[1:3] == ["-m", "agent_safe"] and cmd[-1] == "--help":
                        return subprocess.CompletedProcess(cmd, 0, "usage\n", "")
                    raise AssertionError(f"unexpected command: {cmd}")

                runtime.run = fake_run
                apply_reporter = runtime.Reporter()
                runtime.ensure_agent_safe_runtime(repo, sys.executable, apply_reporter, check=False, skip_install=False)
                self.assertEqual(len(apply_reporter.results), 1)
                self.assertEqual(apply_reporter.results[0].component, "agent-safe runtime")
                self.assertEqual(apply_reporter.results[0].state, runtime.STATE_OK)
                self.assertIn("установлен автоматически", apply_reporter.results[0].detail)
        finally:
            runtime.run = original_run
            runtime._module_origin = original_origin


if __name__ == "__main__":
    unittest.main()
