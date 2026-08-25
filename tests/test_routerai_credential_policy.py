from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEGACY_PLACEHOLDER = b"your-routerai-api-key-here\n"


class RouterAiCredentialPolicyTests(unittest.TestCase):
    def _run_core(self, home: Path) -> subprocess.CompletedProcess[str]:
        config_dir = home / ".config" / "opencode"
        stash_dir = home / "projects" / "stash" / "opencode.ai"
        credential_dir = config_dir / "credentials"
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
        return subprocess.run(
            cmd,
            text=True,
            encoding="utf-8",
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def test_fresh_install_records_canonical_path_without_creating_fake_key(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            canonical = home / ".config" / "opencode" / "credentials" / "routerai-api-key.txt"

            first = self._run_core(home)
            self.assertEqual(first.returncode, 2, first.stdout + first.stderr)
            self.assertFalse(canonical.exists())
            self.assertIn("ключ RouterAI не настроен", first.stdout)

            config_path = home / ".config" / "opencode" / "opencode.jsonc"
            manifest_path = home / "state" / "manifest.json"
            generated = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(
                generated["provider"]["routerai"]["options"]["apiKey"],
                "{file:" + str(canonical.resolve()) + "}",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            credential = manifest["credentials"]["routerai"]
            self.assertEqual(credential["mode"], "managed-path")
            self.assertEqual(credential["path"], str(canonical.resolve()))
            self.assertNotIn("sha256", credential)

            before_config = config_path.read_bytes()
            before_manifest = manifest_path.read_bytes()
            second = self._run_core(home)
            self.assertEqual(second.returncode, 2, second.stdout + second.stderr)
            self.assertFalse(canonical.exists())
            self.assertEqual(config_path.read_bytes(), before_config)
            self.assertEqual(manifest_path.read_bytes(), before_manifest)

    def test_legacy_managed_placeholder_is_unprovisioned_and_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            canonical = home / ".config" / "opencode" / "credentials" / "routerai-api-key.txt"
            canonical.parent.mkdir(parents=True)
            canonical.write_bytes(LEGACY_PLACEHOLDER)
            state = home / "state"
            state.mkdir(parents=True)
            (state / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": 1,
                        "managed_files": {},
                        "credentials": {
                            "routerai": {
                                "provider": "routerai",
                                "mode": "managed-path",
                                "path": str(canonical.resolve()),
                            }
                        },
                    },
                    indent=2,
                ) + "\n",
                encoding="utf-8",
            )

            cp = self._run_core(home)
            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
            self.assertEqual(canonical.read_bytes(), LEGACY_PLACEHOLDER)
            self.assertIn("служебная заглушка предыдущей версии", cp.stdout)
            self.assertIn("missing", cp.stdout)

            canonical.write_bytes(b"test-routerai-key\n")
            provisioned = self._run_core(home)
            self.assertEqual(provisioned.returncode, 2, provisioned.stdout + provisioned.stderr)
            self.assertEqual(canonical.read_bytes(), b"test-routerai-key\n")
            self.assertNotIn("служебная заглушка предыдущей версии", provisioned.stdout)
            if os.name == "nt":
                self.assertIn("up-to-date", provisioned.stdout)
                self.assertIn("existing credential file", provisioned.stdout)
            else:
                self.assertRegex(provisioned.stdout, r"configured\s+RouterAI credential")
                self.assertIn("permissions изменены", provisioned.stdout)
                self.assertIn("0o600", provisioned.stdout)

            repeat = self._run_core(home)
            self.assertEqual(repeat.returncode, 2, repeat.stdout + repeat.stderr)
            self.assertEqual(canonical.read_bytes(), b"test-routerai-key\n")
            self.assertRegex(repeat.stdout, r"up-to-date\s+RouterAI credential")

    def test_external_file_is_not_classified_by_placeholder_like_contents(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            config_dir = home / ".config" / "opencode"
            external = home / "external" / "routerai.txt"
            config_dir.mkdir(parents=True)
            external.parent.mkdir(parents=True)
            external.write_bytes(LEGACY_PLACEHOLDER)
            config = {
                "provider": {
                    "routerai": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "RouterAI",
                        "options": {
                            "baseURL": "https://routerai.ru/api/v1",
                            "apiKey": "{file:" + str(external.resolve()) + "}",
                        },
                        "models": {},
                    }
                }
            }
            (config_dir / "opencode.jsonc").write_text(json.dumps(config, indent=2), encoding="utf-8")

            cp = self._run_core(home)
            self.assertEqual(cp.returncode, 2, cp.stdout + cp.stderr)
            self.assertEqual(external.read_bytes(), LEGACY_PLACEHOLDER)
            self.assertIn("external file referenced by config", cp.stdout)
            self.assertNotIn("служебная заглушка предыдущей версии", cp.stdout)


if __name__ == "__main__":
    unittest.main()
