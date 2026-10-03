from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

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
        self.original_external_inventory = runtime._external_opencode_inventory
        self.original_external_latest = runtime._external_latest

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
        runtime._external_opencode_inventory = lambda: None

    def tearDown(self) -> None:
        runtime.run = self.original_run
        runtime.shutil.which = self.original_which
        runtime.executable_inventory = self.original_inventory
        runtime.report_common_tool_inventory = self.original_common
        runtime._known_opencode_managers = self.original_managers
        runtime._external_opencode_inventory = self.original_external_inventory
        runtime._external_latest = self.original_external_latest

    @staticmethod
    def _config() -> dict:
        return {
            "dependencies": {
                "opencode-cli-package": "opencode-ai",
                "@opencode-ai/plugin": "match-opencode",
            }
        }

    @staticmethod
    def _package_json(config_dir: Path) -> Path:
        return config_dir / "node_modules" / "@opencode-ai" / "plugin" / "package.json"

    @staticmethod
    def _choco_external_inventory() -> SimpleNamespace:
        return SimpleNamespace(
            active=SimpleNamespace(
                path=Path("C:/ProgramData/chocolatey/bin/opencode.exe"),
                version="1.18.18",
                provider="chocolatey",
            ),
            conflict=False,
            update_advice="choco upgrade opencode -y",
        )

    def test_latest_uses_registry_for_each_cli_manager_and_repeat_apply_is_noop(self) -> None:
        for manager, path in (("curl", "/home/test/.opencode/bin/opencode"),
                              ("choco", "C:/ProgramData/chocolatey/bin/opencode.exe"),
                              ("npm", "/usr/bin/opencode")):
            with self.subTest(manager=manager), tempfile.TemporaryDirectory() as td:
                runtime.executable_inventory = lambda command: [
                    ExecutableInstance(Path(path), "1.18.18", manager, True)
                ] if command == "opencode" else []
                runtime._known_opencode_managers = lambda npm: {manager: "1.18.18"}
                config = self._config()
                config["dependencies"]["@opencode-ai/plugin"] = "latest"
                config_dir = Path(td) / "config"
                package_json = self._package_json(config_dir)
                commands = []

                def fake_run(cmd, cwd=None, env=None, timeout=None):
                    commands.append(cmd)
                    if cmd[1] == "list":
                        return subprocess.CompletedProcess(cmd, 0, json.dumps({"dependencies": {"opencode-ai": {"version": "1.18.18"}}}), "")
                    if cmd[1] == "view" and cmd[2] in {"opencode-ai", "@opencode-ai/plugin"}:
                        version = "1.18.18" if cmd[2] == "opencode-ai" else "1.18.34"
                        return subprocess.CompletedProcess(cmd, 0, json.dumps(version), "")
                    if cmd[1] == "install" and "@opencode-ai/plugin@1.18.34" in cmd:
                        package_json.parent.mkdir(parents=True, exist_ok=True)
                        package_json.write_text(json.dumps({"version": "1.18.34"}), encoding="utf-8")
                        return subprocess.CompletedProcess(cmd, 0, "", "")
                    raise AssertionError(f"unexpected command: {cmd}")

                runtime.run = fake_run
                reporter = runtime.Reporter()
                runtime.reconcile_npm(config_dir, config, reporter, check=True, skip=False)
                self.assertFalse(config_dir.exists(), "check wrote configuration")
                self.assertFalse(any("install" in cmd for cmd in commands))
                self.assertIn("цель 1.18.34", reporter.results[-1].detail)
                runtime.reconcile_npm(config_dir, config, runtime.Reporter(), check=False, skip=False)
                installs = len([cmd for cmd in commands if "install" in cmd])
                reporter = runtime.Reporter()
                runtime.reconcile_npm(config_dir, config, reporter, check=False, skip=False)
                self.assertEqual(installs, 1)
                self.assertEqual(len([cmd for cmd in commands if "install" in cmd]), installs)
                self.assertEqual(reporter.results[-1].state, runtime.STATE_OK)
                self.assertNotIn("совпадает", reporter.results[-1].detail)

    def test_match_unpublished_version_explains_policy_without_install_or_retry_loop(self) -> None:
        commands = []
        def fake_run(cmd, cwd=None, env=None, timeout=None):
            commands.append(cmd)
            return subprocess.CompletedProcess(cmd, 1, "", "E404 token@example.invalid")
        runtime.run = fake_run
        with tempfile.TemporaryDirectory() as td:
            reporter = runtime.Reporter()
            runtime.reconcile_npm(Path(td), self._config(), reporter, check=False, skip=False)
        summary = runtime._format_tldr(reporter.results)
        self.assertIn("версия отсутствует", summary)
        self.assertIn("@opencode-ai/plugin@1.18.18 version --json", summary)
        self.assertIn("проверив совместимость", summary)
        self.assertNotIn("token@example.invalid", summary)
        self.assertNotIn("после устранения причины повторите", summary)
        self.assertFalse(any("install" in cmd for cmd in commands))

    def test_match_unknown_cli_version_explains_how_to_diagnose(self) -> None:
        runtime.executable_inventory = lambda command: [
            ExecutableInstance(Path("/home/test/.opencode/bin/opencode"), None, "curl", True)
        ] if command == "opencode" else []
        runtime.run = lambda *args, **kwargs: self.fail("unexpected npm lookup")
        with tempfile.TemporaryDirectory() as td:
            reporter = runtime.Reporter()
            runtime.reconcile_npm(Path(td), self._config(), reporter, check=False, skip=False)
        self.assertIn("opencode --version", runtime._format_tldr(reporter.results))
        self.assertEqual(reporter.results[-1].state, runtime.STATE_CONFLICT)

    def test_explicit_pin_does_not_query_registry_or_claim_cli_match(self) -> None:
        config = self._config()
        config["dependencies"]["@opencode-ai/plugin"] = "1.18.17"
        runtime.run = lambda *args, **kwargs: self.fail("unexpected npm mutation or query")
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            package_json = self._package_json(config_dir)
            package_json.parent.mkdir(parents=True)
            package_json.write_text(json.dumps({"version": "1.18.17"}), encoding="utf-8")
            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, config, reporter, check=True, skip=False)
        self.assertEqual(reporter.results[-1].state, runtime.STATE_OK)
        self.assertNotIn("совпадает", reporter.results[-1].detail)

    def test_summary_keeps_all_remaining_actions(self) -> None:
        reporter = runtime.Reporter()
        for index in range(8):
            reporter.add(f"component {index}", runtime.STATE_CONFLICT, "требуется проверить настройки")
        summary = runtime._format_tldr(reporter.results)
        self.assertEqual(summary.count("  - "), 8)
        self.assertNotIn("TL/DR", summary)

    def test_latest_invalid_metadata_is_actionable_and_does_not_install(self) -> None:
        config = self._config()
        config["dependencies"]["@opencode-ai/plugin"] = "latest"
        runtime.run = lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 0, "{}", "")
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td) / "config"
            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, config, reporter, check=False, skip=False)
            self.assertFalse(config_dir.exists())
        self.assertIn("npm view @opencode-ai/plugin version --json", runtime._format_tldr(reporter.results))
        self.assertEqual(reporter.results[-1].state, runtime.STATE_CONFLICT)

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

    def test_external_choco_update_is_outdated_and_actionable_without_mutation(self) -> None:
        commands: list[list[str]] = []

        def fake_run(cmd, cwd=None, env=None, timeout=None):
            commands.append(cmd)
            if cmd[1:3] == ["view", "@opencode-ai/plugin@1.18.18"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps("1.18.18"), "")
            raise AssertionError(f"unexpected command: {cmd}")

        runtime.run = fake_run
        runtime._external_opencode_inventory = self._choco_external_inventory
        runtime._external_latest = lambda inventory, timeout: ("1.18.25", None)

        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            package_json = self._package_json(config_dir)
            package_json.parent.mkdir(parents=True)
            package_json.write_text(json.dumps({"version": "1.18.18"}), encoding="utf-8")

            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, self._config(), reporter, check=False, skip=False)

        cli = [item for item in reporter.results if item.component == "OpenCode CLI"][-1]
        self.assertEqual(cli.state, runtime.STATE_OUTDATED)
        self.assertIn("установлено 1.18.18; доступно 1.18.25", cli.detail)
        self.assertIn("`choco upgrade opencode -y`", cli.detail)
        self.assertFalse(any("choco" in " ".join(cmd).lower() for cmd in commands), commands)

        summary = runtime._format_tldr(reporter.results)
        self.assertIn("обновить OpenCode 1.18.18 → 1.18.25: `choco upgrade opencode -y`", summary)
        self.assertNotIn("выполнить `toolchainctl apply`", summary)

    def test_external_choco_lookup_failure_is_info_not_false_up_to_date(self) -> None:
        commands: list[list[str]] = []

        def fake_run(cmd, cwd=None, env=None, timeout=None):
            commands.append(cmd)
            if cmd[1:3] == ["view", "@opencode-ai/plugin@1.18.18"]:
                return subprocess.CompletedProcess(cmd, 0, json.dumps("1.18.18"), "")
            raise AssertionError(f"unexpected command: {cmd}")

        runtime.run = fake_run
        runtime._external_opencode_inventory = self._choco_external_inventory
        runtime._external_latest = lambda inventory, timeout: (None, "choco lookup timed out")

        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            package_json = self._package_json(config_dir)
            package_json.parent.mkdir(parents=True)
            package_json.write_text(json.dumps({"version": "1.18.18"}), encoding="utf-8")

            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, self._config(), reporter, check=True, skip=False)

        cli = [item for item in reporter.results if item.component == "OpenCode CLI"][-1]
        self.assertEqual(cli.state, runtime.STATE_INFO)
        self.assertIn("актуальность версии", cli.detail)
        self.assertNotIn("choco lookup timed out", cli.detail)

    def test_external_choco_never_recommends_provider_downgrade(self) -> None:
        runtime._external_opencode_inventory = self._choco_external_inventory
        runtime._external_latest = lambda inventory, timeout: ("1.18.17", None)
        reporter = runtime.Reporter()
        reporter.add("OpenCode CLI", runtime.STATE_OK, "active Chocolatey OpenCode 1.18.18")

        runtime._annotate_external_opencode_freshness(reporter, 0)

        cli = reporter.results[-1]
        self.assertEqual(cli.state, runtime.STATE_INFO)
        self.assertIn("не подтверждена как более новая", cli.detail)
        self.assertNotIn("рекомендуемая команда обновления", cli.detail)
        self.assertNotIn("choco upgrade", runtime._format_tldr(reporter.results))

    def test_external_freshness_does_not_expand_to_scoop(self) -> None:
        inventory = self._choco_external_inventory()
        inventory.active.provider = "scoop"
        inventory.update_advice = "scoop update opencode"
        runtime._external_opencode_inventory = lambda: inventory
        runtime._external_latest = lambda inventory, timeout: self.fail("unexpected Scoop freshness lookup")
        reporter = runtime.Reporter()
        reporter.add("OpenCode CLI", runtime.STATE_OK, "active Scoop OpenCode 1.18.18")

        runtime._annotate_external_opencode_freshness(reporter, 0)

        self.assertEqual(reporter.results[-1].state, runtime.STATE_OK)

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
        self.assertIn("Итог: требуются действия:", summary)
        self.assertIn("выполнить `toolchainctl apply`", summary)
        self.assertIn("запишите реальный ключ RouterAI", summary)
        self.assertNotIn("choco upgrade opencode", summary)

    def test_tldr_opencode_config_conflict_is_actionable(self) -> None:
        reporter = runtime.Reporter()
        reporter.add(
            "OpenCode config",
            runtime.STATE_CONFLICT,
            "managed config was modified locally; preserved: /home/user/.config/opencode/opencode.jsonc; "
            "recorded_sha256=" + "a" * 64 + "; current_sha256=" + "b" * 64,
        )
        for result in reporter.results:
            runtime._localize_actionable_detail(result)
        summary = runtime._format_tldr(reporter.results)
        self.assertIn("toolchainctl diff opencode-config", summary)
        self.assertIn("toolchainctl apply --force", summary)
        self.assertNotIn("исправить «OpenCode config»", summary)

    def test_tldr_automatic_apply_keeps_component_context(self) -> None:
        reporter = runtime.Reporter()
        reporter.add(
            "skill example",
            runtime.STATE_OUTDATED,
            "managed source changed; обычный apply обновит управляемый файл автоматически",
        )
        summary = runtime._format_tldr(reporter.results)
        self.assertIn("«skill example»: выполнить `toolchainctl apply`", summary)

    def test_toolchainctl_check_clarifies_routerai_placeholder_and_emits_tldr(self) -> None:
        script = r'''
import sys
sys.argv[:] = ["toolchainctl.py", "check"]
import setup_runtime as runtime
reporter = runtime.Reporter()
reporter.add(
    "RouterAI credential",
    runtime.STATE_MISSING,
    "MANUAL ACTION REQUIRED: служебная заглушка предыдущей версии не является API key; запишите реальный ключ RouterAI: C:/credential.txt",
)
reporter.render(color=False)
'''
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            env={**os.environ, "PYTHONUTF8": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        output = completed.stdout.decode("utf-8", errors="replace")
        self.assertEqual(completed.returncode, 0, completed.stderr.decode("utf-8", errors="replace"))
        self.assertIn("RouterAI не настроен", output)
        self.assertIn("`your-routerai-api-key-here`", output)
        self.assertNotIn("служебная заглушка предыдущей версии", output)
        expected = (
            "  - RouterAI не настроен: замените `your-routerai-api-key-here` в C:/credential.txt "
            "на реальный API-ключ RouterAI одной строкой, без `Bearer` и кавычек"
        )
        self.assertTrue(output.rstrip().endswith(expected), output)
        self.assertEqual(output.count("Итог: требуются действия:"), 1, output)

    def test_tldr_turns_npm_metadata_failure_into_retry_advice(self) -> None:
        reporter = runtime.Reporter()
        reporter.add(
            "OpenCode plugin",
            runtime.STATE_FAILED,
            "не удалось определить целевую версию; npm metadata lookup: TLS/SSL failure",
        )
        summary = runtime._format_tldr(reporter.results)
        self.assertIn("после устранения причины повторите `toolchainctl apply`", summary)

    def test_tldr_reports_no_action_when_state_is_current(self) -> None:
        reporter = runtime.Reporter()
        reporter.add("OpenCode plugin", runtime.STATE_OK, "1.18.18 (совпадает с OpenCode 1.18.18)")
        self.assertEqual(runtime._format_tldr(reporter.results), "Итог: дополнительных действий не требуется.")


if __name__ == "__main__":
    unittest.main()
