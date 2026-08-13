from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_runtime as runtime  # noqa: E402
from setup_inventory import ExecutableInstance  # noqa: E402


class NpmLatestPolicyTests(unittest.TestCase):
    def _base_config(self) -> dict:
        return {
            "dependencies": {
                "opencode-cli-package": "opencode-ai",
                "@opencode-ai/plugin": "latest",
            }
        }

    def _patch_active_npm(self):
        original_inventory = runtime.executable_inventory
        original_common = runtime.report_common_tool_inventory
        original_managers = runtime._known_opencode_managers
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("/fake/npm/opencode"), "1.2.3", "npm", True)
        ] if command == "opencode" else []
        runtime.report_common_tool_inventory = lambda reporter: None
        runtime._known_opencode_managers = lambda npm: {"npm": "1.2.3"}
        return original_inventory, original_common, original_managers

    def _restore_active_patch(self, originals) -> None:
        runtime.executable_inventory, runtime.report_common_tool_inventory, runtime._known_opencode_managers = originals

    def test_plugin_latest_is_true_noop_when_current(self) -> None:
        commands: list[list[str]] = []
        original_run = runtime.run
        original_which = runtime.shutil.which
        originals = self._patch_active_npm()
        try:
            def fake_run(cmd: list[str], cwd=None, env=None):
                commands.append(cmd)
                if cmd[1:4] == ["list", "-g", "--depth=0"]:
                    return subprocess.CompletedProcess(cmd, 0, json.dumps({"dependencies": {"opencode-ai": {"version": "1.2.3"}}}), "")
                if cmd[1:3] == ["view", "opencode-ai"]:
                    return subprocess.CompletedProcess(cmd, 0, json.dumps("1.2.3"), "")
                if cmd[1:3] == ["view", "@opencode-ai/plugin"]:
                    return subprocess.CompletedProcess(cmd, 0, json.dumps("9.9.9"), "")
                raise AssertionError(f"unexpected npm command: {cmd}")

            runtime.run = fake_run
            runtime.shutil.which = lambda name: "/fake/npm" if name == "npm" else None
            with tempfile.TemporaryDirectory() as td:
                config_dir = Path(td)
                package_json = config_dir / "node_modules" / "@opencode-ai" / "plugin" / "package.json"
                package_json.parent.mkdir(parents=True)
                package_json.write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")
                reporter = runtime.Reporter()
                runtime.reconcile_npm(config_dir, self._base_config(), reporter, check=False, skip=False)
                self.assertFalse(any("install" in cmd for cmd in commands), commands)
                plugin = [r for r in reporter.results if r.component == "OpenCode plugin"]
                self.assertEqual(plugin[-1].state, runtime.STATE_OK)
                self.assertIn("npm latest", plugin[-1].detail)
        finally:
            runtime.run = original_run
            runtime.shutil.which = original_which
            self._restore_active_patch(originals)

    def test_plugin_latest_updates_to_resolved_registry_version_and_validates(self) -> None:
        commands: list[list[str]] = []
        original_run = runtime.run
        original_which = runtime.shutil.which
        originals = self._patch_active_npm()
        try:
            with tempfile.TemporaryDirectory() as td:
                config_dir = Path(td)
                package_json = config_dir / "node_modules" / "@opencode-ai" / "plugin" / "package.json"
                package_json.parent.mkdir(parents=True)
                package_json.write_text(json.dumps({"version": "8.8.8"}), encoding="utf-8")

                def fake_run(cmd: list[str], cwd=None, env=None):
                    commands.append(cmd)
                    if cmd[1:4] == ["list", "-g", "--depth=0"]:
                        return subprocess.CompletedProcess(cmd, 0, json.dumps({"dependencies": {"opencode-ai": {"version": "1.2.3"}}}), "")
                    if cmd[1:3] == ["view", "opencode-ai"]:
                        return subprocess.CompletedProcess(cmd, 0, json.dumps("1.2.3"), "")
                    if cmd[1:3] == ["view", "@opencode-ai/plugin"]:
                        return subprocess.CompletedProcess(cmd, 0, json.dumps("9.9.9"), "")
                    if "install" in cmd and "@opencode-ai/plugin@9.9.9" in cmd:
                        package_json.write_text(json.dumps({"version": "9.9.9"}), encoding="utf-8")
                        return subprocess.CompletedProcess(cmd, 0, "", "")
                    raise AssertionError(f"unexpected npm command: {cmd}")

                runtime.run = fake_run
                runtime.shutil.which = lambda name: "/fake/npm" if name == "npm" else None
                reporter = runtime.Reporter()
                runtime.reconcile_npm(config_dir, self._base_config(), reporter, check=False, skip=False)
                self.assertTrue(any("@opencode-ai/plugin@9.9.9" in cmd for cmd in commands), commands)
                self.assertEqual(runtime.installed_version(package_json), "9.9.9")
                self.assertFalse(any(r.state == runtime.STATE_CONFLICT for r in reporter.results), reporter.results)
        finally:
            runtime.run = original_run
            runtime.shutil.which = original_which
            self._restore_active_patch(originals)


class OpenCodeOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_inventory = runtime.executable_inventory
        self.original_managers = runtime._known_opencode_managers
        self.original_run = runtime.run

    def tearDown(self) -> None:
        runtime.executable_inventory = self.original_inventory
        runtime._known_opencode_managers = self.original_managers
        runtime.run = self.original_run

    def test_choco_plus_npm_in_path_is_conflict_and_no_update_runs(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("C:/ProgramData/chocolatey/bin/opencode.exe"), "1.18.16", "choco", True),
            ExecutableInstance(Path("C:/Users/Dima/AppData/Roaming/npm/opencode.cmd"), "1.18.18", "npm", False),
        ]
        runtime._known_opencode_managers = lambda npm: {"choco": "1.18.16", "npm": "1.18.18"}
        commands: list[list[str]] = []
        runtime.run = lambda cmd, cwd=None, env=None: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")

        reporter = runtime.Reporter()
        runtime._reconcile_opencode_cli({"dependencies": {"opencode-cli-package": "opencode-ai"}}, reporter, False, "npm")

        duplicate = [r for r in reporter.results if r.component == "OpenCode: дублирующиеся установки"]
        self.assertEqual(duplicate[-1].state, runtime.STATE_CONFLICT)
        self.assertIn("choco uninstall opencode", duplicate[-1].detail)
        self.assertIn("npm uninstall -g opencode-ai", duplicate[-1].detail)
        self.assertFalse(commands)

    def test_single_choco_is_adopted_without_creating_npm_duplicate(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("C:/ProgramData/chocolatey/bin/opencode.exe"), "1.18.16", "choco", True),
        ]
        runtime._known_opencode_managers = lambda npm: {"choco": "1.18.16"}
        commands: list[list[str]] = []
        runtime.run = lambda cmd, cwd=None, env=None: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")

        reporter = runtime.Reporter()
        runtime._reconcile_opencode_cli({"dependencies": {"opencode-cli-package": "opencode-ai"}}, reporter, False, "npm")

        cli = [r for r in reporter.results if r.component == "OpenCode CLI"][-1]
        self.assertEqual(cli.state, runtime.STATE_OK)
        self.assertIn("владелец: choco", cli.detail)
        self.assertIn("choco upgrade opencode -y", cli.detail)
        self.assertFalse(commands)

    def test_manager_metadata_divergence_is_reported_without_repair(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("C:/ProgramData/chocolatey/bin/opencode.exe"), "1.18.18", "choco", True),
        ]
        runtime._known_opencode_managers = lambda npm: {"choco": "1.18.16"}
        runtime.run = lambda cmd, cwd=None, env=None: subprocess.CompletedProcess(cmd, 0, "", "")

        reporter = runtime.Reporter()
        runtime._reconcile_opencode_cli({"dependencies": {"opencode-cli-package": "opencode-ai"}}, reporter, True, "npm")

        warning = [r for r in reporter.results if "версия OpenCode расходится" in r.component]
        self.assertTrue(warning)
        self.assertIn("1.18.18", warning[-1].detail)
        self.assertIn("1.18.16", warning[-1].detail)


if __name__ == "__main__":
    unittest.main()
