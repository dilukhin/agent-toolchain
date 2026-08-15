from __future__ import annotations

import sys
import unittest
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


if __name__ == "__main__":
    unittest.main()
