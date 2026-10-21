from __future__ import annotations

import json
import sys
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from setup_tools import TOOL_SPEC_SCHEMA, parse_tool_spec, parse_tool_specs  # noqa: E402


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

    def test_repository_config_follows_main_for_ssh_relay(self) -> None:
        data = json.loads((ROOT / "config_data.json").read_text(encoding="utf-8"))
        env = data["managed_environment"]
        self.assertEqual(env["manifest_schema"], 2)
        self.assertEqual(env["tool_spec_schema"], TOOL_SPEC_SCHEMA)

        raw_ssh = env["tools"]["ssh_relay"]
        self.assertEqual(raw_ssh["update_policy"], "follow-branch")
        self.assertEqual(raw_ssh["branch"], "main")
        self.assertNotIn("ref", raw_ssh)

        raw_safe = env["tools"]["agent-safe"]
        self.assertEqual(raw_safe["update_policy"], "follow-branch")
        self.assertEqual(raw_safe["branch"], "master")
        self.assertNotIn("ref", raw_safe)

        raw_tunnel = env["tools"]["tunnelctl"]
        self.assertEqual(raw_tunnel["update_policy"], "follow-branch")
        self.assertEqual(raw_tunnel["branch"], "main")
        self.assertNotIn("ref", raw_tunnel)

        with mock.patch("setup_tools._resolve_github_branch",
                        side_effect=AssertionError("parsing must not perform network I/O")):
            parsed, error = parse_tool_specs(env)
        self.assertIsNone(error)
        self.assertEqual(set(parsed), {"ssh_relay", "agent-safe", "proxy-tools", "tunnelctl"})

        ssh = parsed["ssh_relay"]
        self.assertEqual(ssh.update_policy, "follow-branch")
        self.assertEqual(ssh.tracking_branch, "main")
        self.assertIsNone(ssh.ref)
        self.assertEqual(ssh.entrypoints, ("ssh_relay",))
        self.assertIn(("ssh_relay", "doctor"), tuple(c.argv for c in ssh.health_contract))

        from setup_tools import resolve_tool_specs
        from setup_managed_tools import _pip_source, _validate_supported_spec

        def resolve(repo: str, branch: str) -> str:
            expected = {
                ("https://github.com/dilukhin/ssh_relay.git", "main"): "a" * 40,
                ("https://github.com/dilukhin/agent-safe.git", "master"): "b" * 40,
                ("https://github.com/dilukhin/tunnelctl.git", "main"): "c" * 40,
            }
            self.assertIn((repo, branch), expected)
            return expected[(repo, branch)]

        with mock.patch("setup_tools._resolve_github_branch", side_effect=resolve) as resolver:
            execution, error = resolve_tool_specs(parsed)
        self.assertIsNone(error)
        self.assertEqual(resolver.call_count, 3)
        checked = execution["ssh_relay"]
        self.assertEqual(checked.ref, "a" * 40)
        self.assertEqual(checked.update_policy, "pinned-tested")
        self.assertIsNone(_validate_supported_spec(checked))
        self.assertEqual(
            _pip_source(checked),
            "git+https://github.com/dilukhin/ssh_relay.git@" + "a" * 40,
        )

        safe = parsed["agent-safe"]
        self.assertEqual(safe.update_policy, "follow-branch")
        self.assertEqual(safe.tracking_branch, "master")
        self.assertIsNone(safe.ref)

        proxy = parsed["proxy-tools"]
        self.assertEqual(proxy.source, "builtin")
        self.assertEqual(proxy.entrypoints, ("opencode-proxied", "codex-proxied"))
        tunnel = parsed["tunnelctl"]
        self.assertEqual(tunnel.update_policy, "follow-branch")
        self.assertEqual(tunnel.tracking_branch, "main")
        self.assertIsNone(tunnel.ref)
        installed_tunnel = execution["tunnelctl"]
        self.assertEqual(installed_tunnel.update_policy, "pinned-tested")
        self.assertEqual(installed_tunnel.ref, "c" * 40)
        self.assertEqual(installed_tunnel.runtime, "go-binary")
        self.assertIsNone(_validate_supported_spec(installed_tunnel))


if __name__ == "__main__":
    unittest.main()
