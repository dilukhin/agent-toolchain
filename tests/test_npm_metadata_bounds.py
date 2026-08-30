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


class NpmMetadataBoundsTests(unittest.TestCase):
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

    def _config(self) -> dict:
        return {
            "dependencies": {
                "opencode-cli-package": "opencode-ai",
                "@opencode-ai/plugin": "latest",
            }
        }

    def _installed_plugin(self, config_dir: Path, version: str = "1.18.25") -> None:
        package_json = config_dir / "node_modules" / "@opencode-ai" / "plugin" / "package.json"
        package_json.parent.mkdir(parents=True)
        package_json.write_text(json.dumps({"version": version}), encoding="utf-8")

    def test_failed_registry_lookup_is_bounded_sanitized_and_does_not_install(self) -> None:
        commands: list[list[str]] = []
        metadata_env: dict[str, str] = {}
        sensitive = "ECONNRESET https://token@example.invalid/private-registry"

        def fake_run(cmd, cwd=None, env=None):
            commands.append(cmd)
            if cmd[1:3] == ["view", "@opencode-ai/plugin"]:
                metadata_env.update(env or {})
                return subprocess.CompletedProcess(cmd, 1, "", sensitive)
            raise AssertionError(f"unexpected command: {cmd}")

        runtime.run = fake_run
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            self._installed_plugin(config_dir)
            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, self._config(), reporter, check=False, skip=False)

        plugin = [item for item in reporter.results if item.component == "OpenCode plugin"][-1]
        self.assertEqual(plugin.state, runtime.STATE_FAILED)
        self.assertIn("network connection failed", plugin.detail)
        self.assertNotIn("token@example.invalid", plugin.detail)
        self.assertFalse(any("install" in cmd for cmd in commands), commands)
        self.assertEqual(metadata_env["npm_config_fetch_timeout"], "10000")
        self.assertEqual(metadata_env["npm_config_fetch_retries"], "1")
        self.assertEqual(metadata_env["npm_config_fetch_retry_mintimeout"], "1000")
        self.assertEqual(metadata_env["npm_config_fetch_retry_maxtimeout"], "5000")

    def test_outer_timeout_is_reported_without_install(self) -> None:
        commands: list[list[str]] = []

        def fake_run(cmd, cwd=None, env=None, timeout=None):
            commands.append(cmd)
            if cmd[1:3] == ["view", "@opencode-ai/plugin"]:
                self.assertEqual(timeout, runtime._NPM_METADATA_TIMEOUT_SECONDS)
                raise subprocess.TimeoutExpired(cmd, timeout)
            raise AssertionError(f"unexpected command: {cmd}")

        runtime.run = fake_run
        with tempfile.TemporaryDirectory() as td:
            config_dir = Path(td)
            self._installed_plugin(config_dir)
            reporter = runtime.Reporter()
            runtime.reconcile_npm(config_dir, self._config(), reporter, check=False, skip=False)

        plugin = [item for item in reporter.results if item.component == "OpenCode plugin"][-1]
        self.assertEqual(plugin.state, runtime.STATE_FAILED)
        self.assertIn("timeout after 30s", plugin.detail)
        self.assertFalse(any("install" in cmd for cmd in commands), commands)


if __name__ == "__main__":
    unittest.main()
