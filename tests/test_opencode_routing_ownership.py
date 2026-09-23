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
from setup_migration import (  # noqa: E402
    OPENCODE_SEMANTIC_MODE,
    adopt_legacy_opencode_config,
    reconcile_opencode_config,
)


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

    def test_mode_less_exact_legacy_record_is_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            existing = desired_config()
            existing["model"] = "routerai/qwen/qwen3.6-plus"
            original = render(existing)
            destination.write_bytes(original)
            manifest = {
                "managed_files": {
                    "OpenCode config": {
                        "path": str(destination),
                        "sha256": sha256_bytes(original),
                        "source": SOURCE,
                    }
                }
            }

            changed, reporter = self._apply(destination, manifest, desired_config(), root / "state")
            self.assertTrue(changed, [row.detail for row in reporter.results])
            owner = manifest["managed_files"]["OpenCode config"]
            self.assertEqual(owner["mode"], OPENCODE_SEMANTIC_MODE)
            self.assertEqual(
                json.loads(destination.read_text(encoding="utf-8"))["model"],
                "openai/gpt-5.6-terra",
            )

    def test_invalid_fresh_routing_policy_fails_before_creating_config(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            invalid = desired_config()
            invalid["agent"] = "not-an-object"
            manifest = {"managed_files": {}}

            changed, reporter = self._apply(destination, manifest, invalid, root / "state")
            self.assertFalse(changed)
            self.assertFalse(destination.exists())
            self.assertEqual(manifest, {"managed_files": {}})
            self.assertTrue(any(row.state == STATE_CONFLICT for row in reporter.results))

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

    def test_explicit_adoption_requires_exact_current_hash_and_preserves_external_route(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_dir = root / "state"
            destination = root / "opencode.jsonc"
            original = desired_config()
            original_data = render(original)
            destination.write_bytes(original_data)
            manifest = self._legacy_manifest(destination, original_data)

            current = copy.deepcopy(original)
            current["model"] = "vendor/user-choice"
            current["user_setting"] = {"keep": True}
            current["agent"].pop("explore")
            current_data = render(current)
            destination.write_bytes(current_data)
            expected = sha256_bytes(current_data)

            mismatch_manifest = copy.deepcopy(manifest)
            reporter = Reporter()
            changed = adopt_legacy_opencode_config(
                destination=destination,
                desired_data=render(desired_config()),
                source_label=SOURCE,
                manifest=mismatch_manifest,
                reporter=reporter,
                expected_current_sha="0" * 64,
                state_dir=state_dir,
            )
            self.assertFalse(changed)
            self.assertEqual(destination.read_bytes(), current_data)
            self.assertEqual(mismatch_manifest, manifest)
            self.assertTrue(any("hash mismatch" in row.detail for row in reporter.results))

            reporter = Reporter()
            changed = adopt_legacy_opencode_config(
                destination=destination,
                desired_data=render(desired_config()),
                source_label=SOURCE,
                manifest=manifest,
                reporter=reporter,
                expected_current_sha=expected,
                state_dir=state_dir,
            )
            self.assertTrue(changed, [row.detail for row in reporter.results])
            updated = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(updated["model"], "vendor/user-choice")
            self.assertTrue(updated["user_setting"]["keep"])
            self.assertEqual(updated["agent"]["explore"]["model"], "openai/gpt-5.6-luna")

            owner = manifest["managed_files"]["OpenCode config"]
            self.assertEqual(owner["mode"], OPENCODE_SEMANTIC_MODE)
            self.assertNotIn("/model", owner["managed_paths"])
            self.assertIn("/small_model", owner["managed_paths"])
            self.assertIn("/agent/general/model", owner["managed_paths"])
            self.assertIn("/agent/explore/model", owner["managed_paths"])
            backups = list((state_dir / "backups").glob("*/OpenCode_config/opencode.jsonc"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), current_data)

    def test_explicit_adoption_of_partial_matching_routes_adds_missing_managed_routes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_dir = root / "state"
            destination = root / "opencode.jsonc"
            original = desired_config()
            original_data = render(original)
            destination.write_bytes(original_data)
            manifest = self._legacy_manifest(destination, original_data)

            current = copy.deepcopy(original)
            current.pop("model")
            current["agent"].pop("explore")
            current["user_note"] = "preserve"
            current_data = render(current)
            destination.write_bytes(current_data)

            reporter = Reporter()
            changed = adopt_legacy_opencode_config(
                destination=destination,
                desired_data=render(desired_config()),
                source_label=SOURCE,
                manifest=manifest,
                reporter=reporter,
                expected_current_sha=sha256_bytes(current_data),
                state_dir=state_dir,
            )
            self.assertTrue(changed, [row.detail for row in reporter.results])
            updated = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(updated["model"], "openai/gpt-5.6-terra")
            self.assertEqual(updated["agent"]["explore"]["model"], "openai/gpt-5.6-luna")
            self.assertEqual(updated["user_note"], "preserve")
            owner = manifest["managed_files"]["OpenCode config"]
            self.assertIn("/model", owner["managed_paths"])
            self.assertIn("/agent/explore/model", owner["managed_paths"])
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

    def test_owned_semantic_routes_upgrade_from_gpt56_to_gpt6_with_plain_apply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            state_dir = root / "state"
            destination = root / "opencode.jsonc"

            old_policy = desired_config()
            original = render(old_policy)
            destination.write_bytes(original)
            manifest = self._legacy_manifest(destination, original)
            changed, reporter = self._apply(destination, manifest, old_policy, state_dir)
            self.assertTrue(changed, [row.detail for row in reporter.results])
            self.assertEqual(manifest["managed_files"]["OpenCode config"]["mode"], OPENCODE_SEMANTIC_MODE)

            user_edit = json.loads(destination.read_text(encoding="utf-8"))
            user_edit["permission"] = {"bash": "ask"}
            destination.write_bytes(render(user_edit))
            before_check = destination.read_bytes()
            manifest_before_check = copy.deepcopy(manifest)

            new_policy = desired_config(model="openai/gpt-6-sol")
            new_policy["small_model"] = "openai/gpt-6-luna"
            new_policy["agent"]["general"]["model"] = "openai/gpt-6-sol"
            new_policy["agent"]["explore"]["model"] = "openai/gpt-6-luna"

            check_reporter = Reporter()
            changed = reconcile_opencode_config(
                destination=destination,
                desired_data=render(new_policy),
                source_label=SOURCE,
                manifest=manifest,
                reporter=check_reporter,
                check=True,
                force=False,
                state_dir=state_dir,
            )
            self.assertFalse(changed)
            self.assertEqual(destination.read_bytes(), before_check)
            self.assertEqual(manifest, manifest_before_check)
            self.assertTrue(any(row.state == "outdated" for row in check_reporter.results))
            self.assertFalse(any(row.state == STATE_CONFLICT for row in check_reporter.results))

            changed, reporter = self._apply(destination, manifest, new_policy, state_dir)
            self.assertTrue(changed, [row.detail for row in reporter.results])
            updated = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(updated["model"], "openai/gpt-6-sol")
            self.assertEqual(updated["small_model"], "openai/gpt-6-luna")
            self.assertEqual(updated["agent"]["general"]["model"], "openai/gpt-6-sol")
            self.assertEqual(updated["agent"]["explore"]["model"], "openai/gpt-6-luna")
            self.assertEqual(updated["permission"], {"bash": "ask"})
            backups = list((state_dir / "backups").glob("*/OpenCode_config/opencode.jsonc"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), before_check)

            stable_config = destination.read_bytes()
            stable_manifest = copy.deepcopy(manifest)
            changed, reporter = self._apply(destination, manifest, new_policy, state_dir)
            self.assertFalse(changed, [row.detail for row in reporter.results])
            self.assertEqual(destination.read_bytes(), stable_config)
            self.assertEqual(manifest, stable_manifest)

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

    def test_matching_unowned_routes_are_adopted_only_by_explicit_apply(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            existing = desired_config()
            data = render(existing)
            destination.write_bytes(data)
            manifest = {"managed_files": {}}
            stable_manifest = copy.deepcopy(manifest)

            check_reporter = Reporter()
            changed = reconcile_opencode_config(
                destination=destination,
                desired_data=render(desired_config()),
                source_label=SOURCE,
                manifest=manifest,
                reporter=check_reporter,
                check=True,
                force=False,
                state_dir=root / "state",
            )
            self.assertFalse(changed)
            self.assertEqual(destination.read_bytes(), data)
            self.assertEqual(manifest, stable_manifest)

            changed, reporter = self._apply(destination, manifest, desired_config(), root / "state")
            self.assertTrue(changed, [row.detail for row in reporter.results])
            self.assertEqual(destination.read_bytes(), data)
            owner = manifest["managed_files"]["OpenCode config"]
            self.assertEqual(owner["mode"], OPENCODE_SEMANTIC_MODE)
            self.assertIn("/model", owner["managed_paths"])
            self.assertIn("/small_model", owner["managed_paths"])
            self.assertIn("/agent/general/model", owner["managed_paths"])

    def test_existing_unowned_nonrouting_fields_keep_preexisting_safe_merge_contract(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            existing = desired_config()
            existing["model"] = "vendor/user-choice"
            existing["autoupdate"] = True
            existing["provider"]["routerai"]["name"] = "Old RouterAI label"
            existing["provider"]["routerai"]["options"]["baseURL"] = "https://old.example.invalid/v1"
            destination.write_bytes(render(existing))
            manifest = {"managed_files": {}}

            changed, reporter = self._apply(destination, manifest, desired_config(), root / "state")
            self.assertTrue(changed, [row.detail for row in reporter.results])
            updated = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(updated["model"], "vendor/user-choice")
            self.assertEqual(updated["autoupdate"], "notify")
            self.assertEqual(updated["provider"]["routerai"]["name"], "RouterAI")
            self.assertEqual(
                updated["provider"]["routerai"]["options"]["baseURL"],
                "https://routerai.ru/api/v1",
            )

            owner = manifest["managed_files"]["OpenCode config"]
            self.assertEqual(owner["mode"], OPENCODE_SEMANTIC_MODE)
            self.assertIn("/autoupdate", owner["managed_paths"])
            self.assertIn("/provider/routerai/name", owner["managed_paths"])
            self.assertIn("/provider/routerai/options/baseURL", owner["managed_paths"])
            self.assertNotIn("/model", owner["managed_paths"])

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
