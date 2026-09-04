from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import setup_tools  # noqa: E402


class FollowBranchToolPolicyTests(unittest.TestCase):
    def _raw(self, **overrides):
        value = {
            "source": "git",
            "repo": "https://github.com/example/tool.git",
            "branch": "main",
            "project_directory": "tool",
            "runtime": "python-venv",
            "update_policy": "follow-branch",
            "entrypoints": ["tool"],
            "health_contract": [{"argv": ["tool", "--version"]}],
            "platforms": ["windows", "linux"],
        }
        value.update(overrides)
        return value

    def test_follow_branch_resolves_once_to_immutable_execution_ref(self) -> None:
        expected = "a" * 40
        with mock.patch.object(setup_tools, "_resolve_github_branch", return_value=expected) as resolver:
            spec, error = setup_tools.parse_tool_spec("tool", self._raw())
        self.assertIsNone(error)
        self.assertIsNotNone(spec)
        assert spec is not None
        resolver.assert_called_once_with("https://github.com/example/tool.git", "main")
        self.assertEqual(spec.ref, expected)
        self.assertEqual(spec.tracking_branch, "main")
        # Downstream deployment remains exact-ref-only after source selection.
        self.assertEqual(spec.update_policy, "pinned-tested")

    def test_follow_branch_requires_branch_and_forbids_fixed_ref(self) -> None:
        missing_branch = self._raw()
        missing_branch.pop("branch")
        spec, error = setup_tools.parse_tool_spec("tool", missing_branch)
        self.assertIsNone(spec)
        self.assertIn("requires an explicit branch", error or "")

        spec, error = setup_tools.parse_tool_spec("tool", self._raw(ref="b" * 40))
        self.assertIsNone(spec)
        self.assertIn("must not define a fixed ref", error or "")

    def test_follow_branch_rejects_non_github_source(self) -> None:
        spec, error = setup_tools.parse_tool_spec(
            "tool",
            self._raw(repo="https://git.example.invalid/example/tool.git"),
        )
        self.assertIsNone(spec)
        self.assertIn("requires an https://github.com", error or "")

    def test_git_ls_remote_yields_exact_sha_without_checkout(self) -> None:
        expected = "39dea792ee2923a8853ba5fa416fde7be24a7db6"
        completed = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=f"{expected}\trefs/heads/main\n".encode("ascii"),
            stderr=b"",
        )
        with (
            mock.patch.object(setup_tools.shutil, "which", return_value="/usr/bin/git"),
            mock.patch.object(setup_tools.subprocess, "run", return_value=completed) as run,
        ):
            actual = setup_tools._resolve_github_branch("https://github.com/dilukhin/ssh_relay.git", "main")
        self.assertEqual(actual, expected)
        run.assert_called_once_with(
            [
                "/usr/bin/git",
                "ls-remote",
                "--refs",
                "https://github.com/dilukhin/ssh_relay.git",
                "refs/heads/main",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )

    def test_follow_branch_requires_git_for_resolution(self) -> None:
        with mock.patch.object(setup_tools.shutil, "which", return_value=None):
            spec, error = setup_tools.parse_tool_spec("tool", self._raw())
        self.assertIsNone(spec)
        self.assertIn("git is required", error or "")

    def test_repository_policy_follows_first_party_production_branches(self) -> None:
        config = json.loads((ROOT / "config_data.json").read_text(encoding="utf-8"))
        tools = config["managed_environment"]["tools"]

        ssh_relay = tools["ssh_relay"]
        self.assertEqual(ssh_relay["update_policy"], "follow-branch")
        self.assertEqual(ssh_relay["branch"], "main")
        self.assertNotIn("ref", ssh_relay)

        agent_safe = tools["agent-safe"]
        self.assertEqual(agent_safe["update_policy"], "follow-branch")
        self.assertEqual(agent_safe["branch"], "master")
        self.assertNotIn("ref", agent_safe)

        proxy_tools = tools["proxy-tools"]
        self.assertEqual(proxy_tools["update_policy"], "bundled-with-setup")


if __name__ == "__main__":
    unittest.main()
