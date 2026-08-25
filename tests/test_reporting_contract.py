from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_core as core  # noqa: E402
import setup_lib as lib  # noqa: E402
import setup_runtime as runtime  # noqa: E402


class ReporterTransitionTests(unittest.TestCase):
    def test_successful_migration_is_reported_as_configured(self) -> None:
        reporter = lib.Reporter()
        reporter.add("OpenCode config", lib.STATE_OUTDATED, "before")
        reporter.add("OpenCode config migration", lib.STATE_OK, "backup: /tmp/example")

        self.assertEqual(len(reporter.results), 1)
        self.assertEqual(reporter.results[0].component, "OpenCode config")
        self.assertEqual(reporter.results[0].state, lib.STATE_CONFIGURED)
        self.assertIn("backup", reporter.results[0].detail)

    def test_successful_runtime_validation_is_reported_as_configured(self) -> None:
        reporter = lib.Reporter()
        reporter.add("ssh_relay runtime", lib.STATE_MISSING, "before")
        reporter.add("ssh_relay runtime validation", lib.STATE_OK, "ssh_relay 0.9.0")

        self.assertEqual(len(reporter.results), 1)
        self.assertEqual(reporter.results[0].component, "ssh_relay runtime")
        self.assertEqual(reporter.results[0].state, lib.STATE_CONFIGURED)

    def test_later_validation_does_not_erase_configured_state(self) -> None:
        reporter = lib.Reporter()
        reporter.add("fixture", lib.STATE_CONFIGURED, "created")
        reporter.add("fixture", lib.STATE_OK, "verified")
        self.assertEqual(len(reporter.results), 1)
        self.assertEqual(reporter.results[0].state, lib.STATE_CONFIGURED)
        self.assertIn("verified", reporter.results[0].detail)

    def test_failed_action_is_an_error_state(self) -> None:
        reporter = lib.Reporter()
        reporter.add("fixture", lib.STATE_FAILED, "install failed")
        self.assertTrue(reporter.has_conflict)


class ReporterColorTests(unittest.TestCase):
    class TtyBuffer(io.StringIO):
        def isatty(self) -> bool:
            return True

    def test_forced_color_marks_only_action_and_error_states(self) -> None:
        reporter = lib.Reporter()
        reporter.add("already", lib.STATE_OK, "unchanged")
        reporter.add("changed", lib.STATE_CONFIGURED, "created")
        reporter.add("failed", lib.STATE_FAILED, "install failed")
        reporter.add("conflict", lib.STATE_CONFLICT, "blocked")

        output = io.StringIO()
        reporter.render(stream=output, color=True)
        lines = output.getvalue().splitlines()
        already = next(line for line in lines if "already" in line)
        changed = next(line for line in lines if "changed" in line)
        failed = next(line for line in lines if "failed" in line and "install failed" in line)
        conflict = next(line for line in lines if "conflict" in line and "blocked" in line)
        self.assertNotIn("\x1b[", already)
        self.assertIn("\x1b[32m", changed)
        self.assertIn("\x1b[31m", failed)
        self.assertIn("\x1b[31m", conflict)

    def test_redirected_output_has_no_ansi(self) -> None:
        reporter = lib.Reporter()
        reporter.add("changed", lib.STATE_CONFIGURED, "created")
        output = io.StringIO()
        reporter.render(stream=output)
        self.assertNotIn("\x1b[", output.getvalue())

    def test_no_color_disables_ansi_even_for_tty(self) -> None:
        reporter = lib.Reporter()
        reporter.add("changed", lib.STATE_CONFIGURED, "created")
        previous = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            output = self.TtyBuffer()
            reporter.render(stream=output)
            self.assertNotIn("\x1b[", output.getvalue())
        finally:
            if previous is None:
                os.environ.pop("NO_COLOR", None)
            else:
                os.environ["NO_COLOR"] = previous


class FileReconciliationReportingTests(unittest.TestCase):
    def test_check_explains_automatic_fix_apply_is_configured_repeat_is_up_to_date(self) -> None:
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
            self.assertEqual(apply_reporter.results[0].state, lib.STATE_CONFIGURED)
            self.assertIn("создан", apply_reporter.results[0].detail)

            repeat_reporter = lib.Reporter()
            changed = lib.reconcile_file(
                component="fixture",
                destination=destination,
                source_data=b"desired\n",
                source_label="test",
                manifest=manifest,
                reporter=repeat_reporter,
                check=False,
                force=False,
                state_dir=state_dir,
            )
            self.assertFalse(changed)
            self.assertEqual(repeat_reporter.results[0].state, lib.STATE_OK)


class RuntimeReportingTests(unittest.TestCase):
    def test_ssh_relay_check_explains_auto_install_and_apply_reports_configured(self) -> None:
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
            self.assertEqual(apply_reporter.results[0].state, runtime.STATE_CONFIGURED)
            self.assertIn("paramiko установлен", apply_reporter.results[0].detail)
        finally:
            runtime.run = original_run

    def test_ssh_relay_install_failure_reports_failed(self) -> None:
        original_run = runtime.run
        try:
            def fake_run(cmd: list[str], cwd=None, env=None):
                if cmd[1:] == ["-c", "import paramiko"]:
                    return subprocess.CompletedProcess(cmd, 1, "", "missing")
                if cmd[1:4] == ["-m", "pip", "install"]:
                    return subprocess.CompletedProcess(cmd, 1, "", "pip failure")
                raise AssertionError(f"unexpected command: {cmd}")

            runtime.run = fake_run
            reporter = runtime.Reporter()
            runtime.ensure_ssh_relay_runtime(Path("/tmp/ssh"), sys.executable, reporter, check=False, skip_install=False)
            self.assertEqual(reporter.results[-1].state, runtime.STATE_FAILED)
            self.assertTrue(reporter.has_conflict)
        finally:
            runtime.run = original_run

    def test_agent_safe_check_explains_auto_install_and_apply_reports_configured(self) -> None:
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
                self.assertEqual(apply_reporter.results[0].state, runtime.STATE_CONFIGURED)
                self.assertIn("установлен", apply_reporter.results[0].detail)
        finally:
            runtime.run = original_run
            runtime._module_origin = original_origin


class CoreActionGuidanceTests(unittest.TestCase):
    def test_check_explains_remaining_missing_actions_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            stash_dir = root / "stash"
            credential_dir = config_dir / "credentials"
            skills_dir = root / "skills"
            state_dir = root / "state"
            projects_dir = root / "projects"
            expected_absent = [config_dir, stash_dir, skills_dir, state_dir, projects_dir]

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                rc = core.main([
                    "--check",
                    "--config-dir", str(config_dir),
                    "--stash-dir", str(stash_dir),
                    "--credential-dir", str(credential_dir),
                    "--skills-dir", str(skills_dir),
                    "--state-dir", str(state_dir),
                    "--projects-dir", str(projects_dir),
                    "--skip-package-install",
                    "--skip-dependency-install",
                    "--ssh-relay-url", str(root / "ssh.git"),
                    "--agent-safe-url", str(root / "safe.git"),
                ])

            self.assertEqual(rc, 0)
            text = output.getvalue()
            self.assertNotIn("\x1b[", text)
            self.assertNotIn(lib.STATE_CONFIGURED, text)
            self.assertRegex(text, r"missing\s+RouterAI credential.*MANUAL ACTION REQUIRED")
            self.assertRegex(text, r"missing\s+skill ssh-relay.*обычный apply.*клонирует")
            self.assertRegex(text, r"missing\s+skill recovery-mode.*обычный apply.*клонирует")
            self.assertRegex(text, r"missing\s+ownership manifest.*обычный apply.*создаст")
            for path in expected_absent:
                self.assertFalse(path.exists(), path)


if __name__ == "__main__":
    unittest.main()
