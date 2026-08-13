from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from setup_lib import (  # noqa: E402
    AGENTS_BLOCK_END,
    AGENTS_BLOCK_START,
    STATE_CONFLICT,
    STATE_OK,
    inspect_repo,
    merge_routerai_config,
    parse_jsonc_object,
    routerai_file_credential,
)


def run_git(*args: str, cwd: Path | None = None) -> None:
    cp = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if cp.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {cp.stderr}")


class JsoncMigrationTests(unittest.TestCase):
    def test_jsonc_parsing_and_merge_preserve_user_settings(self) -> None:
        existing_bytes = b'''{
          // user comment
          "provider": {"routerai": {
            "options": {"baseURL": "https://routerai.ru/api/v1", "apiKey": "{file:/tmp/key.txt}"},
            "models": {"custom/model": {"name": "Custom"}},
          }},
          "model": "custom/model",
          "user_setting": true,
        }'''
        existing, error, has_jsonc = parse_jsonc_object(existing_bytes)
        self.assertIsNone(error)
        self.assertTrue(has_jsonc)
        self.assertEqual(routerai_file_credential(existing or {}), "/tmp/key.txt")

        desired = {
            "$schema": "https://opencode.ai/config.json",
            "provider": {"routerai": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "RouterAI",
                "options": {"baseURL": "https://routerai.ru/api/v1", "apiKey": "{file:/tmp/key.txt}"},
                "models": {"managed/model": {"name": "Managed"}},
            }},
            "model": "managed/model",
            "small_model": "small/model",
        }
        merged, merge_error = merge_routerai_config(existing or {}, desired)
        self.assertIsNone(merge_error)
        self.assertIsNotNone(merged)
        assert merged is not None
        self.assertTrue(merged["user_setting"])
        self.assertEqual(merged["model"], "custom/model")
        self.assertIn("custom/model", merged["provider"]["routerai"]["models"])
        self.assertIn("managed/model", merged["provider"]["routerai"]["models"])
        self.assertEqual(merged["provider"]["routerai"]["options"]["apiKey"], "{file:/tmp/key.txt}")


class CoreMigrationTests(unittest.TestCase):
    def _run_core(self, home: Path, config_dir: Path, stash_dir: Path, credential_dir: Path) -> subprocess.CompletedProcess[str]:
        projects = home / "projects"
        # Existing non-git directories force safe dependency conflicts and prevent network cloning.
        (projects / "ssh_relay").mkdir(parents=True, exist_ok=True)
        (projects / "agent-safe").mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, str(ROOT / "setup_core.py"),
            "--repo-root", str(ROOT),
            "--config-dir", str(config_dir),
            "--stash-dir", str(stash_dir),
            "--credential-dir", str(credential_dir),
            "--skills-dir", str(home / ".agents" / "skills"),
            "--state-dir", str(home / "state"),
            "--projects-dir", str(projects),
            "--skip-package-install", "--skip-dependency-install",
        ]
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(cmd, text=True, encoding="utf-8", env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def test_existing_external_credential_is_preserved_without_parallel_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            stash_dir = home / "projects" / "stash" / "opencode.ai"
            credential_dir = config_dir / "credentials"
            config_dir.mkdir(parents=True)
            stash_dir.mkdir(parents=True)

            external_key = stash_dir / "api-key-account.txt"
            external_key.write_bytes(b"real-existing-key-bytes\r\n")
            before = hashlib.sha256(external_key.read_bytes()).hexdigest()
            existing = {
                "provider": {"routerai": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "RouterAI",
                    "options": {
                        "baseURL": "https://routerai.ru/api/v1",
                        "apiKey": "{file:" + str(external_key) + "}",
                    },
                    "models": {"custom/model": {"name": "Custom"}},
                }},
                "model": "custom/model",
                "user_setting": True,
            }
            (config_dir / "opencode.jsonc").write_text(json.dumps(existing, indent=2), encoding="utf-8")
            (config_dir / "AGENTS.md").write_text("# User instructions\n\nKeep this line.\n", encoding="utf-8")

            cp = self._run_core(home, config_dir, stash_dir, credential_dir)
            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)  # dependency dirs intentionally conflict
            self.assertEqual(before, hashlib.sha256(external_key.read_bytes()).hexdigest())
            self.assertFalse((credential_dir / "routerai-api-key.txt").exists())

            merged = json.loads((config_dir / "opencode.jsonc").read_text(encoding="utf-8"))
            self.assertTrue(merged["user_setting"])
            self.assertEqual(merged["model"], "custom/model")
            self.assertEqual(merged["provider"]["routerai"]["options"]["apiKey"], "{file:" + str(external_key) + "}")
            self.assertIn("custom/model", merged["provider"]["routerai"]["models"])
            self.assertGreaterEqual(len(merged["provider"]["routerai"]["models"]), 14)

            agents = (config_dir / "AGENTS.md").read_text(encoding="utf-8")
            self.assertIn("Keep this line.", agents)
            self.assertIn(AGENTS_BLOCK_START, agents)
            self.assertIn(AGENTS_BLOCK_END, agents)

            manifest = json.loads((home / "state" / "manifest.json").read_text(encoding="utf-8"))
            cred = manifest["credentials"]["routerai"]
            self.assertEqual(cred["mode"], "external-file")
            self.assertEqual(cred["path"], str(external_key.resolve()))
            self.assertNotIn("sha256", cred)

    def test_inline_credential_is_preserved_and_no_placeholder_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            stash_dir = home / "projects" / "stash" / "opencode.ai"
            credential_dir = config_dir / "credentials"
            config_dir.mkdir(parents=True)
            existing = {
                "provider": {"routerai": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "RouterAI",
                    "options": {
                        "baseURL": "https://routerai.ru/api/v1",
                        "apiKey": "INLINE_SECRET_VALUE_MUST_NOT_BE_PRINTED_OR_MIGRATED",
                    },
                    "models": {},
                }},
            }
            config_path = config_dir / "opencode.jsonc"
            before_bytes = (json.dumps(existing, indent=2) + "\n").encode("utf-8")
            config_path.write_bytes(before_bytes)

            cp = self._run_core(home, config_dir, stash_dir, credential_dir)
            self.assertEqual(cp.returncode, 2)
            self.assertEqual(config_path.read_bytes(), before_bytes)
            self.assertFalse((credential_dir / "routerai-api-key.txt").exists())
            self.assertFalse((stash_dir / "api-key.txt").exists())
            self.assertNotIn("INLINE_SECRET_VALUE", cp.stdout)
            self.assertNotIn("INLINE_SECRET_VALUE", cp.stderr)
            self.assertIn("not a {file:...} reference", cp.stdout)

    def test_fresh_install_uses_profile_credential_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            stash_dir = home / "projects" / "stash" / "opencode.ai"
            credential_dir = config_dir / "credentials"
            cp = self._run_core(home, config_dir, stash_dir, credential_dir)
            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
            canonical = credential_dir / "routerai-api-key.txt"
            self.assertTrue(canonical.is_file())
            self.assertFalse((stash_dir / "api-key.txt").exists())
            generated = json.loads((config_dir / "opencode.jsonc").read_text(encoding="utf-8"))
            self.assertEqual(generated["provider"]["routerai"]["options"]["apiKey"], "{file:" + str(canonical.resolve()) + "}")


class RepositoryInspectionTests(unittest.TestCase):
    def test_benign_untracked_is_accepted_but_arbitrary_untracked_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            seed = root / "seed"
            bare = root / "remote.git"
            work = root / "work"
            run_git("init", "-q", "-b", "main", str(seed))
            run_git("-C", str(seed), "config", "user.email", "test@example.invalid")
            run_git("-C", str(seed), "config", "user.name", "test")
            (seed / "tracked.txt").write_text("tracked\n", encoding="utf-8")
            run_git("-C", str(seed), "add", ".")
            run_git("-C", str(seed), "commit", "-qm", "init")
            run_git("clone", "-q", "--bare", str(seed), str(bare))
            run_git("clone", "-q", "--branch", "main", str(bare), str(work))

            (work / ".agent-safety").mkdir()
            (work / ".agent-safety" / "state.json").write_text("{}\n", encoding="utf-8")
            (work / "local-notes.md").write_text("notes\n", encoding="utf-8")
            state, detail = inspect_repo(work, str(bare), "main")
            self.assertEqual(state, STATE_OK, detail)
            self.assertIn("benign untracked", detail)

            (work / "danger.txt").write_text("not classified as benign\n", encoding="utf-8")
            state, detail = inspect_repo(work, str(bare), "main")
            self.assertEqual(state, STATE_CONFLICT)
            self.assertIn("non-benign untracked", detail)


if __name__ == "__main__":
    unittest.main()
