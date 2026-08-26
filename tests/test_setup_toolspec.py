from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setup_tools import TOOL_SPEC_SCHEMA, parse_tool_spec, parse_tool_specs  # noqa: E402

SSH_RELAY_REF = "1a794f84bb3664fe580716195ee939bbe2295675"
AGENT_SAFE_REF = "95545d20533b2dfa1de7d75a30fa1bbfb1d428e3"


class ToolSpecTests(unittest.TestCase):
    def test_valid_pinned_git_tool(self) -> None:
        raw = {
            "source": "git",
            "repo": "https://example.invalid/tool.git",
            "ref": "0123456789abcdef",
            "project_directory": "tool",
            "runtime": "python-venv",
            "update_policy": "pinned-tested",
            "entrypoints": ["tool"],
            "health_contract": [
                {"argv": ["tool", "--version"]},
                {"argv": ["tool", "--help"]},
            ],
            "platforms": ["windows", "linux"],
        }
        spec, error = parse_tool_spec("tool", raw)
        self.assertIsNone(error)
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.name, "tool")
        self.assertEqual(spec.ref, "0123456789abcdef")
        self.assertEqual(spec.entrypoints, ("tool",))
        self.assertEqual(spec.health_contract[0].argv, ("tool", "--version"))

    def test_pinned_tool_requires_explicit_ref(self) -> None:
        spec, error = parse_tool_spec(
            "tool",
            {
                "source": "git",
                "repo": "https://example.invalid/tool.git",
                "project_directory": "tool",
                "runtime": "binary",
                "update_policy": "pinned-tested",
                "entrypoints": ["tool"],
                "health_contract": [{"argv": ["tool", "--version"]}],
            },
        )
        self.assertIsNone(spec)
        self.assertIn("explicit ref", error or "")

    def test_builtin_tool_rejects_repository_fields(self) -> None:
        spec, error = parse_tool_spec(
            "proxy-tools",
            {
                "source": "builtin",
                "repo": "https://example.invalid/should-not-exist.git",
                "runtime": "python",
                "update_policy": "bundled-with-setup",
                "entrypoints": ["run-with-proxy"],
                "health_contract": [{"argv": ["run-with-proxy", "--version"]}],
            },
        )
        self.assertIsNone(spec)
        self.assertIn("must not define", error or "")

    def test_duplicate_entrypoint_across_tools_fails_closed(self) -> None:
        env = {
            "tool_spec_schema": TOOL_SPEC_SCHEMA,
            "tools": {
                "one": {
                    "source": "builtin",
                    "runtime": "python",
                    "update_policy": "bundled-with-setup",
                    "entrypoints": ["shared"],
                    "health_contract": [{"argv": ["shared", "--version"]}],
                },
                "two": {
                    "source": "builtin",
                    "runtime": "python",
                    "update_policy": "bundled-with-setup",
                    "entrypoints": ["shared"],
                    "health_contract": [{"argv": ["shared", "--version"]}],
                },
            },
        }
        parsed, error = parse_tool_specs(env)
        self.assertEqual(parsed, {})
        self.assertIn("declared by both", error or "")

    def test_empty_tool_registry_is_valid_foundation(self) -> None:
        parsed, error = parse_tool_specs({"tool_spec_schema": TOOL_SPEC_SCHEMA, "tools": {}})
        self.assertIsNone(error)
        self.assertEqual(parsed, {})

    def test_repository_config_declares_exact_pinned_production_tools(self) -> None:
        data = json.loads((ROOT / "config_data.json").read_text(encoding="utf-8"))
        env = data["managed_environment"]
        self.assertEqual(env["manifest_schema"], 2)
        self.assertEqual(env["tool_spec_schema"], TOOL_SPEC_SCHEMA)
        parsed, error = parse_tool_specs(env)
        self.assertIsNone(error)
        self.assertEqual(set(parsed), {"ssh_relay", "agent-safe"})

        ssh = parsed["ssh_relay"]
        self.assertEqual(ssh.update_policy, "pinned-tested")
        self.assertEqual(ssh.runtime, "python-venv")
        self.assertEqual(ssh.ref, SSH_RELAY_REF)
        self.assertEqual(ssh.entrypoints, ("ssh_relay",))
        self.assertIn(("ssh_relay", "doctor"), tuple(check.argv for check in ssh.health_contract))

        safe = parsed["agent-safe"]
        self.assertEqual(safe.update_policy, "pinned-tested")
        self.assertEqual(safe.runtime, "python-venv")
        self.assertEqual(safe.ref, AGENT_SAFE_REF)
        self.assertEqual(safe.entrypoints, ("safe",))


if __name__ == "__main__":
    unittest.main()
