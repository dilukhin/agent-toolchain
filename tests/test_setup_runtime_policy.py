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


class NpmLatestPolicyTests(unittest.TestCase):
    def _base_config(self) -> dict:
        return {
            "dependencies": {
                "opencode-cli-package": "opencode-ai",
                "@opencode-ai/plugin": "latest",
            }
        }

    def test_plugin_latest_is_true_noop_when_current(self) -> None:
        commands: list[list[str]] = []
        original_run = runtime.run
        original_which = runtime.shutil.which
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

    def test_plugin_latest_updates_to_resolved_registry_version_and_validates(self) -> None:
        commands: list[list[str]] = []
        original_run = runtime.run
        original_which = runtime.shutil.which
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


if __name__ == "__main__":
    unittest.main()
