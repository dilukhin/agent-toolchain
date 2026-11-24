from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
                plugin = [r for r in reporter.results if r.component == "OpenCode plugin"][-1]
                self.assertEqual(plugin.state, runtime.STATE_CONFIGURED)
                self.assertFalse(reporter.has_conflict, reporter.results)
        finally:
            runtime.run = original_run
            runtime.shutil.which = original_which
            self._restore_active_patch(originals)

    def test_standalone_curl_install_does_not_require_npm(self) -> None:
        original_inventory = runtime.executable_inventory
        original_common = runtime.report_common_tool_inventory
        original_managers = runtime._known_opencode_managers
        original_which = runtime.shutil.which
        try:
            runtime.executable_inventory = lambda command: [
                ExecutableInstance(Path("/home/test/.opencode/bin/opencode"), "1.18.21", "unknown", True)
            ] if command == "opencode" else []
            runtime.report_common_tool_inventory = lambda reporter: None
            runtime._known_opencode_managers = lambda npm: {}
            runtime.shutil.which = lambda name: None

            reporter = runtime.Reporter()
            with mock.patch.object(runtime, "_external_opencode_inventory", return_value=None):
                runtime.reconcile_npm(Path("/unused"), self._base_config(), reporter, check=True, skip=False)

            conflicts = [r for r in reporter.results if r.state == runtime.STATE_CONFLICT]
            self.assertFalse(conflicts, reporter.results)
            cli = [r for r in reporter.results if r.component == "OpenCode CLI"][-1]
            self.assertEqual(cli.state, runtime.STATE_OK)
            self.assertIn("владелец: curl", cli.detail)
            self.assertIn("opencode upgrade --method curl", cli.detail)
            packages = [r for r in reporter.results if r.component == "OpenCode npm packages"][-1]
            self.assertEqual(packages.state, runtime.STATE_OK)
            self.assertIn("не требуется", packages.detail)
        finally:
            runtime.executable_inventory = original_inventory
            runtime.report_common_tool_inventory = original_common
            runtime._known_opencode_managers = original_managers
            runtime.shutil.which = original_which


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
        self.assertNotIn("choco uninstall opencode", duplicate[-1].detail)
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
        with mock.patch.object(runtime, "_external_opencode_inventory", return_value=None):
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

    def test_v2_cli_under_v1_policy_stops_before_plugin_install(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("C:/Users/Dima/AppData/Roaming/npm/opencode.cmd"), "2.3.2", "npm", True),
        ] if command == "opencode" else []
        runtime._known_opencode_managers = lambda npm: {"npm": "2.3.2"}
        commands = []
        runtime.run = lambda cmd, **kwargs: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(runtime.shutil, "which", return_value="npm"), \
             mock.patch.object(runtime, "report_common_tool_inventory", return_value=None):
            reporter = runtime.Reporter()
            runtime.reconcile_npm(Path("/unused"), {"dependencies": {
                "opencode-cli-package": "opencode-ai", "@opencode-ai/plugin": "latest",
            }}, reporter, check=False, skip=False)
        self.assertTrue(reporter.has_conflict)
        self.assertIn("не соответствует политике", next(r.detail for r in reporter.results if r.component == "OpenCode CLI"))
        self.assertFalse(commands)

    def test_v2_policy_with_existing_choco_v1_never_installs_second_cli(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("C:/ProgramData/chocolatey/bin/opencode.exe"), "1.19.5", "choco", True),
        ] if command == "opencode" else []
        runtime._known_opencode_managers = lambda npm: {"choco": "1.19.5"}
        commands = []
        runtime.run = lambda cmd, **kwargs: commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", "")
        with mock.patch.object(runtime.shutil, "which", return_value="npm"), \
             mock.patch.object(runtime, "report_common_tool_inventory", return_value=None):
            reporter = runtime.Reporter()
            runtime.reconcile_npm(Path("/unused"), {"dependencies": {
                "opencode-cli-package": "@opencode/cli", "@opencode/plugin": "match-opencode",
            }}, reporter, check=False, skip=False)
        self.assertTrue(reporter.has_conflict)
        self.assertIn("не соответствует политике", next(r.detail for r in reporter.results if r.component == "OpenCode CLI"))
        self.assertFalse(commands)

    def test_v2_policy_checks_and_reconciles_only_matching_plugin(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("C:/Users/Dima/AppData/Roaming/npm/opencode.cmd"), "2.3.2", "npm", True),
        ] if command == "opencode" else []
        runtime._known_opencode_managers = lambda npm: {"npm": "2.3.2"}
        commands = []

        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(runtime.shutil, "which", return_value="npm"), \
             mock.patch.object(runtime, "report_common_tool_inventory", return_value=None), \
             mock.patch.object(runtime, "_external_opencode_inventory", return_value=None):
            package_json = Path(td) / "node_modules" / "@opencode" / "plugin" / "package.json"

            def fake_run(cmd, **kwargs):
                commands.append(cmd)
                if cmd[1] == "list":
                    return subprocess.CompletedProcess(cmd, 0, json.dumps({"dependencies": {"@opencode/cli": {"version": "2.3.2"}}}), "")
                if cmd[1] == "view":
                    return subprocess.CompletedProcess(cmd, 0, json.dumps("2.3.2"), "")
                if cmd[1] == "install" and "@opencode/plugin@2.3.2" in cmd and "-g" not in cmd:
                    package_json.parent.mkdir(parents=True)
                    package_json.write_text(json.dumps({"version": "2.3.2"}), encoding="utf-8")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                raise AssertionError(cmd)

            runtime.run = fake_run
            config = {"dependencies": {
                "opencode-cli-package": "@opencode/cli", "@opencode/plugin": "match-opencode",
            }}
            reporter = runtime.Reporter()
            runtime.reconcile_npm(Path(td), config, reporter, check=True, skip=False)
            self.assertFalse(any(cmd[1] == "install" for cmd in commands))
            self.assertFalse(package_json.exists())
            self.assertTrue(any(cmd[1:3] == ["view", "@opencode/plugin@2.3.2"] for cmd in commands))
            self.assertIn("@opencode/cli@2.3.2", next(r.detail for r in reporter.results if r.component == "OpenCode CLI"))
            self.assertEqual(next(r.state for r in reporter.results if r.component == "OpenCode plugin"), runtime.STATE_MISSING)
            applied = runtime.Reporter()
            runtime.reconcile_npm(Path(td), config, applied, check=False, skip=False)
            self.assertEqual(package_json.read_text(encoding="utf-8"), '{"version": "2.3.2"}')
            self.assertEqual(next(r.state for r in applied.results if r.component == "OpenCode plugin"), runtime.STATE_CONFIGURED)
            self.assertEqual(len([cmd for cmd in commands if cmd[1] == "install"]), 1)

    def test_existing_v2_cli_update_is_advisory_in_check_and_apply(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("C:/Users/Dima/AppData/Roaming/npm/opencode.cmd"), "2.3.2", "npm", True),
        ] if command == "opencode" else []
        runtime._known_opencode_managers = lambda npm: {"npm": "2.3.2"}
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[1] == "list":
                package = cmd[-1]
                dependencies = {"@opencode/cli": {"version": "2.3.2"}} if package == "@opencode/cli" else {}
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"dependencies": dependencies}), "")
            if cmd[1] == "view":
                version = "2.4.1" if cmd[2] == "@opencode/cli" else "2.3.2"
                return subprocess.CompletedProcess(cmd, 0, json.dumps(version), "")
            raise AssertionError(f"unexpected CLI or plugin mutation: {cmd}")

        runtime.run = fake_run
        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(runtime.shutil, "which", return_value="npm"), \
             mock.patch.object(runtime, "report_common_tool_inventory", return_value=None), \
             mock.patch.object(runtime, "_external_opencode_inventory", return_value=None):
            config_dir = Path(td)
            plugin = config_dir / "node_modules" / "@opencode" / "plugin" / "package.json"
            plugin.parent.mkdir(parents=True)
            plugin.write_text(json.dumps({"version": "2.3.2"}), encoding="utf-8")
            config = {"dependencies": {
                "opencode-cli-package": "@opencode/cli", "@opencode/plugin": "match-opencode",
            }}
            for check in (True, False):
                with self.subTest(check=check):
                    reporter = runtime.Reporter()
                    runtime.reconcile_npm(config_dir, config, reporter, check=check, skip=False)
                    cli = next(r for r in reporter.results if r.component == "OpenCode CLI")
                    matched = next(r for r in reporter.results if r.component == "OpenCode plugin")
                    self.assertEqual(cli.state, runtime.STATE_OUTDATED)
                    self.assertIn("установлено 2.3.2; доступно 2.4.1", cli.detail)
                    self.assertIn("обычный apply не обновляет", cli.detail)
                    self.assertEqual(matched.state, runtime.STATE_OK)
                    self.assertIn("2.3.2", matched.detail)
                    summary = runtime._format_tldr(reporter.results)
                    self.assertIn("npm install -g @opencode/cli@latest", summary)
                    self.assertNotIn("выполнить `toolchainctl apply`", summary)
            self.assertEqual(json.loads(plugin.read_text(encoding="utf-8"))["version"], "2.3.2")
            self.assertFalse(any(cmd[1] == "install" for cmd in commands))

    def test_existing_npm_cli_without_package_metadata_is_not_reinstalled(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("/fake/npm/opencode"), "2.3.2", "npm", True),
        ] if command == "opencode" else []
        runtime._known_opencode_managers = lambda npm: {}
        commands = []
        runtime.run = lambda cmd, **kwargs: (
            commands.append(cmd) or subprocess.CompletedProcess(cmd, 0, '{"dependencies":{}}', "")
        )
        reporter = runtime.Reporter()
        runtime._reconcile_opencode_cli(
            {"dependencies": {"opencode-cli-package": "@opencode/cli"}}, reporter, False, "npm",
        )
        self.assertEqual(next(r.state for r in reporter.results if r.component == "OpenCode CLI"), runtime.STATE_CONFLICT)
        self.assertFalse(any(cmd[1] in {"install", "view"} for cmd in commands))

    def test_apply_installs_matching_plugin_without_upgrading_existing_cli(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("/fake/npm/opencode"), "2.3.2", "npm", True),
        ] if command == "opencode" else []
        runtime._known_opencode_managers = lambda npm: {"npm": "2.3.2"}
        commands = []

        with tempfile.TemporaryDirectory() as td, \
             mock.patch.object(runtime.shutil, "which", return_value="npm"), \
             mock.patch.object(runtime, "report_common_tool_inventory", return_value=None), \
             mock.patch.object(runtime, "_external_opencode_inventory", return_value=None):
            config_dir = Path(td)
            plugin = config_dir / "node_modules" / "@opencode" / "plugin" / "package.json"

            def fake_run(cmd, **kwargs):
                commands.append(cmd)
                if cmd[1] == "list":
                    dependencies = {"@opencode/cli": {"version": "2.3.2"}} if cmd[-1] == "@opencode/cli" else {}
                    return subprocess.CompletedProcess(cmd, 0, json.dumps({"dependencies": dependencies}), "")
                if cmd[1] == "view":
                    version = "2.4.1" if cmd[2] == "@opencode/cli" else "2.3.2"
                    return subprocess.CompletedProcess(cmd, 0, json.dumps(version), "")
                if cmd[1] == "install" and cmd[-1] == "@opencode/plugin@2.3.2" and "-g" not in cmd:
                    plugin.parent.mkdir(parents=True)
                    plugin.write_text('{"version":"2.3.2"}', encoding="utf-8")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                raise AssertionError(f"unexpected CLI mutation: {cmd}")

            runtime.run = fake_run
            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, {"dependencies": {
                "opencode-cli-package": "@opencode/cli", "@opencode/plugin": "match-opencode",
            }}, reporter, check=False, skip=False)
            self.assertEqual(next(r.state for r in reporter.results if r.component == "OpenCode CLI"),
                             runtime.STATE_OUTDATED)
            self.assertEqual(next(r.state for r in reporter.results if r.component == "OpenCode plugin"),
                             runtime.STATE_CONFIGURED)
            self.assertEqual(json.loads(plugin.read_text(encoding="utf-8"))["version"], "2.3.2")
            self.assertEqual(len([cmd for cmd in commands if cmd[1] == "install"]), 1)
            self.assertFalse(any(cmd[1] == "install" and "-g" in cmd for cmd in commands))

    def test_missing_cli_can_still_be_installed_on_fresh_setup(self) -> None:
        installed = False

        def inventory(command):
            if command != "opencode" or not installed:
                return []
            return [ExecutableInstance(Path("/fake/npm/opencode"), "2.4.1", "npm", True)]

        runtime.executable_inventory = inventory
        runtime._known_opencode_managers = lambda npm: {}
        commands = []

        def fake_run(cmd, **kwargs):
            nonlocal installed
            commands.append(cmd)
            if cmd[1] == "view":
                return subprocess.CompletedProcess(cmd, 0, '"2.4.1"', "")
            if cmd[1] == "install" and cmd[-1] == "@opencode/cli@2.4.1":
                installed = True
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(cmd)

        runtime.run = fake_run
        config = {"dependencies": {"opencode-cli-package": "@opencode/cli"}}
        checked = runtime.Reporter()
        runtime._reconcile_opencode_cli(config, checked, True, "npm")
        self.assertEqual(checked.results[-1].state, runtime.STATE_MISSING)
        self.assertFalse(installed)
        applied = runtime.Reporter()
        runtime._reconcile_opencode_cli(config, applied, False, "npm")
        self.assertEqual(applied.results[-1].state, runtime.STATE_CONFIGURED)
        self.assertEqual(len([cmd for cmd in commands if cmd[1] == "install"]), 1)

    def test_v2_policy_refuses_old_npm_package_even_when_v2_is_active(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("C:/Users/Dima/AppData/Roaming/npm/opencode.cmd"), "2.3.2", "npm", True),
        ] if command == "opencode" else []
        runtime._known_opencode_managers = lambda npm: {"npm": "2.3.2"}
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(cmd)
            if cmd[1] == "list":
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"dependencies": {
                    "opencode-ai": {"version": "1.19.6"}, "@opencode/cli": {"version": "2.3.2"},
                }}), "")
            raise AssertionError(cmd)

        runtime.run = fake_run
        with mock.patch.object(runtime.shutil, "which", return_value="npm"), \
             mock.patch.object(runtime, "report_common_tool_inventory", return_value=None):
            reporter = runtime.Reporter()
            runtime.reconcile_npm(Path("/unused"), {"dependencies": {
                "opencode-cli-package": "@opencode/cli", "@opencode/plugin": "latest",
            }}, reporter, check=False, skip=False)
        self.assertTrue(reporter.has_conflict)
        self.assertIn("оба npm-пакета", next(r.detail for r in reporter.results if r.component == "OpenCode CLI"))
        self.assertFalse(any(cmd[1] in {"install", "view"} for cmd in commands))


if __name__ == "__main__":
    unittest.main()
