from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from setup_lib import Reporter, STATE_CONFLICT, sha256_bytes  # noqa: E402
from setup_migration import OPENCODE_SEMANTIC_MODE, reconcile_opencode_config  # noqa: E402


SOURCE = "opencode_setup:managed-merge:templates/opencode.jsonc"


def desired_config(*, model: str = "openai/gpt-5.6-terra") -> dict:
    return {
        "$schema": "https://opencode.ai/config.json",
        "autoupdate": "notify",
        "provider": {
            "routerai": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "RouterAI",
                "options": {
                    "baseURL": "https://routerai.ru/api/v1",
                    "apiKey": "{file:/tmp/routerai-key.txt}",
                },
                "models": {},
            }
        },
        "model": model,
        "small_model": "openai/gpt-5.6-luna",
        "agent": {
            "general": {"model": model},
            "explore": {"model": "openai/gpt-5.6-luna"},
        },
    }


def render(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


class OpenCodeRoutingOwnershipTests(unittest.TestCase):
    def _legacy_manifest(self, destination: Path, data: bytes) -> dict:
        return {
            "managed_files": {
                "OpenCode config": {
                    "path": str(destination),
                    "sha256": sha256_bytes(data),
                    "source": SOURCE,
                    "mode": "merged-json",
                }
            }
        }

    def _apply(self, destination: Path, manifest: dict, desired: dict, state_dir: Path, *, force: bool = False):
        reporter = Reporter()
        changed = reconcile_opencode_config(
            destination=destination,
            desired_data=render(desired),
            source_label=SOURCE,
            manifest=manifest,
            reporter=reporter,
            check=False,
            force=force,
            state_dir=state_dir,
        )
        return changed, reporter

    def test_exact_legacy_ownership_migrates_routes_and_preserves_role_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            existing = desired_config()
            existing["model"] = "routerai/qwen/qwen3.6-plus"
            existing["small_model"] = "opencode/gpt-5-nano"
            existing["agent"]["general"] = {
                "model": "routerai/qwen/qwen3.6-plus",
                "permission": {"bash": "ask"},
                "description": "keep me",
            }
            existing["agent"]["custom-role"] = {
                "model": "vendor/custom",
                "permission": {"read": "allow"},
            }
            original = render(existing)
            destination.write_bytes(original)
            manifest = self._legacy_manifest(destination, original)

            changed, _reporter = self._apply(destination, manifest, desired_config(), root / "state")
            self.assertTrue(changed)

            updated = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(updated["model"], "openai/gpt-5.6-terra")
            self.assertEqual(updated["small_model"], "openai/gpt-5.6-luna")
            self.assertEqual(updated["agent"]["general"]["model"], "openai/gpt-5.6-terra")
            self.assertEqual(updated["agent"]["general"]["permission"], {"bash": "ask"})
            self.assertEqual(updated["agent"]["general"]["description"], "keep me")
            self.assertEqual(updated["agent"]["custom-role"], existing["agent"]["custom-role"])

            owner = manifest["managed_files"]["OpenCode config"]
            self.assertEqual(owner["mode"], OPENCODE_SEMANTIC_MODE)
            self.assertIn("/model", owner["managed_paths"])
            self.assertIn("/small_model", owner["managed_paths"])
            self.assertIn("/agent/general/model", owner["managed_paths"])
            self.assertNotIn("/agent/general/permission", owner["managed_paths"])
            self.assertNotIn("/agent/custom-role/model", owner["managed_paths"])

    def test_legacy_whole_file_drift_is_blocked_even_with_force(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            existing = desired_config()
            original = render(existing)
            destination.write_bytes(original)
            manifest = self._legacy_manifest(destination, original)

            modified = copy.deepcopy(existing)
            modified["user_setting"] = True
            modified_data = render(modified)
            destination.write_bytes(modified_data)
            stable_manifest = copy.deepcopy(manifest)

            changed, reporter = self._apply(
                destination, manifest, desired_config(model="openai/gpt-5.6-terra-next"),
                root / "state", force=True,
            )
            self.assertFalse(changed)
            self.assertEqual(destination.read_bytes(), modified_data)
            self.assertEqual(manifest, stable_manifest)
            self.assertTrue(any(row.state == STATE_CONFLICT for row in reporter.results))
            self.assertTrue(any("--force" in row.detail for row in reporter.results))

    def test_unmanaged_user_drift_does_not_block_later_managed_route_update(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            existing = desired_config()
            original = render(existing)
            destination.write_bytes(original)
            manifest = self._legacy_manifest(destination, original)
            changed, _ = self._apply(destination, manifest, desired_config(), root / "state")
            self.assertTrue(changed)

            user_edit = json.loads(destination.read_text(encoding="utf-8"))
            user_edit["permission"] = {"bash": "ask"}
            destination.write_bytes(render(user_edit))

            next_policy = desired_config(model="openai/gpt-5.6-terra-next")
            changed, reporter = self._apply(destination, manifest, next_policy, root / "state")
            self.assertTrue(changed, [row.detail for row in reporter.results])
            updated = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(updated["permission"], {"bash": "ask"})
            self.assertEqual(updated["model"], "openai/gpt-5.6-terra-next")
            self.assertEqual(updated["agent"]["general"]["model"], "openai/gpt-5.6-terra-next")

    def test_owned_route_drift_conflicts_without_force_and_force_repairs_only_owned_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            existing = desired_config()
            original = render(existing)
            destination.write_bytes(original)
            manifest = self._legacy_manifest(destination, original)
            self._apply(destination, manifest, desired_config(), root / "state")

            user_edit = json.loads(destination.read_text(encoding="utf-8"))
            user_edit["model"] = "routerai/qwen/qwen3.6-plus"
            user_edit["user_note"] = "preserve"
            drifted = render(user_edit)
            destination.write_bytes(drifted)
            stable_manifest = copy.deepcopy(manifest)

            changed, reporter = self._apply(destination, manifest, desired_config(), root / "state")
            self.assertFalse(changed)
            self.assertEqual(destination.read_bytes(), drifted)
            self.assertEqual(manifest, stable_manifest)
            self.assertTrue(any(row.state == STATE_CONFLICT for row in reporter.results))

            changed, reporter = self._apply(destination, manifest, desired_config(), root / "state", force=True)
            self.assertTrue(changed, [row.detail for row in reporter.results])
            repaired = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(repaired["model"], "openai/gpt-5.6-terra")
            self.assertEqual(repaired["user_note"], "preserve")

    def test_existing_unowned_route_is_preserved_while_missing_routes_become_managed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            existing = {
                "$schema": "https://opencode.ai/config.json",
                "model": "vendor/user-choice",
                "permission": {"bash": "ask"},
            }
            destination.write_bytes(render(existing))
            manifest = {"managed_files": {}}

            changed, reporter = self._apply(destination, manifest, desired_config(), root / "state")
            self.assertTrue(changed, [row.detail for row in reporter.results])
            updated = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(updated["model"], "vendor/user-choice")
            self.assertEqual(updated["small_model"], "openai/gpt-5.6-luna")
            self.assertEqual(updated["agent"]["general"]["model"], "openai/gpt-5.6-terra")
            self.assertEqual(updated["permission"], {"bash": "ask"})

            owner = manifest["managed_files"]["OpenCode config"]
            self.assertEqual(owner["mode"], OPENCODE_SEMANTIC_MODE)
            self.assertNotIn("/model", owner["managed_paths"])
            self.assertIn("/small_model", owner["managed_paths"])
            self.assertIn("/agent/general/model", owner["managed_paths"])


if __name__ == "__main__":
    unittest.main()
