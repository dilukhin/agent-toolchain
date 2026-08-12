from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run_core(home: Path, config_dir: Path, stash_dir: Path, credential_dir: Path,
              *, check: bool = False) -> subprocess.CompletedProcess[str]:
    projects = home / "projects"
    (projects / "ssh_relay").mkdir(parents=True, exist_ok=True)
    (projects / "agent-safe").mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-B",
        str(ROOT / "setup_core.py"),
        "--repo-root", str(ROOT),
        "--config-dir", str(config_dir),
        "--stash-dir", str(stash_dir),
        "--credential-dir", str(credential_dir),
        "--skills-dir", str(home / ".agents" / "skills"),
        "--state-dir", str(home / "state"),
        "--projects-dir", str(projects),
        "--skip-package-install",
        "--skip-dependency-install",
    ]
    if check:
        cmd.append("--check")
    return subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _qwen_only_config() -> dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "permission": {"bash": "ask"},
        "plugin": ["example-plugin"],
        "provider": {
            "qwen": {
                "npm": "@ai-sdk/openai-compatible",
                "options": {
                    "baseURL": "https://portal.qwen.ai/v1",
                    "compatibility": "strict",
                },
                "models": {
                    "qwen3-coder-plus": {"name": "Qwen Coder"},
                    "qwen3-vl-plus": {"name": "Qwen VL"},
                },
            }
        },
    }


class ForeignProviderMigrationTests(unittest.TestCase):
    def test_check_reports_additive_migration_without_routerai_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            stash_dir = home / "projects" / "stash" / "opencode.ai"
            credential_dir = config_dir / "credentials"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "opencode.jsonc"
            before = (json.dumps(_qwen_only_config(), indent=2) + "\n").encode("utf-8")
            config_path.write_bytes(before)

            cp = _run_core(home, config_dir, stash_dir, credential_dir, check=True)

            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)  # dependency dirs intentionally conflict
            self.assertIn("existing foreign providers/settings preserved", cp.stdout)
            self.assertNotIn("existing config is not safely compatible with RouterAI migration", cp.stdout)
            self.assertEqual(config_path.read_bytes(), before)
            self.assertFalse((credential_dir / "routerai-api-key.txt").exists())

    def test_apply_adds_only_routerai_and_preserves_foreign_settings(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            stash_dir = home / "projects" / "stash" / "opencode.ai"
            credential_dir = config_dir / "credentials"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "opencode.jsonc"
            original = _qwen_only_config()
            config_path.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")

            cp = _run_core(home, config_dir, stash_dir, credential_dir)

            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)  # dependency dirs intentionally conflict
            canonical = credential_dir / "routerai-api-key.txt"
            self.assertTrue(canonical.is_file())
            merged = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(merged["provider"]["qwen"], original["provider"]["qwen"])
            self.assertEqual(merged["permission"], original["permission"])
            self.assertEqual(merged["plugin"], original["plugin"])
            self.assertIn("routerai", merged["provider"])
            self.assertEqual(
                merged["provider"]["routerai"]["options"]["apiKey"],
                "{file:" + str(canonical.resolve()) + "}",
            )
            self.assertNotIn("model", merged)
            self.assertNotIn("small_model", merged)
            backups = list((home / "state" / "backups").rglob("opencode.jsonc"))
            self.assertEqual(len(backups), 1)

    def test_existing_legacy_key_is_reused_without_moving_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            stash_dir = home / "projects" / "stash" / "opencode.ai"
            credential_dir = config_dir / "credentials"
            config_dir.mkdir(parents=True)
            stash_dir.mkdir(parents=True)
            (config_dir / "opencode.jsonc").write_text(
                json.dumps(_qwen_only_config(), indent=2) + "\n", encoding="utf-8"
            )
            legacy = stash_dir / "api-key.txt"
            legacy.write_bytes(b"existing-routerai-key-bytes\r\n")
            before_hash = hashlib.sha256(legacy.read_bytes()).hexdigest()

            cp = _run_core(home, config_dir, stash_dir, credential_dir)

            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
            self.assertEqual(before_hash, hashlib.sha256(legacy.read_bytes()).hexdigest())
            self.assertFalse((credential_dir / "routerai-api-key.txt").exists())
            merged = json.loads((config_dir / "opencode.jsonc").read_text(encoding="utf-8"))
            self.assertEqual(
                merged["provider"]["routerai"]["options"]["apiKey"],
                "{file:" + str(legacy.resolve()) + "}",
            )
            manifest = json.loads((home / "state" / "manifest.json").read_text(encoding="utf-8"))
            credential = manifest["credentials"]["routerai"]
            self.assertEqual(credential["mode"], "legacy-existing-file")
            self.assertEqual(credential["path"], str(legacy.resolve()))
            self.assertNotIn("sha256", credential)

    def test_format_sensitive_foreign_config_conflicts_without_creating_credential(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            stash_dir = home / "projects" / "stash" / "opencode.ai"
            credential_dir = config_dir / "credentials"
            config_dir.mkdir(parents=True)
            config_path = config_dir / "opencode.jsonc"
            before = b'''{
              // keep this user comment
              "provider": {
                "qwen": {
                  "options": {"baseURL": "https://portal.qwen.ai/v1"},
                },
              },
            }\n'''
            config_path.write_bytes(before)

            cp = _run_core(home, config_dir, stash_dir, credential_dir)

            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
            self.assertIn("contains comments/trailing commas", cp.stdout)
            self.assertEqual(config_path.read_bytes(), before)
            self.assertFalse((credential_dir / "routerai-api-key.txt").exists())
            self.assertFalse((stash_dir / "api-key.txt").exists())


if __name__ == "__main__":
    unittest.main()
