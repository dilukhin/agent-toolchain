from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import setup_agent_inventory
import toolchainctl


class AgentInventoryTests(unittest.TestCase):
    def test_config_summary_emits_safe_metadata_without_sensitive_values(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            config_dir.mkdir()
            project = root / "project"
            (project / ".git").mkdir(parents=True)
            config = {
                "$schema": "https://opencode.ai/config.json",
                "apiKey": "TOP-LEVEL-SECRET",
                "agent": {
                    "reviewer": {
                        "model": "openai/gpt-6-sol",
                        "mode": "subagent",
                        "description": "PRIVATE DESCRIPTION",
                        "prompt": "PRIVATE PROMPT",
                        "permission": {
                            "bash": {
                                "*": "deny",
                                "cat /private/secret": "deny",
                            },
                            "edit": "deny",
                        },
                        "tools": {"write": False, "mcp_secret_tool": False},
                    }
                },
            }
            path = config_dir / "opencode.jsonc"
            path.write_text(json.dumps(config), encoding="utf-8")

            inventory = setup_agent_inventory.collect_agent_inventory(
                project=project,
                config_dir=config_dir,
                environ={},
            )
            serialized = json.dumps(inventory, ensure_ascii=False)

            self.assertNotIn("TOP-LEVEL-SECRET", serialized)
            self.assertNotIn("PRIVATE DESCRIPTION", serialized)
            self.assertNotIn("PRIVATE PROMPT", serialized)
            self.assertNotIn("cat /private/secret", serialized)
            agent = next(
                item
                for source in inventory["sources"]
                for item in source.get("agents", [])
                if item.get("name") == "reviewer"
            )
            self.assertEqual(agent["model"], "openai/gpt-6-sol")
            self.assertEqual(agent["mode"], "subagent")
            self.assertTrue(agent["description_present"])
            self.assertTrue(agent["prompt_present"])
            self.assertEqual(agent["permission_keys"], ["bash", "edit"])
            self.assertEqual(agent["tool_keys"], ["mcp_secret_tool", "write"])

    def test_malformed_model_and_mode_values_are_not_printed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            config_dir.mkdir()
            project = root / "project"
            (project / ".git").mkdir(parents=True)
            config = {
                "agent": {
                    "bad": {
                        "model": "SECRET VALUE WITH SPACES",
                        "mode": "PRIVATE MODE VALUE",
                    }
                }
            }
            (config_dir / "opencode.jsonc").write_text(json.dumps(config), encoding="utf-8")

            inventory = setup_agent_inventory.collect_agent_inventory(
                project=project,
                config_dir=config_dir,
                environ={},
            )
            serialized = json.dumps(inventory, ensure_ascii=False)

            self.assertNotIn("SECRET VALUE WITH SPACES", serialized)
            self.assertNotIn("PRIVATE MODE VALUE", serialized)
            agent = next(
                item
                for source in inventory["sources"]
                for item in source.get("agents", [])
                if item.get("name") == "bad"
            )
            self.assertNotIn("model", agent)
            self.assertNotIn("mode", agent)

    def test_markdown_summary_never_emits_description_or_prompt_body(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            agents = config_dir / "agents"
            agents.mkdir(parents=True)
            project = root / "project"
            (project / ".git").mkdir(parents=True)
            markdown = """---
description: PRIVATE REVIEW DESCRIPTION
mode: subagent
model: openai/gpt-6-astra
permission:
  edit: deny
  bash: deny
---
PRIVATE SYSTEM PROMPT BODY
"""
            path = agents / "astra-reviewer.md"
            path.write_text(markdown, encoding="utf-8")

            inventory = setup_agent_inventory.collect_agent_inventory(
                project=project,
                config_dir=config_dir,
                environ={},
            )
            serialized = json.dumps(inventory, ensure_ascii=False)

            self.assertNotIn("PRIVATE REVIEW DESCRIPTION", serialized)
            self.assertNotIn("PRIVATE SYSTEM PROMPT BODY", serialized)
            record = next(
                source
                for source in inventory["sources"]
                if source.get("agent_id") == "astra-reviewer"
            )
            self.assertEqual(record["model"], "openai/gpt-6-astra")
            self.assertEqual(record["mode"], "subagent")
            self.assertTrue(record["description_present"])
            self.assertTrue(record["prompt_present"])
            self.assertTrue(record["permission_present"])

    def test_project_and_global_collision_is_reported_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            config_dir.mkdir()
            project = root / "repo" / "nested"
            project.mkdir(parents=True)
            (root / "repo" / ".git").mkdir()
            global_config = config_dir / "opencode.jsonc"
            global_config.write_text(
                json.dumps({"agent": {"reviewer": {"model": "openai/gpt-6-sol"}}}),
                encoding="utf-8",
            )
            project_agents = root / "repo" / ".opencode" / "agents"
            project_agents.mkdir(parents=True)
            local_agent = project_agents / "reviewer.md"
            local_agent.write_text(
                "---\nmode: subagent\nmodel: openai/gpt-6-astra\n---\nReview only.\n",
                encoding="utf-8",
            )
            before = {
                global_config: global_config.read_bytes(),
                local_agent: local_agent.read_bytes(),
            }

            inventory = setup_agent_inventory.collect_agent_inventory(
                project=project,
                config_dir=config_dir,
                environ={},
            )

            self.assertIn("reviewer", inventory["collisions"])
            self.assertGreaterEqual(len(inventory["collisions"]["reviewer"]), 2)
            self.assertEqual(inventory["git_root"], str((root / "repo").resolve()))
            self.assertEqual(global_config.read_bytes(), before[global_config])
            self.assertEqual(local_agent.read_bytes(), before[local_agent])

    def test_inline_config_is_hashed_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config_dir = root / "config"
            config_dir.mkdir()
            project = root / "repo"
            (project / ".git").mkdir(parents=True)
            raw = json.dumps({
                "provider": {"example": {"apiKey": "INLINE-SECRET"}},
                "agent": {"inline-worker": {"model": "openai/gpt-6-luna", "mode": "subagent"}},
            })

            inventory = setup_agent_inventory.collect_agent_inventory(
                project=project,
                config_dir=config_dir,
                environ={"OPENCODE_CONFIG_CONTENT": raw},
            )
            serialized = json.dumps(inventory, ensure_ascii=False)
            source = next(item for item in inventory["sources"] if item["id"] == "inline-config")

            self.assertNotIn("INLINE-SECRET", serialized)
            self.assertEqual(source["status"], "present")
            self.assertEqual(len(source["sha256"]), 64)
            self.assertEqual(source["agents"][0]["name"], "inline-worker")

    def test_parser_exposes_read_only_agents_inspect_command(self) -> None:
        args = toolchainctl.build_parser().parse_args(
            ["agents", "inspect", "--project", "/tmp/example", "--json"]
        )
        self.assertEqual(args.command, "agents")
        self.assertEqual(args.agents_command, "inspect")
        self.assertEqual(args.project, "/tmp/example")
        self.assertTrue(args.json)

    def test_render_does_not_include_permission_patterns(self) -> None:
        inventory = {
            "project": "/tmp/project",
            "git_root": "/tmp/project",
            "sources": [{
                "id": "x",
                "kind": "config",
                "layer": "global",
                "precedence": 20,
                "status": "ok",
                "agents": [{
                    "name": "worker",
                    "model": "openai/gpt-6-luna",
                    "mode": "subagent",
                    "description_present": True,
                    "prompt_present": True,
                    "permission_present": True,
                    "permission_keys": ["bash"],
                    "tools_present": False,
                }],
            }],
            "collisions": {},
            "limitations": [],
        }
        rendered = setup_agent_inventory.render_inventory(inventory)
        self.assertIn("agent=worker", rendered)
        self.assertNotIn("permission_keys", rendered)
        self.assertNotIn("bash", rendered)


if __name__ == "__main__":
    unittest.main()
