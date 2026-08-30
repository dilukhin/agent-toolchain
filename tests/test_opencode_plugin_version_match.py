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


class StandaloneOpenCodePluginVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_run = runtime.run
        self.original_which = runtime.shutil.which
        self.original_inventory = runtime.executable_inventory
        self.original_common = runtime.report_common_tool_inventory
        self.original_managers = runtime._known_opencode_managers

        runtime.shutil.which = lambda name: "C:/fake/npm.cmd" if name == "npm" else None
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(
                Path("C:/ProgramData/chocolatey/bin/opencode.exe"),
                "1.18.18",
                "choco",
                True,
            )
        ] if command == "opencode" else []
        runtime.report_common_tool_inventory = lambda reporter: None
        runtime._known_opencode_managers = lambda npm: {"choco": "1.18.18"}

    def tearDown(self) -> None:
        runtime.run = self.original_run
        runtime.shutil.which = self.original_which
        runtime.executable_inventory = self.original_inventory
        runtime.report_common_tool_inventory = self.original_common
        runtime._known_opencode_managers = self.original_managers

    @staticmethod
    def _config() -> dict:
        return {
            "dependencies": {
                "opencode-cli-package": "opencode-ai",
                "@opencode-ai/plugin": "latest",
            }
        }

    @staticmethod
    def _package_json(config_dir: Path) -> Path:
        return config_dir / "node_modules" / "@opencode-ai" / "plugin" / "package.json"

    def test_check_targets_plugin_version_matching_choco_cli(self) -> None:
        commands: list[list[str]] = []

        def fake_run(cmd, cwd=None, env=None, timeout=None):
            commands.append(cmd)
            if cmd[1:3] == ["view", "@opencode-ai/plugin@1.18.18"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps("1.18.18"), "")
            raise AssertionError(f"unexpected command: {cmd}")

        runtime.run = fake_run
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            package_json = self._package_json(config_dir)
            package_json.parent.mkdir(parents=True)
            package_json.write_text(json.dumps({"version": "1.18.25"}), encoding="utf-8")

            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, self._config(), reporter, check=True, skip=False)

        plugin = [item for item in reporter.results if item.component == "OpenCode plugin"][-1]
        self.assertEqual(plugin.state, runtime.STATE_OUTDATED)
        self.assertIn("цель 1.18.18", plugin.detail)
        self.assertIn("активному OpenCode 1.18.18", plugin.detail)
        self.assertFalse(any("install" in cmd for cmd in commands), commands)
        self.assertFalse(any(cmd[1:3] == ["view", "@opencode-ai/plugin"] for cmd in commands), commands)

    def test_apply_replaces_newer_plugin_with_matching_choco_version(self) -> None:
        commands: list[list[str]] = []

        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            package_json = self._package_json(config_dir)
            package_json.parent.mkdir(parents=True)
            package_json.write_text(json.dumps({"version": "1.18.25"}), encoding="utf-8")

            def fake_run(cmd, cwd=None, env=None, timeout=None):
                commands.append(cmd)
                if cmd[1:3] == ["view", "@opencode-ai/plugin@1.18.18"]:
                    return subprocess.CompletedProcess(cmd, 0, json.dumps("1.18.18"), "")
                if "install" in cmd and "@opencode-ai/plugin@1.18.18" in cmd:
                    package_json.write_text(json.dumps({"version": "1.18.18"}), encoding="utf-8")
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                raise AssertionError(f"unexpected command: {cmd}")

            runtime.run = fake_run
            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, self._config(), reporter, check=False, skip=False)

            self.assertEqual(runtime.installed_version(package_json), "1.18.18")

        plugin = [item for item in reporter.results if item.component == "OpenCode plugin"][-1]
        self.assertEqual(plugin.state, runtime.STATE_CONFIGURED)
        self.assertIn("понижен с 1.18.25 до 1.18.18", plugin.detail)
        self.assertNotIn("обновлён с 1.18.25 до 1.18.18", plugin.detail)
        self.assertIn("активному OpenCode 1.18.18", plugin.detail)
        self.assertTrue(any("@opencode-ai/plugin@1.18.18" in cmd for cmd in commands), commands)
        self.assertFalse(any("-g" in cmd and "opencode-ai" in " ".join(cmd) for cmd in commands), commands)

    def test_duplicate_cli_inventory_blocks_plugin_mutation(self) -> None:
        commands: list[list[str]] = []
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(
                Path("C:/ProgramData/chocolatey/bin/opencode.exe"),
                "1.18.18",
                "choco",
                True,
            ),
            ExecutableInstance(
                Path("C:/Users/Dima/AppData/Roaming/npm/opencode.cmd"),
                "1.18.25",
                "npm",
                False,
            ),
        ] if command == "opencode" else []
        runtime._known_opencode_managers = lambda npm: {"choco": "1.18.18", "npm": "1.18.25"}
        runtime.run = lambda cmd, cwd=None, env=None, timeout=None: (
            commands.append(cmd)
            or subprocess.CompletedProcess(cmd, 0, "", "")
        )

        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            package_json = self._package_json(config_dir)
            package_json.parent.mkdir(parents=True)
            package_json.write_text(json.dumps({"version": "1.18.25"}), encoding="utf-8")

            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, self._config(), reporter, check=False, skip=False)

        duplicate = [item for item in reporter.results if item.component == "OpenCode: дублирующиеся установки"][-1]
        plugin = [item for item in reporter.results if item.component == "OpenCode plugin"][-1]
        self.assertEqual(duplicate.state, runtime.STATE_CONFLICT)
        self.assertEqual(plugin.state, runtime.STATE_CONFLICT)
        self.assertFalse(any("install" in cmd for cmd in commands), commands)

    def test_tldr_keeps_only_actionable_recommendations(self) -> None:
        reporter = runtime.Reporter()
        reporter.add(
            "External CLI Opencode",
            runtime.STATE_INFO,
            "активный: C:/ProgramData/chocolatey/bin/opencode.exe; update: choco upgrade opencode -y",
        )
        reporter.add(
            "OpenCode plugin",
            runtime.STATE_OUTDATED,
            "цель 1.18.18, установлено 1.18.25; обычный apply установит/обновит plugin автоматически",
        )
        reporter.add(
            "RouterAI credential",
            runtime.STATE_MISSING,
            "MANUAL ACTION REQUIRED: запишите реальный ключ RouterAI: C:/Users/Dima/.config/opencode/credentials/routerai-api-key.txt",
        )

        summary = runtime._format_tldr(reporter.results)
        self.assertIn("TL/DR: рекомендуется:", summary)
        self.assertIn("выполнить `toolchainctl apply`", summary)
        self.assertIn("запишите реальный ключ RouterAI", summary)
        self.assertNotIn("choco upgrade opencode", summary)

    def test_tldr_turns_npm_metadata_failure_into_retry_advice(self) -> None:
        reporter = runtime.Reporter()
        reporter.add(
            "OpenCode plugin",
            runtime.STATE_FAILED,
            "не удалось определить целевую версию; npm metadata lookup: TLS/SSL failure",
        )
        summary = runtime._format_tldr(reporter.results)
        self.assertIn("повторить `toolchainctl apply` после восстановления доступа к npm registry", summary)

    def test_tldr_reports_no_action_when_state_is_current(self) -> None:
        reporter = runtime.Reporter()
        reporter.add("OpenCode plugin", runtime.STATE_OK, "1.18.18 (совпадает с OpenCode 1.18.18)")
        self.assertEqual(runtime._format_tldr(reporter.results), "TL/DR: дополнительных действий не требуется.")


if __name__ == "__main__":
    unittest.main()
