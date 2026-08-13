from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ExistingQwenConfigTests(unittest.TestCase):
    def _run_core(self, home: Path, *, check: bool = False) -> subprocess.CompletedProcess[str]:
        config_dir = home / ".config" / "opencode"
        projects = home / "projects"
        (projects / "ssh_relay").mkdir(parents=True, exist_ok=True)
        (projects / "agent-safe").mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            str(ROOT / "setup_core.py"),
            "--repo-root", str(ROOT),
            "--config-dir", str(config_dir),
            "--stash-dir", str(home / "legacy-stash"),
            "--credential-dir", str(config_dir / "credentials"),
            "--skills-dir", str(home / ".agents" / "skills"),
            "--state-dir", str(home / "state"),
            "--projects-dir", str(projects),
            "--skip-package-install",
            "--skip-dependency-install",
        ]
        if check:
            cmd.append("--check")
        return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_existing_qwen_provider_is_preserved_and_routerai_added_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "opencode.jsonc"
            manifest_path = home / "state" / "manifest.json"

            existing = {
                "$schema": "https://opencode.ai/config.json",
                "permission": {"bash": "ask"},
                "plugin": ["existing-plugin"],
                "provider": {
                    "qwen": {
                        "npm": "@ai-sdk/openai-compatible",
                        "options": {
                            "baseURL": "https://portal.qwen.ai/v1",
                            "compatibility": "compatible",
                        },
                        "models": {
                            "qwen3-coder-plus": {"name": "Qwen3 Coder Plus"},
                            "qwen3-vl-plus": {"name": "Qwen3 VL Plus"},
                        },
                    }
                },
            }
            original_qwen = json.loads(json.dumps(existing["provider"]["qwen"]))
            original_permission = json.loads(json.dumps(existing["permission"]))
            original_plugin = list(existing["plugin"])
            config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")

            check = self._run_core(home, check=True)
            self.assertEqual(check.returncode, 2, check.stdout + check.stderr)
            self.assertNotIn("existing config is not safely adoptable for RouterAI", check.stdout)
            self.assertIn("соседним provider", check.stdout)
            self.assertFalse((config_dir / "credentials" / "routerai-api-key.txt").exists())

            first = self._run_core(home)
            self.assertEqual(first.returncode, 2, first.stdout + first.stderr)

            merged = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["provider"]["qwen"], original_qwen)
            self.assertEqual(merged["permission"], original_permission)
            self.assertEqual(merged["plugin"], original_plugin)
            self.assertIn("routerai", merged["provider"])
            self.assertNotIn("model", merged)
            self.assertNotIn("small_model", merged)
            self.assertEqual(merged["autoupdate"], "notify")

            canonical = config_dir / "credentials" / "routerai-api-key.txt"
            self.assertTrue(canonical.is_file())
            self.assertEqual(
                merged["provider"]["routerai"]["options"]["apiKey"],
                "{file:" + str(canonical.resolve()) + "}",
            )

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                manifest["managed_files"]["OpenCode config"]["mode"],
                "merged-json-sibling-provider",
            )
            stable_config = config_path.read_bytes()
            stable_manifest = manifest_path.read_bytes()

            # Второй apply раньше не менял config, но деградировал manifest mode.
            second = self._run_core(home)
            self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
            self.assertEqual(config_path.read_bytes(), stable_config)
            self.assertEqual(manifest_path.read_bytes(), stable_manifest)

            # Третий apply проявлял latent bug и добавлял model/small_model.
            third = self._run_core(home)
            self.assertEqual(third.returncode, 2, third.stdout + third.stderr)
            self.assertEqual(config_path.read_bytes(), stable_config)
            self.assertEqual(manifest_path.read_bytes(), stable_manifest)
            final = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertNotIn("model", final)
            self.assertNotIn("small_model", final)
            self.assertEqual(final["autoupdate"], "notify")

    def test_qwen_jsonc_with_comments_remains_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "opencode.jsonc"
            original = b'''{
  // keep this comment
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "qwen": {
      "options": {"baseURL": "https://portal.qwen.ai/v1"},
    },
  },
}\n'''
            config_path.write_bytes(original)

            cp = self._run_core(home)
            self.assertEqual(cp.returncode, 2)
            self.assertIn("JSONC", cp.stdout)
            self.assertEqual(config_path.read_bytes(), original)
            self.assertFalse((config_dir / "credentials" / "routerai-api-key.txt").exists())


if __name__ == "__main__":
    unittest.main()
