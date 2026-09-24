from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setup_agent_roles import (
    AGENT_MODEL_MODE,
    adopt_agent_model,
    model_component,
    reconcile_adopted_agent_models,
    reconcile_managed_role_templates,
)
from setup_lib import Reporter, STATE_CONFLICT, STATE_OUTDATED, sha256_bytes
from setup_manifest import empty_manifest
from setup_migration import reconcile_opencode_config


class ManagedAgentRoleTests(unittest.TestCase):
    def test_model_adoption_changes_only_model_and_preserves_user_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            agents_dir = config_dir / "agents"
            agents_dir.mkdir(parents=True)
            state_dir = root / "state"
            path = agents_dir / "luna.md"
            original = (
                "---\n"
                "description: User-owned description\n"
                "mode: subagent\n"
                "model: openai/gpt-5.6-luna\n"
                "permission:\n"
                "  edit: deny\n"
                "  bash: deny\n"
                "---\n"
                "USER OWNED PROMPT\n"
            ).encode()
            path.write_bytes(original)
            manifest = empty_manifest()
            reporter = Reporter()

            changed = adopt_agent_model(
                role="luna",
                destination=path,
                desired_model="openai/gpt-6-luna",
                expected_current_sha=sha256_bytes(original),
                manifest=manifest,
                reporter=reporter,
                state_dir=state_dir,
            )

            self.assertTrue(changed)
            updated = path.read_text(encoding="utf-8")
            self.assertIn("model: openai/gpt-6-luna", updated)
            self.assertIn("description: User-owned description", updated)
            self.assertIn("  edit: deny", updated)
            self.assertIn("USER OWNED PROMPT", updated)
            record = manifest["managed_files"][model_component("luna")]
            self.assertEqual(record["mode"], AGENT_MODEL_MODE)
            self.assertEqual(record["role"], "luna")
            backups = list((state_dir / "backups").glob("*/OpenCode_agent_model_luna/luna.md"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)

    def test_model_adoption_stale_hash_is_read_only_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = root / "luna.md"
            original = b"---\nmodel: openai/gpt-5.6-luna\n---\nPrompt\n"
            path.write_bytes(original)
            manifest = empty_manifest()
            reporter = Reporter()

            changed = adopt_agent_model(
                role="luna",
                destination=path,
                desired_model="openai/gpt-6-luna",
                expected_current_sha="0" * 64,
                manifest=manifest,
                reporter=reporter,
                state_dir=root / "state",
            )

            self.assertFalse(changed)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(manifest, empty_manifest())
            self.assertTrue(any(row.state == STATE_CONFLICT for row in reporter.results))

    def test_adopted_model_future_policy_update_preserves_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            agents_dir = config_dir / "agents"
            agents_dir.mkdir(parents=True)
            state_dir = root / "state"
            path = agents_dir / "sol-specialist.md"
            original = b"---\nmode: subagent\nmodel: openai/gpt-5.6-sol\npermission:\n  edit: deny\n---\nKEEP THIS PROMPT\n"
            path.write_bytes(original)
            manifest = empty_manifest()
            reporter = Reporter()
            self.assertTrue(adopt_agent_model(
                role="sol-specialist",
                destination=path,
                desired_model="openai/gpt-6-sol",
                expected_current_sha=sha256_bytes(original),
                manifest=manifest,
                reporter=reporter,
                state_dir=state_dir,
            ))

            before = path.read_text(encoding="utf-8")
            reporter = Reporter()
            changed = reconcile_adopted_agent_models(
                config_dir=config_dir,
                desired_models={"sol-specialist": "openai/gpt-6-sol-next"},
                manifest=manifest,
                reporter=reporter,
                check=False,
                force=False,
                state_dir=state_dir,
            )
            self.assertTrue(changed)
            after = path.read_text(encoding="utf-8")
            self.assertIn("model: openai/gpt-6-sol-next", after)
            self.assertIn("KEEP THIS PROMPT", after)
            self.assertEqual(before.replace("openai/gpt-6-sol", "openai/gpt-6-sol-next"), after)

    def test_new_managed_roles_create_and_repeat_is_noop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            state_dir = root / "state"
            manifest = empty_manifest()
            reporter = Reporter()

            changed = reconcile_managed_role_templates(
                repo_root=ROOT,
                config_dir=config_dir,
                manifest=manifest,
                reporter=reporter,
                check=True,
                force=False,
                state_dir=state_dir,
            )
            self.assertFalse(changed)
            self.assertFalse((config_dir / "agents").exists())

            reporter = Reporter()
            changed = reconcile_managed_role_templates(
                repo_root=ROOT,
                config_dir=config_dir,
                manifest=manifest,
                reporter=reporter,
                check=False,
                force=False,
                state_dir=state_dir,
            )
            self.assertTrue(changed)
            for name in ("docs-researcher", "code-reviewer", "evidence-auditor", "code-worker", "test-runner"):
                self.assertTrue((config_dir / "agents" / f"{name}.md").is_file())

            stable = {path: path.read_bytes() for path in (config_dir / "agents").glob("*.md")}
            reporter = Reporter()
            changed = reconcile_managed_role_templates(
                repo_root=ROOT,
                config_dir=config_dir,
                manifest=manifest,
                reporter=reporter,
                check=False,
                force=False,
                state_dir=state_dir,
            )
            self.assertFalse(changed)
            self.assertEqual(stable, {path: path.read_bytes() for path in (config_dir / "agents").glob("*.md")})

    def test_existing_unowned_role_is_preserved_and_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            agents_dir = config_dir / "agents"
            agents_dir.mkdir(parents=True)
            path = agents_dir / "code-reviewer.md"
            original = b"---\nmode: subagent\nmodel: vendor/user-model\n---\nUSER ROLE\n"
            path.write_bytes(original)
            manifest = empty_manifest()
            reporter = Reporter()

            reconcile_managed_role_templates(
                repo_root=ROOT,
                config_dir=config_dir,
                manifest=manifest,
                reporter=reporter,
                check=False,
                force=False,
                state_dir=root / "state",
            )

            self.assertEqual(path.read_bytes(), original)
            self.assertNotIn("OpenCode agent code-reviewer", manifest["managed_files"])
            self.assertTrue(any(
                row.component == "OpenCode agent code-reviewer" and row.state == STATE_CONFLICT
                for row in reporter.results
            ))

    def test_semantic_policy_adds_missing_explore_and_leaf_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            destination = root / "opencode.jsonc"
            state_dir = root / "state"
            desired = json.loads((ROOT / "templates" / "opencode.jsonc").read_text(encoding="utf-8"))
            desired["provider"]["routerai"]["options"]["apiKey"] = "{file:/tmp/key}"
            old = copy.deepcopy(desired)
            old["agent"]["explore"].pop("description")
            old["agent"]["explore"].pop("permission")
            for role in ("luna", "luna-safe-worker", "sol-specialist", "astra-reviewer"):
                old["agent"][role].pop("permission")
            old_data = (json.dumps(old, ensure_ascii=False, indent=2) + "\n").encode()
            desired_data = (json.dumps(desired, ensure_ascii=False, indent=2) + "\n").encode()
            destination.write_bytes(old_data)
            manifest = empty_manifest()

            reporter = Reporter()
            self.assertTrue(reconcile_opencode_config(
                destination=destination,
                desired_data=old_data,
                source_label="test",
                manifest=manifest,
                reporter=reporter,
                check=False,
                force=False,
                state_dir=state_dir,
            ))

            stable = destination.read_bytes()
            reporter = Reporter()
            changed = reconcile_opencode_config(
                destination=destination,
                desired_data=desired_data,
                source_label="test",
                manifest=manifest,
                reporter=reporter,
                check=True,
                force=False,
                state_dir=state_dir,
            )
            self.assertFalse(changed)
            self.assertEqual(destination.read_bytes(), stable)
            self.assertTrue(any(row.state == STATE_OUTDATED for row in reporter.results))

            reporter = Reporter()
            self.assertTrue(reconcile_opencode_config(
                destination=destination,
                desired_data=desired_data,
                source_label="test",
                manifest=manifest,
                reporter=reporter,
                check=False,
                force=False,
                state_dir=state_dir,
            ))
            updated = json.loads(destination.read_text(encoding="utf-8"))
            self.assertEqual(updated["agent"]["explore"]["permission"]["bash"] if "bash" in updated["agent"]["explore"]["permission"] else "deny", "deny")
            self.assertEqual(updated["agent"]["explore"]["permission"]["task"], "deny")
            self.assertEqual(updated["agent"]["luna"]["permission"]["task"], "deny")
            paths = manifest["managed_files"]["OpenCode config"]["managed_paths"]
            self.assertIn("/agent/explore/description", paths)
            self.assertIn("/agent/explore/permission", paths)
            self.assertIn("/agent/luna/permission", paths)


if __name__ == "__main__":
    unittest.main()
